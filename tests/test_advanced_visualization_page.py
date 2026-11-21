"""Phase 7 tests: the Advanced Visualization dashboard page (AppTest).

Nothing heavy runs on load: the three-section workbench only builds a
chart when **Generate visualization** is pressed. All data is synthetic
and lives in pytest temp dirs; research files and the research database
are asserted untouched via stat signatures.
"""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest

import config
from fixtures.synthetic import assert_outside_research_data, write_csv
from ingestion import workflow

_VIZ_ID_RE = re.compile(r"viz_\d{8}_\d{6}_[0-9a-f]{6}")
_DRIVE_RE = re.compile(r"[A-Za-z]:[\\/]")


@pytest.fixture()
def platform_dirs(tmp_path, monkeypatch):
    out = tmp_path / "platform_out"
    inputs = tmp_path / "inputs"
    monkeypatch.setattr(config, "OUTPUT_DIR", out)
    monkeypatch.setattr(config, "OUTPUT_METADATA_DIR", out / "metadata")
    monkeypatch.setattr(config, "OUTPUT_DATASETS_DIR", out / "datasets")
    monkeypatch.setattr(config, "OUTPUT_COMPARISONS_DIR",
                        out / "comparisons")
    monkeypatch.setattr(config, "REGISTRY_DB",
                        out / "metadata" / "dataset_registry.db")
    monkeypatch.setattr(config, "INPUT_CSV_DIR", inputs)
    monkeypatch.setattr(config, "OUTPUT_VISUALIZATIONS_DIR",
                        out / "visualizations")
    monkeypatch.setattr(config, "SNAPSHOT_DIR", out / "snapshots")
    monkeypatch.setattr(config, "SNAPSHOT_INDEX",
                        out / "snapshots" / "snapshots.json")
    return out


def _research_signature() -> tuple[int, int]:
    stat = config.FILES_CSV.stat()
    return stat.st_size, stat.st_mtime_ns


def _register(tmp_path, name: str, text: str) -> str:
    write_csv(tmp_path / name, text)
    insp = workflow.inspect_csv(str(tmp_path / name))
    assert insp.ok, insp.error
    res = workflow.register(insp)
    assert res.ok, res.error
    return res.dataset_id


def _rows(n: int) -> str:
    cats = ["x", "y", "z"]
    return "".join(
        f"{i},{(i % 7) + 1.5},{cats[i % 3]}\n" for i in range(n))


def _page_script(tmp_path: Path) -> Path:
    script = tmp_path / "adv_viz_page.py"
    script.write_text(textwrap.dedent("""
        import numpy as np
        import pandas as pd
        from dashboard.advanced_visualization import render

        rng = np.random.default_rng(11)
        files = pd.DataFrame({
            "file_name": [f"f{i % 40}.dat" for i in range(200)],
            "file_extension": rng.choice([".dat", ".csv", ".txt"], 200),
            "file_size_bytes": (np.arange(200) % 40 * 1000.0 + 10.0),
            "creation_timestamp": pd.date_range(
                "2019-01-01", periods=200, freq="12h").astype(str),
            "modified_timestamp": pd.date_range(
                "2020-01-01", periods=200, freq="D").astype(str),
            "file_category": rng.choice(
                ["document", "image", "code"], 200),
            "folder_depth": rng.integers(0, 8, 200),
            "naming_pattern": rng.choice(
                ["snake_case", "kebab-case", "camelCase"], 200),
            "capitalization_style": rng.choice(
                ["lower", "UPPER", "Title", "mixed"], 200),
            "contains_version_indicator": rng.choice(
                [True, False], 200),
            "contains_final_keyword": rng.choice([True, False], 200),
            "contains_date_indicator": rng.choice([True, False], 200),
            "contains_copy_indicator": rng.choice([True, False], 200),
            "has_underscore": rng.choice([True, False], 200),
            "has_space": rng.choice([True, False], 200),
            "has_hyphen": rng.choice([True, False], 200),
            "root_folder": ["D:/scan"] * 200,
            "parent_folder": (["D:/scan/a"] * 100
                              + ["D:/scan/b"] * 100),
        })
        folders = pd.DataFrame({
            "folder_path": ["D:/scan", "D:/scan/a", "D:/scan/b"],
            "parent_folder": ["", "D:/scan", "D:/scan"],
            "folder_depth": [0, 1, 1],
            "total_size_bytes": [1000.0, 600.0, 400.0],
        })
        render(files, folders, {}, {})
    """), encoding="utf-8")
    return script


def _run(tmp_path: Path):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(_page_script(tmp_path)), default_timeout=60)
    at.run()
    assert not at.exception, f"page raised: {at.exception}"
    return at


def _section(at, label: str):
    radio = [r for r in at.radio if r.label == "Section"]
    assert radio, "section radio missing"
    radio[0].set_value(label).run()
    assert not at.exception, f"page raised: {at.exception}"


