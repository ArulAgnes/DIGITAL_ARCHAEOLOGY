"""Phase 5 tests: reusable analysis modules (pure functions + accumulators).

Hand-verified values, cross-checks against the standard library, and
documented edge cases (missing, inf, empty, constant, mixed types).
Everything runs on tiny in-memory frames - no research data is read.
"""
from __future__ import annotations

import json
import statistics as py_statistics

import numpy as np
import pandas as pd
import pytest

from analysis.correlation import correlation_matrix
from analysis.data_quality import (QualityAccumulator, analyze_quality,
                                   assess_quality, classify_value,
                                   column_fingerprint, duplicate_column_groups)
from analysis.duplicate_analysis import record_duplicates
from analysis.distributions import (frequency_distribution,
                                    grouped_statistics,
                                    numerical_histogram)
from analysis.dataset_profiler import classify_column, profile_dataset
from analysis.frequency_analysis import (ValueCountAccumulator,
                                         categorical_stats)
from analysis.outliers import iqr_fences, outlier_report, outlier_summary
from analysis.statistics import NumericAccumulator, numerical_stats


# ----------------------------------------------------------------- #
# Numerical statistics
# ----------------------------------------------------------------- #
def test_numerical_stats_basic_cross_checked_with_stdlib():
    values = [float(i) for i in range(1, 11)]
    out = numerical_stats(pd.Series(values), "v")
    assert out.count == 10
    assert out.missing == 0
    assert out.excluded_nonfinite == 0
    assert out.unique == 10
    assert out.mean == pytest.approx(py_statistics.mean(values), abs=1e-9)
    assert out.median == 5.5
    assert out.std == pytest.approx(py_statistics.stdev(values), abs=1e-9)
    assert out.variance == pytest.approx(py_statistics.variance(values),
                                         abs=1e-9)
    assert out.min == 1.0 and out.max == 10.0
    assert out.range == 9.0
    assert out.q1 == pytest.approx(3.25, abs=1e-9)
    assert out.q3 == pytest.approx(7.75, abs=1e-9)
    assert out.iqr == pytest.approx(4.5, abs=1e-9)


def test_numerical_stats_mode_and_notes():
    out = numerical_stats(pd.Series([1.0, 2.0, 2.0, 3.0]), "v")
    assert out.mode == 2.0
    assert out.basis == "exact"


def test_numerical_stats_basis_sample_passthrough():
    out = numerical_stats(pd.Series([1.0, 2.0]), "v", basis="sample")
    assert out.basis == "sample"


def test_numerical_stats_missing_nonfinite_and_text():
    s = pd.Series([1.0, 2.0, None, np.inf, -np.inf, "text"], dtype=object)
    out = numerical_stats(s, "v")
    assert out.count == 2
    assert out.missing == 2            # None + non-numeric text
    assert out.excluded_nonfinite == 2  # +inf and -inf
    assert any("non-numeric" in n for n in out.notes)


def test_numerical_stats_constant_column():
    out = numerical_stats(pd.Series([5.0, 5.0, 5.0]), "v")
    assert out.unique == 1
    assert out.range == 0.0
    assert out.std == 0.0
    assert out.min == out.max == 5.0


def test_numerical_stats_empty_and_single_value():
    empty = numerical_stats(pd.Series([], dtype="float64"), "v")
    assert empty.count == 0
    assert any("no usable" in n for n in empty.notes)

    single = numerical_stats(pd.Series([7.0]), "v")
    assert single.count == 1
    assert single.std is None
    assert any("at least 2" in n for n in single.notes)


# ----------------------------------------------------------------- #
# Streaming numeric accumulator
# ----------------------------------------------------------------- #
def _values(n: int = 1000) -> list[float]:
    return [((i * i) % 97) * 0.5 + (i % 3) for i in range(n)]


def test_numeric_accumulator_chunked_matches_exact():
    values = _values()
    full = numerical_stats(pd.Series(values), "v")
    acc = NumericAccumulator("v")
    step = 137
    for start in range(0, len(values), step):
        acc.update(pd.Series(values[start:start + step]))
    got = acc.finalize()
    assert got.count == full.count
    assert got.missing == full.missing
    assert got.unique == full.unique
    assert got.min == full.min and got.max == full.max
    assert got.mean == pytest.approx(full.mean, abs=1e-9)
    assert got.std == pytest.approx(full.std, abs=1e-9)
    assert got.q2 == pytest.approx(full.q2, abs=1e-9)


