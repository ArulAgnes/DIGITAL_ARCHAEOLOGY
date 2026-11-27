"""Filesystem input loader: wraps the existing FilesystemScanner safely.

Guarantees
----------
* Scan output (files/folders/errors CSVs) is written ONLY into the caller's
  output directory (a ProcessingContext dataset dir or a temp dir) - never to
  the research raw CSVs, never with resume state shared with research scans.
* Only file *metadata* is read; file contents are never opened, so the source
  tree stays byte-for-byte unchanged.
* Whole-drive roots require explicit confirmation.
"""
from __future__ import annotations

import logging
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pandas as pd

import config
from core.dataset import (Dataset, DatasetMetadata, SourceType,
                          ValidationStatus, make_dataset_id)
from core.dataset_metadata import (build_metadata, columns_from_dataframe,
                                   now_iso)
from core.exceptions import (InputValidationError, MalformedDataError,
                             redact_path)
from core.processing_context import (ProcessingContext, assert_writable_target,
                                     is_protected_research_path)
from ingestion.base_loader import BaseLoader
from ingestion.validators import (ValidationResult, combine,
                                  is_drive_root, require,
                                  validate_drive_confirmation,
                                  validate_empty_dir_warning,
                                  validate_exists, validate_is_dir,
                                  validate_readable)
from scanner.filesystem_scanner import FilesystemScanner, ScanResult

log = logging.getLogger("da.ingestion.filesystem")

DEFAULT_CHUNK_SIZE = 100_000
SAMPLE_ROWS = 1_000
PREVIEW_LIMIT = 50