def _chart(at, chart_id: str):
    pick = [s for s in at.selectbox if s.label == "Chart type"]
    assert pick, "chart type selectbox missing"
    pick[0].select(chart_id).run()
    assert not at.exception, f"page raised: {at.exception}"


def _captions(at) -> list[str]:
    return [c.value for c in at.caption]


def _errors(at) -> list[str]:
    return [e.value for e in at.error]


# ------------------------------------------------------------------ #
# Signature + imports
# ------------------------------------------------------------------ #
def test_advanced_visualization_render_signature_matches_pages():
    import inspect as pyinspect
    from dashboard.advanced_visualization import render
    params = list(pyinspect.signature(render).parameters)
    assert params == ["files", "folders", "findings", "filters"]


def test_advanced_visualization_page_module_imports():
    import dashboard.advanced_visualization  # noqa: F401
    import visualization  # noqa: F401


# ------------------------------------------------------------------ #
# Load: three sections, nothing computed
# ------------------------------------------------------------------ #
def test_page_renders_three_sections_and_computes_nothing(platform_dirs,
                                                          tmp_path):
    at = _run(tmp_path)
    assert any("Advanced Visualization" in s.value for s in at.subheader)
    radio = [r for r in at.radio if r.label == "Section"]
    assert radio, "section radio missing"
    assert radio[0].options == [
        "Archaeology (scan & snapshots)",
        "Dataset (rows & analysis runs)",
        "Comparison (A vs B)",
    ]
    assert "ds7_artifact" not in at.session_state, \
        "nothing must be computed on load"
    assert any("Nothing generated yet" in c for c in _captions(at)), \
        _captions(at)


def test_dataset_section_without_registrations_info(platform_dirs,
                                                    tmp_path):
    at = _run(tmp_path)
    _section(at, "Dataset (rows & analysis runs)")
    infos = [i.value for i in at.info]
    assert any("No datasets registered yet" in i for i in infos), infos


def test_comparison_section_without_runs_info(platform_dirs, tmp_path):
    at = _run(tmp_path)
    _section(at, "Comparison (A vs B)")
    infos = [i.value for i in at.info]
    assert any("No comparison runs yet" in i for i in infos), infos


# ------------------------------------------------------------------ #
# Archaeology: generate + export bundle
# ------------------------------------------------------------------ #
def test_archaeology_generate_and_export_bundle(platform_dirs, tmp_path):
    before = _research_signature()
    at = _run(tmp_path)
    _chart(at, "type_distribution")
    assert at.button("ds7_generate").disabled is False

    at.button("ds7_generate").click().run()
    assert not at.exception, f"generate raised: {at.exception}"
    artifact = at.session_state.get("ds7_artifact")
    assert artifact is not None, "chart was not generated"
    assert artifact.chart_id == "type_distribution"
    assert not _errors(at), _errors(at)
    caps = _captions(at)
    assert any("exact counts over" in c for c in caps), caps
    assert "ds7_artifact" in at.session_state

    at.button("ds7_export_btn").click().run()
    assert not at.exception, f"export raised: {at.exception}"
    successes = [s.value for s in at.success]
    assert any("Exported `viz_" in s for s in successes), successes
    assert any(_VIZ_ID_RE.search(s) for s in successes), successes

    viz_root = Path(config.OUTPUT_VISUALIZATIONS_DIR)
    bundles = list(viz_root.glob("viz_*"))
    assert len(bundles) == 1, bundles
    bundle = bundles[0]
    assert_outside_research_data(bundle)
    names = {p.name for p in bundle.iterdir() if p.is_file()}
    assert {"chart.html", "chart_data.csv", "config.json",
            "metadata.json"} <= names, names
    meta_text = (bundle / "metadata.json").read_text(encoding="utf-8")
    assert not _DRIVE_RE.search(meta_text), "absolute path in metadata"
    assert str(tmp_path) not in meta_text
    assert _research_signature() == before


def test_archaeology_snapshot_diff_needs_two_snapshots(platform_dirs,
                                                       tmp_path):
    at = _run(tmp_path)
    _chart(at, "snapshot_diff")
    infos = [i.value for i in at.info]
    assert any("two stored snapshots" in i for i in infos), infos

    at.button("ds7_generate").click().run()
    assert not at.exception, f"generate raised: {at.exception}"
    assert any("Select two snapshots" in e for e in _errors(at)), \
        _errors(at)
    assert at.session_state.get("ds7_artifact") is None


def test_reset_clears_generated_state(platform_dirs, tmp_path):
    at = _run(tmp_path)
    _chart(at, "type_distribution")
    at.button("ds7_generate").click().run()
    assert at.session_state.get("ds7_artifact") is not None

    at.button("ds7_reset").click().run()
    assert not at.exception, f"reset raised: {at.exception}"
    assert at.session_state.get("ds7_artifact") is None
    assert at.session_state.get("ds7_built") is None
    # widget-owned keys re-register with their defaults on the rerun
    assert at.session_state.get("ds7_chart") == "creation_timeline"


