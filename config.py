"""Central configuration for the Digital Archaeology project.

All paths are local to the project workspace. No network access is used.

Workspace resolution (Phase 8):
- Default: the workspace equals the project root (the folder containing this
  file). Behavior is identical to Phases 0-7.
- Override: set the environment variable DIGITAL_ARCHAEOLOGY_HOME to resolve
  data, reports, logs, output and input_data under a different directory.
- The override never moves, copies or deletes existing research data. If an
  override is active while research data exists under the project root, the
  application warns instead of silently switching (workspace_notice()).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent


def resolve_workspace_root(env: Mapping[str, str] | None = None) -> Path:
    """Return the active workspace root.

    DIGITAL_ARCHAEOLOGY_HOME wins when set to a non-empty value; otherwise
    the workspace is the project root. Surrounding quotes (common in Windows
    `set` output) are tolerated; the path is expanded and resolved.
    """
    source = os.environ if env is None else env
    raw = (source.get("DIGITAL_ARCHAEOLOGY_HOME") or "").strip().strip('"')
    if not raw:
        return PROJECT_ROOT
    return Path(raw).expanduser().resolve()


WORKSPACE_ROOT = resolve_workspace_root()
WORKSPACE_ENV_ACTIVE = WORKSPACE_ROOT != PROJECT_ROOT

DATA_DIR = WORKSPACE_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
DATABASE_DIR = DATA_DIR / "database"

REPORTS_DIR = WORKSPACE_ROOT / "reports"
CHARTS_DIR = REPORTS_DIR / "charts"
LOGS_DIR = WORKSPACE_ROOT / "logs"
# The test suite ships with the code, so it stays with the project root even
# when a workspace override is active.
TESTS_DIR = PROJECT_ROOT / "tests"


def _ensure_dir(path: Path) -> None:
    """Create one required directory, failing with an actionable message.

    Never deletes or overwrites anything: mkdir(..., exist_ok=True) only
    creates what is missing.
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OSError(
            f"Cannot create required directory '{path}' "
            f"(workspace: {WORKSPACE_ROOT}). "
            f"{exc.__class__.__name__}: {exc.strerror or exc}. "
            "Check that the parent location exists and is writable, or set "
            "DIGITAL_ARCHAEOLOGY_HOME to a writable folder."
        ) from exc


for _d in (RAW_DIR, PROCESSED_DIR, SNAPSHOT_DIR, DATABASE_DIR, REPORTS_DIR,
           CHARTS_DIR, LOGS_DIR, TESTS_DIR):
    _ensure_dir(_d)


def _research_data_present(root: Path) -> bool:
    """True when real research data exists under root/data.

    Uses sentinel files (not bare directories) because the import-time loop
    creates empty directories in every new workspace.
    """
    data = root / "data"
    return ((data / "raw" / "files_metadata.csv").exists()
            or any((data / "database").glob("*.db"))
            or (data / "snapshots" / "snapshots.json").exists())


def workspace_notice() -> str | None:
    """Warning when an active override would hide existing research data.

    Returns None in the default configuration (workspace == project root).
    The application must show this instead of silently loading an empty
    workspace.
    """
    if not WORKSPACE_ENV_ACTIVE:
        return None
    legacy_data = PROJECT_ROOT / "data"
    if _research_data_present(PROJECT_ROOT) and \
            not _research_data_present(WORKSPACE_ROOT):
        return (
            f"Workspace override active ({WORKSPACE_ROOT}) but existing "
            f"research data was found under {legacy_data} and is NOT being "
            "loaded. Unset DIGITAL_ARCHAEOLOGY_HOME to use the original "
            "data, or copy it into the new workspace manually."
        )
    return None


def workspace_description() -> dict:
    """Structured workspace status for the dashboard and diagnostics."""
    return {
        "root": str(WORKSPACE_ROOT),
        "project_root": str(PROJECT_ROOT),
        "env_active": WORKSPACE_ENV_ACTIVE,
        "env_var": "DIGITAL_ARCHAEOLOGY_HOME",
        "notice": workspace_notice(),
    }


# --------------------------------------------------------------------------
# Scan target (the research CLI scans this root; it is NOT the workspace)
# --------------------------------------------------------------------------
# Default is the original machine's drive, kept for reproducibility of the
# historical research workflow. Override per machine with the DA_SCAN_ROOT
# environment variable or per run with `pipeline.py --scan-root PATH`.
# The value is only used by an explicit scan; nothing scans automatically.
SCAN_ROOT = Path(os.environ.get("DA_SCAN_ROOT", "D:\\"))