def test_numeric_accumulator_reservoir_caps_and_labels_basis():
    acc = NumericAccumulator("v", max_values=10, seed=1)
    acc.update(pd.Series(_values(100)))
    got = acc.finalize()
    assert acc.basis == "reservoir_sample"
    assert got.basis == "reservoir_sample"
    assert got.count == 100
    assert got.notes  # sampling is stated in the output


def test_numeric_accumulator_distinct_cap_makes_unique_unknown():
    acc = NumericAccumulator("v", distinct_cap=3, seed=1)
    acc.update(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]))
    got = acc.finalize()
    assert got.unique is None
    assert any("unique count not tracked" in n for n in got.notes)


def test_numeric_accumulator_counts_missing_and_nonfinite():
    acc = NumericAccumulator("v")
    acc.update(pd.Series([1.0, np.nan, np.inf, -np.inf, 2.0]))
    got = acc.finalize()
    assert got.count == 2
    assert got.missing == 1
    assert got.excluded_nonfinite == 2


# ----------------------------------------------------------------- #
# Categorical / frequency analysis
# ----------------------------------------------------------------- #
def test_categorical_stats_basics_with_rare_and_top():
    out = categorical_stats(pd.Series(["x", "x", "y", None]), "c")
    assert out.missing == 1
    assert out.non_missing == 3
    assert out.unique == 2
    assert out.most_frequent["value"] == "x"
    assert out.most_frequent["count"] == 2
    assert out.most_frequent["pct"] == pytest.approx(66.6667, abs=1e-4)
    assert any(e["value"] == "y" for e in out.rare_categories)
    assert out.basis == "exact"
    assert out.high_cardinality is False


def test_categorical_stats_constant_high_cardinality_and_top_cap():
    const = categorical_stats(pd.Series(["a", "a"]), "c")
    assert const.constant is True

    high = categorical_stats(pd.Series(["a", "b", "c"]), "c",
                             high_cardinality_unique=3)
    assert high.high_cardinality is True

    capped = categorical_stats(pd.Series(["a", "b", "c", "a"]), "c", top=1)
    assert len(capped.top_categories) == 1
    assert any("top 1 of" in n for n in capped.notes)


def test_categorical_stats_all_missing():
    out = categorical_stats(pd.Series([None, None, None]), "c")
    assert out.unique == 0
    assert any("no non-missing" in n for n in out.notes)


def test_value_count_accumulator_matches_exact_counts():
    s = pd.Series(["x", "y", "x", None, "y", "y"])
    acc = ValueCountAccumulator("c")
    acc.update(s.iloc[:3])
    acc.update(s.iloc[3:])
    got = acc.finalize(top=10)
    exact = categorical_stats(s, "c", top=10)
    assert got.missing == exact.missing == 1
    assert got.non_missing == exact.non_missing == 5
    assert got.unique == exact.unique == 2
    assert got.top_categories == exact.top_categories
    assert got.rare_categories == exact.rare_categories


def test_value_count_accumulator_distinct_cap_labeled():
    acc = ValueCountAccumulator("c", max_distinct=2, seed=1)
    acc.update(pd.Series(["a", "b", "c", "d", "a", "b"]))
    assert acc.distinct_exact is False
    got = acc.finalize()
    assert got.unique is None
    assert got.basis == "reservoir_sample"
    assert any("reservoir sample" in n for n in got.notes)


def test_frequency_distribution_ordering_top_and_pct():
    rows = frequency_distribution({"a": 3, "b": 5, "c": 1})
    assert [r["value"] for r in rows] == ["b", "a", "c"]
    assert rows[0]["count"] == 5
    assert rows[0]["pct"] == pytest.approx(55.5556, abs=1e-4)
    top2 = frequency_distribution({"a": 3, "b": 5, "c": 1}, top=2)
    assert [r["value"] for r in top2] == ["b", "a"]


# ----------------------------------------------------------------- #
# Distributions
# ----------------------------------------------------------------- #
def test_numerical_histogram_edges_and_counts():
    # last numpy bin is closed on the right: 2, 2, 3 all land in [2, 3]
    out = numerical_histogram(pd.Series([1.0, 2.0, 2.0, 3.0]), bins=2)
    assert out["counts"] == [1, 3]
    assert out["edges"] == [1.0, 2.0, 3.0]
    assert out["used"] == 4
    assert out["excluded_missing"] == 0
    assert out["excluded_nonfinite"] == 0


