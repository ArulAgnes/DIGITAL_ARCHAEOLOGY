"""Phase 7 tests: the visualization engine (registry, builders, export).

All data is synthetic and lives in OS temporary directories. No research
file is read, written or modified; no absolute path ever reaches an
export bundle.
"""
from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.exceptions import PlatformError
from visualization import (ChartArtifact, VisualizationConfig,
                            build_chart, charts_for, export_visualization,
                            get_chart, validate_chart_request)
from visualization.chart_registry import (MODE_ARCHAEOLOGY, MODE_COMPARISON,
                                          MODE_DATASET, NEEDS_PREVIEW,
                                          CHARTS)
from visualization import filters as flt
from visualization.validation import (column_kind, kind_from_dtype_text,
                                      kind_matches)

# ------------------------------------------------------------------ #
# Synthetic fixtures (mirrors the builder smoke inputs)
# ------------------------------------------------------------------ #
CFG = VisualizationConfig(max_points=800)


@pytest.fixture()
def frame() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    df = pd.DataFrame({
        "num1": rng.normal(50, 10, 300),
        "num2": rng.normal(0, 5, 300),
        "num3": rng.integers(0, 100, 300).astype(float),
        "cat": rng.choice(list("abcdef"), 300),
        "grp": rng.choice(["x", "y", "z"], 300),
        "when": pd.date_range("2020-01-01", periods=300, freq="D"),
        "many": rng.choice([f"c{i}" for i in range(40)], 300),
    })
    df.loc[::17, "num1"] = np.nan
    return df


@pytest.fixture()
def files() -> pd.DataFrame:
    rng = np.random.default_rng(11)
    return pd.DataFrame({
        "file_name": [f"f{i % 40}.dat" for i in range(200)],
        "file_extension": rng.choice([".dat", ".csv", ".txt", None], 200),
        "file_size_bytes": (np.arange(200) % 40 * 1000.0 + 10.0),
        "creation_timestamp": pd.date_range(
            "2019-01-01", periods=200, freq="12h").astype(str),
        "modified_timestamp": pd.date_range(
            "2020-01-01", periods=200, freq="D").astype(str),
        "file_category": rng.choice(["document", "image", "code"], 200),
        "folder_depth": rng.integers(0, 8, 200),
        "naming_pattern": rng.choice(
            ["snake_case", "kebab-case", "camelCase"], 200),
        "capitalization_style": rng.choice(
            ["lower", "UPPER", "Title", "mixed"], 200),
        "contains_version_indicator": rng.choice([True, False], 200),
        "contains_final_keyword": rng.choice([True, False], 200),
        "contains_date_indicator": rng.choice([True, False], 200),
        "contains_copy_indicator": rng.choice([True, False], 200),
        "has_underscore": rng.choice([True, False], 200),
        "has_space": rng.choice([True, False], 200),
        "has_hyphen": rng.choice([True, False], 200),
        "root_folder": ["D:\\scan"] * 200,
        "parent_folder": ["D:\\scan\\a"] * 100 + ["D:\\scan\\b"] * 100,
    })


@pytest.fixture()
def folders_abs() -> pd.DataFrame:
    return pd.DataFrame({
        "folder_path": ["D:\\scan", "D:\\scan\\a", "D:\\scan\\b"],
        "parent_folder": ["", "D:\\scan", "D:\\scan"],
        "folder_depth": [0, 1, 1],
        "total_size_bytes": [1000.0, 600.0, 400.0],
    })


@pytest.fixture()
def snapshots() -> list[dict]:
    return [
        {"snapshot_id": "s1", "created_at": "2026-01-01T00:00:00",
         "total_files": 180, "total_storage_bytes": 1000},
        {"snapshot_id": "s2", "created_at": "2026-02-01T00:00:00",
         "total_files": 200, "total_storage_bytes": 1400},
    ]


@pytest.fixture()
def snap_diff() -> dict:
    return {
        "baseline": "s1", "comparison": "s2",
        "file_count_before": 180, "file_count_after": 200,
        "file_count_delta": 20,
        "storage_before_bytes": 1000, "storage_after_bytes": 1400,
        "storage_delta_bytes": 400,
        "new_files_count": 30, "removed_files_count": 10,
        "modified_files_count": 5, "new_folders_count": 2,
        "extension_changes": {".dat": {"before": 100, "after": 120,
                                       "delta": 20},
                              ".csv": {"before": 50, "after": 45,
                                       "delta": -5}},
    }


