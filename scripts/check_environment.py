"""Environment checker for Digital Archaeology (Windows install helper).

Run directly or through scripts\\check_environment.bat.

Exit codes:
    0 = every required check passed
    1 = one or more required checks failed

Optional dependencies are reported separately and never fail the check.
This script is read-only: it never creates, modifies or deletes data.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

REQUIRED_PACKAGES = ("pandas", "numpy", "streamlit", "plotly")
OPTIONAL_PACKAGES = {"scikit-learn": "sklearn"}
MIN_PYTHON = (3, 11)

_failures = 0


def _ok(msg: str) -> None:
    print(f"[OK]      {msg}")


def _warn(msg: str) -> None:
    print(f"[WARN]    {msg}")


def _missing(msg: str) -> None:
    global _failures
    _failures += 1
    print(f"[MISSING] {msg}")


def main() -> int:
    print("=== Digital Archaeology - environment check ===")

    vi = sys.version_info
    if vi >= MIN_PYTHON:
        _ok(f"Python {vi.major}.{vi.minor}.{vi.micro} "
            f"(requires >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]})")
    else:
        _missing(f"Python {vi.major}.{vi.minor}.{vi.micro} is too old; "
                 f"install Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer "
                 "from https://www.python.org/downloads/")

    in_venv = (sys.prefix != getattr(sys, "base_prefix", sys.prefix)
               or bool(os.environ.get("VIRTUAL_ENV")))
    if in_venv:
        _ok(f"virtual environment: {sys.prefix}")
    else:
        _warn("no virtual environment active - run "
              "scripts\\install_windows.bat to create .venv")

    for name in REQUIRED_PACKAGES:
        if importlib.util.find_spec(name) is None:
            _missing(f"required package '{name}' is not installed - "
                     "run scripts\\install_windows.bat "
                     "(pip install -r requirements.txt)")
        else:
            _ok(f"required package '{name}'")

    for label, module in OPTIONAL_PACKAGES.items():
        if importlib.util.find_spec(module) is None:
            _warn(f"optional {label} is not installed - PCA works without "
                  "it; t-SNE charts will show a clear install hint")
        else:
            _ok(f"optional {label} available (t-SNE enabled)")

    try:
        import config
    except OSError as exc:
        _missing(f"workspace configuration failed: {exc}")
        return 1

    _ok(f"project root   : {config.PROJECT_ROOT}")
    suffix = (" (DIGITAL_ARCHAEOLOGY_HOME override)"
              if config.WORKSPACE_ENV_ACTIVE else " (default)")
    _ok(f"workspace root : {config.WORKSPACE_ROOT}{suffix}")

    for label, directory in (("data", config.DATA_DIR),
                             ("reports", config.REPORTS_DIR),
                             ("logs", config.LOGS_DIR)):
        if not directory.is_dir():
            _missing(f"directory missing: {label} -> {directory}")
        elif not os.access(directory, os.W_OK):
            _missing(f"directory not writable: {label} -> {directory} - "
                     "fix permissions or set DIGITAL_ARCHAEOLOGY_HOME "
                     "to a writable folder")
        else:
            _ok(f"directory accessible: {label} -> {directory}")

    notice = config.workspace_notice()
    if notice:
        _warn(notice)

    if config.DATABASE_PATH.exists():
        size = config.DATABASE_PATH.stat().st_size
        _ok(f"research database present, never modified by this check: "
            f"{config.DATABASE_PATH.name} ({size:,} bytes)")
    else:
        _warn("research database not found in this workspace")

    print("=== result ===")
    if _failures:
        print(f"{_failures} required check(s) failed - see [MISSING] above.")
        return 1
    print("All required checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
