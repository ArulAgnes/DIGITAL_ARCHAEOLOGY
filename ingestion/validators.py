"""Shared input validators.

Validators return a ValidationResult (ok | warning | error). Loaders turn
error-level results into typed PlatformError exceptions via ``require``.
All user messages are path-redacted; full paths stay in ``detail`` for logs.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from core.exceptions import (ConfirmationRequiredError, DatasetEmptyError,
                             InputValidationError, PlatformError,
                             SourceNotReadableError, SourceNotFoundError,
                             UnsupportedSourceError, redact_path)

_SQLITE_MAGIC = b"SQLite format 3\x00"
_SQLITE_EXTENSIONS = {".db", ".sqlite", ".sqlite3", ".sqlite-shm", ".sqlite-wal"}
_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class ValidationResult:
    ok: bool
    level: str
    code: str
    message: str
    detail: str = ""

    @classmethod
    def success(cls, code: str = "ok", message: str = "") -> "ValidationResult":
        return cls(True, "ok", code, message)

    @classmethod
    def warning(cls, code: str, message: str, detail: str = ""
                ) -> "ValidationResult":
        return cls(True, "warning", code, message, detail)

    @classmethod
    def error(cls, code: str, message: str, detail: str = ""
              ) -> "ValidationResult":
        return cls(False, "error", code, message, detail)


_CODE_TO_EXCEPTION = {
    "source_not_found": SourceNotFoundError,
    "source_not_readable": SourceNotReadableError,
    "unsupported_source": UnsupportedSourceError,
    "dataset_empty": DatasetEmptyError,
    "confirmation_required": ConfirmationRequiredError,
}


def require(result: ValidationResult) -> ValidationResult:
    """Raise a typed error if *result* is error-level; otherwise return it."""
    if not result.ok:
        exc_cls = _CODE_TO_EXCEPTION.get(result.code, InputValidationError)
        raise exc_cls(result.message, detail=result.detail)
    return result


def combine(results: list[ValidationResult]) -> ValidationResult:
    """Merge results: first error wins, else first warning, else ok."""
    for r in results:
        if r.level == "error":
            return r
    for r in results:
        if r.level == "warning":
            return r
    return ValidationResult.success()


def validate_exists(path, kind: str = "file") -> ValidationResult:
    p = Path(path)
    if not p.exists():
        what = "folder" if kind == "dir" else "file"
        return ValidationResult.error(
            "source_not_found",
            f"The selected {what} '{redact_path(p)}' does not exist.",
            detail=str(p))
    return ValidationResult.success("exists", str(p))


def validate_is_file(path) -> ValidationResult:
    p = Path(path)
    if not p.exists():
        return validate_exists(p, kind="file")
    if not p.is_file():
        return ValidationResult.error(
            "unsupported_source",
            f"'{redact_path(p)}' is not a file.",
            detail=str(p))
    return ValidationResult.success("is_file", str(p))


def validate_is_dir(path) -> ValidationResult:
    p = Path(path)
    if not p.exists():
        return validate_exists(p, kind="dir")
    if not p.is_dir():
        return ValidationResult.error(
            "unsupported_source",
            f"'{redact_path(p)}' is not a folder.",
            detail=str(p))
    return ValidationResult.success("is_dir", str(p))


def validate_readable(path) -> ValidationResult:
    p = Path(path)
    try:
        if p.is_dir():
            next(p.iterdir(), None)
            return ValidationResult.success("readable", str(p))
        with open(p, "rb") as fh:
            fh.read(1)
        return ValidationResult.success("readable", str(p))
    except PermissionError as exc:
        return ValidationResult.error(
            "source_not_readable",
            f"'{redact_path(p)}' cannot be read (permission denied).",
            detail=f"{p}: {exc}")
    except OSError as exc:
        return ValidationResult.error(
            "source_not_readable",
            f"'{redact_path(p)}' cannot be read.",
            detail=f"{p}: {exc}")


def validate_extension(path, allowed: set[str],
                       fallback_level: str = "error") -> ValidationResult:
    p = Path(path)
    ext = p.suffix.lower()
    if ext in {a.lower() for a in allowed}:
        return ValidationResult.success("extension_ok", ext)
    if fallback_level == "warning":
        return ValidationResult.warning(
            "unexpected_extension",
            f"'{redact_path(p)}' has extension '{ext or '(none)'}'; "
            "proceeding as delimited text.",
            detail=str(p))
    return ValidationResult.error(
        "unsupported_source",
        f"'{redact_path(p)}' has unsupported extension "
        f"'{ext or '(none)'}'.",
        detail=str(p))


def validate_nonempty_file(path) -> ValidationResult:
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError as exc:
        return ValidationResult.error(
            "source_not_readable",
            f"'{redact_path(p)}' cannot be read.",
            detail=f"{p}: {exc}")
    if size == 0:
        return ValidationResult.error(
            "dataset_empty",
            f"'{redact_path(p)}' is empty (0 bytes).",
            detail=str(p))
    return ValidationResult.success("nonempty", str(size))


def validate_empty_dir_warning(path) -> ValidationResult:
    p = Path(path)
    try:
        has_entries = next(p.iterdir(), None) is not None
    except OSError as exc:
        return ValidationResult.error(
            "source_not_readable",
            f"'{redact_path(p)}' cannot be read.",
            detail=f"{p}: {exc}")
    if not has_entries:
        return ValidationResult.warning(
            "empty_folder", f"'{redact_path(p)}' contains no entries.",
            detail=str(p))
    return ValidationResult.success("has_entries", str(p))


def validate_csv_headers(names) -> ValidationResult:
    cols = [str(n) for n in names]
    if not cols:
        return ValidationResult.error(
            "dataset_empty", "The file has no header row.", detail="no columns")
    seen: set[str] = set()
    dupes: list[str] = []
    for c in cols:
        key = c.casefold()
        if key in seen and c not in dupes:
            dupes.append(c)
        seen.add(key)
    if dupes:
        return ValidationResult.error(
            "duplicate_headers",
            "The file contains duplicate column names: "
            + ", ".join(dupes[:5]),
            detail=f"duplicates={dupes}")
    if all(c.lower().startswith("unnamed:") for c in cols):
        return ValidationResult.warning(
            "missing_header",
            "The file has no header row; generic column names will be used.",
            detail=str(cols[:5]))
    return ValidationResult.success("headers_ok", str(cols[:5]))


def validate_table_name(name) -> ValidationResult:
    if not name or not _TABLE_NAME_RE.match(str(name)):
        return ValidationResult.error(
            "invalid_table_name",
            "Table names may only contain letters, digits and underscores "
            "and must not start with a digit.",
            detail=f"name={name!r}")
    return ValidationResult.success("table_name_ok", str(name))


def sqlite_has_magic(path) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(16) == _SQLITE_MAGIC
    except OSError:
        return False


def validate_sqlite_file(path) -> ValidationResult:
    p = Path(path)
    ext_res = validate_extension(p, _SQLITE_EXTENSIONS)
    if ext_res.level == "error":
        return ext_res
    if not sqlite_has_magic(p):
        return ValidationResult.error(
            "unsupported_source",
            f"'{redact_path(p)}' is not a valid SQLite database file.",
            detail=str(p))
    return ValidationResult.success("sqlite_ok", str(p))


def is_drive_root(path) -> bool:
    p = Path(path)
    try:
        return p.parent == p
    except (OSError, ValueError):
        return False


def validate_drive_confirmation(path, confirmed: bool) -> ValidationResult:
    if is_drive_root(path) and not confirmed:
        return ValidationResult.error(
            "confirmation_required",
            f"Scanning an entire drive ('{redact_path(path)}') requires "
            "explicit confirmation (--confirm-drive).",
            detail=str(path))
    return ValidationResult.success("confirmed", str(path))


def validate_file_readable(path) -> ValidationResult:
    return validate_readable(path)


def check_file_access(path) -> bool:
    return os.access(Path(path), os.R_OK)