@pytest.fixture()
def run() -> dict:
    return {
        "run_id": "r1", "dataset_id": "d1",
        "files": {"config.json": "x"},
        "profile": {
            "rows": 300, "basis": "exact",
            "column_detail": {
                "num1": {"missing": 17, "missing_pct": 5.67,
                         "kind": "numeric"},
                "cat": {"missing": 0, "missing_pct": 0.0,
                        "kind": "categorical"},
                "when": {"missing": 0, "missing_pct": 0.0,
                         "kind": "datetime"},
            },
        },
        "statistics": {"num1": {"missing": 17, "count": 283, "mean": 50.1},
                       "_notes": []},
        "quality": {
            "score": 92.5, "basis": "exact", "rows": 300,
            "score_formula": "score = clamp(...)",
            "score_limitations": ["weights are heuristics"],
            "warnings": ["1 column(s) with IQR outliers (k=1.5)"],
            "duplicate_rows": 3, "columns_high_missing": [],
            "duplicate_columns": [], "inconsistent_types": [],
            "constant_columns": [], "high_cardinality_columns": [],
            "potential_outliers": {"k": 1.5, "basis": "exact",
                                   "columns": {"num1": {"count": 5,
                                                        "pct": 1.7}}},
        },
        "distributions": {
            "histograms": {"num1": {"column": "num1", "bins": 30,
                                    "excluded_missing": 17,
                                    "excluded_nonfinite": 0, "used": 283,
                                    "edges": list(np.linspace(20, 80, 31)),
                                    "counts": list(np.full(30, 9)),
                                    "basis": "exact"}},
            "categorical": {"cat": {"missing": 0, "missing_pct": 0.0,
                                    "unique": 6, "high_cardinality": False,
                                    "constant": False, "basis": "exact",
                                    "top_categories": [
                                        {"value": "a", "count": 60,
                                         "pct": 20.0},
                                        {"value": "b", "count": 55,
                                         "pct": 18.3}]}},
            "grouped": {"group_by": "grp", "basis": "exact",
                        "groups": {
                            "x": {"num1": {"count": 100, "mean": 51.0,
                                           "median": 50.5, "min": 20.0,
                                           "max": 79.0, "std": 9.0}},
                            "y": {"num1": {"count": 95, "mean": 49.0,
                                           "median": 49.5, "min": 22.0,
                                           "max": 75.0, "std": 8.0}}}},
            "bases": {"histograms": "exact", "categorical": "exact"},
            "notes": [],
        },
        "correlation": {"basis": "exact",
                        "methods": {"pearson": {
                            "columns": ["num1", "num2"],
                            "matrix": [[1.0, -0.2], [-0.2, 1.0]]}},
                        "excluded_columns": {"cat": "not numeric"},
                        "notes": ["pairwise-complete observations"],
                        "rows_total": 300, "rows_complete": 300},
        "run_metadata": {"status": "completed"},
    }


@pytest.fixture()
def comparison() -> dict:
    return {
        "comparison_id": "c1", "run_id": "r1",
        "comparison_summary": {
            "comparison_id": "c1", "kind": "csv", "status": "completed",
            "datasets": {"a": {"name": "a.csv", "dataset_id": "x",
                               "source_type": "csv"},
                         "b": {"name": "b.csv", "dataset_id": "y",
                               "source_type": "csv"}},
            "strategy": "key", "exactness": "exact", "keys": ["id"],
            "counts": {"a_rows": 300, "b_rows": 310, "matched": 290,
                       "modified": 12, "added": 20, "removed": 10},
            "truncated": {}, "warnings_count": 0, "notes": [],
        },
        "schema_differences": {
            "kind": "csv", "status": "ok",
            "columns": {"added": ["new_col"], "removed": [],
                        "dtype_changes": [
                            {"column": "num1", "a_dtype": "int64",
                             "b_dtype": "float64"}]},
            "row_counts": {"a": 300, "b": 310},
            "note": "first-chunk types"},
        "statistics_differences": {
            "kind": "csv", "basis": "exact",
            "columns": {
                "num1": {"basis": "numeric",
                         "a": {"count": 300}, "b": {"count": 310},
                         "delta": {"count": 10, "missing": 2, "mean": 1.5,
                                   "std": -0.1, "min": 0.0, "max": 3.0,
                                   "unique": 5}},
                "cat": {"basis": "categorical",
                        "a": {"missing": 0}, "b": {"missing": 1},
                        "delta": {"missing": 1},
                        "top_values": [{"value": "a", "a": 100, "b": 120},
                                       {"value": "b", "a": 80, "b": 75}],
                        "top_k": 20},
            },
            "notes": ["full pass"], "warnings": []},
        "warnings": [], "run_metadata": {"status": "completed"},
        "config": {}, "files": {}, "tables": {},
    }


