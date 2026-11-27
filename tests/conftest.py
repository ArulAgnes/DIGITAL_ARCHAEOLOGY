"""Shared test fixtures.

IMPORTANT: all test data is created inside an OS temporary directory.
No test record is ever written into the real research dataset.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def temp_tree(tmp_path: Path) -> Path:
    """Small artificial file tree used only for software testing."""
    root = tmp_path / "scanroot"
    (root / "projectA" / "src").mkdir(parents=True)
    (root / "projectB" / "docs").mkdir(parents=True)
    (root / "empty_folder").mkdir(parents=True)

    (root / "top.txt").write_text("top level", encoding="utf-8")
    (root / "projectA" / "app.py").write_text("print(1)", encoding="utf-8")
    (root / "projectA" / "app_v2.py").write_text("print(2)", encoding="utf-8")
    (root / "projectA" / "src" / "main.py").write_text("x=1", encoding="utf-8")
    (root / "projectA" / "src" / "final_report.pdf").write_bytes(b"%PDF")
    (root / "projectB" / "docs" / "notes docx.docx").write_bytes(b"PK")
    (root / "projectB" / "docs" / "notes docx (1).docx").write_bytes(b"PK")
    (root / "projectB" / "image.png").write_bytes(b"\x89PNG")
    (root / "top.txt").write_text("top level again", encoding="utf-8")  # modify
    (root / "duplicate_name.txt").write_text("aaa", encoding="utf-8")
    (root / "projectA" / "duplicate_name.txt").write_text("aaa", encoding="utf-8")
    return root


@pytest.fixture()
def scanner_config(temp_tree, monkeypatch):
    """Point the scanner at the temp tree and isolate all outputs."""
    import config

    out = temp_tree.parent / "test_output"
    for sub in ("raw", "processed", "snapshots", "database", "reports",
                "logs"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config, "SCAN_ROOT", temp_tree)
    monkeypatch.setattr(config, "WORKSPACE_EXCLUSION", out)
    monkeypatch.setattr(config, "RAW_DIR", out / "raw")
    monkeypatch.setattr(config, "PROCESSED_DIR", out / "processed")
    monkeypatch.setattr(config, "SNAPSHOT_DIR", out / "snapshots")
    monkeypatch.setattr(config, "REPORTS_DIR", out / "reports")
    monkeypatch.setattr(config, "LOGS_DIR", out / "logs")
    monkeypatch.setattr(config, "FILES_CSV", out / "raw" / "files_metadata.csv")
    monkeypatch.setattr(config, "FOLDERS_CSV",
                        out / "raw" / "folders_metadata.csv")
    monkeypatch.setattr(config, "ERRORS_CSV", out / "raw" / "scan_errors.csv")
    monkeypatch.setattr(config, "SCAN_REPORT_JSON",
                        out / "reports" / "scan_report.json")
    monkeypatch.setattr(config, "FINDINGS_JSON",
                        out / "reports" / "findings.json")
    monkeypatch.setattr(config, "NAMING_CSV",
                        out / "processed" / "naming_analysis.csv")
    monkeypatch.setattr(config, "DUP_CANDIDATES_CSV",
                        out / "processed" / "duplicate_candidates.csv")
    monkeypatch.setattr(config, "DUP_VERIFIED_CSV",
                        out / "processed" / "verified_duplicates.csv")
    monkeypatch.setattr(config, "PROCESSED_FILES_CSV",
                        out / "processed" / "files_processed.csv")
    monkeypatch.setattr(config, "PROCESSED_FOLDERS_CSV",
                        out / "processed" / "folders_processed.csv")
    monkeypatch.setattr(config, "SNAPSHOT_INDEX",
                        out / "snapshots" / "snapshots.json")
    monkeypatch.setattr(config, "DATABASE_PATH",
                        out / "database" / "test.db")
    monkeypatch.setattr(config, "RESUME_FILE", out / "logs" / "resume.json")
    monkeypatch.setattr(config, "RESUME_ENABLED", False)
    monkeypatch.setattr(config, "ENABLE_HASH_VERIFICATION", False)
    return config
