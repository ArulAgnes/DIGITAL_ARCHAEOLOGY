"""Phase 5 tests: the reusable analysis runner (run_analysis + outputs).

All outputs go to pytest temp dirs (platform_dirs fixture); the research
dataset and the research database are asserted untouched via stat-only
signatures. Sources are only ever opened read-only.
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pandas as pd
import pytest

import config
from analysis.analysis_runner import (AnalysisConfig, list_runs, load_run,
                                      run_analysis, verify_source)
from core.exceptions import PlatformError
from fixtures.synthetic import (assert_outside_research_data, make_sqlite,
                                sha256_file, write_csv)
from ingestion import workflow

RUN_FILES = ("config.json", "fingerprint.json", "summary.json",
             "statistics.json", "quality_report.json", "distributions.json",
             "correlation.csv", "errors.json", "run_metadata.json")


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


def _research_raw_signature() -> tuple[int, int]:
    stat = config.FILES_CSV.stat()
    return stat.st_size, stat.st_mtime_ns


def _sample_rows(n: int = 200) -> str:
    return "".join(
        f"{i},{(i % 7) + 1.5},{['x', 'y', 'z'][i % 3]}\n" for i in range(n))


def _register_csv(tmp_path, name="data.csv", n=200):
    p = write_csv(tmp_path / name, "id,score,cat\n" + _sample_rows(n))
    insp = workflow.inspect_csv(str(p))
    assert insp.ok, insp.error
    res = workflow.register(insp)
    assert res.ok, res.error
    return p, res.dataset_id


# ----------------------------------------------------------------- #
# Happy path: full-mode run with all nine outputs
# ----------------------------------------------------------------- #
def test_full_run_writes_all_outputs_and_valid_summary(platform_dirs,
                                                       tmp_path):
    _before = _research_raw_signature()
    _, ds_id = _register_csv(tmp_path)
    res = run_analysis(ds_id, AnalysisConfig(group_by="cat"))
    assert res.ok, res.error
    assert_outside_research_data(Path(res.run_dir))

    run_dir = Path(res.run_dir)
    for name in RUN_FILES:
        assert (run_dir / name).exists(), f"missing {name}"

    summary = json.loads((run_dir / "summary.json").read_text("utf-8"))
    assert summary["mode"] == "full"
    assert summary["rows_analyzed"] == 200
    assert summary["source_status"] == "ok"
    assert summary["dataset_id"] == ds_id
    assert summary["quality_score"] is not None
    assert 0 <= summary["quality_score"] <= 100
    assert summary["correlation_basis"] == "exact"

    stats = json.loads((run_dir / "statistics.json").read_text("utf-8"))
    assert stats["basis"] == "exact"
    assert stats["columns"]["id"]["count"] == 200
    expected_mean = sum((i % 7) + 1.5 for i in range(200)) / 200
    assert stats["columns"]["score"]["mean"] == pytest.approx(
        expected_mean, abs=1e-9)
    assert stats["excluded_records"]["nonfinite_values_excluded"] == 0
    assert "_notes" in stats["columns"]

    quality = json.loads((run_dir / "quality_report.json").read_text("utf-8"))
    assert quality["score"] is not None
    assert quality["score_formula"]
    assert quality["score_limitations"]
    assert quality["duplicate_rows"] == 0

    dist = json.loads((run_dir / "distributions.json").read_text("utf-8"))
    assert "id" in dist["histograms"]
    assert dist["categorical"]["cat"]["unique"] == 3
    assert dist["grouped"]["group_by"] == "cat"
    assert "x" in dist["grouped"]["groups"]

    corr = pd.read_csv(run_dir / "correlation.csv")
    assert {"method", "column_a", "column_b", "value"} <= set(corr.columns)
    assert len(corr) > 0

    meta = json.loads((run_dir / "run_metadata.json").read_text("utf-8"))
    assert meta["status"] == "completed"
    assert meta["duration_seconds"] >= 0
    assert meta["pandas"] and meta["numpy"] and meta["python"]

    assert _research_raw_signature() == _before  # research data untouched


def test_progress_events_cover_all_stages(platform_dirs, tmp_path):
    _, ds_id = _register_csv(tmp_path, n=50)
    res = run_analysis(ds_id)
    assert res.ok, res.error
    stages = [p["stage"] for p in res.progress]
    assert stages[0] == "verify"
    for expected in ("load", "profile", "statistics", "quality",
                     "distributions", "correlation", "outputs"):
        assert expected in stages
    assert stages.index("verify") < stages.index("outputs")


# ----------------------------------------------------------------- #
# Mode selection: streaming and sample
# ----------------------------------------------------------------- #
def test_streaming_mode_matches_full_mode_exactly_on_counts(
        platform_dirs, tmp_path):
    _, ds_id = _register_csv(tmp_path, n=250)
    full = run_analysis(ds_id, AnalysisConfig())
    assert full.ok, full.error
    streamed = run_analysis(
        ds_id, AnalysisConfig(full_load_max_rows=10, chunk_size=1_000))
    assert streamed.ok, streamed.error
    assert full.summary["mode"] == "full"
    assert streamed.summary["mode"] == "streaming"

    a = json.loads(Path(full.run_dir, "statistics.json").read_text("utf-8"))
    b = json.loads(Path(streamed.run_dir, "statistics.json")
                   .read_text("utf-8"))
    for col in ("id", "score"):
        for field in ("count", "mean", "std", "min", "max", "unique",
                      "missing", "excluded_nonfinite"):
            assert a["columns"][col][field] == b["columns"][col][field], \
                (col, field)
        assert a["columns"][col]["q2"] == pytest.approx(
            b["columns"][col]["q2"], abs=1e-6)

    qa = json.loads(Path(full.run_dir, "quality_report.json")
                    .read_text("utf-8"))
    qb = json.loads(Path(streamed.run_dir, "quality_report.json")
                    .read_text("utf-8"))
    for field in ("score", "completeness_pct", "duplicate_rows",
                  "missing_cells", "total_cells", "constant_columns"):
        assert qa[field] == qb[field], field

    dist_b = json.loads(Path(streamed.run_dir, "distributions.json")
                        .read_text("utf-8"))
    assert dist_b["grouped"]["notes"]  # streaming states its limits


def test_sample_mode_is_labeled_everywhere(platform_dirs, tmp_path):
    _, ds_id = _register_csv(tmp_path, n=200)
    res = run_analysis(ds_id, AnalysisConfig(sample_rows=25))
    assert res.ok, res.error
    assert res.summary["mode"] == "sample"
    assert res.summary["rows_analyzed"] == 25

    run_dir = Path(res.run_dir)
    stats = json.loads((run_dir / "statistics.json").read_text("utf-8"))
    assert stats["basis"] == "sample"
    assert stats["columns"]["id"]["count"] == 25
    qual = json.loads((run_dir / "quality_report.json").read_text("utf-8"))
    assert qual["basis"] == "sample"


# ----------------------------------------------------------------- #
# Source verification and refusal
# ----------------------------------------------------------------- #
def test_changed_source_refuses_run_and_keeps_evidence(platform_dirs,
                                                       tmp_path):
    p, ds_id = _register_csv(tmp_path, n=100)
    before = _research_raw_signature()

    p.write_text("id,score,cat\n" + _sample_rows(101), encoding="utf-8")
    res = run_analysis(ds_id, AnalysisConfig())
    assert not res.ok
    assert res.verification is not None
    assert res.verification.status == "changed"
    assert "re-register" in res.error.lower()

    run_dir = Path(res.run_dir)
    assert (run_dir / "summary.json").exists()
    summary = json.loads((run_dir / "summary.json").read_text("utf-8"))
    assert summary["refused"] is True
    assert summary["reason"] == "changed"
    meta = json.loads((run_dir / "run_metadata.json").read_text("utf-8"))
    assert meta["status"] == "refused"
    errors = json.loads((run_dir / "errors.json").read_text("utf-8"))
    assert errors and errors[0]["type"] == "changed"
    # no analysis outputs were produced for the stale source
    assert not (run_dir / "statistics.json").exists()
    assert _research_raw_signature() == before


def test_missing_source_refuses_run(platform_dirs, tmp_path):
    p, ds_id = _register_csv(tmp_path, n=50)
    p.unlink()
    res = run_analysis(ds_id)
    assert not res.ok
    assert res.verification.status == "missing"
    assert "re-register" in res.error.lower()


def test_unregistered_dataset_fails_cleanly(platform_dirs):
    res = run_analysis("does-not-exist")
    assert not res.ok
    assert "not registered" in res.error.lower()


def test_verify_source_reports_checks_for_unchanged_file(platform_dirs,
                                                         tmp_path):
    p, ds_id = _register_csv(tmp_path, n=10)
    from ingestion.dataset_registry import DatasetRegistry
    reg = DatasetRegistry().get(ds_id)
    ver = verify_source(reg, AnalysisConfig())
    assert ver.status == "ok"
    assert ver.ok_to_run
    assert any(c["code"] == "fingerprint" and c["ok"]
               for c in ver.checks)
    assert ver.content_hash == sha256_file(p)
    assert "sha-256" in ver.content_hash_note.lower()


# ----------------------------------------------------------------- #
# Reproducibility
# ----------------------------------------------------------------- #
def test_identical_runs_produce_byte_identical_result_files(
        platform_dirs, tmp_path):
    _, ds_id = _register_csv(tmp_path, n=150)
    one = run_analysis(ds_id, AnalysisConfig(group_by="cat"))
    two = run_analysis(ds_id, AnalysisConfig(group_by="cat"))
    assert one.ok and two.ok
    for name in ("statistics.json", "quality_report.json",
                 "distributions.json", "correlation.csv"):
        a = Path(one.run_dir, name).read_bytes()
        b = Path(two.run_dir, name).read_bytes()
        assert a == b, f"{name} differs between identical runs"
    # config.json intentionally carries its own run_id
    cfg1 = json.loads(Path(one.run_dir, "config.json").read_text("utf-8"))
    cfg2 = json.loads(Path(two.run_dir, "config.json").read_text("utf-8"))
    assert cfg1["config"] == cfg2["config"]


# ----------------------------------------------------------------- #
# SQLite: read-only proof
# ----------------------------------------------------------------- #
def test_sqlite_run_never_modifies_source(platform_dirs, tmp_path):
    db = make_sqlite(tmp_path / "src.db", {
        "people": (["id INTEGER", "score REAL"],
                   [(i, i * 1.5) for i in range(120)]),
    })
    before = sha256_file(db)
    insp = workflow.inspect_sqlite(str(db), table="people")
    assert insp.ok, insp.error
    res_reg = workflow.register(insp)
    assert res_reg.ok, res_reg.error

    res = run_analysis(res_reg.dataset_id, AnalysisConfig())
    assert res.ok, res.error
    assert sha256_file(db) == before, "sqlite source was modified"
    assert res.summary["rows_analyzed"] == 120


# ----------------------------------------------------------------- #
# Folder dataset: analysed from its registered scan output
# ----------------------------------------------------------------- #
def test_folder_dataset_analysed_from_scan_artifact(platform_dirs,
                                                    tmp_path):
    root = tmp_path / "scanme"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "a.txt").write_text("a", encoding="utf-8")
    (root / "b.txt").write_text("b", encoding="utf-8")
    insp = workflow.inspect_folder(str(root))
    assert insp.ok, insp.error
    res_reg = workflow.register(insp, confirm_drive=False)
    assert res_reg.ok, res_reg.error

    res = run_analysis(res_reg.dataset_id)
    assert res.ok, res.error
    assert res.summary["source_type"] == "filesystem"
    assert res.summary["rows_analyzed"] >= 1
    assert any("files_metadata.csv" in n for n in res.summary["notes"])


# ----------------------------------------------------------------- #
# Run listing / loading
# ----------------------------------------------------------------- #
def test_list_and_load_runs_roundtrip(platform_dirs, tmp_path):
    _, ds_id = _register_csv(tmp_path, n=80)
    assert list_runs(ds_id) == []
    first = run_analysis(ds_id)
    second = run_analysis(ds_id)
    assert first.ok and second.ok

    runs = list_runs(ds_id)
    assert [r["run_id"] for r in runs] == [second.run_id, first.run_id]
    assert all(r["status"] == "completed" for r in runs)

    loaded = load_run(ds_id, first.run_id)
    assert loaded["statistics"]["columns"]["id"]["count"] == 80
    assert loaded["summary"]["mode"] == "full"
    assert loaded["correlation_df"] is not None
    assert Path(loaded["run_dir"]).is_dir()

    with pytest.raises(PlatformError):
        load_run(ds_id, "..")
    with pytest.raises(PlatformError):
        load_run(ds_id, "nope-run")


def test_correlation_can_be_disabled_without_breaking_run(platform_dirs,
                                                          tmp_path):
    _, ds_id = _register_csv(tmp_path, n=60)
    res = run_analysis(ds_id, AnalysisConfig(correlation_enabled=False))
    assert res.ok, res.error
    corr = pd.read_csv(Path(res.run_dir, "correlation.csv"))
    assert len(corr) == 0
    assert res.summary["correlation_basis"] == "disabled"


def test_invalid_correlation_method_rejected_at_config_time():
    with pytest.raises(ValueError):
        AnalysisConfig(correlation_methods=("spearman-maybe",))


# ----------------------------------------------------------------- #
# Dashboard page (Mode B)
# ----------------------------------------------------------------- #
def test_dataset_analysis_render_signature_matches_pages():
    import inspect as pyinspect
    from dashboard.dataset_analysis import render
    params = list(pyinspect.signature(render).parameters)
    assert params == ["files", "folders", "findings", "filters"]


def test_dataset_analysis_module_imports_cleanly():
    import dashboard.dataset_analysis  # noqa: F401
    import analysis.analysis_runner  # noqa: F401


def test_dataset_analysis_page_full_journey(platform_dirs, tmp_path):
    """Verify -> start -> results tabs -> runs list, in a real runtime."""
    from streamlit.testing.v1 import AppTest

    _, _ds_id = _register_csv(tmp_path, n=120)
    script = tmp_path / "page_script.py"
    script.write_text(textwrap.dedent("""
        import pandas as pd
        from dashboard.dataset_analysis import render
        render(pd.DataFrame(), pd.DataFrame(), {}, {})
    """), encoding="utf-8")

    at = AppTest.from_file(str(script), default_timeout=60)
    at.run()
    assert not at.exception, f"page raised: {at.exception}"
    assert any("Dataset Analysis" in s.value for s in at.subheader)

    start = at.button("ds5_start")
    assert start.disabled is True, "start must be locked before verification"

    at.button("ds5_verify_btn").click().run()
    assert not at.exception, f"verify raised: {at.exception}"
    assert any("Source verified" in s.value for s in at.success)
    assert at.button("ds5_start").disabled is False

    at.button("ds5_start").click().run()
    assert not at.exception, f"start raised: {at.exception}"
    done = [s.value for s in at.success if "completed" in s.value]
    assert done, [e.value for e in at.error]

    labels = [t.label for t in at.tabs]
    assert labels == ["Summary", "Statistics", "Quality", "Distributions",
                      "Correlation", "Run files & progress"]

    metric_labels = [m.label for m in at.metric]
    for want in ("Mode", "Rows analysed", "Quality score"):
        assert want in metric_labels, metric_labels

    pickers = [s for s in at.selectbox if s.label == "Run to view"]
    assert pickers and pickers[0].options, "no runs listed"

    at.button("ds5_reset").click().run()
    assert not at.exception, f"reset raised: {at.exception}"


def test_dataset_analysis_page_renders_without_datasets(platform_dirs,
                                                        tmp_path):
    from streamlit.testing.v1 import AppTest
    script = tmp_path / "empty_page.py"
    script.write_text(textwrap.dedent("""
        import pandas as pd
        from dashboard.dataset_analysis import render
        render(pd.DataFrame(), pd.DataFrame(), {}, {})
    """), encoding="utf-8")
    at = AppTest.from_file(str(script), default_timeout=30)
    at.run()
    assert not at.exception, f"page raised: {at.exception}"
    infos = [i.value for i in at.info]
    assert any("No datasets registered" in i for i in infos)
