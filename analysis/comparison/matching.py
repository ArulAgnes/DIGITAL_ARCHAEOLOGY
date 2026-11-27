"""Row matching for the comparison engine (Phase 6).

Two strategies:

* ``key`` - pair rows by one to eight key columns. Pairs whose other
  columns are equal are *matched*; pairs whose other columns differ are
  *modified*. Rows with a missing key value or a key value that occurs
  more than once on either side cannot be paired honestly and are
  counted separately (never silently dropped, never guessed).
* ``content-multiset`` - fallback when no usable key column exists. Rows
  are compared by exact content, which cannot pair records: a changed
  row therefore appears as one removal plus one addition. The strategy
  is always labelled and warned about.

Row order is never used for matching. Values are canonicalised first so
``1`` and ``1.0`` compare equal and missing matches missing. Keyed
matching keeps one map of dataset A in memory (O(rows_A)); ``max_keys``
bounds that index and overflow downgrades the run to *approximate*.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from analysis.comparison.base import warn
from core.exceptions import PlatformError

_SAMPLE_LIMIT = 3
_CHANGED_FIELD_LIMIT = 30
_VALUE_TEXT_LIMIT = 80
_KEY_TEXT_LIMIT = 60


# ---------------------------------------------------------------------- #
# Value canonicalisation
# ---------------------------------------------------------------------- #
def normalize_value(value):
    """Canonical, hashable form of one cell value (None = missing)."""
    if value is None or value is pd.NaT:
        return None
    if value is pd.NA:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        f = float(value)
        if math.isnan(f):
            return None
        if f.is_integer():
            return int(f)
        return f
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        hash(value)
    except TypeError:
        return str(value)
    return value


def normalize_row(row) -> tuple:
    return tuple(normalize_value(v) for v in row)


def key_positions(columns, keys) -> list[int]:
    """Column indexes of *keys* (raises PlatformError when one is absent)."""
    cols = [str(c) for c in columns]
    missing = [str(k) for k in keys if str(k) not in cols]
    if missing:
        raise PlatformError(
            "Key column(s) not present in this dataset: "
            + ", ".join(missing)
            + ". Choose key columns that exist in both datasets before "
              "starting the comparison.")
    return [cols.index(str(k)) for k in keys]


def make_key(row, positions) -> tuple | None:
    """Canonical key tuple for one row; None when any key value is missing."""
    values = []
    for pos in positions:
        value = normalize_value(row[pos])
        if value is None:
            return None
        values.append(value)
    return tuple(values)


def format_key(key) -> str:
    if key is None:
        return ""
    parts = []
    for value in key:
        text = "(missing)" if value is None else str(value)
        if len(text) > _KEY_TEXT_LIMIT:
            text = text[:_KEY_TEXT_LIMIT - 3] + "..."
        parts.append(text)
    return " | ".join(parts)


def _value_text(value) -> str:
    if value is None:
        return "(missing)"
    text = str(value)
    if len(text) > _VALUE_TEXT_LIMIT:
        text = text[:_VALUE_TEXT_LIMIT - 3] + "..."
    return text


# ---------------------------------------------------------------------- #
# Outcome
# ---------------------------------------------------------------------- #
class DiffOutcome:
    """Result of one record comparison (CSV / SQLite rows)."""

    def __init__(self, *, strategy: str, exactness: str, keys: list[str],
                 columns: list[str], counts: dict, warnings: list,
                 rows_added: list, rows_removed: list, rows_modified: list,
                 totals: dict, summary_extra: dict | None = None):
        self.strategy = strategy
        self.exactness = exactness
        self.keys = list(keys)
        self.columns = list(columns)
        self.counts = dict(counts)
        self.warnings = list(warnings)
        self.rows_added = rows_added
        self.rows_removed = rows_removed
        self.rows_modified = rows_modified
        self.totals = dict(totals)
        self.summary_extra = dict(summary_extra or {})

    @property
    def exported(self) -> dict:
        return {"added": len(self.rows_added),
                "removed": len(self.rows_removed),
                "modified": len(self.rows_modified)}

    def record_tables(self) -> list[dict]:
        """Bounded export tables (headers always written, totals exact)."""
        return [
            {"file": "records_added.csv", "columns": ["key"] + self.columns,
             "rows": self.rows_added, "total": self.totals.get("added", 0)},
            {"file": "records_removed.csv", "columns": ["key"] + self.columns,
             "rows": self.rows_removed,
             "total": self.totals.get("removed", 0)},
            {"file": "records_modified.csv",
             "columns": ["key", "changed_fields"] + self.columns,
             "rows": self.rows_modified,
             "total": self.totals.get("modified", 0)},
        ]


# ---------------------------------------------------------------------- #
# Shared helpers
# ---------------------------------------------------------------------- #
def _union_columns(columns_a, columns_b) -> list[str]:
    union = [str(c) for c in columns_a]
    seen = set(union)
    for col in columns_b:
        col = str(col)
        if col not in seen:
            seen.add(col)
            union.append(col)
    return union


def make_matcher(columns_a, columns_b, keys, cfg):
    """Pick the strategy: keyed pairing when keys are usable, else content."""
    if keys:
        return KeyedMatcher(columns_a, columns_b, keys, cfg)
    return ContentMatcher(columns_a, columns_b, keys, cfg)


# ---------------------------------------------------------------------- #
# Keyed matching
# ---------------------------------------------------------------------- #
class KeyedMatcher:
    strategy = "key"
    needs_count_pass = True

    def __init__(self, columns_a, columns_b, keys, cfg):
        self.columns_a = [str(c) for c in columns_a]
        self.columns_b = [str(c) for c in columns_b]
        self.keys = [str(k) for k in keys]
        self.cfg = cfg
        self.key_pos_a = key_positions(self.columns_a, self.keys)
        self.key_pos_b = key_positions(self.columns_b, self.keys)

        self.union_columns = _union_columns(self.columns_a, self.columns_b)
        set_a, set_b = set(self.columns_a), set(self.columns_b)
        self._pos_a = [self.columns_a.index(c) if c in set_a else -1
                       for c in self.union_columns]
        self._pos_b = [self.columns_b.index(c) if c in set_b else -1
                       for c in self.union_columns]

        set_keys = set(self.keys)
        self.common = [c for c in self.columns_a if c in set_b]
        self.nonkey_common = [c for c in self.common if c not in set_keys]
        self._changed_pairs = [
            (c, self.columns_a.index(c), self.columns_b.index(c))
            for c in self.nonkey_common]

        self.counts = {
            "a_rows": 0, "b_rows": 0, "matched": 0, "modified": 0,
            "added": 0, "removed": 0, "duplicate_key_rows_a": 0,
            "duplicate_key_rows_b": 0, "null_key_rows_a": 0,
            "null_key_rows_b": 0, "unindexed_rows_a": 0,
        }
        self.totals = {"added": 0, "removed": 0, "modified": 0}
        self._export = {"added": [], "removed": [], "modified": []}
        self._index: dict[tuple, list] = {}
        self._a_dups: set = set()
        self._b_counts: dict = {}
        self._b_dups: set = set()
        self._samples = {"dup_a": [], "dup_b": [], "null_a": [], "null_b": []}
        self._overflow_a = False
        self._overflow_b = False
        self._dtype_a: dict | None = None
        self._dtype_b: dict | None = None

    # -- dtype observation (for the mismatch warning) ------------------- #
    def observe_dtypes(self, dtypes_a: dict, dtypes_b: dict) -> None:
        self._dtype_a = {str(k): str(v) for k, v in (dtypes_a or {}).items()}
        self._dtype_b = {str(k): str(v) for k, v in (dtypes_b or {}).items()}

    # -- helpers -------------------------------------------------------- #
    def _align(self, row, pos_list) -> tuple:
        return tuple(None if p < 0 else row[p] for p in pos_list)

    def _keep(self, category: str, row: tuple, times: int = 1) -> None:
        self.counts[category] += times
        self.totals[category] += times
        bucket = self._export[category]
        for _ in range(times):
            if len(bucket) >= self.cfg.max_export_rows:
                break
            bucket.append(row)

    def _sample(self, bucket: str, key) -> None:
        samples = self._samples[bucket]
        if len(samples) < _SAMPLE_LIMIT:
            text = format_key(key)
            if text not in samples:
                samples.append(text)

    # -- pass A: index dataset A ---------------------------------------- #
    def feed_a(self, rows) -> None:
        index = self._index
        cap = self.cfg.max_keys
        for row in rows:
            self.counts["a_rows"] += 1
            key = make_key(row, self.key_pos_a)
            if key is None:
                self.counts["null_key_rows_a"] += 1
                self._sample(
                    "null_a",
                    tuple(normalize_value(row[p]) for p in self.key_pos_a))
                continue
            bucket = index.get(key)
            if bucket is None:
                if len(index) >= cap:
                    self._overflow_a = True
                    self.counts["unindexed_rows_a"] += 1
                    continue
                index[key] = [normalize_row(row)]
            else:
                bucket.append(normalize_row(row))

    def finish_a(self) -> None:
        dup_keys = [k for k, bucket in self._index.items()
                    if len(bucket) > 1]
        for key in dup_keys:
            self.counts["duplicate_key_rows_a"] += len(self._index.pop(key))
            self._sample("dup_a", key)
        self._a_dups = set(dup_keys)

    # -- pass B1: count keys (duplicate detection) ---------------------- #
    def count_b(self, rows) -> None:
        counts = self.counts
        tracked = self._b_counts
        cap = self.cfg.max_keys
        for row in rows:
            counts["b_rows"] += 1
            key = make_key(row, self.key_pos_b)
            if key is None:
                counts["null_key_rows_b"] += 1
                self._sample(
                    "null_b",
                    tuple(normalize_value(row[p]) for p in self.key_pos_b))
                continue
            seen = tracked.get(key)
            if seen is None:
                if len(tracked) >= cap:
                    self._overflow_b = True
                else:
                    tracked[key] = 1
            else:
                tracked[key] = seen + 1

    def finish_counts(self) -> None:
        dups = [k for k, c in self._b_counts.items() if c > 1]
        dups.sort(key=str)
        self._b_dups = set(dups)
        for key in dups[:_SAMPLE_LIMIT]:
            self._sample("dup_b", key)
        self._b_counts.clear()

    # -- pass B2: match -------------------------------------------------- #
    def feed_b(self, rows) -> None:
        index = self._index
        for row in rows:
            key = make_key(row, self.key_pos_b)
            if key is None:
                continue                       # counted in the count pass
            if key in self._a_dups or key in self._b_dups:
                self.counts["duplicate_key_rows_b"] += 1
                self._sample("dup_b", key)
                continue
            bucket = index.get(key)
            if bucket is None:
                self._keep("added",
                           (format_key(key),)
                           + self._align(normalize_row(row), self._pos_b))
                continue
            old = bucket.pop(0)
            if not bucket:
                del index[key]
            new = normalize_row(row)
            if all(old[pa] == new[pb] for _, pa, pb in self._changed_pairs):
                self.counts["matched"] += 1
                continue
            changed = []
            for col, pa, pb in self._changed_pairs:
                if old[pa] != new[pb]:
                    changed.append(f"{col}: {_value_text(old[pa])} -> "
                                   f"{_value_text(new[pb])}")
            if len(changed) > _CHANGED_FIELD_LIMIT:
                extra = len(changed) - _CHANGED_FIELD_LIMIT
                changed = changed[:_CHANGED_FIELD_LIMIT]
                changed.append(f"... (+{extra} more field(s))")
            self._keep("modified",
                       (format_key(key), "; ".join(changed))
                       + self._align(new, self._pos_b))

    def finish_b(self) -> DiffOutcome:
        for key, bucket in self._index.items():
            if key in self._b_dups:
                self.counts["duplicate_key_rows_a"] += len(bucket)
                self._sample("dup_a", key)
                continue
            for old in bucket:
                self._keep("removed",
                           (format_key(key),)
                           + self._align(old, self._pos_a))
        self._index.clear()
        return self.outcome()

    # -- outcome --------------------------------------------------------- #
    def outcome(self) -> DiffOutcome:
        c = self.counts
        warnings: list[dict] = []
        if c["duplicate_key_rows_a"] or c["duplicate_key_rows_b"]:
            warnings.append(warn(
                "duplicate_keys",
                "rows whose key value occurs more than once on either side "
                f"cannot be paired: {c['duplicate_key_rows_a']} row(s) in "
                f"dataset A and {c['duplicate_key_rows_b']} in dataset B are "
                "counted separately (not added/removed/modified). "
                f"key examples: {', '.join(self._samples['dup_a'] + self._samples['dup_b']) or 'n/a'}"))
        if c["null_key_rows_a"] or c["null_key_rows_b"]:
            warnings.append(warn(
                "null_keys",
                "rows with a missing key value cannot be matched: "
                f"{c['null_key_rows_a']} row(s) in dataset A and "
                f"{c['null_key_rows_b']} in dataset B are counted "
                "separately. key examples: "
                f"{', '.join(self._samples['null_a'] + self._samples['null_b']) or 'n/a'}"))
        if self._overflow_a:
            warnings.append(warn(
                "key_index_limit",
                f"the key index reached its limit of "
                f"{self.cfg.max_keys:,} distinct keys; "
                f"{c['unindexed_rows_a']:,} row(s) of dataset A could not "
                "be indexed, so added/removed counts are approximate."))
        if self._overflow_b:
            warnings.append(warn(
                "key_count_limit",
                f"duplicate detection reached its limit of "
                f"{self.cfg.max_keys:,} distinct keys in dataset B; "
                "duplicate keys beyond the limit may be reported as "
                "added/matched instead - counts are approximate."))
        if self._dtype_a is not None and self._dtype_b is not None:
            mixed = [k for k in self.keys
                     if self._dtype_a.get(k) != self._dtype_b.get(k)]
            if mixed:
                warnings.append(warn(
                    "key_dtype_mismatch",
                    "key column type(s) differ between the datasets ("
                    + "; ".join(f"{k}: {self._dtype_a.get(k)} vs "
                                f"{self._dtype_b.get(k)}" for k in mixed)
                    + ") - values were canonicalised for matching "
                      "(1 and 1.0 are equal), but rows may still fail to "
                      "match."))
        if not self.nonkey_common:
            warnings.append(warn(
                "no_comparable_columns",
                "the datasets share only the key column(s): matched rows "
                "have no other column to compare, so record changes cannot "
                "be detected."))

        exactness = "approximate" if (self._overflow_a or self._overflow_b) \
            else "exact"
        return DiffOutcome(
            strategy=self.strategy, exactness=exactness, keys=self.keys,
            columns=self.union_columns, counts=c, warnings=warnings,
            rows_added=self._export["added"],
            rows_removed=self._export["removed"],
            rows_modified=self._export["modified"], totals=self.totals)


# ---------------------------------------------------------------------- #
# Content-multiset fallback (no usable key columns)
# ---------------------------------------------------------------------- #
class ContentMatcher:
    strategy = "content-multiset"
    needs_count_pass = False

    def __init__(self, columns_a, columns_b, keys, cfg):
        self.columns_a = [str(c) for c in columns_a]
        self.columns_b = [str(c) for c in columns_b]
        self.keys = []
        self.cfg = cfg
        self.union_columns = _union_columns(self.columns_a, self.columns_b)
        set_a, set_b = set(self.columns_a), set(self.columns_b)
        self._pos_a = [self.columns_a.index(c) if c in set_a else -1
                       for c in self.union_columns]
        self._pos_b = [self.columns_b.index(c) if c in set_b else -1
                       for c in self.union_columns]
        self.counts = {
            "a_rows": 0, "b_rows": 0, "matched": 0, "modified": 0,
            "added": 0, "removed": 0, "duplicate_key_rows_a": 0,
            "duplicate_key_rows_b": 0, "null_key_rows_a": 0,
            "null_key_rows_b": 0, "unindexed_rows_a": 0,
        }
        self.totals = {"added": 0, "removed": 0, "modified": 0}
        self._export = {"added": [], "removed": [], "modified": []}
        self._a: dict = {}
        self._b: dict = {}

    def observe_dtypes(self, dtypes_a: dict, dtypes_b: dict) -> None:
        return None                       # irrelevant without key columns

    def _align(self, row, pos_list) -> tuple:
        return tuple(None if p < 0 else row[p] for p in pos_list)

    def _keep(self, category: str, row: tuple, times: int = 1) -> None:
        self.totals[category] += times
        bucket = self._export[category]
        for _ in range(times):
            if len(bucket) >= self.cfg.max_export_rows:
                break
            bucket.append(row)

    def feed_a(self, rows) -> None:
        for row in rows:
            self.counts["a_rows"] += 1
            content = normalize_row(row)
            entry = self._a.get(content)
            if entry is None:
                self._a[content] = [1, self._align(row, self._pos_a)]
            else:
                entry[0] += 1

    def finish_a(self) -> None:
        return None

    def count_b(self, rows) -> None:
        return None                       # single pass is sufficient

    def finish_counts(self) -> None:
        return None

    def feed_b(self, rows) -> None:
        for row in rows:
            self.counts["b_rows"] += 1
            content = normalize_row(row)
            entry = self._b.get(content)
            if entry is None:
                self._b[content] = [1, self._align(row, self._pos_b)]
            else:
                entry[0] += 1

    def finish_b(self) -> DiffOutcome:
        for content, (count, row) in self._a.items():
            shared = min(count, self._b.get(content, (0,))[0])
            if shared:
                self.counts["matched"] += shared
            extra = count - shared
            if extra:
                self.counts["removed"] += extra
                self._keep("removed", (format_key(None),) + row, times=extra)
        for content, (count, row) in self._b.items():
            extra = count - self._a.get(content, (0,))[0]
            if extra:
                self.counts["added"] += extra
                self._keep("added", (format_key(None),) + row, times=extra)
        self._a.clear()
        self._b.clear()
        return self.outcome()

    def outcome(self) -> DiffOutcome:
        warnings = [warn(
            "no_key_columns",
            "no usable key column was configured or detected: rows were "
            "compared by exact content only. Records cannot be paired, so "
            "a changed record appears as one removal plus one addition, "
            "and row order is ignored. Counts are exact for identical "
            "rows.")]
        return DiffOutcome(
            strategy=self.strategy, exactness="exact", keys=[],
            columns=self.union_columns, counts=self.counts,
            warnings=warnings, rows_added=self._export["added"],
            rows_removed=self._export["removed"],
            rows_modified=self._export["modified"], totals=self.totals,
            summary_extra={
                "limitations": [
                    "content matching pairs rows by exact equality only",
                    "changed records are reported as removed + added",
                    "row order is ignored (never used for matching)",
                ]})
