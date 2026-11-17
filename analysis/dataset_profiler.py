"""Dataset profiling foundation (Phase 3): per-column statistics for any
tabular DataFrame, independent of the Digital Archaeology research data."""
from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger("da.analysis.profile")


def _profile_column(series: pd.Series) -> dict:
    profile: dict = {
        "dtype": str(series.dtype),
        "missing": int(series.isna().sum()),
        "missing_pct": round(float(series.isna().mean()) * 100, 2),
        "unique": None,
    }
    try:
        profile["unique"] = int(series.nunique(dropna=True))
    except TypeError:
        pass

    if pd.api.types.is_numeric_dtype(series) and series.notna().any():
        desc = series.describe()
        profile.update({
            "min": _num(desc.get("min")),
            "max": _num(desc.get("max")),
            "mean": _num(desc.get("mean")),
            "median": _num(series.median()),
            "std": _num(desc.get("std")),
            "zeros": int((series == 0).sum()),
            "negative": int((series < 0).sum()),
        })
    elif pd.api.types.is_datetime64_any_dtype(series) and series.notna().any():
        profile.update({
            "min": str(series.min()),
            "max": str(series.max()),
        })
    else:
        try:
            top = series.astype(str).value_counts().head(5)
            profile["top_values"] = [
                {"value": str(v)[:200], "count": int(c)}
                for v, c in top.items()
            ]
        except (TypeError, ValueError):
            pass
    return profile


def _num(value) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def profile_dataframe(df: pd.DataFrame) -> dict:
    """Structured profile: shape, per-column stats, duplicates."""
    profile: dict = {
        "rows": int(len(df)),
        "columns": int(len(df.columns)),
        "column_profiles": {},
        "duplicate_rows": int(df.duplicated().sum()),
        "generated_by": "dataset_profiler",
    }
    if df.empty:
        profile["warning"] = "dataset is empty"
        return profile
    for name in df.columns:
        profile["column_profiles"][str(name)] = _profile_column(df[name])
    log.info("Profiled %d rows x %d cols", profile["rows"],
             profile["columns"])
    return profile


def profile_sample(df: pd.DataFrame, max_rows: int = 10_000) -> dict:
    """Profile at most *max_rows* - explicit, bounded-memory variant."""
    if len(df) > max_rows:
        sampled = df.head(max_rows)
        profile = profile_dataframe(sampled)
        profile["sampled_rows"] = max_rows
        profile["total_rows"] = int(len(df))
        return profile
    return profile_dataframe(df)


# ---------------------------------------------------------------------- #
# Phase 5: rich dataset profiling (Mode B)
# ---------------------------------------------------------------------- #
def _json_safe(v):
    import math as _math
    if v is None:
        return None
    try:
        if v != v:  # NaN
            return None
    except Exception:  # noqa: BLE001
        pass
    if isinstance(v, (pd.Timestamp,)):
        return str(v)
    if hasattr(v, "isoformat") and not isinstance(v, (str, bytes)):
        try:
            return v.isoformat()
        except Exception:  # noqa: BLE001
            return str(v)
    if isinstance(v, float) and (_math.isnan(v) or _math.isinf(v)):
        return None
    if isinstance(v, (int, float, bool, str)):
        return v
    return str(v)


def classify_column(series: pd.Series) -> str:
    """Column kind: numeric | boolean | datetime | categorical | other."""
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_numeric_dtype(series):
        return "numeric"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    if (pd.api.types.is_object_dtype(series)
            or pd.api.types.is_string_dtype(series)
            or isinstance(series.dtype, pd.CategoricalDtype)):
        return "categorical"
    return "other"


def profile_dataset(df: pd.DataFrame, sample_rows: int = 5) -> dict:
    """Mode B profile: shape, memory, column kinds, per-column detail and
    a bounded set of sample records (never more than *sample_rows*)."""
    profile: dict = {
        "rows": int(len(df)),
        "columns": int(df.shape[1]),
        "column_names": [str(c) for c in df.columns],
        "generated_by": "dataset_profiler.profile_dataset",
        "memory_bytes_total": int(df.memory_usage(deep=True).sum()),
        "column_kinds": {"numeric": [], "boolean": [], "datetime": [],
                         "categorical": [], "other": []},
        "column_detail": {},
        "sample_records": [],
        "duplicate_rows": int(df.duplicated().sum()) if not df.empty else 0,
    }
    if df.empty:
        profile["warning"] = "dataset is empty"
        return profile

    per_col_bytes = {
        str(c): int(df[c].memory_usage(deep=True)) for c in df.columns}
    profile["memory_bytes_per_column"] = per_col_bytes

    for name in df.columns:
        s = df[name]
        kind = classify_column(s)
        profile["column_kinds"][kind].append(str(name))
        try:
            unique = int(s.nunique(dropna=True))
        except TypeError:
            unique = None
        detail = {
            "dtype": str(s.dtype),
            "kind": kind,
            "missing": int(s.isna().sum()),
            "missing_pct": round(float(s.isna().mean()) * 100, 4),
            "unique": unique,
            "memory_bytes": per_col_bytes[str(name)],
        }
        if kind == "numeric" and not pd.api.types.is_bool_dtype(s):
            finite = pd.to_numeric(s, errors="coerce").replace(
                [float("inf"), float("-inf")], float("nan")).dropna()
            if len(finite):
                detail["min"] = _num(finite.min())
                detail["max"] = _num(finite.max())
        elif kind == "categorical":
            try:
                top = s.astype(str).value_counts().head(3)
                detail["top_values"] = [
                    {"value": str(v)[:200], "count": int(c)}
                    for v, c in top.items()]
            except (TypeError, ValueError):
                pass
        elif kind == "datetime" and s.notna().any():
            detail["min"] = str(s.min())
            detail["max"] = str(s.max())
        profile["column_detail"][str(name)] = detail

    sample = df.head(max(int(sample_rows), 0))
    profile["sample_records"] = [
        {str(k): _json_safe(v) for k, v in row.items()}
        for row in sample.to_dict(orient="records")]
    return profile
