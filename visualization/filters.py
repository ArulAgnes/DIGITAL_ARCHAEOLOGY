"""Pure, explicit filters for visualizations (Phase 7).

Every function returns a new frame plus a human-readable description of
what was applied. Nothing runs implicitly — the dashboard page only
calls these after *Generate visualization* is pressed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from core.exceptions import PlatformError


@dataclass
class FilterResult:
    """A filtered frame plus a description of every active filter."""

    frame: pd.DataFrame
    applied: list[str] = field(default_factory=list)
    dropped_rows: int = 0

    @property
    def active_summary(self) -> list[str]:
        return list(self.applied)


def parse_dates(series: pd.Series) -> pd.Series:
    """Coerce a column to datetime (errors become NaT)."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    return pd.to_datetime(series, errors="coerce")


def apply_date_range(
    frame: pd.DataFrame,
    column: str,
    start: "np.datetime64 | str | None",
    end: "np.datetime64 | str | None",
) -> FilterResult:
    """Keep rows whose ``column`` falls inside [start, end] (inclusive)."""
    if column not in frame.columns:
        raise PlatformError(
            f"Date column '{column}' is not present in the data.",
            detail="date filter column missing",
        )
    if start is None and end is None:
        return FilterResult(frame)
    parsed = parse_dates(frame[column])
    mask = pd.Series(True, index=frame.index)
    if start is not None:
        mask &= parsed >= pd.to_datetime(start)
    if end is not None:
        mask &= parsed <= pd.to_datetime(end)
    dropped = int((~mask.fillna(False)).sum())
    out = frame[mask.fillna(False)]
    parts = []
    if start is not None:
        parts.append(f"from {pd.to_datetime(start).date()}")
    if end is not None:
        parts.append(f"to {pd.to_datetime(end).date()}")
    return FilterResult(
        out,
        applied=[f"{column}: " + " ".join(parts)],
        dropped_rows=dropped,
    )


def apply_numeric_range(
    frame: pd.DataFrame,
    column: str,
    low: float | None,
    high: float | None,
) -> FilterResult:
    """Keep rows whose ``column`` lies inside [low, high] (inclusive)."""
    if column not in frame.columns:
        raise PlatformError(
            f"Numeric column '{column}' is not present in the data.",
            detail="numeric filter column missing",
        )
    if low is None and high is None:
        return FilterResult(frame)
    if not pd.api.types.is_numeric_dtype(frame[column]):
        raise PlatformError(
            f"Column '{column}' is not numeric; numeric range filters "
            "cannot be applied to it.",
            detail="numeric filter on non-numeric column",
        )
    values = pd.to_numeric(frame[column], errors="coerce")
    mask = pd.Series(True, index=frame.index)
    if low is not None:
        mask &= values >= low
    if high is not None:
        mask &= values <= high
    mask = mask.fillna(False)
    parts = []
    if low is not None:
        parts.append(f">= {low:g}")
    if high is not None:
        parts.append(f"<= {high:g}")
    return FilterResult(
        frame[mask],
        applied=[f"{column}: " + ", ".join(parts)],
        dropped_rows=int((~mask).sum()),
    )


def apply_categories(
    frame: pd.DataFrame, column: str, selected: list[str] | None
) -> FilterResult:
    """Keep rows whose ``column`` value is in ``selected``."""
    if column not in frame.columns:
        raise PlatformError(
            f"Category column '{column}' is not present in the data.",
            detail="category filter column missing",
        )
    if not selected:
        return FilterResult(frame)
    mask = frame[column].astype(str).isin({str(s) for s in selected})
    return FilterResult(
        frame[mask],
        applied=[f"{column}: {len(selected)} value(s) selected"],
        dropped_rows=int((~mask).sum()),
    )


def apply_missing_policy(
    frame: pd.DataFrame, column: str, drop_missing: bool
) -> FilterResult:
    """Optionally drop rows where ``column`` is missing."""
    if column not in frame.columns:
        raise PlatformError(
            f"Column '{column}' is not present in the data.",
            detail="missing-policy column missing",
        )
    if not drop_missing:
        return FilterResult(frame)
    mask = frame[column].notna()
    return FilterResult(
        frame[mask],
        applied=[f"{column}: rows with missing values removed"],
        dropped_rows=int((~mask).sum()),
    )


