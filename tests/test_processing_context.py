"""ProcessingContext tests: lazy dirs, metadata writes, research guard."""
from __future__ import annotations

import json

import pytest

import config
from core.exceptions import PlatformError
from core.processing_context import (ProcessingContext,
                                     assert_writable_target,
                                     is_protected_research_path)
from fixtures.synthetic import assert_outside_research_data


def test_context_paths_live_under_base_dir(tmp_path):
    ctx = ProcessingContext(base_dir=tmp_path / "platform_out",
                            job_id="job_test_1")
    ctx.ensure_dirs()
    for d in (ctx.datasets_dir, ctx.analysis_dir, ctx.reports_dir,
              ctx.metadata_dir):
        assert d.exists()
        assert_outside_research_data(d)
    ctx.close()


def test_write_metadata_creates_json_under_metadata_dir(tmp_path):
    ctx = ProcessingContext(base_dir=tmp_path / "pout", job_id="job_meta")
    path = ctx.write_metadata("inspect", {"rows": 3, "ok": True})
    assert path.exists()
    assert path.parent == ctx.metadata_dir
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["rows"] == 3
    assert path.name.startswith("job_meta_")
    ctx.close()


def test_research_paths_are_protected():
    assert is_protected_research_path(config.RAW_DIR)
    assert is_protected_research_path(config.RAW_DIR / "files_metadata.csv")
    assert is_protected_research_path(config.PROCESSED_DIR / "x.csv")
    assert is_protected_research_path(config.DATABASE_PATH)
    assert is_protected_research_path(config.REPORTS_DIR / "findings.json")
    assert is_protected_research_path(config.LOGS_DIR)
    assert not is_protected_research_path(config.OUTPUT_DIR / "datasets")


def test_assert_writable_target_blocks_research_writes():
    with pytest.raises(PlatformError):
        assert_writable_target(config.RAW_DIR / "evil.csv")
    with pytest.raises(PlatformError):
        assert_writable_target(config.REPORTS_DIR / "evil.md")
    assert_writable_target(config.OUTPUT_DIR / "datasets" / "x.csv")


def test_ensure_dirs_refuses_protected_base(tmp_path):
    ctx = ProcessingContext(base_dir=config.RAW_DIR)
    with pytest.raises(PlatformError):
        ctx.ensure_dirs()


def test_progress_feed_and_status(tmp_path):
    ctx = ProcessingContext(base_dir=tmp_path / "pout")
    seen = []
    ctx.on_progress = lambda entry: seen.append(entry)
    ctx.report_progress("scan", 100, None, message="C:\\somewhere")
    assert ctx.progress[-1]["stage"] == "scan"
    assert seen and seen[0]["current"] == 100
    ctx.set_status("processing")
    assert ctx.status == "processing"
    ctx.cancel()
    assert ctx.cancelled
    assert ctx.status == "cancelled"
    ctx.close()
    assert ctx.status == "closed"


def test_dataset_dir_is_deterministic_and_isolated(tmp_path):
    ctx = ProcessingContext(base_dir=tmp_path / "pout")
    d1 = ctx.dataset_dir("ds_abc")
    d2 = ctx.dataset_dir("ds_abc")
    assert d1 == d2
    assert d1.parent == ctx.datasets_dir
    assert_outside_research_data(d1)
    ctx.close()


def test_platform_dirs_created_only_on_explicit_request(tmp_path, monkeypatch):
    import config
    target = tmp_path / "explicit_platform_dir"
    monkeypatch.setattr(config, "PLATFORM_DIRS", (target,))
    assert not target.exists()
    config.ensure_platform_dirs()
    assert target.exists()
    assert_outside_research_data(target)
