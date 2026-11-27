"""Numerical statistics (Mode B, Phase 5).

Two layers with one result shape:

* ``numerical_stats(series)`` - exact statistics for an in-memory Series.
* ``NumericAccumulator`` - memory-bounded streaming aggregation (chunked
  CSV/SQLite reads) that finalizes into the same ``NumericalStats``.

Conventions (documented, fixed):
* Missing values (NaN) are excluded from every calculation; counted in
  ``missing``.
* Non-finite values (+/-inf) are excluded from every calculation; counted in
  ``excluded_nonfinite``.
* Variance/std are SAMPLE statistics (ddof=1); undefined for n < 2.
* Quartiles use pandas' default linear interpolation between order statistics.
* ``mode`` is only reported where meaningful: omitted when every value is
  unique or when a column has more than ``mode_max_unique`` distinct values.
  Ties resolve to the smallest value (deterministic).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

log = logging.getLogger("da.analysis.statistics")

MODE_MAX_UNIQUE = 10_000
DEFAULT_RESERVOIR = 1_000_000


@dataclass
class NumericalStats:
    column: str
    count: int = 0            # non-finite, non-missing values used
    missing: int = 0
    excluded_nonfinite: int = 0
    unique: int | None = None
    mean: float | None = None
    median: float | None = None
    mode: float | None = None
    mode_note: str = ""
    min: float | None = None
    max: float | None = None
    range: float | None = None
    std: float | None = None
    variance: float | None = None
    q1: float | None = None
    q2: float | None = None
    q3: float | None = None
    iqr: float | None = None
    basis: str = "exact"       # exact | reservoir_sample | sample
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "missing": self.missing,
            "excluded_nonfinite": self.excluded_nonfinite,
            "unique": self.unique,
            "mean": _r(self.mean),
            "median": _r(self.median),
            "mode": _r(self.mode),
            "mode_note": self.mode_note,
            "min": _r(self.min),
            "max": _r(self.max),
            "range": _r(self.range),
            "std": _r(self.std),
            "variance": _r(self.variance),
            "q1": _r(self.q1),
            "q2": _r(self.q2),
            "q3": _r(self.q3),
            "iqr": _r(self.iqr),
            "basis": self.basis,
            "notes": list(self.notes),
        }


def _r(value, digits: int = 10) -> float | None:
    """Round for stable, reproducible JSON; NaN/inf -> None."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, digits)


def _mode_of(values: pd.Series, unique: int) -> tuple[float | None, str]:
    if values.empty:
        return None, "no values"
    if unique >= len(values):
        return None, "no repeated values"
    if unique > MODE_MAX_UNIQUE:
        return None, f"more than {MODE_MAX_UNIQUE} unique values"
    counts = values.value_counts()
    top = counts.max()
    tied = sorted(counts[counts == top].index.tolist())
    try:
        return float(tied[0]), ""
    except (TypeError, ValueError):
        return None, "mode not representable as a number"


def numerical_stats(series: pd.Series, column: str | None = None,
                    *, basis: str = "exact") -> NumericalStats:
    """Exact statistics for an in-memory numeric Series.

    ``missing`` = NaN after numeric coercion (original NaN plus any
    non-numeric text that could not be coerced - the latter is also noted).
    ``excluded_nonfinite`` = +/-inf values. Both are excluded from every
    calculation.
    """
    name = str(column if column is not None else series.name)
    out = NumericalStats(column=name, basis=basis)
    numeric = pd.to_numeric(series, errors="coerce")
    arr = numeric.to_numpy(dtype="float64", na_value=np.nan)
    out.excluded_nonfinite = int(np.isinf(arr).sum())
    out.missing = int(numeric.isna().sum())
    non_numeric = out.missing - int(series.isna().sum())
    if non_numeric > 0:
        out.notes.append(f"{non_numeric} non-numeric value(s) excluded")

    values = numeric.replace([np.inf, -np.inf], np.nan).dropna()
    out.count = int(len(values))
    if out.count == 0:
        out.notes.append("no usable numeric values")
        return out
    out.unique = int(values.nunique())
    out.mean = _r(values.mean())
    out.median = _r(values.median())
    out.min = _r(values.min())
    out.max = _r(values.max())
    out.range = _r(out.max - out.min) if out.max is not None else None
    if out.count >= 2:
        out.variance = _r(values.var(ddof=1))
        out.std = _r(values.std(ddof=1))
    else:
        out.notes.append("variance/std need at least 2 values")
    q1, q2, q3 = (values.quantile(0.25), values.quantile(0.5),
                  values.quantile(0.75))
    out.q1, out.q2, out.q3 = _r(q1), _r(q2), _r(q3)
    out.iqr = _r(out.q3 - out.q1) if out.q1 is not None else None
    mode, note = _mode_of(values, out.unique or 0)
    out.mode, out.mode_note = _r(mode), note
    if out.excluded_nonfinite:
        out.notes.append(
            f"{out.excluded_nonfinite} non-finite value(s) excluded")
    return out


