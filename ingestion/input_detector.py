"""Input source detection: extension hints + content sniffing (never executes
the file - only reads a few bytes in binary mode)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from core.dataset import SourceType
from core.exceptions import redact_path
from ingestion.validators import sqlite_has_magic

log = logging.getLogger("da.ingestion.detect")

TEXT_EXTENSIONS = {".csv", ".tsv", ".txt"}
SQLITE_EXTENSIONS = {".db", ".sqlite", ".sqlite3"}
HINT_ALIASES = {
    "csv": SourceType.CSV,
    "table": SourceType.CSV,
    "sqlite": SourceType.SQLITE,
    "db": SourceType.SQLITE,
    "database": SourceType.SQLITE,
    "filesystem": SourceType.FILESYSTEM,
    "folder": SourceType.FILESYSTEM,
    "dir": SourceType.FILESYSTEM,
    "directory": SourceType.FILESYSTEM,
}


@dataclass
class DetectionResult:
    path: Path
    source_type: SourceType | None = None
    status: str = "unsupported"
    reason: str = ""
    confidence: str = "high"
    warnings: list[str] = field(default_factory=list)

    @property
    def detected(self) -> bool:
        return self.source_type is not None and self.status == "detected"

    def suggested_loader(self):
        if self.source_type is SourceType.CSV:
            from ingestion.csv_loader import CsvLoader
            return CsvLoader
        if self.source_type is SourceType.SQLITE:
            from ingestion.sqlite_loader import SqliteLoader
            return SqliteLoader
        if self.source_type is SourceType.FILESYSTEM:
            from ingestion.filesystem_loader import FilesystemLoader
            return FilesystemLoader
        return None


def _normalize_hint(hint) -> SourceType | None:
    if hint is None:
        return None
    if isinstance(hint, SourceType):
        return hint
    key = str(hint).strip().lower()
    if key in ("", "auto", "none", "detect"):
        return None
    return HINT_ALIASES.get(key)


def _looks_like_text(path: Path) -> tuple[bool, str]:
    try:
        with open(path, "rb") as fh:
            head = fh.read(65_536)
    except OSError as exc:
        return False, f"unreadable: {exc}"
    if b"\x00" in head:
        return False, "binary content"
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            text = head.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return False, "not decodable as text"
    if "\n" in text or "\r" in text:
        return True, f"text ({enc})"
    return False, "no row separators found"


def detect(path, source_type=None) -> DetectionResult:
    p = Path(path)
    hint = _normalize_hint(source_type)

    if not p.exists():
        return DetectionResult(
            p, None, "not_found",
            f"'{redact_path(p)}' does not exist.", "high")

    if p.is_dir():
        if hint is not None and hint is not SourceType.FILESYSTEM:
            return DetectionResult(
                p, None, "unsupported",
                "The selected source is a folder, but a "
                f"{hint.value} input was requested.", "high")
        return DetectionResult(
            p, SourceType.FILESYSTEM, "detected",
            "folder selected", "high")

    is_sqlite = sqlite_has_magic(p)
    ext = p.suffix.lower()
    warnings: list[str] = []

    if is_sqlite:
        if hint is SourceType.CSV:
            return DetectionResult(
                p, None, "unsupported",
                f"'{redact_path(p)}' contains SQLite data, not CSV text.",
                "high")
        if ext not in SQLITE_EXTENSIONS:
            warnings.append(
                f"extension '{ext or '(none)'}' is not a typical SQLite "
                "extension - content header says SQLite")
        return DetectionResult(
            p, SourceType.SQLITE, "detected",
            "SQLite content header detected", "high", warnings)

    if ext in SQLITE_EXTENSIONS:
        return DetectionResult(
            p, None, "unsupported",
            f"'{redact_path(p)}' has a SQLite extension but is not a valid "
            "SQLite file.", "high")

    if hint is SourceType.FILESYSTEM:
        return DetectionResult(
            p, None, "unsupported",
            f"'{redact_path(p)}' is a file; a folder is required.", "high")

    text_ok, text_detail = _looks_like_text(p)
    if hint is SourceType.SQLITE:
        return DetectionResult(
            p, None, "unsupported",
            f"'{redact_path(p)}' is not a valid SQLite file "
            f"({text_detail}).", "high")

    if text_ok:
        if ext not in TEXT_EXTENSIONS:
            warnings.append(
                f"extension '{ext or '(none)'}' treated as delimited text")
        if hint is SourceType.CSV:
            return DetectionResult(
                p, SourceType.CSV, "detected", "csv hint confirmed", "high",
                warnings)
        return DetectionResult(
            p, SourceType.CSV, "detected", f"text content detected "
            f"({text_detail})", "medium" if ext in TEXT_EXTENSIONS else "low",
            warnings)

    if hint is SourceType.CSV:
        return DetectionResult(
            p, None, "unsupported",
            f"'{redact_path(p)}' does not look like delimited text "
            f"({text_detail}).", "high")

    return DetectionResult(
        p, None, "unsupported",
        f"'{redact_path(p)}' is not a supported input type "
        f"({text_detail}).", "high")
