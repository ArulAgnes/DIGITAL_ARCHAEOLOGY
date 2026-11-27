"""Distribution analysis (Mode B, Phase 5).

All functions state which records they exclude:

* Histograms use FINITE values only; NaN and +/-inf are excluded and
  counted separately.
* Frequency distributions operate on non-missing values (NaN excluded and
  counted).
* Grouped statistics drop rows whose group key is NaN (count reported);
  per-cell ``count`` is the number of non-missing values of that column in
  that group (pandas groupby semantics).
"""
from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd

log = logging.getLogger("da.analysis.distributions")

DEFAULT_BINS = 20
GROUPED_STATS = ("count", "mean", "median", "min", "max", "std")
DEFAULT_MAX_GROUPS = 100


def numerical_histogram(series: pd.Series, bins: int = DEFAULT_BINS,
                        column: str | None = None) -> dict:
    """Histogram of finite values (linear bins, numpy semantics).

    When all values are identical, numpy widens the window by +/-0.5 so a
    histogram is still produced (noted).
    """
    name = str(column if column is not None else series.name)
    coerced = pd.to_numeric(series, errors="coerce")
    arr = coerced.to_numpy(dtype="float64", na_value=np.nan)
    excluded_nonfinite = int(np.isinf(arr).sum())
    excluded_missing = int(np.isnan(arr).sum())
    finite = arr[np.isfinite(arr)]
    out: dict = {
        "column": name,
        "bins": int(bins),
        "excluded_missing": max(excluded_missing, 0),
        "excluded_nonfinite": excluded_nonfinite,
        "used": int(finite.size),
        "notes": [],
    }
    if finite.size == 0:
        out["notes"].append("no finite values")
        out["edges"] = []
        out["counts"] = []
        return out
    lo, hi = float(finite.min()), float(finite.max())
    widened = False
    if lo == hi:
        widened = True
        out["notes"].append("all values identical - window widened by +/-0.5")
    counts, edges = np.histogram(finite, bins=int(bins),
                                 range=None if widened else (lo, hi))
    out["edges"] = [round(float(e), 10) for e in edges]
    out["counts"] = [int(c) for c in counts]
    out["min"] = round(lo, 10)
    out["max"] = round(hi, 10)
    out["basis"] = "exact"
    return out


def frequency_distribution(counts: dict | list, top: int | None = None
                           ) -> list[dict]:
    """Ordered frequency rows ``[{"value","count","pct"}]``.

    *counts* is either ``{value: count}`` or ``[(value, count), ...]``.
    Percentages are of the total count (documented denominator = sum of
    counts passed in).
    """
    pairs = (list(counts.items()) if isinstance(counts, dict)
             else list(counts))
    total = sum(int(c) for _v, c in pairs) or 1
    pairs = sorted(pairs, key=lambda kv: (-int(kv[1]), str(kv[0])))
    if top is not None:
        pairs = pairs[: int(top)]
    return [
        {"value": str(v)[:300], "count": int(c),
         "pct": round(100.0 * int(c) / total, 4)}
        for v, c in pairs
    ]


def grouped_statistics(df: pd.DataFrame, by: str,
                       numeric_columns: list[str] | None = None,
                       stats: tuple[str, ...] = GROUPED_STATS,
                       max_groups: int = DEFAULT_MAX_GROUPS) -> dict:
    """Per-group statistics for numeric columns.

    Rows with a NaN group key are excluded (counted). When there are more
    than *max_groups* distinct keys, only the largest groups are shown and
    the result is flagged. Values are rounded for stable JSON.
    """
    out: dict = {"group_by": str(by), "groups": {}, "notes": []}
    if df.empty:
        out["notes"].append("dataset is empty")
        return out
    if by not in df.columns:
        out["notes"].append(f"group column '{by}' not found")
        return out

    excluded_group_missing = int(df[by].isna().sum())
    out["excluded_rows_missing_group_key"] = excluded_group_missing
    work = df[df[by].notna()]

    if numeric_columns is None:
        numeric_columns = [
            c for c in work.columns
            if pd.api.types.is_numeric_dtype(work[c])
            and not pd.api.types.is_bool_dtype(work[c])]
    numeric_columns = [c for c in numeric_columns if c in work.columns]
    if not numeric_columns:
        out["notes"].append("no numeric columns to aggregate")
        return out

    sizes = work[by].value_counts()
    out["groups_total"] = int(len(sizes))
    if len(sizes) > max_groups:
        keep = sizes.head(int(max_groups)).index
        out["notes"].append(
            f"showing the largest {max_groups} of {len(sizes)} groups")
        work = work[work[by].isin(keep)]
    out["groups_shown"] = int(out["groups_total"] if len(sizes) <= max_groups
                              else max_groups)

    valid_stats = [s for s in stats if s in GROUPED_STATS]
    grouped = work.groupby(by, dropna=True)[numeric_columns].agg(
        list(valid_stats))
    for group_value, row in grouped.iterrows():
        entry: dict = {}
        for col in numeric_columns:
            col_stats: dict = {}
            for stat in valid_stats:
                try:
                    v = row[(col, stat)]
                except KeyError:
                    continue
                if isinstance(v, (int, np.integer)):
                    col_stats[stat] = int(v)
                elif isinstance(v, float):
                    col_stats[stat] = (None if math.isnan(v)
                                       else round(v, 10))
                elif isinstance(v, (np.floating, np.integer)):
                    col_stats[stat] = round(float(v), 10)
                else:
                    col_stats[stat] = None
            entry[str(col)] = col_stats
        out["groups"][str(group_value)] = entry
    return out
