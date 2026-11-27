"""File metadata extraction: classification, naming rules, attribute parsing.

Everything here operates on file *names* and file-system *attributes* only.
File contents are never read.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Extension -> category mapping (transparent, documented, data-driven)
# ---------------------------------------------------------------------------
EXTENSION_CATEGORY_MAP: dict[str, str] = {
    # Programming
    ".py": "Programming", ".js": "Programming", ".ts": "Programming",
    ".jsx": "Programming", ".tsx": "Programming", ".java": "Programming",
    ".c": "Programming", ".cpp": "Programming", ".h": "Programming",
    ".cs": "Programming", ".go": "Programming", ".rs": "Programming",
    ".php": "Programming", ".rb": "Programming", ".swift": "Programming",
    ".kt": "Programming", ".scala": "Programming", ".r": "Programming",
    ".m": "Programming", ".sql": "Programming", ".sh": "Programming",
    ".bat": "Programming", ".ps1": "Programming", ".cmd": "Programming",
    ".html": "Programming", ".css": "Programming", ".scss": "Programming",
    ".vue": "Programming", ".dart": "Programming", ".pl": "Programming",
    ".lua": "Programming", ".jl": "Programming", ".dart": "Programming",
    ".ipynb": "Programming", ".json": "Programming", ".yaml": "Programming",
    ".yml": "Programming", ".toml": "Programming", ".xml": "Programming",
    ".gradle": "Programming", ".cmake": "Programming", ".makefile": "Programming",
    ".tf": "Programming", ".dockerfile": "Programming", ".gitignore": "Programming",
    ".md": "Programming", ".ipynb": "Programming",

    # Documents
    ".doc": "Documents", ".docx": "Documents", ".pdf": "Documents",
    ".txt": "Documents", ".rtf": "Documents", ".odt": "Documents",
    ".pages": "Documents", ".tex": "Documents", ".bib": "Documents",
    ".epub": "Documents", ".mobi": "Documents", ".csv": "Spreadsheets",

    # Spreadsheets
    ".xls": "Spreadsheets", ".xlsx": "Spreadsheets", ".xlsm": "Spreadsheets",
    ".ods": "Spreadsheets", ".tsv": "Spreadsheets", ".numbers": "Spreadsheets",

    # Presentations
    ".ppt": "Presentations", ".pptx": "Presentations", ".odp": "Presentations",
    ".key": "Presentations",

    # Images
    ".png": "Images", ".jpg": "Images", ".jpeg": "Images", ".gif": "Images",
    ".bmp": "Images", ".webp": "Images", ".svg": "Images", ".ico": "Images",
    ".tiff": "Images", ".tif": "Images", ".psd": "Images", ".ai": "Images",
    ".heic": "Images", ".raw": "Images", ".cr2": "Images", ".nef": "Images",
    ".avif": "Images", ".jfif": "Images",

    # Videos
    ".mp4": "Videos", ".mkv": "Videos", ".avi": "Videos", ".mov": "Videos",
    ".wmv": "Videos", ".flv": "Videos", ".webm": "Videos", ".m4v": "Videos",
    ".mpg": "Videos", ".mpeg": "Videos", ".3gp": "Videos",

    # Audio
    ".mp3": "Audio", ".wav": "Audio", ".flac": "Audio", ".aac": "Audio",
    ".ogg": "Audio", ".wma": "Audio", ".m4a": "Audio", ".opus": "Audio",

    # Datasets
    ".parquet": "Datasets", ".feather": "Datasets", ".arrow": "Datasets",
    ".db": "Datasets", ".sqlite": "Datasets", ".sqlite3": "Datasets",
    ".sav": "Datasets", ".dta": "Datasets", ".arff": "Datasets",
    ".geojson": "Datasets", ".shp": "Datasets",

    # Archives
    ".zip": "Archives", ".rar": "Archives", ".7z": "Archives",
    ".tar": "Archives", ".gz": "Archives", ".bz2": "Archives",
    ".xz": "Archives", ".iso": "Archives", ".cab": "Archives",

    # Executables
    ".exe": "Executables", ".msi": "Executables", ".dll": "Executables",
    ".app": "Executables", ".apk": "Executables", ".com": "Executables",
    ".scr": "Executables", ".bin": "Executables",

    # System-related
    ".sys": "System-related", ".ini": "System-related", ".log": "System-related",
    ".tmp": "System-related", ".bak": "System-related", ".dat": "System-related",
    ".reg": "System-related", ".lnk": "System-related", ".url": "System-related",
    ".manifest": "System-related", ".desktop": "System-related",
}

# File names (no extension or exact names) treated as system-related
SYSTEM_FILENAMES = {"desktop.ini", "thumbs.db", ".ds_store", "ntuser.dat"}

CATEGORY_ORDER = [
    "Programming", "Documents", "Spreadsheets", "Presentations", "Images",
    "Videos", "Audio", "Datasets", "Archives", "Executables",
    "System-related", "Other",
]


def normalize_extension(name: str) -> str:
    """Return a normalised lower-case extension including the leading dot.

    Handles multi-part names such as ``archive.tar.gz`` -> ``.gz`` and
    names without an extension -> ``""``.
    """
    p = Path(name)
    suffix = p.suffix.lower()
    return suffix


def classify_file(file_name: str) -> str:
    """Map a file name to a transparent category. Contents are never read."""
    low = file_name.lower()
    if low in SYSTEM_FILENAMES:
        return "System-related"
    ext = normalize_extension(file_name)
    if not ext:
        # Extension-less special files
        if low in {"makefile", "dockerfile", "license", "readme"}:
            return "Programming"
        return "Other"
    return EXTENSION_CATEGORY_MAP.get(ext, "Other")


# ---------------------------------------------------------------------------
# Naming pattern rules  (documented, deterministic)
# ---------------------------------------------------------------------------
# NOTE: look-arounds are used instead of \b because "_" is a word character
# in regex, so "\bfinal\b" would not match "essay_final".
VERSION_RE = re.compile(
    r"(?<![a-z0-9])v(\d{1,3})(?:[._-]?\d{1,3})?(?![a-z0-9])", re.I)
VERSION_WORD_RE = re.compile(
    r"(?<![a-z0-9])version[\s._-]?\d{1,3}(?![a-z0-9])", re.I)
FINAL_RE = re.compile(r"(?<![a-z0-9])final(?:\d{1,2})?(?![a-z0-9])", re.I)
LATEST_RE = re.compile(r"(?<![a-z0-9])latest(?![a-z0-9])", re.I)
DATE_RE = re.compile(
    r"(?:19|20)\d{2}[-_.]?\d{1,2}[-_.]?\d{1,2}"
    r"|(?<!\d)\d{1,2}[-_.]\d{1,2}[-_.](?:19|20)\d{2}(?!\d)"
    r"|(?<![\d])(?:19|20)\d{2}(?![\d])", re.I)
COPY_RE = re.compile(
    r"(?:\(\d+\)|(?<![a-z0-9])copy(?:\s?\(\d+\))?(?![a-z0-9])"
    r"|(?<![a-z0-9])duplicate(?![a-z0-9])|(?<![a-z0-9])backup(?![a-z0-9]))",
    re.I)
UNDERSCORE_RE = re.compile(r"_")
SPACE_RE = re.compile(r"\s")
HYPHEN_RE = re.compile(r"-")


def _stem(name: str) -> str:
    p = Path(name)
    # strip the last suffix only (keeps "report.final.pdf" -> "report.final")
    return p.stem


def extract_naming_features(file_name: str) -> dict:
    """Extract documented naming features from a file name."""
    stem = _stem(file_name)
    low = stem.lower()
    has_version = bool(VERSION_RE.search(stem) or VERSION_WORD_RE.search(stem))
    has_final = bool(FINAL_RE.search(stem))
    has_latest = bool(LATEST_RE.search(stem))
    has_date = bool(DATE_RE.search(stem))
    has_copy = bool(COPY_RE.search(stem))
    has_underscore = bool(UNDERSCORE_RE.search(stem))
    has_space = bool(SPACE_RE.search(stem))
    has_hyphen = bool(HYPHEN_RE.search(stem))

    # capitalisation inconsistency: mixed case that is neither all-lower,
    # all-upper, nor Title Case
    letters = [c for c in stem if c.isalpha()]
    if not letters:
        cap_style = "no_letters"
    elif stem == stem.lower():
        cap_style = "lower"
    elif stem == stem.upper():
        cap_style = "upper"
    elif _is_title_case(stem):
        cap_style = "title"
    else:
        cap_style = "mixed"

    return {
        "naming_pattern": _naming_pattern_signature(
            has_underscore, has_space, has_hyphen, has_version,
            has_final, has_latest, has_date, has_copy),
        "contains_version_indicator": has_version,
        "contains_final_keyword": has_final or has_latest,
        "contains_date_indicator": has_date,
        "contains_copy_indicator": has_copy,
        "has_underscore": has_underscore,
        "has_space": has_space,
        "has_hyphen": has_hyphen,
        "capitalization_style": cap_style,
        "filename_length": len(file_name),
    }


def _is_title_case(stem: str) -> bool:
    """True when every word starts with an upper-case letter (or is all-caps/
    all-lower single token). CamelCase counts as title-like only if the first
    letter of each whitespace/punctuation separated word is upper-case."""
    words = [w for w in re.split(r"[\s_\-.]+", stem) if w]
    if not words:
        return False
    for w in words:
        if w[0].isdigit():
            continue
        if not w[0].isupper():
            return False
        # a word that is neither all-upper nor has the remainder lower-case
        # (e.g. "iPhone") is treated as mixed
        if not w.isupper() and not w[1:].islower():
            return False
    return True


def _naming_pattern_signature(u: bool, s: bool, h: bool, v: bool,
                              f: bool, l: bool, d: bool, c: bool) -> str:
    parts = []
    if u:
        parts.append("underscore")
    if s:
        parts.append("space")
    if h:
        parts.append("hyphen")
    if v:
        parts.append("version")
    if f:
        parts.append("final")
    if l:
        parts.append("latest")
    if d:
        parts.append("date")
    if c:
        parts.append("copy")
    return "+".join(parts) if parts else "plain"


# Inconsistency flags according to the documented rules (see README).
def naming_inconsistencies(row) -> list[str]:
    """Return the list of documented naming issues for a record."""
    issues: list[str] = []
    name = str(row.get("file_name", ""))
    stem = _stem(name)
    if row.get("contains_final_keyword"):
        issues.append("final_or_latest_keyword")
    if row.get("contains_version_indicator"):
        issues.append("version_indicator")
    if row.get("contains_copy_indicator"):
        issues.append("copy_indicator")
    if row.get("capitalization_style") == "mixed":
        issues.append("mixed_capitalization")
    if len(stem) > 80:
        issues.append("very_long_filename")
    if re.search(r" {2,}|\s{2,}", stem):
        issues.append("repeated_whitespace")
    if re.search(r"[_-]{3,}", stem):
        issues.append("repeated_separators")
    return issues


# ---------------------------------------------------------------------------
# File attribute / timestamp helpers
# ---------------------------------------------------------------------------
def safe_stat(path: Path):
    """Return os.stat_result or None; never raises."""
    try:
        return os.stat(path, follow_symlinks=False)
    except (OSError, PermissionError, ValueError):
        return None


def ts_to_iso(ts: float | None) -> str:
    if ts is None:
        return ""
    try:
        return datetime.fromtimestamp(ts).isoformat(sep=" ", timespec="seconds")
    except (OSError, OverflowError, ValueError):
        return ""


def ts_to_date(ts: float | None) -> str:
    if ts is None:
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except (OSError, OverflowError, ValueError):
        return ""


def folder_depth(path: Path, root: Path) -> int:
    """Depth of *path* relative to scan root. Root children = depth 1."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return 0
    return len(rel.parts)


def root_folder_of(path: Path, root: Path) -> str:
    """Top-level folder name under the scan root, or '(scan root)'.

    Called with file paths: a file directly in the scan root contributes a
    single relative component (its own name) and is attributed to
    ``(scan root)`` because it has no parent folder.
    """
    try:
        rel = path.relative_to(root)
    except ValueError:
        return "(outside root)"
    if len(rel.parts) <= 1:
        return "(scan root)"
    return rel.parts[0]


def human_size(nbytes: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(nbytes)
    for u in units:
        if size < 1024 or u == units[-1]:
            return f"{size:.2f} {u}"
        size /= 1024
    return f"{size:.2f} TB"
