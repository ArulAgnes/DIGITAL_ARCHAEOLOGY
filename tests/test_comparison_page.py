"""Phase 6 tests: the Dataset Comparison dashboard page (AppTest).

The page never runs a comparison by itself: nothing happens on load,
Start stays locked until **Validate keys & sources** passes for the
current selection, and a completed run is rendered from its on-disk
report under the isolated platform output dir.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

import config
from fixtures.synthetic import assert_outside_research_data, write_csv
from ingestion import workflow


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
    return out


def _register(tmp_path, name: str, text: str) -> str:
    write_csv(tmp_path / name, text)
    insp = workflow.inspect_csv(str(tmp_path / name))
    assert insp.ok, insp.error
    res = workflow.register(insp)
    assert res.ok, res.error
    return res.dataset_id


def _page_script(tmp_path: Path) -> Path:
    script = tmp_path / "compare_page.py"
    script.write_text(textwrap.dedent("""
        import pandas as pd
        from dashboard.dataset_comparison import render
        render(pd.DataFrame(), pd.DataFrame(), {}, {})
    """), encoding="utf-8")
    return script


def test_dataset_comparison_render_signature_matches_pages():
    import inspect as pyinspect
    from dashboard.dataset_comparison import render
    params = list(pyinspect.signature(render).parameters)
    assert params == ["files", "folders", "findings", "filters"]


def test_comparison_page_module_imports():
    import dashboard.dataset_comparison  # noqa: F401
    import analysis.comparison  # noqa: F401


def test_comparison_page_renders_without_datasets(platform_dirs, tmp_path):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(_page_script(tmp_path)), default_timeout=30)
    at.run()
    assert not at.exception, f"page raised: {at.exception}"
    infos = [i.value for i in at.info]
    assert any("No datasets registered yet" in i for i in infos)


def test_comparison_page_full_journey(platform_dirs, tmp_path):
    """Load (no auto-run) -> validate -> start -> results -> history."""
    from streamlit.testing.v1 import AppTest

    from analysis.comparison import list_comparisons
    from dashboard.dataset_comparison import _dataset_options

    ds_a = _register(tmp_path, "page_a.csv",
                     "id,score,cat\n1,10,x\n2,20,y\n3,30,z\n")
    ds_b = _register(tmp_path, "page_b.csv",
                     "id,score,cat\n1,10,x\n2,25,y\n4,40,x\n")
    labels, _ = _dataset_options("csv")
    label_a = next(l for l in labels if l.endswith(ds_a[:8]))
    label_b = next(l for l in labels if l.endswith(ds_b[:8]))

    at = AppTest.from_file(str(_page_script(tmp_path)), default_timeout=60)
    at.run()
    assert not at.exception, f"page raised: {at.exception}"
    assert any("Dataset Comparison" in s.value for s in at.subheader)

    # step: nothing runs automatically on load
    assert list_comparisons() == []
    assert at.button("ds6_start").disabled is True, \
        "start must be locked before validation"

    # steps: pick the two datasets
    pick_a = [s for s in at.selectbox
              if s.label == "Dataset A (first)"]
    assert pick_a, "dataset A selectbox missing"
    pick_a[0].select(label_a).run()
    assert not at.exception, f"page raised: {at.exception}"
    pick_b = [s for s in at.selectbox
              if s.label == "Dataset B (second)"]
    pick_b[0].select(label_b).run()
    assert not at.exception, f"page raised: {at.exception}"

    # step: key columns
    keys = [m for m in at.multiselect
            if m.label.startswith("Key column(s)")]
    assert keys, "key multiselect missing"
    assert "id" in keys[0].options, keys[0].options
    keys[0].set_value(["id"]).run()
    assert not at.exception, f"page raised: {at.exception}"
    assert at.button("ds6_start").disabled is True

    # step: validation gates the start button
    at.button("ds6_validate_btn").click().run()
    assert not at.exception, f"validate raised: {at.exception}"
    successes = [s.value for s in at.success]
    assert any("Validation passed" in s for s in successes), successes
    assert at.button("ds6_start").disabled is False

    # step: start the comparison (spinner + progress inside)
    at.button("ds6_start").click().run()
    assert not at.exception, f"start raised: {at.exception}"
    done = [s.value for s in at.success if "completed" in s.value]
    assert done, [e.value for e in at.error]
    assert list_comparisons(), "start did not create a run"

    # steps: result tabs and summary metrics render from the report
    labels_seen = [t.label for t in at.tabs]
    assert labels_seen == ["Summary", "Added", "Removed", "Modified",
                           "Schema", "Statistics", "Warnings",
                           "Files & downloads"]
    metric_labels = [m.label for m in at.metric]
    for want in ("Status", "Strategy", "Exactness", "Warnings"):
        assert want in metric_labels, metric_labels

    pickers = [s for s in at.selectbox if s.label == "Comparison run to "
                                                  "view"]
    assert pickers and pickers[0].options, "no comparison runs listed"

    # outputs stayed outside the research data
    from analysis.comparison import load_comparison
    runs = list_comparisons()
    loaded = load_comparison(runs[0]["comparison_id"], runs[0]["run_id"])
    assert_outside_research_data(Path(loaded["run_dir"]))

    # reset returns the page to its initial, locked state
    at.button("ds6_reset").click().run()
    assert not at.exception, f"reset raised: {at.exception}"
    assert at.button("ds6_start").disabled is True


def test_comparison_page_validation_blocks_bad_selection(
        platform_dirs, tmp_path):
    """Same dataset on both sides: validation errors, Start stays locked."""
    from streamlit.testing.v1 import AppTest

    from dashboard.dataset_comparison import _dataset_options

    ds_a = _register(tmp_path, "only.csv", "id,v\n1,1\n")
    _register(tmp_path, "other.csv", "id,v\n2,2\n")
    labels, _ = _dataset_options("csv")
    label_a = next(l for l in labels if l.endswith(ds_a[:8]))

    at = AppTest.from_file(str(_page_script(tmp_path)), default_timeout=60)
    at.run()
    assert not at.exception, f"page raised: {at.exception}"

    pick_a = [s for s in at.selectbox
              if s.label == "Dataset A (first)"]
    pick_b = [s for s in at.selectbox
              if s.label == "Dataset B (second)"]
    pick_a[0].select(label_a).run()
    pick_b = [s for s in at.selectbox
              if s.label == "Dataset B (second)"]
    pick_b[0].select(label_a).run()
    assert not at.exception, f"page raised: {at.exception}"

    assert any("two different datasets" in w.value for w in at.warning)
    at.button("ds6_validate_btn").click().run()
    assert not at.exception, f"validate raised: {at.exception}"
    assert any("two different datasets" in e.value for e in at.error)
    assert at.button("ds6_start").disabled is True