def top_categories(
    frame: pd.DataFrame,
    column: str,
    n: int,
    other_label: str = "Other (rest)",
) -> tuple[pd.DataFrame, dict]:
    """Return the top ``n`` categories of ``column`` with counts.

    Remainder rows are grouped under ``other_label``; the returned info
    dict always reports ``regrouped`` so charts can disclose it.
    """
    if column not in frame.columns:
        raise PlatformError(
            f"Category column '{column}' is not present in the data.",
            detail="top-n column missing",
        )
    counts = (
        frame[column]
        .astype(str)
        .value_counts(dropna=False)
        .rename_axis(column)
        .reset_index(name="count")
    )
    total_categories = int(len(counts))
    if total_categories <= n:
        return counts, {
            "regrouped": False,
            "total_categories": total_categories,
            "shown": total_categories,
        }
    head = counts.head(n).copy()
    rest_count = int(counts.iloc[n:]["count"].sum())
    head.loc[len(head)] = {column: other_label, "count": rest_count}
    return head, {
        "regrouped": True,
        "total_categories": total_categories,
        "shown": n,
    }


def summarize_filters(
    sidebar_filters: dict | None, section_filters: list[str]
) -> list[str]:
    """Human-readable list of every active filter (sidebar + section)."""
    lines: list[str] = []
    if sidebar_filters:
        for key in sorted(sidebar_filters):
            value = sidebar_filters[key]
            if value is None or value == "" or value == []:
                continue
            lines.append(f"{key}: {value}")
    lines.extend(section_filters)
    return lines


def ensure_finite(frame: pd.DataFrame, columns: list[str]) -> tuple[pd.DataFrame, int]:
    """Replace +-inf with NaN in the given columns; return replaced count."""
    replaced = 0
    out = frame
    for col in columns:
        if col not in out.columns:
            continue
        if not pd.api.types.is_numeric_dtype(out[col]):
            continue
        arr = out[col].to_numpy(dtype="float64", na_value=np.nan)
        inf_mask = np.isinf(arr)
        n = int(inf_mask.sum())
        if n:
            arr = arr.copy()
            arr[inf_mask] = np.nan
            out = out.copy()
            out[col] = arr
            replaced += n
    return out, replaced


# ------------------------------------------------------------- sampling

def sample_frame(
    frame: pd.DataFrame, max_points: int, seed: int
) -> tuple[pd.DataFrame, dict]:
    """Deterministic random sample when a frame exceeds ``max_points``.

    Same seed + same input always yields the same sample. The returned
    disclosure dict records method, parameters and approximate=True.
    """
    n = int(len(frame))
    if n <= max_points:
        return frame, {
            "applied": False,
            "method": "none",
            "params": {},
            "approximate": False,
        }
    rng = np.random.default_rng(seed)
    idx = np.sort(rng.choice(n, size=int(max_points), replace=False))
    sampled = frame.iloc[idx].reset_index(drop=True)
    return sampled, {
        "applied": True,
        "method": "random_sample",
        "params": {"seed": int(seed), "limit": int(max_points),
                   "requested_rows": n},
        "approximate": True,
    }


def sample_array(
    array: np.ndarray, max_points: int, seed: int
) -> tuple[np.ndarray, dict]:
    """Deterministic random sample of a 1-D numpy array."""
    n = int(len(array))
    if n <= max_points:
        return array, {
            "applied": False, "method": "none", "params": {},
            "approximate": False,
        }
    rng = np.random.default_rng(seed)
    idx = np.sort(rng.choice(n, size=int(max_points), replace=False))
    return array[idx], {
        "applied": True,
        "method": "random_sample",
        "params": {"seed": int(seed), "limit": int(max_points),
                   "requested_rows": n},
        "approximate": True,
    }

