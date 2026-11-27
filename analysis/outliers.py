"""Outlier analysis (Mode B, Phase 5) - IQR (Tukey) method.

Conventions (documented):
* Fences: ``lower = q1 - k * IQR``, ``upper = q3 + k * IQR`` with
  ``k = 1.5`` by default; quartiles use pandas' linear interpolation.
* Only finite values are used; NaN and +/-inf are excluded and counted.
* An "outlier" is a value strictly below the lower fence or strictly above
  the upper fence. This is a STATISTICAL flag, not an error claim.
* The same fences are used for the count and the percentage; the
  denominator (``used``) is stated in every result.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger("da.analysis.outliers")

DEFAULT_K = 1.5
MIN_VALUES = 4


def iqr_fences(q1: float, q3: float, k: float = DEFAULT_K
               ) -> tuple[float, float]:
    iqr = float(q3) - float(q1)
    return float(q1) - k * iqr, float(q3) + k * iqr


def outlier_summary(values: pd.Series, k: float = DEFAULT_K,
                    basis: str = "exact",
                    column: str | None = None) -> dict:
    """Outlier summary for one numeric Series."""
    name = str(column if column is not None else values.name)
    coerced = pd.to_numeric(values, errors="coerce")
    arr = coerced.to_numpy(dtype="float64", na_value=np.nan)
    excluded_nonfinite = int(np.isinf(arr).sum())
    excluded_missing = int(np.isnan(arr).sum())
    finite = arr[np.isfinite(arr)]
    out: dict = {
        "column": name,
        "k": float(k),
        "basis": basis,
        "used": int(finite.size),
        "excluded_missing": max(excluded_missing, 0),
        "excluded_nonfinite": excluded_nonfinite,
        "count": 0,
        "pct": 0.0,
        "lower_fence": None,
        "upper_fence": None,
        "lower_count": 0,
        "upper_count": 0,
        "notes": [],
    }
    if finite.size == 0:
        out["notes"].append("no finite values")
        return out
    if finite.size < MIN_VALUES:
        out["notes"].append(
            f"fewer than {MIN_VALUES} values - fences still computed, "
            "treat with care")
    series = pd.Series(finite)
    q1 = float(series.quantile(0.25))
    q3 = float(series.quantile(0.75))
    lo, hi = iqr_fences(q1, q3, k)
    lower = int((series < lo).sum())
    upper = int((series > hi).sum())
    out.update({
        "q1": round(q1, 10),
        "q3": round(q3, 10),
        "lower_fence": round(lo, 10),
        "upper_fence": round(hi, 10),
        "lower_count": lower,
        "upper_count": upper,
        "count": lower + upper,
        "pct": round(100.0 * (lower + upper) / finite.size, 4),
    })
    return out


def outlier_report(df: pd.DataFrame, k: float = DEFAULT_K,
                   basis: str = "exact",
                   max_columns: int = 100) -> dict:
    """Per-column IQR outlier summaries for numeric columns of *df*."""
    out: dict = {"k": float(k), "basis": basis, "columns": {}, "notes": []}
    if df.empty:
        out["notes"].append("dataset is empty")
        return out
    shown = 0
    for c in df.columns:
        s = df[c]
        if not pd.api.types.is_numeric_dtype(s) \
                or pd.api.types.is_bool_dtype(s):
            continue
        if shown >= max_columns:
            out["notes"].append(
                f"column limit ({max_columns}) - remaining columns skipped")
            break
        out["columns"][str(c)] = outlier_summary(s, k=k, basis=basis,
                                                  column=str(c))
        shown += 1
    if not out["columns"]:
        out["notes"].append("no numeric columns")
    return out
