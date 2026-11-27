"""Processing context: per-run output locations, progress, and safety guards.

All platform outputs live under ``output/`` (created lazily, never at import
time). The context refuses to write anywhere near protected research data.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import config
from core.exceptions import PlatformError

_PROTECTED_RESEARCH_ROOTS: tuple[Path, ...] = (
    config.RAW_DIR,
    config.PROCESSED_DIR,
    config.SNAPSHOT_DIR,
    config.REPORTS_DIR,
    config.LOGS_DIR,
    config.DATABASE_PATH,
)


def is_protected_research_path(path) -> bool:
    """True if *path* lies inside a protected research location."""
    try:
        target = Path(path).resolve()
    except (OSError, ValueError):
        return True
    for root in _PROTECTED_RESEARCH_ROOTS:
        try:
            root_r = Path(root).resolve()
        except (OSError, ValueError):
            continue
        if target == root_r or root_r in target.parents:
            return True
    return False


def assert_writable_target(path) -> None:
    if is_protected_research_path(path):
        raise PlatformError(
            "Refusing to write into protected research data.",
            detail=f"blocked write target: {path}")


class ProcessingContext:
    """Per-run output directories, progress feed, and cancellation flag."""

    def __init__(self, base_dir: str | Path | None = None,
                 job_id: str | None = None):
        self.base_dir = Path(base_dir) if base_dir else config.OUTPUT_DIR
        self.job_id = job_id or (
            f"job_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:6]}")
        self.datasets_dir = self.base_dir / "datasets"
        self.analysis_dir = self.base_dir / "analysis"
        self.reports_dir = self.base_dir / "reports"
        self.metadata_dir = self.base_dir / "metadata"
        self.comparisons_dir = self.base_dir / "comparisons"
        self.status = "created"
        self._progress: list[dict] = []
        self._cancelled = False
        self.on_progress = None

    def ensure_dirs(self) -> None:
        for d in (self.datasets_dir, self.analysis_dir,
                  self.reports_dir, self.metadata_dir,
                  self.comparisons_dir):
            assert_writable_target(d)
            d.mkdir(parents=True, exist_ok=True)

    def dataset_dir(self, dataset_id: str) -> Path:
        self.ensure_dirs()
        target = self.datasets_dir / dataset_id
        assert_writable_target(target)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def comparison_dir(self, comparison_id: str) -> Path:
        """Per-pair comparison home: ``output/comparisons/<comparison_id>``."""
        self.ensure_dirs()
        target = self.comparisons_dir / comparison_id
        assert_writable_target(target)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def metadata_path(self, name: str) -> Path:
        self.ensure_dirs()
        return self.metadata_dir / f"{self.job_id}_{name}.json"

    def write_metadata(self, name: str, payload: dict) -> Path:
        target = self.metadata_path(name)
        assert_writable_target(target)
        target.write_text(
            json.dumps(payload, indent=2, default=str, sort_keys=True),
            encoding="utf-8")
        return target

    def report_progress(self, stage: str, current: int = 0,
                        total: int | None = None, message: str = "") -> None:
        entry = {
            "stage": stage,
            "current": current,
            "total": total,
            "message": message,
            "at": datetime.now().isoformat(timespec="seconds"),
        }
        self._progress.append(entry)
        if self.on_progress:
            self.on_progress(entry)

    @property
    def progress(self) -> list[dict]:
        return list(self._progress)

    def cancel(self) -> None:
        self._cancelled = True
        self.status = "cancelled"

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def set_status(self, status: str) -> None:
        self.status = status

    def close(self) -> None:
        self.status = "closed"