def test_numerical_histogram_constant_and_nonfinite_handling():
    same = numerical_histogram(pd.Series([5.0, 5.0, 5.0]), bins=3)
    assert any("all values identical" in n for n in same["notes"])
    assert sum(same["counts"]) == 3

    dirty = numerical_histogram(pd.Series([1.0, 2.0, np.inf, np.nan]),
                                bins=2)
    assert dirty["used"] == 2
    assert dirty["excluded_nonfinite"] == 1
    assert dirty["excluded_missing"] == 1

    none = numerical_histogram(pd.Series([np.nan]), bins=2)
    assert any("no finite values" in n for n in none["notes"])
    assert none["counts"] == []


def test_grouped_statistics_values_and_excluded_keys():
    df = pd.DataFrame({"g": ["a", "a", "b", None],
                       "v": [1.0, 3.0, 2.0, 9.0]})
    out = grouped_statistics(df, "g")
    assert out["group_by"] == "g"
    assert out["excluded_rows_missing_group_key"] == 1
    assert out["groups"]["a"]["v"]["mean"] == pytest.approx(2.0, abs=1e-9)
    assert out["groups"]["a"]["v"]["count"] == 2
    assert out["groups"]["b"]["v"]["count"] == 1
    assert "v" not in out["groups"].get("a", {"g": {}}).get("g", {})


def test_grouped_statistics_notes_for_missing_column_and_cap():
    df = pd.DataFrame({"g": ["a", "b"], "v": [1.0, 2.0]})
    missing = grouped_statistics(df, "zz")
    assert any("not found" in n for n in missing["notes"])

    big = pd.DataFrame({"g": ["a", "a", "b", "c"], "v": [1.0, 2.0, 3.0, 4.0]})
    capped = grouped_statistics(big, "g", max_groups=2)
    assert any("largest 2" in n for n in capped["notes"])
    assert capped["groups_total"] == 3
    assert capped["groups_shown"] == 2


# ----------------------------------------------------------------- #
# Correlation
# ----------------------------------------------------------------- #
def test_correlation_matrix_perfect_pair_and_exclusions():
    df = pd.DataFrame({
        "x": [1.0, 2.0, 3.0, 4.0, 5.0],
        "y": [2.0, 4.0, 6.0, 8.0, 10.0],
        "cat": ["a", "b", "c", "d", "e"],
        "flag": [True, False, True, False, True],
        "flat": [1.0, 1.0, 1.0, 1.0, 1.0],
    })
    out = correlation_matrix(df, methods=("pearson",), basis="exact")
    pear = out["methods"]["pearson"]
    assert pear["columns"] == ["x", "y"]
    assert pear["matrix"][0][1] == pytest.approx(1.0, abs=1e-6)
    assert pear["matrix"][0][0] == pytest.approx(1.0, abs=1e-6)
    assert out["excluded_columns"]["cat"] == "not numeric"
    assert "boolean" in out["excluded_columns"]["flag"]
    assert "constant" in out["excluded_columns"]["flat"]
    assert out["rows_total"] == 5
    assert out["rows_complete"] == 5
    assert out["basis"] == "exact"
    assert any("pairwise-complete" in n for n in out["notes"])


def test_correlation_matrix_edge_cases():
    few = correlation_matrix(pd.DataFrame({"a": [1.0, 2.0]}))
    assert any("fewer than 2" in n for n in few["notes"])

    empty = correlation_matrix(pd.DataFrame())
    assert any("empty" in n for n in empty["notes"])

    limited = correlation_matrix(
        pd.DataFrame({"a": [1.0, 2.0], "b": [2.0, 3.0]}),
        max_columns=1)
    assert any("limited to 1" in n for n in limited["notes"])


# ----------------------------------------------------------------- #
# Outliers
# ----------------------------------------------------------------- #
def test_iqr_fences_formula():
    lo, hi = iqr_fences(1.0, 5.0, k=1.5)
    assert lo == pytest.approx(-5.0, abs=1e-9)
    assert hi == pytest.approx(11.0, abs=1e-9)


def test_outlier_summary_flags_and_reports_basis():
    out = outlier_summary(pd.Series([1.0, 2.0, 3.0, 100.0]), k=1.5,
                          column="v")
    # q1=1.75, q3=27.25, IQR=25.5 -> upper fence 27.25 + 38.25 = 65.5
    assert out["upper_fence"] == pytest.approx(65.5, abs=1e-6)
    assert out["count"] == 1
    assert out["pct"] == pytest.approx(25.0, abs=1e-9)
    assert out["basis"] == "exact"
    assert out["used"] == 4

    small = outlier_summary(pd.Series([1.0, 100.0]))
    assert any("fewer than 4" in n for n in small["notes"])

    none = outlier_summary(pd.Series([np.nan, np.nan]))
    assert any("no finite" in n for n in none["notes"])
    assert none["count"] == 0


