"""Phase 4 tests: data-source workflow (controller) + page compatibility.

All outputs are redirected to pytest temp dirs; protected research paths are
asserted untouched (stat-only signatures for the big research files).
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pandas as pd
import pytest

import config
from core.processing_context import ProcessingContext
from fixtures.synthetic import (assert_outside_research_data, make_sqlite,
                                sha256_file, write_csv)
from ingestion import workflow


@pytest.fixture()
def platform_dirs(tmp_path, monkeypatch):
    out = tmp_path / "platform_out"
    inputs = tmp_path / "inputs"
    monkeypatch.setattr(config, "OUTPUT_DIR", out)
    monkeypatch.setattr(config, "OUTPUT_METADATA_DIR", out / "metadata")
    monkeypatch.setattr(config, "OUTPUT_DATASETS_DIR", out / "datasets")
    monkeypatch.setattr(config, "REGISTRY_DB",
                        out / "metadata" / "dataset_registry.db")
    monkeypatch.setattr(config, "INPUT_CSV_DIR", inputs)
    return out


def _research_raw_signature() -> tuple[int, int] | None:
    if not config.FILES_CSV.exists():
        return None  # clean installation without research data
    stat = config.FILES_CSV.stat()
    return stat.st_size, stat.st_mtime_ns


# ----------------------------------------------------------------- #
# CSV selection and preview
# ----------------------------------------------------------------- #
def test_inspect_csv_ok_with_bounded_preview(tmp_path):
    rows = "".join(f"{i},name{i},{i * 1.5}\n" for i in range(500))
    p = write_csv(tmp_path / "sel.csv", "id,name,score\n" + rows)
    insp = workflow.inspect_csv(str(p), preview_rows=10)
    assert insp.ok
    assert insp.kind == "CSV"
    assert insp.row_count == 500
    assert insp.column_count == 3
    assert len(insp.preview) == 10
    assert [c["name"] for c in insp.columns] == ["id", "name", "score"]
    assert insp.extra["encoding"]
    assert insp.extra["delimiter"] in (",", ";", "\t", "|")
    assert insp.file_size == p.stat().st_size


def test_inspect_csv_missing_file_fails_cleanly(tmp_path):
    insp = workflow.inspect_csv(str(tmp_path / "ghost.csv"))
    assert not insp.ok
    assert "does not exist" in insp.error
    assert str(tmp_path) not in insp.error


def test_inspect_csv_validation_failure_duplicate_headers(tmp_path):
    p = write_csv(tmp_path / "dup.csv", "id,name,name\n1,a,b\n")
    insp = workflow.inspect_csv(str(p))
    assert not insp.ok
    assert "duplicate" in insp.error.lower()


def test_inspect_csv_large_file_skips_full_count(tmp_path, monkeypatch):
    p = write_csv(tmp_path / "big.csv",
                  "id,v\n" + "".join(f"{i},{i}\n" for i in range(50)))
    monkeypatch.setattr(workflow, "CSV_ROW_COUNT_MAX_BYTES", 10)
    insp = workflow.inspect_csv(str(p), preview_rows=5)
    assert insp.ok
    assert insp.row_count is None
    assert insp.extra["count_rows_computed"] is False
    assert any("row count skipped" in w for w in insp.warnings)
    assert len(insp.preview) == 5


def test_inspect_csv_never_reads_beyond_preview(tmp_path):
    p = write_csv(tmp_path / "tall.csv",
                  "id\n" + "".join(f"{i}\n" for i in range(5000)))
    insp = workflow.inspect_csv(str(p), preview_rows=7)
    assert len(insp.preview) == 7
    assert insp.row_count == 5000


# ----------------------------------------------------------------- #
# SQLite table selection + read-only
# ----------------------------------------------------------------- #
def test_inspect_sqlite_single_table_auto(tmp_path):
    db = make_sqlite(tmp_path / "solo.db", {
        "people": (["id INTEGER", "name TEXT"],
                   [(1, "Ada"), (2, "Grace")]),
    })
    insp = workflow.inspect_sqlite(str(db))
    assert insp.ok
    assert insp.extra["needs_table"] is False
    assert insp.extra["table"] == "people"
    assert insp.row_count == 2
    assert len(insp.preview) == 2
    dtypes = {c["name"]: c["dtype"] for c in insp.columns}
    assert dtypes["name"] == "TEXT"


def test_inspect_sqlite_multi_table_requires_selection(tmp_path):
    db = make_sqlite(tmp_path / "multi.db", {
        "a": (["id INTEGER"], [(1,)]),
        "b": (["id INTEGER"], [(2,)]),
    })
    insp = workflow.inspect_sqlite(str(db))
    assert insp.ok
    assert insp.extra["needs_table"] is True
    assert insp.preview is None
    assert set(insp.extra["tables"]) == {"a", "b"}
    assert any("select one" in w.lower() for w in insp.warnings)


def test_inspect_sqlite_with_explicit_table(tmp_path):
    db = make_sqlite(tmp_path / "multi2.db", {
        "a": (["id INTEGER"], [(1,)]),
        "b": (["id INTEGER"], [(2,), (3,)]),
    })
    insp = workflow.inspect_sqlite(str(db), table="b")
    assert insp.ok
    assert insp.extra["table"] == "b"
    assert insp.row_count == 2


def test_inspect_sqlite_is_read_only(tmp_path):
    db = make_sqlite(tmp_path / "ro.db", {
        "t": (["id INTEGER"], [(1,), (2,)]),
    })
    before = sha256_file(db)
    platform_dirs  # noqa: F841 - fixture ensures isolated outputs
    insp = workflow.inspect_sqlite(str(db))
    assert insp.ok
    res = workflow.register(insp)
    assert res.ok
    assert sha256_file(db) == before


def test_inspect_sqlite_invalid_file(tmp_path):
    fake = write_csv(tmp_path / "fake.db", "not a database\n")
    insp = workflow.inspect_sqlite(str(fake))
    assert not insp.ok
    assert insp.error


# ----------------------------------------------------------------- #
# Filesystem confirmation rules
# ----------------------------------------------------------------- #
def test_inspect_folder_previews_without_scanning(tmp_path):
    root = tmp_path / "tree"
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("x", encoding="utf-8")
    insp = workflow.inspect_folder(str(root))
    assert insp.ok
    assert insp.extra["scanned"] is False
    assert insp.extra["entry_count"] == 2
    assert not insp.needs_drive_confirm
    assert not insp.needs_large_ack
    names = {e["name"] for e in insp.extra["entries_preview"]}
    assert names == {"sub", "a.txt"}
    assert (root / "a.txt").read_text(encoding="utf-8") == "x"


def test_inspect_folder_missing_path(tmp_path):
    insp = workflow.inspect_folder(str(tmp_path / "ghost"))
    assert not insp.ok
    assert "does not exist" in insp.error


def test_inspect_drive_root_flags_confirmation():
    insp = workflow.inspect_folder("D:\\")
    assert insp.ok
    assert insp.needs_drive_confirm is True
    assert insp.needs_large_ack is True
    assert insp.extra["scanned"] is False


def test_register_folder_on_drive_root_needs_confirmation(platform_dirs):
    insp = workflow.inspect_folder("D:\\")
    res = workflow.register(insp, confirm_drive=False)
    assert not res.ok
    assert "drive" in res.error.lower()
    assert not (platform_dirs / "datasets").exists()


def test_large_scan_rule_uses_top_level_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "LARGE_SCAN_TOP_LEVEL_ENTRIES", 3)
    small = tmp_path / "small"
    small.mkdir()
    for i in range(2):
        (small / f"f{i}").write_text("x", encoding="utf-8")
    assert not workflow.inspect_folder(str(small)).needs_large_ack
    big = tmp_path / "big"
    big.mkdir()
    for i in range(4):
        (big / f"f{i}").write_text("x", encoding="utf-8")
    assert workflow.inspect_folder(str(big)).needs_large_ack


# ----------------------------------------------------------------- #
# Registration & processing jobs
# ----------------------------------------------------------------- #
def test_register_csv_creates_job_registry_and_outputs(platform_dirs,
                                                       tmp_path):
    p = write_csv(tmp_path / "reg.csv", "a,b\n1,2\n3,4\n")
    insp = workflow.inspect_csv(str(p))
    res = workflow.register(insp)
    assert res.ok
    assert len(res.dataset_id) == 36
    assert res.job_id.startswith("job_")
    meta = Path(res.metadata_path)
    assert meta.exists()
    assert meta.name.endswith("_registration.json")
    assert_outside_research_data(meta)
    assert_outside_research_data(config.REGISTRY_DB)
    summary_files = list((platform_dirs / "metadata").glob(
        f"{res.job_id}_job_summary.json"))
    assert summary_files
    job = summary_files[0].read_text(encoding="utf-8")
    assert res.dataset_id in job
    from ingestion.dataset_registry import DatasetRegistry
    rows = DatasetRegistry().list()
    assert len(rows) == 1
    assert rows[0]["dataset_id"] == res.dataset_id
    assert rows[0]["row_count"] == 2


def test_register_refuses_failed_inspection(platform_dirs, tmp_path):
    insp = workflow.inspect_csv(str(tmp_path / "nope.csv"))
    res = workflow.register(insp)
    assert not res.ok
    assert "inspect" in res.error.lower()
    assert not config.REGISTRY_DB.exists()


def test_register_folder_scan_isolated_from_research(platform_dirs,
                                                     tmp_path):
    root = tmp_path / "scanme"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "data.txt").write_text("hello", encoding="utf-8")
    before = _research_raw_signature()
    insp = workflow.inspect_folder(str(root))
    res = workflow.register(insp)
    after = _research_raw_signature()
    assert before == after
    assert res.ok
    assert res.summary["scan"]["files_scanned"] == 1
    assert res.summary["scan"]["status"] == "completed"
    dataset_dirs = list((platform_dirs / "datasets").iterdir())
    assert len(dataset_dirs) == 1
    assert_outside_research_data(dataset_dirs[0])
    out_csv = dataset_dirs[0] / "files_metadata.csv"
    assert out_csv.exists()


def test_processing_error_writes_failed_job_summary(platform_dirs,
                                                    tmp_path, monkeypatch):
    p = write_csv(tmp_path / "x.csv", "a\n1\n")
    insp = workflow.inspect_csv(str(p))

    original = ProcessingContext.ensure_dirs

    def boom(self):
        raise RuntimeError("simulated disk failure")

    monkeypatch.setattr(ProcessingContext, "ensure_dirs", boom)
    res = workflow.register(insp)
    monkeypatch.setattr(ProcessingContext, "ensure_dirs", original)
    assert not res.ok
    assert "Registration failed" in res.error
    assert str(tmp_path) not in res.error
    assert res.job_id.startswith("job_")


def test_cancellation_flag_blocks_registration(platform_dirs, tmp_path):
    p = write_csv(tmp_path / "c.csv", "a\n1\n")
    insp = workflow.inspect_csv(str(p))
    ctx = ProcessingContext(base_dir=platform_dirs / "ctx")
    ctx.cancel()
    res = workflow.register(insp, context=ctx)
    assert not res.ok
    assert "cancelled" in res.error.lower()
    assert res.job_id == ctx.job_id
    assert not config.REGISTRY_DB.exists()


# ----------------------------------------------------------------- #
# Uploads & history
# ----------------------------------------------------------------- #
def test_save_upload_sanitizes_and_dedupes(platform_dirs):
    p1 = workflow.save_upload(r"..\evil\name file.csv", b"a,b\n1,2\n")
    assert p1.name == "name_file.csv"
    assert p1.parent == config.INPUT_CSV_DIR / "uploads"
    assert_outside_research_data(p1)
    p2 = workflow.save_upload("name file.csv", b"a,b\n3,4\n")
    assert p2 != p1
    assert p2.name == "name_file_1.csv"
    assert p1.read_bytes() == b"a,b\n1,2\n"


def test_save_upload_handles_hostile_names(platform_dirs):
    assert workflow.sanitize_filename("../../x.csv") == "x.csv"
    assert workflow.sanitize_filename("") == "upload.csv"
    assert workflow.sanitize_filename("...") == "upload.csv"
    assert workflow.sanitize_filename("na;me$#.csv") == "na_me_.csv"


def test_history_empty_then_populated(platform_dirs, tmp_path):
    assert workflow.history() == []
    p = write_csv(tmp_path / "h.csv", "a\n1\n")
    res = workflow.register(workflow.inspect_csv(str(p)))
    assert res.ok
    rows = workflow.history()
    assert len(rows) == 1
    assert rows[0]["source_type"] == "csv"
    assert rows[0]["dataset_id"] == res.dataset_id


# ----------------------------------------------------------------- #
# Dashboard compatibility
# ----------------------------------------------------------------- #
def test_app_pages_include_new_page_and_keep_old_eight():
    import app
    expected_first_eight = [
        "Overview", "File Creation Timeline", "Storage Analytics",
        "File Type Analysis", "Naming Pattern Analysis",
        "Duplicate Analysis", "Folder Structure", "Research Findings",
    ]
    keys = list(app.PAGES.keys())
    assert keys[:8] == expected_first_eight
    # Phase 4 added "Data Sources"; Phase 5 added "Dataset Analysis";
    # Phase 6 added "Dataset Comparison"; Phase 7 added
    # "Advanced Visualization".
    assert keys[8:] == ["Data Sources", "Dataset Analysis",
                        "Dataset Comparison", "Advanced Visualization"]
    assert all(callable(app.PAGES[k]) for k in keys)


def test_data_sources_render_signature_matches_pages():
    import inspect as pyinspect
    from dashboard.data_sources import render
    params = list(pyinspect.signature(render).parameters)
    assert params == ["files", "folders", "findings", "filters"]


def test_workflow_and_page_modules_import_cleanly():
    import dashboard.data_sources  # noqa: F401
    import ingestion.workflow  # noqa: F401


def test_data_sources_page_renders_in_streamlit_runtime(tmp_path):
    """Bounded UI check: the page renders in a real Streamlit runtime."""
    from streamlit.testing.v1 import AppTest
    script = tmp_path / "page_script.py"
    script.write_text(textwrap.dedent("""
        import pandas as pd
        from dashboard.data_sources import render
        render(pd.DataFrame(), pd.DataFrame(), {}, {})
    """), encoding="utf-8")
    at = AppTest.from_file(str(script), default_timeout=30)
    at.run()
    assert not at.exception, f"page raised: {at.exception}"
    radios = [r for r in at.radio]
    assert radios and list(radios[0].options) == list(
        workflow.SOURCE_CHOICES)
    labels = [b.label for b in at.button]
    assert "Inspect source" in labels
    assert "Reset workflow" in labels