# The active workspace itself must never be scanned (avoids self-reference).
WORKSPACE_EXCLUSION = WORKSPACE_ROOT

# Windows system / recovery locations excluded at every level of the tree.
SYSTEM_EXCLUSION_NAMES = {
    "system volume information",
    "$recycle.bin",
    "recycler",
    "recovery",
    "windowsapps",   # Store apps container: permission noise, not user work
    "config.msi",
}

# Optional hard excludes (path prefixes) - relative names handled above.
EXTRA_EXCLUDED_DIRS: set[str] = set()

# --------------------------------------------------------------------------
# Scan behaviour
# --------------------------------------------------------------------------
INCLUDE_HIDDEN = True            # hidden files are part of the real file system
FOLLOW_SYMLINKS = False          # never follow links / junctions out of boundary
MAX_DEPTH = 64                   # guard against pathological recursion
WRITE_BATCH_SIZE = 20000         # rows buffered before flushing CSV
RESUME_ENABLED = True            # skip folders recorded as done in resume file
RESUME_FILE = LOGS_DIR / "scan_resume.json"

# --------------------------------------------------------------------------
# Duplicate detection
# --------------------------------------------------------------------------
ENABLE_HASH_VERIFICATION = False  # SHA-256 only when explicitly enabled
HASH_MAX_BYTES = 0                # 0 = hash whole file
HASH_MIN_GROUP_SIZE = 2

# --------------------------------------------------------------------------
# Output files
# --------------------------------------------------------------------------
FILES_CSV = RAW_DIR / "files_metadata.csv"
FOLDERS_CSV = RAW_DIR / "folders_metadata.csv"
ERRORS_CSV = RAW_DIR / "scan_errors.csv"
SCAN_REPORT_JSON = REPORTS_DIR / "scan_report.json"
FINDINGS_JSON = REPORTS_DIR / "findings.json"

NAMING_CSV = PROCESSED_DIR / "naming_analysis.csv"
DUP_CANDIDATES_CSV = PROCESSED_DIR / "duplicate_candidates.csv"
DUP_VERIFIED_CSV = PROCESSED_DIR / "verified_duplicates.csv"
PROCESSED_FILES_CSV = PROCESSED_DIR / "files_processed.csv"
PROCESSED_FOLDERS_CSV = PROCESSED_DIR / "folders_processed.csv"

DATABASE_PATH = DATABASE_DIR / "digital_archaeology.db"
SNAPSHOT_INDEX = SNAPSHOT_DIR / "snapshots.json"

RESEARCH_REPORT = REPORTS_DIR / "research_report.md"
DATA_DICTIONARY = REPORTS_DIR / "data_dictionary.md"

# --------------------------------------------------------------------------
# Platform input/output (Phase 3) - created lazily via ensure_platform_dirs()
# Deliberately NOT part of the import-time loop above.
# --------------------------------------------------------------------------
INPUT_DATA_DIR = WORKSPACE_ROOT / "input_data"
INPUT_CSV_DIR = INPUT_DATA_DIR / "csv"
INPUT_DB_DIR = INPUT_DATA_DIR / "databases"
INPUT_META_DIR = INPUT_DATA_DIR / "metadata"

OUTPUT_DIR = WORKSPACE_ROOT / "output"
OUTPUT_DATASETS_DIR = OUTPUT_DIR / "datasets"
OUTPUT_ANALYSIS_DIR = OUTPUT_DIR / "analysis"
OUTPUT_REPORTS_DIR = OUTPUT_DIR / "reports"
OUTPUT_METADATA_DIR = OUTPUT_DIR / "metadata"
OUTPUT_COMPARISONS_DIR = OUTPUT_DIR / "comparisons"
OUTPUT_VISUALIZATIONS_DIR = OUTPUT_DIR / "visualizations"
REGISTRY_DB = OUTPUT_METADATA_DIR / "dataset_registry.db"

PLATFORM_DIRS = (
    INPUT_DATA_DIR, INPUT_CSV_DIR, INPUT_DB_DIR, INPUT_META_DIR,
    OUTPUT_DIR, OUTPUT_DATASETS_DIR, OUTPUT_ANALYSIS_DIR,
    OUTPUT_REPORTS_DIR, OUTPUT_METADATA_DIR, OUTPUT_COMPARISONS_DIR,
    OUTPUT_VISUALIZATIONS_DIR,
)


def ensure_platform_dirs() -> None:
    """Explicitly create the platform input/output directories."""
    for _p in PLATFORM_DIRS:
        _p.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------
LOG_FILE = LOGS_DIR / "run.log"
LOG_LEVEL = "INFO"
