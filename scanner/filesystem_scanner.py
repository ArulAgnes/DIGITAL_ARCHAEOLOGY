"""Recursive Windows file-system metadata scanner.

Design notes
------------
* Single pass using ``os.scandir`` (fast, one syscall per directory entry).
* Only file *metadata* is read. File contents are never opened.
* Symlinks / junctions / reparse points are not followed.
* The project workspace and Windows system folders are excluded.
* Every failure is recorded in ``scan_errors.csv`` - nothing is silently dropped.
* Folder statistics are accumulated bottom-up during the walk.
* Resumable: folders already completed in a previous interrupted run are skipped
  when ``config.RESUME_ENABLED`` is True and a resume file exists.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import config
from scanner.metadata_extractor import (
    classify_file,
    extract_naming_features,
    folder_depth,
    normalize_extension,
    root_folder_of,
    safe_stat,
    ts_to_date,
    ts_to_iso,
)

log = logging.getLogger("da.scanner")

FILE_FIELDS = [
    "file_id", "file_name", "file_extension", "file_size_bytes",
    "file_size_kb", "file_size_mb", "creation_timestamp", "creation_date",
    "modified_timestamp", "modified_date", "accessed_date",
    "full_file_path", "parent_folder", "root_folder", "folder_depth",
    "folder_path", "hidden_file_status", "read_only_status", "file_category",
    "naming_pattern", "contains_version_indicator", "contains_final_keyword",
    "contains_date_indicator", "contains_copy_indicator", "filename_length",
    "capitalization_style", "has_underscore", "has_space", "has_hyphen",
    "collection_timestamp",
]

FOLDER_FIELDS = [
    "folder_path", "folder_name", "parent_folder", "folder_depth",
    "direct_file_count", "recursive_file_count", "direct_subfolder_count",
    "recursive_folder_count", "total_size_bytes", "total_size_mb",
    "collection_timestamp",
]

ERROR_FIELDS = [
    "error_timestamp", "error_type", "path", "operation", "message",
]


@dataclass
class FolderStats:
    direct_files: int = 0
    recursive_files: int = 0
    direct_subfolders: int = 0
    recursive_folders: int = 0
    total_size: int = 0


@dataclass
class ScanResult:
    scan_id: str = ""
    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float = 0.0
    root: str = ""
    files_scanned: int = 0
    folders_scanned: int = 0
    total_size_bytes: int = 0
    errors: int = 0
    excluded_dirs: list = field(default_factory=list)
    skipped_resumed: int = 0
    hidden_included: bool = True
    status: str = "not_started"


class _RowWriter:
    """Thread-safe buffered CSV writer."""

    def __init__(self, path: Path, fields: list[str], append: bool = False):
        self.path = path
        self.fields = fields
        mode = "a" if append else "w"
        self._fh = path.open(mode, newline="", encoding="utf-8-sig")
        self._w = csv.DictWriter(self._fh, fieldnames=fields,
                                 extrasaction="ignore")
        if not append:
            self._w.writeheader()
        self._lock = threading.Lock()
        self.count = 0

    def write(self, row: dict):
        with self._lock:
            self._w.writerow(row)
            self.count += 1

    def flush(self):
        with self._lock:
            try:
                self._fh.flush()
            except ValueError:
                pass

    def close(self):
        with self._lock:
            self._fh.close()


class FilesystemScanner:
    """Collects file and folder metadata beneath ``config.SCAN_ROOT``."""

    def __init__(self, root: Path | None = None,
                 workspace: Path | None = None,
                 outputs: dict | None = None,
                 enable_resume: bool | None = None):
        self.root = Path(root) if root else config.SCAN_ROOT
        self.workspace = Path(workspace) if workspace else config.WORKSPACE_EXCLUSION
        outputs = outputs or {}
        self.out_files = (Path(outputs["files"]) if "files" in outputs
                          else config.FILES_CSV)
        self.out_folders = (Path(outputs["folders"]) if "folders" in outputs
                            else config.FOLDERS_CSV)
        self.out_errors = (Path(outputs["errors"]) if "errors" in outputs
                           else config.ERRORS_CSV)
        if "resume" in outputs:
            self.out_resume = Path(outputs["resume"])
        elif outputs:
            self.out_resume = self.out_files.with_name("scan_resume.json")
        else:
            self.out_resume = config.RESUME_FILE
        self._custom_outputs = bool(outputs)
        self._forced_resume = enable_resume
        self.scan_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.started_at = ""
        self.finished_at = ""
        self.duration = 0.0
        self.errors = 0
        self.files = 0
        self.folders = 0
        self.total_size = 0
        self.skipped_resumed = 0
        self.excluded_paths: list[str] = []
        self._file_id = 0
        self._writer: _RowWriter | None = None
        self._folder_writer: _RowWriter | None = None
        self._err_writer: _RowWriter | None = None
        self._collection_ts = ""
        self._done_folders: set[str] = set()
        self._resuming = False
        self._written_paths: set[str] | None = None
        self._seeded_folder_stats: dict[str, FolderStats] = {}
        self._completed_since_save = 0

    def _resume_allowed(self) -> bool:
        """Custom outputs never mix with research resume state."""
        if self._forced_resume is not None:
            return bool(self._forced_resume)
        if self._custom_outputs:
            return False
        return bool(config.RESUME_ENABLED)

    # ------------------------------------------------------------------ #
    # Exclusion logic
    # ------------------------------------------------------------------ #
    def _is_excluded_dir(self, dir_path: Path) -> bool:
        name = dir_path.name.lower()
        if name in config.SYSTEM_EXCLUSION_NAMES:
            return True
        if name in {e.lower() for e in config.EXTRA_EXCLUDED_DIRS}:
            return True
        # Workspace exclusion: never scan the project itself
        try:
            if self.workspace and (
                dir_path == self.workspace
                or self.workspace in dir_path.parents
                or dir_path in self.workspace.parents and False
            ):
                return True
        except (OSError, ValueError):
            pass
        try:
            if self.workspace and str(dir_path).lower().startswith(
                    str(self.workspace).lower() + os.sep):
                return True
            if self.workspace and str(dir_path).lower() == str(self.workspace).lower():
                return True
        except Exception:
            pass
        return False

    def _record_exclusion(self, p: Path):
        if str(p) not in self.excluded_paths:
            self.excluded_paths.append(str(p))

    # ------------------------------------------------------------------ #
    # Error recording
    # ------------------------------------------------------------------ #
    def _record_error(self, err_type: str, path, operation: str, message: str):
        self.errors += 1
        if self._err_writer:
            self._err_writer.write({
                "error_timestamp": datetime.now().isoformat(timespec="seconds"),
                "error_type": err_type,
                "path": str(path),
                "operation": operation,
                "message": str(message)[:500],
            })
        log.warning("%s | %s | %s", err_type, path, message)

    # ------------------------------------------------------------------ #
    # Main entry point
    # ------------------------------------------------------------------ #
    def scan(self, progress_cb=None) -> ScanResult:
        self.started_at = datetime.now().isoformat(timespec="seconds")
        t0 = time.time()
        self._collection_ts = self.started_at
        log.info("Scan started: root=%s id=%s", self.root, self.scan_id)

        self._load_resume()
        append = self._resuming
        for _p in (self.out_files, self.out_folders, self.out_errors):
            _p.parent.mkdir(parents=True, exist_ok=True)
        self._writer = _RowWriter(self.out_files, FILE_FIELDS, append=append)
        self._folder_writer = _RowWriter(self.out_folders, FOLDER_FIELDS,
                                         append=append)
        self._err_writer = _RowWriter(self.out_errors, ERROR_FIELDS,
                                      append=append)
        if self._resuming:
            self._seed_from_existing()

        if not self.root.exists():
            self._record_error("RootMissing", self.root, "scan",
                               "Scan root does not exist or is not accessible")
            self._finish("failed", t0)
            self._close_writers()
            return self.result()

        walk_failed = False
        try:
            stats = self._walk(self.root, progress_cb)
        except Exception as exc:  # noqa: BLE001 - never crash the scan silently
            self._record_error("FatalError", self.root, "scan", repr(exc))
            stats = FolderStats()
            walk_failed = True

        # folder rows written during the walk already include the root itself
        self.total_size = stats.total_size
        self._finish("completed", t0)
        if walk_failed:
            # keep the in-progress resume state so the run can be continued
            self._save_resume()
        self._close_writers()
        if not walk_failed:
            self._clear_resume()
        log.info("Scan finished: %d files, %d folders, %d errors",
                 self.files, self.folders, self.errors)
        return self.result()

    def _finish(self, status: str, t0: float):
        self.finished_at = datetime.now().isoformat(timespec="seconds")
        self.duration = round(time.time() - t0, 3)
        self.status = status

    def _close_writers(self):
        for w in (self._writer, self._folder_writer, self._err_writer):
            if w:
                try:
                    w.close()
                except Exception:
                    pass

    def result(self) -> ScanResult:
        return ScanResult(
            scan_id=self.scan_id,
            started_at=self.started_at,
            finished_at=self.finished_at,
            duration_seconds=self.duration,
            root=str(self.root),
            files_scanned=self.files,
            folders_scanned=self.folders,
            total_size_bytes=self.total_size,
            errors=self.errors,
            excluded_dirs=list(self.excluded_paths),
            skipped_resumed=self.skipped_resumed,
            hidden_included=config.INCLUDE_HIDDEN,
            status=self.status,
        )

    # ------------------------------------------------------------------ #
    # Resume support
    # ------------------------------------------------------------------ #
    @staticmethod
    def _csv_has_rows(path: Path) -> bool:
        try:
            with open(path, encoding="utf-8-sig", newline="") as fh:
                reader = csv.reader(fh)
                next(reader, None)
                return next(reader, None) is not None
        except (OSError, UnicodeDecodeError):
            return False

    def _load_resume(self):
        if not (self._resume_allowed() and self.out_resume.exists()):
            return
        try:
            data = json.loads(self.out_resume.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._record_error("ResumeLoad", self.out_resume, "resume",
                               repr(exc))
            self._clear_resume()
            return
        if data.get("status") != "in_progress":
            # leftover from a completed run (or an old format): never trust it
            self._clear_resume()
            return
        if data.get("scan_root", "").lower() != str(self.root).lower():
            return
        done = {str(p).lower() for p in data.get("done_folders", [])}
        if str(self.root).lower() in done:
            # the previous walk reached the root: it had finished
            self._clear_resume()
            return
        if not (self._csv_has_rows(self.out_files)
                and self._csv_has_rows(self.out_folders)):
            self._clear_resume()
            return
        self._done_folders = done
        self._resuming = True
        log.info("Resume: %d folders already completed",
                 len(self._done_folders))

    def _seed_from_existing(self):
        """Take over counters and state from the partially written CSVs."""
        paths: set[str] = set()
        file_id = 0
        count = 0
        try:
            with open(self.out_files, encoding="utf-8-sig",
                      newline="") as fh:
                for row in csv.DictReader(fh):
                    count += 1
                    try:
                        fid = int(row.get("file_id") or 0)
                    except ValueError:
                        fid = 0
                    if fid > file_id:
                        file_id = fid
                    p = row.get("full_file_path")
                    if p:
                        paths.add(str(p).lower())
        except OSError as exc:
            log.warning("resume seed (files) failed: %s", exc)
        self.files = count
        self._file_id = file_id
        self._written_paths = paths

        try:
            with open(self.out_folders, encoding="utf-8-sig",
                      newline="") as fh:
                for row in csv.DictReader(fh):
                    self.folders += 1
                    key = str(row.get("folder_path", "")).lower()
                    try:
                        self._seeded_folder_stats[key] = FolderStats(
                            recursive_files=int(
                                row.get("recursive_file_count") or 0),
                            recursive_folders=int(
                                row.get("recursive_folder_count") or 0),
                            total_size=int(row.get("total_size_bytes") or 0),
                        )
                    except (TypeError, ValueError):
                        self._seeded_folder_stats[key] = FolderStats()
        except OSError as exc:
            log.warning("resume seed (folders) failed: %s", exc)

        try:
            with open(self.out_errors, encoding="utf-8-sig",
                      newline="") as fh:
                self.errors = sum(1 for _ in csv.DictReader(fh))
        except OSError:
            pass
        log.info("Resume seed: %d file rows, %d folder rows, "
                 "file_id up to %d", self.files, self.folders, self._file_id)

    def _save_resume(self):
        if not self._resume_allowed():
            return
        for w in (self._writer, self._folder_writer, self._err_writer):
            if w:
                w.flush()
        try:
            payload = {
                "status": "in_progress",
                "scan_id": self.scan_id,
                "scan_root": str(self.root),
                "updated": datetime.now().isoformat(timespec="seconds"),
                "done_folders": sorted(self._done_folders),
            }
            self.out_resume.write_text(json.dumps(payload, indent=1),
                                       encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            self._record_error("ResumeSave", self.out_resume, "resume",
                               repr(exc))

    def _clear_resume(self):
        if not self._resume_allowed():
            return
        try:
            if self.out_resume.exists():
                self.out_resume.unlink()
        except OSError as exc:  # noqa: BLE001
            log.warning("could not remove resume file: %s", exc)

    # ------------------------------------------------------------------ #
    # Recursive walk (bottom-up folder stats)
    # ------------------------------------------------------------------ #
    def _walk(self, dir_path: Path, progress_cb=None) -> FolderStats:
        stats = FolderStats()
        is_root = (str(dir_path).lower() == str(self.root).lower())

        if not is_root and self._is_excluded_dir(dir_path):
            self._record_exclusion(dir_path)
            return stats

        if not is_root and str(dir_path).lower() in self._done_folders:
            self.skipped_resumed += 1
            seeded = self._seeded_folder_stats.get(str(dir_path).lower())
            return seeded if seeded is not None else FolderStats()

        depth = folder_depth(dir_path, self.root)

        if depth > config.MAX_DEPTH:
            self._record_error("MaxDepthExceeded", dir_path, "scandir",
                               f"depth {depth} > {config.MAX_DEPTH}")
            return stats

        try:
            with os.scandir(dir_path) as it:
                entries = list(it)
        except PermissionError as exc:
            self._record_error("PermissionDenied", dir_path, "scandir", exc)
            return stats
        except FileNotFoundError as exc:
            self._record_error("NotFound", dir_path, "scandir", exc)
            return stats
        except OSError as exc:
            self._record_error("OSError", dir_path, "scandir", exc)
            return stats

        subdirs: list[Path] = []
        for entry in entries:
            try:
                is_dir = entry.is_dir(follow_symlinks=config.FOLLOW_SYMLINKS)
            except OSError as exc:
                self._record_error("StatFailed", entry.path, "is_dir", exc)
                continue

            if is_dir:
                child = Path(entry.path)
                if self._is_excluded_dir(child):
                    self._record_exclusion(child)
                    continue
                subdirs.append(child)
            else:
                fsize = self._record_file(entry, dir_path, depth)
                if fsize is None:
                    continue
                stats.direct_files += 1
                stats.recursive_files += 1
                stats.total_size += fsize

        for sd in subdirs:
            try:
                child_stats = self._walk(sd, progress_cb)
            except Exception as exc:  # noqa: BLE001
                self._record_error("WalkError", sd, "walk", repr(exc))
                continue
            stats.direct_subfolders += 1
            stats.recursive_folders += 1 + child_stats.recursive_folders
            stats.recursive_files += child_stats.recursive_files
            stats.total_size += child_stats.total_size

        # folder row
        try:
            self._folder_writer.write({
                "folder_path": str(dir_path),
                "folder_name": dir_path.name or str(dir_path),
                "parent_folder": str(dir_path.parent),
                "folder_depth": depth,
                "direct_file_count": stats.direct_files,
                "recursive_file_count": stats.recursive_files,
                "direct_subfolder_count": stats.direct_subfolders,
                "recursive_folder_count": stats.recursive_folders,
                "total_size_bytes": stats.total_size,
                "total_size_mb": round(stats.total_size / (1024 * 1024), 3),
                "collection_timestamp": self._collection_ts,
            })
            self.folders += 1
        except Exception as exc:  # noqa: BLE001
            self._record_error("FolderRowWrite", dir_path, "write", repr(exc))
        else:
            # a folder counts as done only once its row is safely buffered
            self._done_folders.add(str(dir_path).lower())
            self._completed_since_save += 1
            if self._completed_since_save >= 500:
                self._save_resume()
                self._completed_since_save = 0

        if progress_cb and self.folders % 200 == 0:
            try:
                progress_cb(self.files, self.folders, str(dir_path))
            except Exception:
                pass
        return stats

    # ------------------------------------------------------------------ #
    # Per-file record
    # ------------------------------------------------------------------ #
    def _record_file(self, entry, parent: Path,
                     parent_depth: int) -> int | None:
        try:
            st = entry.stat(follow_symlinks=config.FOLLOW_SYMLINKS)
        except PermissionError as exc:
            self._record_error("PermissionDenied", entry.path, "stat", exc)
            return None
        except FileNotFoundError as exc:
            self._record_error("NotFound", entry.path, "stat", exc)
            return None
        except OSError as exc:
            self._record_error("OSError", entry.path, "stat", exc)
            return None

        path = Path(entry.path)
        name = entry.name

        # Skip directories already handled; reparse-point guards
        try:
            if entry.is_symlink() and not config.FOLLOW_SYMLINKS:
                self._record_error("SymlinkSkipped", path, "walk",
                                   "symbolic link not followed")
                return None
        except OSError:
            pass

        # Hidden / read-only via attributes (fallback: dot prefix)
        hidden = False
        readonly = False
        try:
            attrs = entry.stat(follow_symlinks=False).st_file_attributes
            hidden = bool(attrs & 0x2)          # FILE_ATTRIBUTE_HIDDEN
            readonly = bool(attrs & 0x1)        # FILE_ATTRIBUTE_READONLY
        except (OSError, AttributeError, ValueError):
            hidden = name.startswith(".")
            readonly = bool(st.st_mode & 0o200) is False

        if hidden and not config.INCLUDE_HIDDEN:
            return None

        if (self._written_paths is not None
                and str(path).lower() in self._written_paths):
            # row already present from the interrupted run; count its size
            # for folder statistics but never write it twice
            return int(st.st_size)

        naming = extract_naming_features(name)
        ext = normalize_extension(name)
        category = classify_file(name)

        self._file_id += 1
        try:
            self._writer.write({
                "file_id": self._file_id,
                "file_name": name,
                "file_extension": ext,
                "file_size_bytes": st.st_size,
                "file_size_kb": round(st.st_size / 1024, 3),
                "file_size_mb": round(st.st_size / (1024 * 1024), 3),
                "creation_timestamp": ts_to_iso(getattr(st, "st_ctime", None)),
                "creation_date": ts_to_date(getattr(st, "st_ctime", None)),
                "modified_timestamp": ts_to_iso(st.st_mtime),
                "modified_date": ts_to_date(st.st_mtime),
                "accessed_date": ts_to_date(getattr(st, "st_atime", None)),
                "full_file_path": str(path),
                "parent_folder": str(parent),
                "root_folder": root_folder_of(path, self.root),
                "folder_depth": parent_depth,
                "folder_path": str(parent),
                "hidden_file_status": hidden,
                "read_only_status": readonly,
                "file_category": category,
                "naming_pattern": naming["naming_pattern"],
                "contains_version_indicator": naming["contains_version_indicator"],
                "contains_final_keyword": naming["contains_final_keyword"],
                "contains_date_indicator": naming["contains_date_indicator"],
                "contains_copy_indicator": naming["contains_copy_indicator"],
                "filename_length": naming["filename_length"],
                "capitalization_style": naming["capitalization_style"],
                "has_underscore": naming["has_underscore"],
                "has_space": naming["has_space"],
                "has_hyphen": naming["has_hyphen"],
                "collection_timestamp": self._collection_ts,
            })
            self.files += 1
            return int(st.st_size)
        except Exception as exc:  # noqa: BLE001
            self._record_error("RowWrite", path, "write", repr(exc))
            return 0
