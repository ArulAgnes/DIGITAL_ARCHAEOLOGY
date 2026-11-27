"""Interactive ingestion workflow (Phase 4): UI-independent controller.

Pure logic shared by the Streamlit "Data Sources" page and the tests.
Guarantees: source files are never modified; previews are bounded (a large
file is never fully loaded just to show a few rows); every registration runs
inside its own ProcessingContext under ``output/`` and never touches the
protected research data.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

import config
from core.dataset import Dataset, DatasetMetadata, SourceType, ValidationStatus
from core.exceptions import PlatformError, redact_path
from core.processing_context import ProcessingContext, assert_writable_target
from ingestion.csv_loader import CsvLoader
from ingestion.dataset_registry import DatasetRegistry
from ingestion.filesystem_loader import FilesystemLoader
from ingestion.sqlite_loader import SqliteLoader
from ingestion import validators
from ingestion.validators import combine, is_drive_root

log = logging.getLogger("da.workflow")

SOURCE_CHOICES = ("CSV", "SQLite", "Folder")
DEFAULT_PREVIEW_ROWS = 100
CSV_ROW_COUNT_MAX_BYTES = 100_000_000
LARGE_SCAN_TOP_LEVEL_ENTRIES = 1000
MAX_PREVIEW_ROWS = 500

_KIND_TO_TYPE = {
    "CSV": SourceType.CSV,
    "SQLite": SourceType.SQLITE,
    "Folder": SourceType.FILESYSTEM,
}


@dataclass
class Inspection:
    ok: bool
    kind: str
    path: Path
    detection: str = ""
    error: str = ""
    validations: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    row_count: int | None = None
    column_count: int | None = None
    file_size: int | None = None
    columns: list[dict] = field(default_factory=list)
    preview: pd.DataFrame | None = None
    metadata: DatasetMetadata | None = None
    extra: dict = field(default_factory=dict)

    @property
    def source_type(self) -> SourceType:
        return _KIND_TO_TYPE[self.kind]

    @property
    def validation_level(self) -> str:
        for v in self.validations:
            if v["level"] == "error":
                return "error"
        for v in self.validations:
            if v["level"] == "warning":
                return "warning"
        return "ok"

    @property
    def needs_drive_confirm(self) -> bool:
        return bool(self.extra.get("is_drive_root"))

    @property
    def needs_large_ack(self) -> bool:
        return bool(self.extra.get("is_large_scan"))

    def summary(self) -> dict:
        """JSON-safe description (preview excluded)."""
        return {
            "ok": self.ok,
            "kind": self.kind,
            "path_name": redact_path(self.path),
            "detection": self.detection,
            "error": self.error,
            "validations": self.validations,
            "warnings": self.warnings,
            "row_count": self.row_count,
            "column_count": self.column_count,
            "file_size": self.file_size,
            "columns": self.columns,
            "extra": {k: v for k, v in self.extra.items()
                      if k != "entries_preview"},
        }


@dataclass
class RegistrationResult:
    ok: bool
    error: str = ""
    dataset_id: str = ""
    job_id: str = ""
    metadata_path: str = ""
    registry_id: str | None = None
    summary: dict = field(default_factory=dict)


# ---------------------------------------------------------------------- #
# Inspection helpers
# ---------------------------------------------------------------------- #
def _validations(results) -> list[dict]:
    return [{"level": r.level, "code": r.code, "message": r.message}
            for r in results]


def _columns_df_list(columns) -> list[dict]:
    return [c.to_dict() if hasattr(c, "to_dict") else dict(c)
            for c in columns]


def _fail(kind: str, path, message: str,
          validations: list[dict] | None = None,
          extra: dict | None = None) -> Inspection:
    return Inspection(ok=False, kind=kind, path=Path(path),
                      error=message, validations=validations or [],
                      extra=extra or {})


def inspect_csv(path, preview_rows: int = DEFAULT_PREVIEW_ROWS) -> Inspection:
    """Validate + bounded preview of a CSV (no full read for large files)."""
    p = Path(path)
    loader = CsvLoader(p)
    results = loader.validate()
    combined = combine(results)
    if combined.level == "error":
        return _fail("CSV", p, combined.message, _validations(results))
    try:
        preview = loader.preview(min(preview_rows, MAX_PREVIEW_ROWS))
        count_rows = p.stat().st_size <= CSV_ROW_COUNT_MAX_BYTES
        metadata = loader.load_metadata(count_rows=count_rows)
    except PlatformError as exc:
        return _fail("CSV", p, exc.user_message, _validations(results))
    warnings = [r["message"] for r in _validations(results)
                if r["level"] == "warning"] + list(metadata.warnings)
    return Inspection(
        ok=True, kind="CSV", path=p,
        detection=(f"delimited text - encoding {metadata.source_info.get('encoding')}, "
                   f"delimiter {metadata.source_info.get('delimiter')!r}"),
        validations=_validations(results), warnings=warnings,
        row_count=metadata.row_count, column_count=metadata.column_count,
        file_size=metadata.file_size_bytes,
        columns=_columns_df_list(metadata.columns),
        preview=preview, metadata=metadata,
        extra={"encoding": metadata.source_info.get("encoding"),
               "delimiter": metadata.source_info.get("delimiter"),
               "count_rows_computed": count_rows},
    )


def inspect_sqlite(path, table: str | None = None,
                   preview_rows: int = DEFAULT_PREVIEW_ROWS) -> Inspection:
    """Validate + bounded preview of a SQLite source (read-only)."""
    p = Path(path)
    with SqliteLoader(p, table=table) as loader:
        results = loader.validate()
        combined = combine(results)
        if combined.level == "error":
            return _fail("SQLite", p, combined.message,
                         _validations(results))
        tables = loader.list_tables()
        if table is None and len(tables) > 1:
            warnings = [r["message"] for r in _validations(results)
                        if r["level"] == "warning"]
            warnings.append(
                f"The database contains {len(tables)} tables - select one "
                "to preview and register.")
            return Inspection(
                ok=True, kind="SQLite", path=p,
                detection="SQLite content header detected",
                validations=_validations(results), warnings=warnings,
                extra={"tables": tables, "table": None, "needs_table": True},
            )
        try:
            metadata = loader.load_metadata()
            preview = loader.preview(min(preview_rows, MAX_PREVIEW_ROWS))
        except PlatformError as exc:
            return _fail("SQLite", p, exc.user_message,
                         _validations(results))
    warnings = [r["message"] for r in _validations(results)
                if r["level"] == "warning"] + list(metadata.warnings)
    return Inspection(
        ok=True, kind="SQLite", path=p,
        detection="SQLite content header detected",
        validations=_validations(results), warnings=warnings,
        row_count=metadata.row_count, column_count=metadata.column_count,
        file_size=metadata.file_size_bytes,
        columns=_columns_df_list(metadata.columns),
        preview=preview, metadata=metadata,
        extra={"tables": tables, "table": metadata.source_info.get("table"),
               "needs_table": False},
    )


def inspect_folder(path) -> Inspection:
    """Validate a folder and show top-level orientation WITHOUT scanning.

    Drive-root / large-scan confirmation is deferred to registration: the
    inspection itself only checks existence, readability and emptiness.
    """
    p = Path(path)
    loader = FilesystemLoader(p)
    results = [
        validators.validate_exists(p, "dir"),
        validators.validate_is_dir(p),
        validators.validate_readable(p),
        validators.validate_empty_dir_warning(p),
    ]
    combined = combine(results)
    if combined.level == "error":
        return _fail("Folder", p, combined.message, _validations(results))
    try:
        entries = loader.preview()
        entry_count = sum(1 for _ in p.iterdir())
    except OSError as exc:
        return _fail("Folder", p,
                     f"'{redact_path(p)}' cannot be listed.",
                     _validations(results), {"detail": repr(exc)})
    drive = is_drive_root(p)
    is_large = drive or entry_count >= LARGE_SCAN_TOP_LEVEL_ENTRIES
    warnings = [r["message"] for r in _validations(results)
                if r["level"] == "warning"]
    if drive:
        warnings.append(
            "This path is a whole drive root - scanning it requires an "
            "extra confirmation.")
    if is_large:
        warnings.append(
            "Large scan: this source may contain a very large number of "
            "files; the scan can take a long time.")
    warnings.append(
        "Scan not started - confirm below. Metadata only; file contents are "
        "never read. Mid-scan cancellation is not supported by the scanner.")
    return Inspection(
        ok=True, kind="Folder", path=p,
        detection="folder selected (metadata-only scan pending)",
        validations=_validations(results), warnings=warnings,
        file_size=None,
        extra={"entries_preview": entries[:50],
               "entry_count": entry_count,
               "is_drive_root": drive,
               "is_large_scan": is_large,
               "scanned": False},
    )


# ---------------------------------------------------------------------- #
# Registration
# ---------------------------------------------------------------------- #
def register(inspection: Inspection, confirm_drive: bool = False,
             context: ProcessingContext | None = None) -> RegistrationResult:
    """Validate confirmations, create a job context, register the dataset."""
    if inspection is None or not inspection.ok or inspection.error:
        return RegistrationResult(
            ok=False,
            error="Nothing to register - inspect a valid source first.")
    if inspection.kind == "Folder" and inspection.needs_drive_confirm \
            and not confirm_drive:
        return RegistrationResult(
            ok=False,
            error="Scanning a whole drive requires the explicit drive "
                  "confirmation.")

    ctx = context if context is not None else ProcessingContext()
    if ctx.cancelled:
        return RegistrationResult(
            ok=False,
            error="The job was cancelled before it started.",
            job_id=ctx.job_id)
    started = datetime.now()
    try:
        assert_writable_target(ctx.metadata_dir)
        ctx.ensure_dirs()
        ctx.set_status("processing")
        ctx.report_progress("registration_started", 0, None,
                            message=inspection.kind)

        scan_summary: dict = {}
        if inspection.kind == "Folder":
            loader = FilesystemLoader(inspection.path, context=ctx,
                                      confirm_drive=confirm_drive)
            dataset = loader.to_dataset()
            res = loader.scan_result
            if res is not None:
                scan_summary = {
                    "files_scanned": res.files_scanned,
                    "folders_scanned": res.folders_scanned,
                    "errors": res.errors,
                    "duration_seconds": res.duration_seconds,
                    "status": res.status,
                }
        else:
            dataset = Dataset.from_source(inspection.source_type,
                                          inspection.path)
            dataset.metadata = inspection.metadata or DatasetMetadata()
            dataset.validation_status = ValidationStatus(
                "warning" if inspection.validation_level == "warning"
                else "ok")
            dataset.notes.append(
                "registered from the Data Sources workflow")

        meta_path = ctx.write_metadata(
            "registration",
            {"job_id": ctx.job_id, "dataset": dataset.to_dict(),
             "detection": inspection.detection,
             "progress": ctx.progress})
        registry = DatasetRegistry()
        registry_id = registry.register(dataset, meta_path)
        ctx.set_status("completed")
        summary = {
            "job_id": ctx.job_id,
            "dataset_id": dataset.dataset_id,
            "name": dataset.name,
            "source_type": dataset.source_type.value,
            "row_count": dataset.metadata.row_count,
            "column_count": dataset.metadata.column_count,
            "validation_status": dataset.validation_status.value,
            "registry_id": registry_id,
            "metadata_path": str(meta_path),
            "duration_seconds": round(
                (datetime.now() - started).total_seconds(), 3),
            "scan": scan_summary,
        }
        ctx.write_metadata("job_summary", summary)
        log.info("Registered %s (%s)", redact_path(inspection.path),
                 dataset.dataset_id)
        return RegistrationResult(
            ok=True, dataset_id=dataset.dataset_id, job_id=ctx.job_id,
            metadata_path=str(meta_path), registry_id=registry_id,
            summary=summary)

    except PlatformError as exc:
        ctx.set_status("failed")
        _write_failure(ctx, inspection, exc.user_message, started)
        return RegistrationResult(ok=False, error=exc.user_message,
                                  job_id=ctx.job_id)
    except Exception as exc:  # noqa: BLE001 - surface without leaking paths
        ctx.set_status("failed")
        _write_failure(ctx, inspection,
                       f"{type(exc).__name__}: {exc}", started)
        return RegistrationResult(
            ok=False,
            error=f"Registration failed ({type(exc).__name__}). "
                  "Details are in the job summary under output/metadata/.",
            job_id=ctx.job_id)


def _write_failure(ctx: ProcessingContext, inspection: Inspection,
                   detail: str, started: datetime) -> None:
    try:
        ctx.write_metadata("job_summary", {
            "job_id": ctx.job_id,
            "status": "failed",
            "error": detail,
            "source": inspection.summary() if inspection else {},
            "duration_seconds": round(
                (datetime.now() - started).total_seconds(), 3),
        })
    except Exception:  # noqa: BLE001
        pass


def history(limit: int = 50) -> list[dict]:
    """Dataset history from the registry (empty list when never used)."""
    try:
        if not Path(config.REGISTRY_DB).exists():
            return []
        return DatasetRegistry().list()[:limit]
    except PlatformError:
        return []


# ---------------------------------------------------------------------- #
# Upload handling
# ---------------------------------------------------------------------- #
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_filename(filename: str) -> str:
    name = Path(str(filename).replace("\\", "/")).name
    name = _SAFE_NAME_RE.sub("_", name).lstrip(".")
    if not name:
        name = "upload.csv"
    return name[:150]


def save_upload(filename: str, data: bytes) -> Path:
    """Store a local copy of an uploaded file under input_data/csv/uploads/."""
    target_dir = config.INPUT_CSV_DIR / "uploads"
    assert_writable_target(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    name = sanitize_filename(filename)
    stem, dot, ext = name.partition(".")
    candidate = target_dir / name
    counter = 1
    while candidate.exists():
        candidate = target_dir / f"{stem}_{counter}{dot}{ext}"
        counter += 1
    candidate.write_bytes(data)
    log.info("Upload stored: %s (%d bytes)", redact_path(candidate),
             len(data))
    return candidate
