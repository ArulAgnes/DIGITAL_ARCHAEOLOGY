"""Profiler + data quality foundation tests."""
from __future__ import annotations

import pandas as pd

from analysis.data_quality import assess_quality
from analysis.dataset_profiler import profile_dataframe, profile_sample


def test_profile_dataframe_basic_shape_and_stats():
    df = pd.DataFrame({
        "id": [1, 2, 3, 4],
        "score": [9.5, 8.0, None, 7.0],
        "name": ["Ada", "Grace", "Ada", "Alan"],
    })
    profile = profile_dataframe(df)
    assert profile["rows"] == 4
    assert profile["columns"] == 3
    assert profile["duplicate_rows"] == 0
    score = profile["column_profiles"]["score"]
    assert score["missing"] == 1
    assert score["missing_pct"] == 25.0
    assert score["min"] == 7.0
    assert score["max"] == 9.5
    name = profile["column_profiles"]["name"]
    assert name["unique"] == 3
    assert name["top_values"][0]["value"] == "Ada"
    assert name["top_values"][0]["count"] == 2


def test_profile_dataframe_empty_dataset():
    profile = profile_dataframe(pd.DataFrame())
    assert profile["rows"] == 0
    assert "warning" in profile


def test_profile_sample_bounds_rows():
    df = pd.DataFrame({"x": range(10)})
    profile = profile_sample(df, max_rows=4)
    assert profile["rows"] == 4
    assert profile["sampled_rows"] == 4
    assert profile["total_rows"] == 10
    assert profile_sample(df, max_rows=50)["rows"] == 10


def test_quality_clean_dataset_scores_full():
    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    result = assess_quality(df)
    assert result["completeness_pct"] == 100.0
    assert result["score"] == 100.0
    assert result["warnings"] == []
    assert result["duplicate_rows"] == 0


def test_quality_flags_duplicates_and_missing():
    df = pd.DataFrame({
        "a": [1, 1, 1],
        "b": [None, None, None],
        "c": [1, 1, 3],
    })
    result = assess_quality(df)
    assert result["duplicate_rows"] == 1
    assert any("missing" in w for w in result["warnings"])
    assert any(col["column"] == "b" and col["severity"] == "fail"
               for col in result["columns_high_missing"])
    assert "a" in result["constant_columns"]
    assert result["score"] < 100.0


def test_quality_empty_dataset():
    result = assess_quality(pd.DataFrame())
    assert result["score"] == 0
    assert "dataset is empty" in result["warnings"]