@pytest.fixture()
def fs_comparison(comparison) -> dict:
    import copy
    other = copy.deepcopy(comparison)
    other["comparison_summary"]["kind"] = "filesystem"
    other["comparison_summary"]["counts"] = {
        "files_a": 200, "files_b": 210, "unchanged": 180,
        "modified": 15, "added": 30, "removed": 20, "moved": 4,
        "renamed": 3}
    other["schema_differences"] = {
        "kind": "filesystem", "status": "ok",
        "files": {"a_only_columns": [], "b_only_columns": ["new_field"]},
        "row_counts": {"a": 200, "b": 210},
        "folders": {"status": "ok", "a_only_count": 1, "b_only_count": 2},
        "note": "from scan outputs"}
    other["statistics_differences"] = {
        "kind": "filesystem", "basis": "exact",
        "storage": {"a_total_bytes": 1000000, "b_total_bytes": 1100000,
                    "delta_bytes": 100000, "a_files": 200, "b_files": 210},
        "extensions": {"top": [{"extension": ".dat", "a": 100, "b": 120,
                                "delta": 20},
                               {"extension": ".csv", "a": 50, "b": 45,
                                "delta": -5}],
                       "distinct_a": 4, "distinct_b": 4, "shown": 2,
                       "truncated": False},
        "duplicate_basenames": {"a_groups": 3, "b_groups": 4, "delta": 1},
        "naming_patterns": {"compared_pairs": 195, "changed": 6,
                            "samples": ["a -> b"]},
        "path_case_changes": {"count": 2, "samples": []},
        "notes": ["exact sums"]}
    return other


def _annotation_text(fig) -> str:
    out = []
    for a in (fig.layout.annotations or []):
        if isinstance(a, dict):
            out.append(str(a.get("text", "")))
        else:
            out.append(str(getattr(a, "text", "") or ""))
    return " | ".join(out)


# ------------------------------------------------------------------ #
# Registry
# ------------------------------------------------------------------ #
def test_registry_counts_and_sections():
    assert len(CHARTS) == 57
    assert len(charts_for(MODE_ARCHAEOLOGY)) == 15
    assert len(charts_for(MODE_DATASET)) == 33
    assert len(charts_for(MODE_COMPARISON)) == 9
    assert len(charts_for(needs=NEEDS_PREVIEW)) == 26


def test_charts_for_filters_by_needs_and_kinds():
    run_charts = charts_for(needs="run")
    assert run_charts and all(c.needs == "run" for c in run_charts)
    fs_only = charts_for(MODE_COMPARISON, kinds="filesystem")
    ids = {c.chart_id for c in fs_only}
    assert "comparison_storage" in ids and "comparison_counts" in ids
    csv_charts = charts_for(MODE_COMPARISON, kinds="csv")
    csv_ids = {c.chart_id for c in csv_charts}
    assert "comparison_storage" not in csv_ids
    assert "comparison_counts" in csv_ids


def test_get_chart_unknown_raises():
    with pytest.raises(PlatformError) as exc:
        get_chart("no_such_chart")
    assert "no_such_chart" in exc.value.user_message


def test_every_builder_is_importable():
    for spec in CHARTS.values():
        module_name, _, attr = spec.builder.partition(":")
        module = importlib.import_module(module_name)
        assert callable(getattr(module, attr)), spec.chart_id


def test_build_chart_rejects_unknown_input(frame):
    with pytest.raises(PlatformError) as exc:
        build_chart("bar", CFG, frame=frame, bogus_input=1)
    assert "unknown input" in exc.value.user_message


def test_chart_spec_roles_declare_kinds_and_labels():
    for spec in CHARTS.values():
        for name, role in spec.roles.items():
            assert role.kinds, f"{spec.chart_id}.{name} without kinds"
            assert isinstance(role.kinds, tuple)
            assert role.label, f"{spec.chart_id}.{name} without label"
        assert spec.description, spec.chart_id
        assert spec.label and spec.category


# ------------------------------------------------------------------ #
# Validation
# ------------------------------------------------------------------ #
def test_column_kind_and_dtype_text_mapping():
    assert column_kind(pd.Series([1, 2])) == "int"
    assert column_kind(pd.Series([1.5])) == "float"
    assert column_kind(pd.Series([True, False])) == "bool"
    assert column_kind(pd.Series(pd.to_datetime(["2020-01-01"]))) \
        == "datetime"
    assert column_kind(pd.Series(["a"])) == "object"
    assert kind_from_dtype_text("int64") == "int"
    assert kind_from_dtype_text("float64") == "float"
    assert kind_from_dtype_text("str") == "object"
    assert kind_from_dtype_text("datetime64[us]") == "datetime"
    assert kind_from_dtype_text("bool") == "bool"
    assert kind_from_dtype_text("") == "object"


