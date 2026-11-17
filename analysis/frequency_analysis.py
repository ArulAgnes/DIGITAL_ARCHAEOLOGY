"""Categorical / frequency analysis (Mode B, Phase 5).

Definitions (documented, fixed):
* ``missing`` = NaN only. Explicit strings such as "N/A" or "unknown" are
  regular category values - they are never silently converted.
* ``rare category`` = count <= ``rare_max_count`` OR percentage below
  ``rare_pct_threshold`` (percentage of non-missing values).
* ``high cardinality`` = unique count >= ``high_cardinality_unique``
  (configurable per run).
* Percentages are of non-missing values (denominator stated in the result).
* Top lists are truncated to ``top`` entries; truncation is flagged.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

log = logging.getLogger("da.analysis.frequency")

DEFAULT_TOP = 50
DEFAULT_RARE_PCT = 0.5
DEFAULT_RARE_MAX_COUNT = 5
DEFAULT_HIGH_CARDINALITY_UNIQUE = 1_000
DEFAULT_DISTINCT_CAP = 50_000
RARE_LIST_LIMIT = 1_000


@dataclass
class CategoryColumnStats:
    column: str
    missing: int = 0
    missing_pct: float = 0.0
    non_missing: int = 0
    unique: int | None = None
    most_frequent: dict | None = None
    top_categories: list[dict] = field(default_factory=list)
    rare_categories: list[dict] = field(default_factory=list)
    rare_truncated: bool = False
    high_cardinality: bool = False
    constant: bool = False
    basis: str = "exact"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "missing": self.missing,
            "missing_pct": round(self.missing_pct, 4),
            "non_missing": self.non_missing,
            "unique": self.unique,
            "most_frequent": self.most_frequent,
            "top_categories": self.top_categories,
            "rare_categories": self.rare_categories,
            "rare_truncated": self.rare_truncated,
            "high_cardinality": self.high_cardinality,
            "constant": self.constant,
            "basis": self.basis,
            "notes": list(self.notes),
        }


def _json_value(v) -> str:
    if isinstance(v, float) and math.isnan(v):
        return "<NaN>"
    if isinstance(v, (bool, np.bool_)):
        return "true" if bool(v) else "false"
    return str(v)[:300]


def categorical_stats(series: pd.Series, column: str | None = None, *,
                      top: int = DEFAULT_TOP,
                      rare_pct_threshold: float = DEFAULT_RARE_PCT,
                      rare_max_count: int = DEFAULT_RARE_MAX_COUNT,
                      high_cardinality_unique: int
                      = DEFAULT_HIGH_CARDINALITY_UNIQUE) -> CategoryColumnStats:
    """Exact frequency analysis for an in-memory Series."""
    name = str(column if column is not None else series.name)
    out = CategoryColumnStats(column=name)
    total = len(series)
    out.missing = int(series.isna().sum())
    non_missing = total - out.missing
    out.non_missing = non_missing
    out.missing_pct = (100.0 * out.missing / total) if total else 0.0
    if non_missing == 0:
        out.notes.append("no non-missing values")
        out.unique = 0
        return out

    counts = series.dropna().value_counts()
    out.unique = int(len(counts))
    out.high_cardinality = out.unique >= high_cardinality_unique
    out.constant = out.unique <= 1

    rare = [
        {"value": _json_value(v), "count": int(c),
         "pct": round(100.0 * int(c) / non_missing, 4)}
        for v, c in counts.items()
        if int(c) <= rare_max_count
        or (100.0 * int(c) / non_missing) < rare_pct_threshold
    ]
    rare.sort(key=lambda d: (d["count"], d["value"]))
    if len(rare) > RARE_LIST_LIMIT:
        out.rare_truncated = True
        rare = rare[:RARE_LIST_LIMIT]
    out.rare_categories = rare

    tops = [
        {"value": _json_value(v), "count": int(c),
         "pct": round(100.0 * int(c) / non_missing, 4)}
        for v, c in counts.head(int(top)).items()
    ]
    out.top_categories = tops
    if len(counts) > int(top):
        out.notes.append(
            f"top {top} of {len(counts)} categories shown")
    if tops:
        out.most_frequent = tops[0]
    return out


# ---------------------------------------------------------------------- #
# Streaming accumulation
# ---------------------------------------------------------------------- #
class ValueCountAccumulator:
    """Chunked value counts for one non-numeric column.

    Exact counts while the distinct count stays within ``max_distinct``;
    beyond that a seeded reservoir sample of values is kept and the result
    is flagged ``basis="reservoir_sample"`` (frequencies become estimates).
    ``missing`` (NaN) is always exact.
    """

    def __init__(self, column: str, max_distinct: int = DEFAULT_DISTINCT_CAP,
                 seed: int = 42):
        self.column = str(column)
        self.max_distinct = int(max_distinct)
        self.rng = np.random.default_rng(seed)
        self.missing = 0
        self.total = 0
        self.counts: dict[tuple[str, object], int] = {}
        self.capped = False
        self.sample: list = []
        self._seen_non_missing = 0

    def update(self, series: pd.Series) -> None:
        self.total += len(series)
        mask = series.isna()
        self.missing += int(mask.sum())
        present = series[~mask]
        if self.capped:
            for v in present.tolist():
                self._reservoir_value(v)
            return
        for v in present.tolist():
            key = (type(v).__name__, v)
            self.counts[key] = self.counts.get(key, 0) + 1
        if len(self.counts) > self.max_distinct:
            self._go_capped()

    def _go_capped(self) -> None:
        """Distinct cap exceeded: switch to a reservoir seeded with every
        value seen so far (exact up to this point), then keep sampling."""
        seed_values: list = []
        for (_t, v), c in self.counts.items():
            seed_values.extend([v] * c)
        self.capped = True
        self.counts.clear()
        self.sample = []
        self._seen_non_missing = 0
        for v in seed_values:
            self._reservoir_value(v)

    def _reservoir_value(self, v) -> None:
        self._seen_non_missing += 1
        if len(self.sample) < self.max_distinct:
            self.sample.append(v)
        else:
            j = int(self.rng.integers(0, self._seen_non_missing))
            if j < self.max_distinct:
                self.sample[j] = v

    @property
    def basis(self) -> str:
        return "reservoir_sample" if self.capped else "exact"

    @property
    def distinct_exact(self) -> bool:
        return not self.capped

    def finalize(self, *, top: int = DEFAULT_TOP,
                 rare_pct_threshold: float = DEFAULT_RARE_PCT,
                 rare_max_count: int = DEFAULT_RARE_MAX_COUNT,
                 high_cardinality_unique: int
                 = DEFAULT_HIGH_CARDINALITY_UNIQUE) -> CategoryColumnStats:
        out = CategoryColumnStats(column=self.column, basis=self.basis)
        out.missing = self.missing
        total = self.total
        out.missing_pct = (100.0 * self.missing / total) if total else 0.0
        out.non_missing = total - self.missing
        if self.capped:
            vc = pd.Series(self.sample, dtype="object").value_counts()
            pairs = [(v, int(c)) for v, c in vc.items()]
            pairs.sort(key=lambda kv: (-kv[1], _json_value(kv[0])))
            out.unique = None
            out.notes.append(
                "frequencies estimated from a reservoir sample "
                f"({len(self.sample)} values kept; distinct cap "
                f"{self.max_distinct} exceeded)")
        else:
            ordered = sorted(self.counts.items(),
                             key=lambda kv: (-kv[1], str(kv[0][1])))
            pairs = [(kv[1], int(c)) for kv, c in ordered]
            out.unique = len(pairs)
        if out.non_missing <= 0:
            out.notes.append("no non-missing values")
            out.unique = out.unique or 0
            return out
        out.high_cardinality = (
            out.unique is not None and out.unique >= high_cardinality_unique)
        out.constant = out.unique is not None and out.unique <= 1

        entries = [
            {"value": _json_value(v), "count": c,
             "pct": round(100.0 * c / out.non_missing, 4)}
            for v, c in pairs
        ]
        rare = [e for e in entries
                if e["count"] <= rare_max_count
                or e["pct"] < rare_pct_threshold]
        rare.sort(key=lambda d: (d["count"], d["value"]))
        if len(rare) > RARE_LIST_LIMIT:
            out.rare_truncated = True
            rare = rare[:RARE_LIST_LIMIT]
        out.rare_categories = rare
        out.top_categories = entries[: int(top)]
        if len(entries) > int(top):
            out.notes.append(f"top {top} of {len(entries)} categories shown")
        if out.top_categories:
            out.most_frequent = out.top_categories[0]
        return out