class FilesystemLoader(BaseLoader):
    def __init__(self, root, context: ProcessingContext | None = None,
                 confirm_drive: bool = False,
                 workspace: Path | None = None,
                 chunk_size: int = DEFAULT_CHUNK_SIZE):
        self.root = Path(root)
        self.context = context
        self.confirm_drive = bool(confirm_drive)
        self.workspace = Path(workspace) if workspace else config.WORKSPACE_EXCLUSION
        self.chunk_size = int(chunk_size)
        self._out_dir: Path | None = None
        self._scan_result: ScanResult | None = None
        self._validated = False

    # ------------------------------------------------------------------ #
    # Output isolation
    # ------------------------------------------------------------------ #
    def output_dir(self) -> Path:
        if self._out_dir is None:
            if self.context is not None:
                ds_id = make_dataset_id(SourceType.FILESYSTEM, self.root)
                target = self.context.dataset_dir(ds_id)
            else:
                target = Path(tempfile.mkdtemp(prefix="da_filesystem_"))
            assert_writable_target(target)
            if is_protected_research_path(target):
                raise InputValidationError(
                    "Scan output would land in protected research data.",
                    detail=str(target))
            self._out_dir = target
        return self._out_dir

    def _outputs(self) -> dict:
        d = self.output_dir()
        return {
            "files": d / "files_metadata.csv",
            "folders": d / "folders_metadata.csv",
            "errors": d / "scan_errors.csv",
            "resume": d / "scan_resume.json",
        }

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #
    def validate(self) -> list[ValidationResult]:
        results: list[ValidationResult] = [validate_exists(self.root, "dir")]
        if results[-1].level == "error":
            self._results = results
            return results
        results.extend([
            validate_is_dir(self.root),
            validate_drive_confirmation(self.root, self.confirm_drive),
            validate_readable(self.root),
            validate_empty_dir_warning(self.root),
        ])
        self._results = results
        return results

    def _require_valid(self) -> None:
        if not self._validated:
            require(combine(self.validate()))
            self._validated = True

    def preview(self, limit: int = PREVIEW_LIMIT) -> list[dict]:
        """Top-level entries only - cheap orientation before a full scan.

        Deliberately does NOT require the drive-root confirmation: listing
        one level is not a scan. Confirmation is enforced by ``scan()`` /
        ``to_dataset()`` via ``_require_valid()``.
        """
        for res in (validate_exists(self.root, "dir"),
                    validate_is_dir(self.root),
                    validate_readable(self.root)):
            require(res)
        entries: list[dict] = []
        for i, item in enumerate(sorted(self.root.iterdir(),
                                        key=lambda p: p.name.lower())):
            if i >= limit:
                break
            entries.append({
                "name": item.name,
                "kind": "folder" if item.is_dir() else "file",
            })
        return entries

    # ------------------------------------------------------------------ #
    # Scan
    # ------------------------------------------------------------------ #
    def _progress_bridge(self, files: int, folders: int, path: str):
        if self.context is not None:
            self.context.report_progress(
                "scan", files, None, message=str(path))

    def scan(self, progress_cb=None) -> ScanResult:
        self._require_valid()
        if (self.context is not None and self.context.cancelled):
            raise InputValidationError(
                "The operation was cancelled before the scan started.",
                detail="context.cancelled")
        outputs = self._outputs()
        assert_writable_target(outputs["files"])
        scanner = FilesystemScanner(
            root=self.root,
            workspace=self.workspace,
            outputs=outputs,
            enable_resume=False,
        )
        cb = progress_cb
        if cb is None and self.context is not None:
            cb = self._progress_bridge
        log.info("Filesystem scan: root=%s out=%s",
                 redact_path(self.root), redact_path(self.output_dir()))
        result = scanner.scan(progress_cb=cb)
        self._scan_result = result
        if self.context is not None:
            self.context.report_progress(
                "scan_done", result.files_scanned, result.files_scanned,
                message=f"files={result.files_scanned} "
                        f"folders={result.folders_scanned}")
        return result

    @property
    def scan_result(self) -> ScanResult | None:
        return self._scan_result

    # ------------------------------------------------------------------ #
    # Metadata
    # ------------------------------------------------------------------ #
    def load_metadata(self) -> DatasetMetadata:
        self._require_valid()
        if self._scan_result is None:
            self.scan()
        outputs = self._outputs()
        files_csv = outputs["files"]
        if not files_csv.exists():
            raise InputValidationError(
                "The folder scan produced no metadata output.",
                detail=str(files_csv))
        header = pd.read_csv(files_csv, nrows=0, encoding="utf-8-sig")
        sample = pd.read_csv(files_csv, nrows=SAMPLE_ROWS, encoding="utf-8-sig")
        res = self._scan_result
        warnings: list[str] = []
        if res is not None and res.errors:
            warnings.append(
                f"{res.errors} items could not be read during the scan")
        if res is not None and res.excluded_dirs:
            warnings.append(
                f"{len(res.excluded_dirs)} system/protected folders skipped")
        if res is not None and res.files_scanned == 0:
            warnings.append("no files found under the selected folder")
        try:
            source_size = sum(
                p.stat().st_size for p in self.root.iterdir() if p.is_file())
        except OSError:
            source_size = None
        columns = columns_from_dataframe(sample)
        return build_metadata(
            row_count=res.files_scanned if res else None,
            columns=columns,
            file_size_bytes=source_size,
            source_info={
                "format": "filesystem",
                "root": str(self.root),
                "scan_id": res.scan_id if res else "",
                "status": res.status if res else "",
                "folders_scanned": res.folders_scanned if res else 0,
                "errors": res.errors if res else 0,
                "duration_seconds": res.duration_seconds if res else None,
                "sample_rows": len(sample),
                "header": [str(c) for c in header.columns],
                "path_name": redact_path(self.root),
            },
            warnings=warnings,
        )

    # ------------------------------------------------------------------ #
    # Reading (chunked access to the scan output)
    # ------------------------------------------------------------------ #
    def iter_chunks(self, chunk_size: int | None = None
                    ) -> Iterator[pd.DataFrame]:
        self._require_valid()
        if self._scan_result is None:
            self.scan()
        files_csv = self._outputs()["files"]
        if not files_csv.exists():
            return
        size = chunk_size or self.chunk_size
        try:
            yield from pd.read_csv(files_csv, encoding="utf-8-sig",
                                   chunksize=size)
        except (pd.errors.ParserError, UnicodeDecodeError) as exc:
            raise MalformedDataError(
                "The scan output could not be read back.",
                detail=repr(exc)) from exc

    # ------------------------------------------------------------------ #
    # Dataset
    # ------------------------------------------------------------------ #
    def to_dataset(self) -> Dataset:
        results = self.validate()
        combined = combine(results)
        if combined.level == "error":
            require(combined)
        ds = Dataset.from_source(SourceType.FILESYSTEM, self.root)
        ds.validation_status = ValidationStatus(
            "ok" if combined.level == "ok"
            else "warning" if combined.level == "warning" else "failed")
        ds.metadata = self.load_metadata()
        ds.notes.append(f"validated at {now_iso()}")
        log.info("Filesystem dataset ready: %s files=%s",
                 redact_path(self.root), ds.metadata.row_count)
        return ds