def test_kind_matches_tolerances():
    assert kind_matches("int", ("float", "int"))
    assert kind_matches("float", ("int",))
    assert kind_matches("object", ("object", "category", "string"))
    assert kind_matches("bool", ("bool", "object"))
    assert kind_matches("datetime", ("any",))
    assert not kind_matches("datetime", ("int", "float"))
    assert not kind_matches("object", ("int", "float"))


def test_validate_chart_request_pre_flight():
    kinds = {"num1": "float", "cat": "object", "when": "datetime"}
    ok = validate_chart_request(
        "bar", kinds, {"x": "cat", "y": "num1"}, needs="preview",
        role_kinds={"x": ("any",), "y": ("int", "float")},
        role_required={"x": True, "y": False})
    assert ok.ok, ok.errors

    missing = validate_chart_request("bar", kinds, {"x": None},
                                     needs="preview",
                                     role_required={"x": True})
    assert not missing.ok
    assert any("Select a column for 'x'" in e for e in missing.errors)

    mismatch = validate_chart_request(
        "bar", kinds, {"x": "when", "y": "num1"}, needs="preview",
        role_kinds={"x": ("int", "float"), "y": ("int", "float")},
        role_required={"x": True, "y": False})
    assert not mismatch.ok
    assert any("temporal" in e for e in mismatch.errors)

    run_gate = validate_chart_request("run_histogram", kinds,
                                      {"column": "num1"},
                                      run_available=False, needs="run")
    assert not run_gate.ok
    assert any("Dataset Analysis page" in e for e in run_gate.errors)

    empty = validate_chart_request("bar", kinds, {"x": "cat"},
                                   needs="preview", frame_empty=True,
                                   role_required={"x": True})
    assert not empty.ok
    assert any("No rows" in e for e in empty.errors)

    unknown = validate_chart_request(None, {}, {})
    assert not unknown.ok


# ------------------------------------------------------------------ #
# Filters
# ------------------------------------------------------------------ #
def test_apply_row_filters_and_summarize():
    df = pd.DataFrame({
        "when": pd.to_datetime(["2020-01-01", "2020-06-01",
                                "2021-01-01"]),
        "score": [1.0, 5.0, 9.0],
        "grp": ["a", "b", "a"],
        "note": ["x", None, "y"],
    })
    res = flt.apply_date_range(df, "when", "2020-01-01", "2020-12-31")
    assert len(res.frame) == 2 and res.dropped_rows == 1
    assert res.applied and "from 2020-01-01" in res.applied[0]

    res = flt.apply_numeric_range(df, "score", 2.0, None)
    assert len(res.frame) == 2
    assert ">= 2" in res.applied[0]

    res = flt.apply_categories(df, "grp", ["a"])
    assert len(res.frame) == 2
    assert "1 value(s) selected" in res.applied[0]

    res = flt.apply_missing_policy(df, "note", True)
    assert len(res.frame) == 2
    assert res.dropped_rows == 1

    lines = flt.summarize_filters({"root": "D:\\x", "ext": ".csv",
                                   "skip": []}, ["grp: 1 value(s)"])
    assert "root: D:\\x" in lines and "grp: 1 value(s)" in lines
    assert not any("skip" in line for line in lines)


def test_top_categories_regroups_with_disclosure():
    df = pd.DataFrame({"c": [f"v{i}" for i in range(10)] * 3})
    out, info = flt.top_categories(df, "c", 4)
    assert info["regrouped"] is True
    assert info["total_categories"] == 10
    assert "Other (rest)" in set(out["c"])
    assert len(out) == 5


def test_sample_frame_deterministic_and_disclosed():
    df = pd.DataFrame({"v": range(400)})
    a, info_a = flt.sample_frame(df, 100, 42)
    b, info_b = flt.sample_frame(df, 100, 42)
    assert info_a["applied"] is True
    assert info_a["approximate"] is True
    assert info_a["method"] == "random_sample"
    assert info_a["params"]["seed"] == 42
    assert info_a["params"]["requested_rows"] == 400
    assert a["v"].tolist() == b["v"].tolist()
    small, info_s = flt.sample_frame(df.head(10), 100, 42)
    assert info_s["applied"] is False and len(small) == 10


