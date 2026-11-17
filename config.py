"""Central configuration for the Digital Archaeology project.

All paths are local to the project workspace. No network access is used.
"""
from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent

DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
DATABASE_DIR = DATA_DIR / "database"

REPORTS_DIR = PROJECT_ROOT / "reports"
CHARTS_DIR = REPORTS_DIR / "charts"
LOGS_DIR = PROJECT_ROOT / "logs"
TESTS_DIR = PROJECT_ROOT / "tests"

for _d in (RAW_DIR, PROCESSED_DIR, SNAPSHOT_DIR, DATABASE_DIR, REPORTS_DIR,
           CHARTS_DIR, LOGS_DIR, TESTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------
# Scan target
# --------------------------------------------------------------------------
SCAN_ROOT = Path(os.environ.get("DA_SCAN_ROOT", "D:\\"))

# The project workspace itself must never be scanned (avoids self-reference).
WORKSPACE_EXCLUSION = PROJECT_ROOT

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
INPUT_DATA_DIR = PROJECT_ROOT / "input_data"
INPUT_CSV_DIR = INPUT_DATA_DIR / "csv"
INPUT_DB_DIR = INPUT_DATA_DIR / "databases"
INPUT_META_DIR = INPUT_DATA_DIR / "metadata"

OUTPUT_DIR = PROJECT_ROOT / "output"
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