def test_outlier_report_numeric_only():
    df = pd.DataFrame({"v": [1.0, 2.0, 3.0, 100.0],
                       "cat": ["a", "b", "c", "d"]})
    rep = outlier_report(df, k=1.5, basis="exact")
    assert "v" in rep["columns"]
    assert "cat" not in rep["columns"]
    assert rep["columns"]["v"]["count"] == 1


# ----------------------------------------------------------------- #
# Quality checks
# ----------------------------------------------------------------- #
def test_analyze_quality_clean_frame():
    df = pd.DataFrame({"a": [1.0, 2.0, 3.0, 4.0],
                       "b": ["x", "y", "x", "y"]})
    q = analyze_quality(df)
    assert q["score"] is not None
    assert 0 <= q["score"] <= 100
    assert q["completeness_pct"] == 100.0
    assert q["duplicate_rows"] == 0
    assert q["duplicate_columns"] == []
    assert q["constant_columns"] == []
    assert q["score_formula"]
    assert q["score_limitations"]
    assert q["invalid_values"]["nonfinite_cells"] == 0


def test_analyze_quality_duplicates_constants_missing_inf():
    df = pd.DataFrame({"a": [1.0, 1.0, 2.0, 3.0, 1.0],
                       "flat": [7.0, 7.0, 7.0, 7.0, 7.0],
                       "mostly": [np.nan, np.nan, np.nan, np.nan, 1.0],
                       "inf": [1.0, 1.0, np.inf, 3.0, 4.0]})
    q = analyze_quality(df)
    assert q["duplicate_rows"] == 1  # rows 0 and 1 are identical
    assert any("duplicate row" in w for w in q["warnings"])
    assert "flat" in q["constant_columns"]
    missing_cols = {e["column"]: e for e in q["columns_high_missing"]}
    assert "mostly" in missing_cols
    assert missing_cols["mostly"]["severity"] in ("warn", "fail")
    assert q["invalid_values"]["nonfinite_cells"] == 1
    assert q["invalid_values"]["columns"][0]["column"] == "inf"


def test_analyze_quality_duplicate_and_inconsistent_columns():
    df = pd.DataFrame({"a": [1.0, 2.0, 3.0],
                       "b": [1.0, 2.0, 3.0],
                       "mixed": [1, "two", 3]})
    q = analyze_quality(df)
    assert ["a", "b"] in q["duplicate_columns"]
    assert any(e["column"] == "mixed" for e in q["inconsistent_types"])
    incons = next(e for e in q["inconsistent_types"]
                  if e["column"] == "mixed")
    assert "int" in incons["categories"]
    assert "str" in incons["categories"]


def test_analyze_quality_outliers_flagged_and_skippable():
    df = pd.DataFrame({"v": [1.0, 2.0, 3.0, 100.0]})
    flagged = analyze_quality(df, k=1.5, detect_outliers=True)
    assert "v" in flagged["potential_outliers"]["columns"]
    assert any("never auto-removed" in w for w in flagged["warnings"])

    skipped = analyze_quality(df, detect_outliers=False)
    assert skipped["potential_outliers"]["basis"] == "skipped"
    assert skipped["potential_outliers"]["columns"] == {}


def test_analyze_quality_empty_and_high_cardinality():
    empty = analyze_quality(pd.DataFrame())
    assert empty["score"] == 0
    assert any("empty" in w for w in empty["warnings"])
    assert empty["completeness_pct"] is None

    df = pd.DataFrame({"id": [1, 2, 3, 4]})
    q = analyze_quality(df, high_cardinality_unique=3)
    assert q["high_cardinality_columns"][0]["column"] == "id"


def test_assess_quality_phase2_entry_point_still_works():
    df = pd.DataFrame({"a": [1.0, 2.0, None], "b": ["x", "y", "z"]})
    res = assess_quality(df)
    assert "score" in res or "status" in res


# ----------------------------------------------------------------- #
# Fingerprints, duplicate columns, record duplicates
# ----------------------------------------------------------------- #
def test_column_fingerprint_order_stable_across_chunks():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    whole = column_fingerprint(s)
    import hashlib
    h = hashlib.sha256()
    from analysis.data_quality import column_fingerprint_update
    column_fingerprint_update(h, s.iloc[:2])
    column_fingerprint_update(h, s.iloc[2:])
    assert h.hexdigest() == whole