# ------------------------------------------------------------------ #
# Generic builders
# ------------------------------------------------------------------ #
def test_bar_counts_and_top_n_disclosure(frame):
    art = build_chart("bar", VisualizationConfig(max_bar_categories=5),
                      roles={"x": "many"}, frame=frame,
                      source_label="synthetic.csv")
    assert art.display["displayed_rows"] == len(frame)
    assert any("Other (rest)" in n for n in art.notes), art.notes
    assert "Records by many" == art.title


def test_bar_with_aggregation(frame):
    art = build_chart("bar", CFG, roles={"x": "cat", "y": "num1"},
                      options={"agg": "mean"}, frame=frame,
                      source_label="synthetic.csv")
    assert "Mean of num1" in art.title
    assert "mean_num1" in art.data.columns


def test_histogram_bins_and_exclusions():
    df = pd.DataFrame({"v": [1.0, 2.0, np.inf, -np.inf, np.nan, 3.0]})
    art = build_chart("histogram", CFG, roles={"values": "v"},
                      options={"bins": 3}, frame=df,
                      source_label="synthetic.csv")
    assert len(art.data) == 3
    joined = " ".join(art.notes)
    assert "non-finite" in joined or "excluded" in joined


def test_scatter_samples_and_marks_approximate(frame):
    art = build_chart("scatter",
                      VisualizationConfig(max_points=50),
                      roles={"x": "num1", "y": "num2"}, frame=frame,
                      source_label="synthetic.csv")
    assert art.display["approximate"] is True
    sampling = art.display["sampling"]
    assert sampling["applied"] is True and sampling["params"]["seed"] == 42
    assert "Sampling:" in _annotation_text(art.figure)


def test_pie_groups_beyond_limit(frame):
    art = build_chart("pie", VisualizationConfig(pie_max_categories=3),
                      roles={"names": "many"}, frame=frame,
                      source_label="synthetic.csv")
    assert any("Other (rest)" in n for n in art.notes), art.notes


def test_pca_deterministic_same_seed(frame):
    roles = {"columns": ["num1", "num2", "num3"], "color": "grp"}
    a = build_chart("pca_scatter", CFG, roles=roles, frame=frame,
                    source_label="s")
    b = build_chart("pca_scatter", CFG, roles=roles, frame=frame,
                    source_label="s")
    xa = np.asarray(a.figure.data[0].x, dtype=float)
    xb = np.asarray(b.figure.data[0].x, dtype=float)
    assert np.allclose(xa, xb)
    assert "explained" in _annotation_text(a.figure).lower() or \
        "variance" in _annotation_text(a.figure).lower() or \
        any("explained" in n.lower() for n in a.notes)


def test_tsne_builds_or_explains_missing_dependency(frame):
    roles = {"columns": ["num1", "num2", "num3"], "color": "grp"}
    try:
        art = build_chart("tsne_scatter", CFG, roles=roles, frame=frame,
                          source_label="s")
    except PlatformError as exc:
        assert "scikit-learn" in exc.user_message
        pytest.skip("scikit-learn not installed in this environment")
    assert len(art.figure.data) >= 1
    joined = " ".join(art.notes).lower()
    assert "not meaningful" in joined or "neighbourhood" in joined or \
        "neighborhood" in joined


def test_missing_role_raises_platform_error(frame):
    with pytest.raises(PlatformError):
        build_chart("bar", CFG, roles={}, frame=frame,
                    source_label="synthetic.csv")


def test_empty_frame_raises_clear_error():
    with pytest.raises(PlatformError) as exc:
        build_chart("bar", CFG, roles={"x": "cat"},
                    frame=pd.DataFrame({"cat": []}),
                    source_label="synthetic.csv")
    assert "No rows" in exc.value.user_message


def test_parallel_and_matrix_charts(frame):
    art = build_chart("parallel_coordinates", CFG,
                      roles={"columns": ["num1", "num2", "num3"],
                             "color": "grp"}, frame=frame,
                      source_label="s")
    assert len(art.figure.data) >= 1
    matrix = build_chart("scatter_matrix", CFG,
                         roles={"columns": ["num1", "num2", "num3"]},
                         frame=frame, source_label="s")
    assert len(matrix.figure.data) >= 1


def test_hierarchy_counts_and_paths(frame):
    art = build_chart("treemap", CFG,
                      roles={"cat": "cat", "sub": "grp"}, frame=frame,
                      source_label="s")
    trace = art.figure.data[0]
    assert len(trace.labels) >= 6
    assert "count" in art.data.columns
    values = build_chart("sunburst", CFG,
                         roles={"cat": "cat", "values": "num1"},
                         frame=frame, source_label="s")
    assert "value" in values.data.columns