def test_settings_change_offers_refresh(platform_dirs, tmp_path):
    at = _run(tmp_path)
    _chart(at, "type_distribution")
    at.button("ds7_generate").click().run()
    assert at.session_state.get("ds7_artifact") is not None

    top_n = [n for n in at.number_input
             if n.label == "Top-N categories"]
    assert top_n, "top-N limit widget missing"
    top_n[0].set_value(15).run()
    assert not at.exception, f"page raised: {at.exception}"
    caps = _captions(at)
    assert any("Settings changed since this chart was generated"
               in c for c in caps), caps


# ------------------------------------------------------------------ #
# Dataset section: validation gates, filters, truncation, runs
# ------------------------------------------------------------------ #
def test_required_role_blocks_generate(platform_dirs, tmp_path):
    _register(tmp_path, "page_ds.csv", "id,score,cat\n" + _rows(60))
    at = _run(tmp_path)
    _section(at, "Dataset (rows & analysis runs)")
    _chart(at, "pair")

    errs = _errors(at)
    assert any("Select a column for 'columns'" in e for e in errs), errs
    assert at.button("ds7_generate").disabled is True
    assert any("Fix the selections" in c for c in _captions(at))

    cols = [m for m in at.multiselect
            if m.label.startswith("numeric columns")]
    assert cols, "pair columns multiselect missing"
    cols[0].set_value(["id", "score"]).run()
    assert not at.exception, f"page raised: {at.exception}"
    assert at.button("ds7_generate").disabled is False
    assert not any("Select a column" in e for e in _errors(at)), \
        _errors(at)


def test_row_filters_disclose_removed_rows(platform_dirs, tmp_path):
    _register(tmp_path, "filt.csv", "id,score,cat\n" + _rows(100))
    at = _run(tmp_path)
    _section(at, "Dataset (rows & analysis runs)")
    _chart(at, "histogram")

    num_col = [s for s in at.selectbox
               if s.label == "Numeric range column"]
    assert num_col, "numeric filter column selectbox missing"
    num_col[0].select("score").run()
    assert not at.exception, f"page raised: {at.exception}"

    low = [n for n in at.number_input if n.label == "Minimum"]
    assert low, "filter minimum widget missing"
    low[0].set_value(2).run()
    assert not at.exception, f"page raised: {at.exception}"

    at.button("ds7_generate").click().run()
    assert not at.exception, f"generate raised: {at.exception}"
    assert at.session_state.get("ds7_artifact") is not None
    assert not _errors(at), _errors(at)
    caps = _captions(at)
    assert any("score: >= 2" in c for c in caps), caps
    assert any("section filters removed" in c for c in caps), caps


def test_preview_truncation_is_disclosed(platform_dirs, tmp_path):
    _register(tmp_path, "wide.csv", "id,score,cat\n" + _rows(1200))
    at = _run(tmp_path)
    _section(at, "Dataset (rows & analysis runs)")

    cap = [n for n in at.number_input if n.label == "Preview row cap"]
    assert cap, "preview cap widget missing"
    cap[0].set_value(1000).run()
    assert not at.exception, f"page raised: {at.exception}"

    at.button("ds7_generate").click().run()
    assert not at.exception, f"generate raised: {at.exception}"
    assert at.session_state.get("ds7_artifact") is not None
    caps = _captions(at)
    assert any("Preview shows the first 1,000 of 1,200" in c
               for c in caps), caps


def test_run_histogram_journey(platform_dirs, tmp_path):
    from analysis.analysis_runner import run_analysis

    ds_id = _register(tmp_path, "run_ds.csv", "id,score,cat\n"
                      + _rows(200))
    res = run_analysis(ds_id)
    assert res.ok, res.error

    at = _run(tmp_path)
    _section(at, "Dataset (rows & analysis runs)")
    _chart(at, "run_histogram")

    run_pick = [s for s in at.selectbox
                if s.label == "Run to visualize"]
    assert run_pick, "run selectbox missing"

    at.button("ds7_generate").click().run()
    assert not at.exception, f"generate raised: {at.exception}"
    artifact = at.session_state.get("ds7_artifact")
    assert artifact is not None, "run chart was not generated"
    assert artifact.chart_id == "run_histogram"
    assert not _errors(at), _errors(at)


def test_run_chart_without_run_warns_and_blocks(platform_dirs, tmp_path):
    _register(tmp_path, "norun.csv", "id,score,cat\n" + _rows(40))
    at = _run(tmp_path)
    _section(at, "Dataset (rows & analysis runs)")
    _chart(at, "run_histogram")

    warns = [w.value for w in at.warning]
    assert any("No analysis runs for this dataset" in w
               for w in warns), warns
    assert at.button("ds7_generate").disabled is True