# ---------------------------------------------------------------------- #
# Streaming accumulation
# ---------------------------------------------------------------------- #
class NumericAccumulator:
    """Memory-bounded chunked aggregation for one numeric column.

    Exact: count, missing, nonfinite, min, max, mean, variance/std (Chan et
    al. parallel merge of per-chunk moments - stable). Quantile-basis values
    are kept up to ``max_values`` (exact); beyond that a seeded reservoir
    sample is kept and the result is flagged ``basis="reservoir_sample"``.
    ``quantiles`` from a reservoir are estimates; ``mean/std/min/max`` stay
    exact regardless of the reservoir cap.
    """

    def __init__(self, column: str, max_values: int = DEFAULT_RESERVOIR,
                 seed: int = 42, distinct_cap: int = 10_000):
        self.column = str(column)
        self.max_values = int(max_values)
        self.distinct_cap = int(distinct_cap)
        self.rng = np.random.default_rng(seed)
        self.count = 0
        self.missing = 0
        self.excluded_nonfinite = 0
        self._mean = 0.0
        self._m2 = 0.0
        self._min = math.inf
        self._max = -math.inf
        self.values: list[float] = []
        self.capped = False
        self._seen_finite = 0
        self.unique_exact = True
        self._distinct: set[float] = set()

    def update(self, series: pd.Series) -> None:
        arr = pd.to_numeric(series, errors="coerce").to_numpy(
            dtype="float64", na_value=np.nan)
        self.missing += int(np.isnan(arr).sum())
        nonfinite = int(np.isinf(arr).sum())
        self.excluded_nonfinite += nonfinite
        finite = arr[np.isfinite(arr)]
        if finite.size:
            self._merge_moments(finite)
            lo, hi = float(finite.min()), float(finite.max())
            self._min = min(self._min, lo)
            self._max = max(self._max, hi)
            self._reservoir(finite)
            if self.unique_exact:
                self._distinct.update(finite.tolist())
                if len(self._distinct) > self.distinct_cap:
                    self.unique_exact = False
                    self._distinct.clear()

    def _merge_moments(self, chunk: np.ndarray) -> None:
        n_b = int(chunk.size)
        mean_b = float(chunk.mean())
        m2_b = float(((chunk - mean_b) ** 2).sum())
        n = self.count
        n_new = n + n_b
        if n:
            delta = mean_b - self._mean
            self._m2 += m2_b + delta * delta * n * n_b / n_new
            self._mean += delta * n_b / n_new
        else:
            self._m2 = m2_b
            self._mean = mean_b
        self.count = n_new

    def _reservoir(self, finite: np.ndarray) -> None:
        for v in finite.tolist():
            self._seen_finite += 1
            if len(self.values) < self.max_values:
                self.values.append(v)
            else:
                j = int(self.rng.integers(0, self._seen_finite))
                if j < self.max_values:
                    self.values[j] = v
                    self.capped = True

    @property
    def basis(self) -> str:
        return "reservoir_sample" if self.capped else "exact"

    def finalize(self) -> NumericalStats:
        out = NumericalStats(column=self.column,
                             basis="exact" if not self.capped
                             else "reservoir_sample")
        out.count = self.count
        out.missing = self.missing
        out.excluded_nonfinite = self.excluded_nonfinite
        if self.count == 0:
            out.notes.append("no usable numeric values")
            return out
        out.min = _r(self._min)
        out.max = _r(self._max)
        out.range = _r(self._max - self._min)
        out.mean = _r(self._mean)
        if self.count >= 2:
            out.variance = _r(self._m2 / (self.count - 1))
            out.std = _r(math.sqrt(self._m2 / (self.count - 1)))
        else:
            out.notes.append("variance/std need at least 2 values")

        sample = pd.Series(self.values, dtype="float64")
        if self.unique_exact:
            out.unique = len(self._distinct)
        else:
            out.unique = None
            out.notes.append(
                f"unique count not tracked past {self.distinct_cap} values")
        if self.values:
            out.q1 = _r(sample.quantile(0.25))
            out.q2 = _r(sample.quantile(0.5))
            out.q3 = _r(sample.quantile(0.75))
            out.iqr = _r((out.q3 or 0) - (out.q1 or 0))
            out.median = out.q2
            if self.capped:
                out.notes.append(
                    "quantiles/median/mode computed from a reservoir sample "
                    f"({len(self.values)} values kept)")
                out.mode = None
                out.mode_note = "mode skipped on reservoir basis"
            else:
                mode, note = _mode_of(sample, out.unique or 0)
                out.mode = _r(mode)
                out.mode_note = note
        return out