# ------------------------------------------------------------------ #
# Archaeology builders
# ------------------------------------------------------------------ #
def test_archaeology_timelines_and_distributions(files):
    art = build_chart("creation_timeline", CFG,
                      roles={"group": "file_category"}, files=files,
                      source_label="scan")
    assert len(art.figure.data) >= 1
    types = build_chart("type_distribution",
                        VisualizationConfig(max_bar_categories=2),
                        files=files, source_label="scan")
    assert any("top" in n.lower() or "Other (rest)" in n
               for n in types.notes), types.notes
    storage = build_chart("storage_by_category", CFG,
                          roles={"split": "file_category"}, files=files,
                          source_label="scan")
    assert len(storage.figure.data) >= 1
    growth = build_chart("growth_timeline", CFG, files=files,
                         source_label="scan")
    assert len(growth.figure.data) >= 1


def test_size_distribution_log_axis(files):
    art = build_chart("size_distribution", CFG, options={"log_x": True},
                      files=files, source_label="scan")
    assert art.figure.layout.xaxis.type == "log"


def test_folder_hierarchy_labels_are_relative(folders_abs):
    art = build_chart("folder_hierarchy", CFG, folders=folders_abs,
                      source_label="scan")
    trace = art.figure.data[0]
    labels = [str(x) for x in trace.labels]
    assert labels, "no hierarchy labels"
    drive = re.compile(r"^[A-Za-z]:")
    assert not any(drive.match(lab) or "\\" in lab or "/" in lab
                   for lab in labels), labels
    assert "a" in labels and "b" in labels
    assert any("relative" in n for n in art.notes)


def test_depth_naming_capitalization_duplicates(files):
    for chart_id in ("depth_distribution", "naming_patterns",
                     "capitalization_distribution", "duplicate_candidates"):
        art = build_chart(chart_id, CFG, files=files, folders=None,
                          source_label="scan")
        assert art.title, chart_id
        assert len(art.figure.data) >= 1 or art.figure.layout.annotations


def test_duplicate_candidates_are_candidates_only(files):
    art = build_chart("duplicate_candidates", CFG, files=files,
                      source_label="scan")
    joined = " ".join(art.notes).lower()
    assert "candidate" in joined


def test_snapshot_charts(snapshots, snap_diff):
    timeline = build_chart("snapshot_timeline", CFG,
                           snapshots=snapshots, source_label="scan")
    assert len(timeline.figure.data) >= 1
    diff = build_chart("snapshot_diff", CFG, snapshot_diff=snap_diff,
                       source_label="scan")
    assert set(diff.data["change"]) >= {"new files", "removed files"}
    growth = build_chart("snapshot_storage_growth", CFG,
                         snapshot_diff=snap_diff, source_label="scan")
    assert "400" in " ".join(growth.notes) or \
        int(growth.data["delta"].iloc[-1]) == 400 or \
        any("400" in n for n in growth.notes)
    ext = build_chart("snapshot_extension_changes", CFG,
                      snapshot_diff=snap_diff, source_label="scan")
    assert ".dat" in set(ext.data["extension"])


# ------------------------------------------------------------------ #
# Analysis-run builders (load_run shape)
# ------------------------------------------------------------------ #
def test_run_histogram_uses_stored_bins(run):
    art = build_chart("run_histogram", CFG, roles={"column": "num1"},
                      run=run, source_label="run")
    assert int(art.data["count"].sum()) == 270
    assert any("exactly those stored" in n for n in art.notes), art.notes


def test_run_categorical_and_grouped(run):
    cat = build_chart("run_categorical", CFG, roles={"column": "cat"},
                      run=run, source_label="run")
    assert len(cat.data) == 2
    grouped = build_chart("run_grouped_stats", CFG,
                          roles={"column": "num1"}, run=run,
                          source_label="run")
    assert set(grouped.data["group"]) == {"x", "y"}


def test_run_correlation_from_correlation_df(run):
    run = dict(run)
    run.pop("correlation", None)
    run["correlation_df"] = pd.DataFrame({
        "method": ["pearson", "pearson"],
        "column_a": ["num1", "num1"],
        "column_b": ["num1", "num2"],
        "value": [1.0, -0.2],
    })
    art = build_chart("run_correlation", CFG, roles={"method": "pearson"},
                      run=run, source_label="run")
    assert len(art.figure.data) == 1
    assert any("correlation.csv" in n for n in art.notes), art.notes