def test_duplicate_column_groups_grouping():
    df = pd.DataFrame({"a": [1.0, 2.0], "b": [1.0, 2.0], "c": [9.0, 8.0]})
    fps = {c: column_fingerprint(df[c]) for c in df.columns}
    groups = duplicate_column_groups(fps)
    assert groups == [["a", "b"]]
    assert duplicate_column_groups(
        {c: column_fingerprint(df[c]) for c in ["c"]}) == []


def test_record_duplicates_counts_and_empty():
    df = pd.DataFrame({"a": [1, 1, 2, 1], "b": ["x", "x", "y", "x"]})
    out = record_duplicates(df)
    assert out["duplicate_rows"] == 2
    assert out["rows_in_duplicate_groups"] == 3  # the unique (2, y) row
    assert out["duplicate_row_groups"] == 1
    assert out["rows"] == 4

    empty = record_duplicates(pd.DataFrame({"a": []}))
    assert empty["duplicate_rows"] == 0
    assert any("empty" in n for n in empty["notes"])


# ----------------------------------------------------------------- #
# Quality accumulator parity with in-memory analysis
# ----------------------------------------------------------------- #
def test_quality_accumulator_matches_analyze_quality():
    df = pd.DataFrame({"a": [1.0, 1.0, 2.0, 3.0, 1.0, 5.0],
                       "b": ["x", "x", "y", "z", "x", "z"],
                       "c": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]})
    acc = QualityAccumulator(list(df.columns))
    acc.update(df.iloc[:3])
    acc.update(df.iloc[3:])
    unique_info = {c: int(df[c].nunique(dropna=True)) for c in df.columns}
    streamed = acc.finalize(unique_info=unique_info, completeness_pct=None,
                            missing_cells=0, total_cells=0, k=1.5)
    exact = analyze_quality(df, k=1.5)
    assert streamed["score"] == exact["score"]
    assert streamed["completeness_pct"] == exact["completeness_pct"]
    assert streamed["missing_cells"] == exact["missing_cells"]
    assert streamed["total_cells"] == exact["total_cells"]
    assert streamed["duplicate_rows"] == exact["duplicate_rows"]
    assert streamed["constant_columns"] == exact["constant_columns"]
    assert streamed["duplicate_columns"] == exact["duplicate_columns"]


def test_quality_accumulator_empty_frame():
    acc = QualityAccumulator(["a", "b"])
    out = acc.finalize(unique_info={}, completeness_pct=None,
                       missing_cells=0, total_cells=0)
    assert out["score"] == 0
    assert any("empty" in w for w in out["warnings"])


# ----------------------------------------------------------------- #
# Profiling
# ----------------------------------------------------------------- #
def test_classify_column_and_classify_value():
    assert classify_column(pd.Series([True, False])) == "boolean"
    assert classify_column(pd.Series([1, 2, 3])) == "numeric"
    assert classify_column(pd.Series(["a", "b"])) == "categorical"
    assert classify_column(
        pd.Series(pd.to_datetime(["2026-01-01", "2026-01-02"]))) == "datetime"
    assert classify_value(True) == "bool"
    assert classify_value(3) == "int"
    assert classify_value(2.5) == "float"
    assert classify_value("s") == "str"


def test_profile_dataset_shape_kinds_and_bounded_samples():
    df = pd.DataFrame({"n": [1.0, 2.0, 3.0, 1.0],
                       "t": ["a", "b", "c", "a"],
                       "d": pd.to_datetime(["2026-01-01", "2026-01-02",
                                            "2026-01-03", "2026-01-01"])})
    p = profile_dataset(df, sample_rows=2)
    assert p["rows"] == 4 and p["columns"] == 3
    assert p["column_kinds"]["numeric"] == ["n"]
    assert p["column_kinds"]["categorical"] == ["t"]
    assert p["column_kinds"]["datetime"] == ["d"]
    assert len(p["sample_records"]) == 2
    assert p["memory_bytes_total"] > 0
    assert p["duplicate_rows"] == 1  # rows 0 and 3 are identical
    assert p["column_detail"]["n"]["kind"] == "numeric"
    json.dumps(p)  # must be JSON-serializable as-is


def test_profile_dataset_empty_frame_warns():
    p = profile_dataset(pd.DataFrame({"a": []}))
    assert p["rows"] == 0
    assert "empty" in p["warning"]
    assert p["sample_records"] == []
    json.dumps(p)


def test_profile_dataset_json_safe_with_nan():
    df = pd.DataFrame({"a": [1.0, np.nan, np.inf]})
    p = profile_dataset(df)
    text = json.dumps(p, allow_nan=False)
    assert "NaN" not in text
