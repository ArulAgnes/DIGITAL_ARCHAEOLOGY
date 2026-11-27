"""Snapshot system: store per-scan summaries and compare consecutive scans.

A single scan is only a *current* snapshot. Historical claims are made only
when at least two snapshots exist.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

import config

log = logging.getLogger("da.snapshots")


def _index() -> list[dict]:
    if config.SNAPSHOT_INDEX.exists():
        try:
            return json.loads(config.SNAPSHOT_INDEX.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            log.warning("snapshot index unreadable: %s", exc)
    return []


def _save_index(idx: list[dict]):
    config.SNAPSHOT_INDEX.write_text(json.dumps(idx, indent=2),
                                     encoding="utf-8")


def create_snapshot(scan_report: dict,
                    files: pd.DataFrame | None = None,
                    folders: pd.DataFrame | None = None,
                    scan_errors: pd.DataFrame | None = None) -> dict:
    """Persist a snapshot for one completed scan."""
    if files is None:
        files = pd.read_csv(config.FILES_CSV, encoding="utf-8-sig") \
            if config.FILES_CSV.exists() else pd.DataFrame()
    if folders is None:
        folders = pd.read_csv(config.FOLDERS_CSV, encoding="utf-8-sig") \
            if config.FOLDERS_CSV.exists() else pd.DataFrame()

    snapshot_id = scan_report.get("scan_id") or datetime.now().strftime(
        "%Y%m%d_%H%M%S")

    ext_counts = (files["file_extension"].value_counts().to_dict()
                  if not files.empty else {})
    root_storage = (files.groupby("root_folder")["file_size_bytes"].sum()
                    .to_dict() if not files.empty else {})
    root_counts = (files.groupby("root_folder").size().to_dict()
                   if not files.empty else {})

    # folder-level storage for change detection (path -> bytes)
    folder_storage = (folders.set_index("folder_path")["total_size_bytes"]
                      .to_dict() if not folders.empty else {})

    snapshot = {
        "snapshot_id": snapshot_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "scan_started_at": scan_report.get("started_at", ""),
        "scan_finished_at": scan_report.get("finished_at", ""),
        "scan_duration_seconds": scan_report.get("duration_seconds", 0),
        "status": scan_report.get("status", ""),
        "root": scan_report.get("root", ""),
        "total_files": int(scan_report.get("files_scanned", len(files))),
        "total_folders": int(scan_report.get("folders_scanned", len(folders))),
        "total_storage_bytes": int(scan_report.get("total_size_bytes",
                                     int(files["file_size_bytes"].sum())
                                     if not files.empty else 0)),
        "scan_errors": int(scan_report.get("scan_errors", 0)),
        "extension_counts": {str(k): int(v) for k, v in ext_counts.items()},
        "root_storage_bytes": {str(k): int(v) for k, v in root_storage.items()},
        "root_file_counts": {str(k): int(v) for k, v in root_counts.items()},
        "folder_storage_bytes": {str(k): int(v)
                                 for k, v in folder_storage.items()},
        "file_paths": files["full_file_path"].tolist() if not files.empty else [],
        "file_modified": (files.set_index("full_file_path")["modified_timestamp"]
                          .to_dict() if not files.empty else {}),
    }

    # store the heavy parts separately to keep the index light
    snap_path = config.SNAPSHOT_DIR / f"snapshot_{snapshot_id}.json"
    slim = {k: v for k, v in snapshot.items()
            if k not in ("file_paths", "file_modified", "folder_storage_bytes")}
    slim["has_file_level_detail"] = True
    snap_path.write_text(json.dumps(slim, indent=1), encoding="utf-8")

    detail_path = config.SNAPSHOT_DIR / f"snapshot_{snapshot_id}_detail.json"
    detail_path.write_text(json.dumps({
        "file_paths": snapshot["file_paths"],
        "file_modified": snapshot["file_modified"],
        "folder_storage_bytes": snapshot["folder_storage_bytes"],
    }), encoding="utf-8")

    idx = [s for s in _index() if s.get("snapshot_id") != snapshot_id]
    idx.append({k: v for k, v in slim.items()
                if k not in ("extension_counts", "root_storage_bytes",
                             "root_file_counts")})
    idx.sort(key=lambda s: s.get("created_at", ""))
    _save_index(idx)
    log.info("Snapshot %s stored (%d files)", snapshot_id,
             snapshot["total_files"])
    return snapshot


def list_snapshots() -> list[dict]:
    return _index()


def load_snapshot(snapshot_id: str) -> dict | None:
    p = config.SNAPSHOT_DIR / f"snapshot_{snapshot_id}.json"
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    d = config.SNAPSHOT_DIR / f"snapshot_{snapshot_id}_detail.json"
    if d.exists():
        detail = json.loads(d.read_text(encoding="utf-8"))
        data.update(detail)
    else:
        data.setdefault("file_paths", [])
        data.setdefault("file_modified", {})
        data.setdefault("folder_storage_bytes", {})
    return data


def compare(snapshot_a: str, snapshot_b: str) -> dict:
    """Compare two snapshots. Returns {} if either is unavailable."""
    a = load_snapshot(snapshot_a)
    b = load_snapshot(snapshot_b)
    if not a or not b:
        log.warning("Comparison unavailable: missing snapshot")
        return {}
    for label, data in (("baseline", a), ("comparison", b)):
        if data.get("valid", True) is False:
            log.warning("Comparison refused: %s snapshot %s is marked invalid",
                        label, data.get("snapshot_id"))
            return {}

    paths_a = set(a.get("file_paths", []))
    paths_b = set(b.get("file_paths", []))
    new_files = sorted(paths_b - paths_a)
    removed_files = sorted(paths_a - paths_b)

    mod_a = a.get("file_modified", {})
    mod_b = b.get("file_modified", {})
    modified = sorted(
        p for p in (paths_a & paths_b)
        if mod_a.get(p) != mod_b.get(p)
    )

    folders_a = set(a.get("folder_storage_bytes", {}))
    folders_b = set(b.get("folder_storage_bytes", {}))
    new_folders = sorted(folders_b - folders_a)

    ext_a = a.get("extension_counts", {})
    ext_b = b.get("extension_counts", {})
    ext_change = {
        k: {"before": ext_a.get(k, 0), "after": ext_b.get(k, 0),
            "delta": ext_b.get(k, 0) - ext_a.get(k, 0)}
        for k in sorted(set(ext_a) | set(ext_b))
        if ext_a.get(k, 0) != ext_b.get(k, 0)
    }

    storage_a = int(a.get("total_storage_bytes", 0))
    storage_b = int(b.get("total_storage_bytes", 0))

    return {
        "baseline": snapshot_a,
        "comparison": snapshot_b,
        "baseline_created": a.get("created_at", ""),
        "comparison_created": b.get("created_at", ""),
        "file_count_before": int(a.get("total_files", 0)),
        "file_count_after": int(b.get("total_files", 0)),
        "file_count_delta": int(b.get("total_files", 0)) - int(a.get("total_files", 0)),
        "storage_before_bytes": storage_a,
        "storage_after_bytes": storage_b,
        "storage_delta_bytes": storage_b - storage_a,
        "new_files_count": len(new_files),
        "removed_files_count": len(removed_files),
        "modified_files_count": len(modified),
        "new_folders_count": len(new_folders),
        "new_files_sample": new_files[:200],
        "removed_files_sample": removed_files[:200],
        "modified_files_sample": modified[:200],
        "new_folders_sample": new_folders[:100],
        "extension_changes": ext_change,
        "historical_claim_supported": True,
    }