def test_run_missing_values_prefers_profile_then_statistics(run):
    art = build_chart("run_missing_values", CFG, run=run,
                      source_label="run")
    assert set(art.data["column"]) == {"num1", "cat", "when"}

    # load_run file shape: profile only inside summary
    load_shaped = {
        "files": {"summary.json": "x"},
        "summary": {"profile": run["profile"]},
        "statistics": {"basis": "exact",
                       "columns": {"num1": {"missing": 17},
                                   "_notes": ["n"]}},
        "quality": run["quality"],
        "distributions": run["distributions"],
    }
    art2 = build_chart("run_missing_values", CFG, run=load_shaped,
                       source_label="run")
    assert set(art2.data["column"]) == {"num1", "cat", "when"}

    # statistics-only fallback (no profile anywhere)
    stats_only = {
        "files": {"statistics.json": "x"},
        "statistics": {"basis": "exact",
                       "columns": {"num1": {"missing": 17},
                                   "num2": {"missing": 0},
                                   "_notes": []}},
        "quality": {}, "distributions": {},
    }
    art3 = build_chart("run_missing_values", CFG, run=stats_only,
                       source_label="run")
    assert set(art3.data["column"]) == {"num1", "num2"}


def test_run_quality_and_outliers(run):
    quality = build_chart("run_quality", CFG, run=run,
                          source_label="run")
    assert "quality" in quality.title.lower()
    assert int(quality.data["count"].sum()) >= 1
    outliers = build_chart("run_outliers", CFG, run=run,
                           source_label="run")
    assert set(outliers.data["column"]) == {"num1"}
    assert any("review" in n.lower() for n in outliers.notes)


def test_run_charts_without_run_raise():
    with pytest.raises(PlatformError) as exc:
        build_chart("run_histogram", CFG, roles={"column": "num1"},
                    run=None, source_label="")
    assert "Dataset Analysis page" in exc.value.user_message


# ------------------------------------------------------------------ #
# Comparison builders
# ------------------------------------------------------------------ #
def test_comparison_counts_and_exactness_badge(comparison):
    art = build_chart("comparison_counts", CFG, comparison=comparison,
                      source_label="comparison")
    text = _annotation_text(art.figure)
    assert "exactness: exact" in text
    assert art.approximate is False
    assert any("exact totals" in n for n in art.notes)


def test_comparison_non_exact_marks_approximate(comparison):
    import copy
    other = copy.deepcopy(comparison)
    other["comparison_summary"]["exactness"] = "sample"
    art = build_chart("comparison_counts", CFG, comparison=other,
                      source_label="comparison")
    assert art.approximate is True
    assert "exactness: sample" in _annotation_text(art.figure)


def test_comparison_numeric_delta_direction(comparison):
    art = build_chart("comparison_numeric_delta", CFG,
                      roles={"column": "num1"}, comparison=comparison,
                      source_label="comparison")
    row = art.data[art.data["field"] == "mean"].iloc[0]
    assert float(row["delta_b_minus_a"]) == 1.5      # B − A, positive
    assert "B − A" in art.title or any("B − A" in n for n in art.notes)


def test_comparison_category_change(comparison):
    art = build_chart("comparison_category_change", CFG,
                      roles={"column": "cat"}, comparison=comparison,
                      source_label="comparison")
    row = art.data[art.data[art.data.columns[0]] == "a"].iloc[0]
    assert int(row["delta_b_minus_a"]) == 20         # 120 − 100


def test_comparison_missing_and_schema(comparison):
    missing = build_chart("comparison_missing_delta", CFG,
                          comparison=comparison, source_label="c")
    assert set(missing.data["column"]) == {"num1", "cat"}
    schema = build_chart("comparison_schema", CFG,
                         comparison=comparison, source_label="c")
    aspects = set(schema.data["aspect"])
    assert "columns added in B" in aspects
    assert "dtype changes" in aspects


def test_comparison_storage_extension_naming_fs(fs_comparison):
    storage = build_chart("comparison_storage", CFG,
                          comparison=fs_comparison, source_label="c")
    assert int(storage.data["bytes"].iloc[1]) == 1100000
    ext = build_chart("comparison_extension", CFG,
                      comparison=fs_comparison, source_label="c")
    assert ".dat" in set(ext.data["extension"])
    naming = build_chart("comparison_naming", CFG,
                         comparison=fs_comparison, source_label="c")
    changes = set(naming.data["change"])
    assert "moved files" in changes and "renamed files" in changes


def test_comparison_storage_on_csv_is_clear_empty_state(comparison):
    art = build_chart("comparison_storage", CFG, comparison=comparison,
                      source_label="c")
    assert "filesystem" in " ".join(art.notes).lower()
    assert len(art.figure.data) == 0


def test_comparison_disabled_statistics_raise(comparison):
    import copy
    other = copy.deepcopy(comparison)
    other["statistics_differences"] = {"status": "disabled"}
    with pytest.raises(PlatformError) as exc:
        build_chart("comparison_numeric_delta", CFG,
                    roles={"column": "num1"}, comparison=other,
                    source_label="c")
    assert "statistical differences" in exc.value.user_message


def test_comparison_not_completed_raises(comparison):
    import copy
    other = copy.deepcopy(comparison)
    other["comparison_summary"]["status"] = "failed"
    with pytest.raises(PlatformError) as exc:
        build_chart("comparison_counts", CFG, comparison=other,
                    source_label="c")
    assert "failed" in exc.value.user_message


def test_comparison_without_run_raises():
    with pytest.raises(PlatformError) as exc:
        build_chart("comparison_counts", CFG, comparison=None,
                    source_label="")
    assert "Dataset Comparison page" in exc.value.user_message


# ------------------------------------------------------------------ #
# Export bundle
# ------------------------------------------------------------------ #
def _simple_artifact() -> ChartArtifact:
    df = pd.DataFrame({"cat": ["a", "b"], "count": [3, 2]})
    return build_chart("bar", CFG, roles={"x": "cat"},
                       frame=pd.DataFrame(
                           {"cat": ["a", "b", "a", "b", "a"]}),
                       source_label="synthetic.csv")


def test_export_bundle_writes_all_files_without_paths(tmp_path):
    artifact = _simple_artifact()
    out_root = tmp_path / "viz_out"
    manifest = export_visualization(
        artifact, cfg=CFG,
        source={"section": "dataset", "chart_id": "bar",
                "source_label": "synthetic.csv"},
        section="dataset", out_root=out_root)
    target = Path(manifest["directory"])
    assert target.parent == out_root
    assert re.match(r"^viz_\d{8}_\d{6}_[0-9a-f]{6}$",
                    manifest["visualization_id"])
    for name in ("chart.html", "chart_data.csv", "config.json",
                 "metadata.json"):
        assert (target / name).is_file(), name
    assert manifest["relative_dir"].startswith("output/visualizations/")
    metadata = json.loads((target / "metadata.json")
                          .read_text(encoding="utf-8"))
    assert metadata["export"]["contains_absolute_paths"] is False
    # no absolute personal/source path anywhere in the bundle text
    leak = re.compile(r"[A-Za-z]:\\Users\\|/Users/")
    for name in ("config.json", "metadata.json", "chart_data.csv"):
        text = (target / name).read_text(encoding="utf-8")
        assert not leak.search(text), name
        assert str(tmp_path) not in text, name


def test_export_truncates_data_and_records_it(tmp_path):
    artifact = _simple_artifact()
    artifact.data = pd.DataFrame({"v": range(250)})
    cfg = VisualizationConfig(export_max_rows=100)
    manifest = export_visualization(
        artifact, cfg=cfg, source={"section": "dataset"},
        section="dataset", out_root=tmp_path / "viz_out")
    assert manifest["chart_data_rows"] == 100
    assert manifest["chart_data_rows_total"] == 250
    assert manifest["chart_data_truncated"] is True
    target = Path(manifest["directory"])
    csv_text = (target / "chart_data.csv").read_text(encoding="utf-8")
    assert len(csv_text.strip().splitlines()) == 101   # header + 100


def test_export_without_artifact_raises(tmp_path):
    with pytest.raises(PlatformError):
        export_visualization(None, cfg=CFG, source={}, section="dataset",
                             out_root=tmp_path / "viz_out")


# ------------------------------------------------------------------ #
# Configuration
# ------------------------------------------------------------------ #
def test_visualization_config_clamps_and_roundtrips():
    cfg = VisualizationConfig(max_points=1, preview_rows=1,
                              top_n=0, histogram_bins=1,
                              export_max_rows=1, seed=7)
    assert cfg.max_points == 50
    assert cfg.preview_rows == 100
    assert cfg.top_n == 1
    assert cfg.histogram_bins == 2
    assert cfg.export_max_rows == 100
    assert cfg.seed == 7
    again = VisualizationConfig(**cfg.to_dict())
    assert again.to_dict() == cfg.to_dict()
    assert cfg.to_dict()["seed"] == 7
