"""CSV-to-CSV record comparison (Phase 6).

Chunked streaming: dataset A is read once (key index + statistics),
dataset B is read twice when key columns are used (count pass for
duplicate detection, then the matching pass) and once for content
matching. Row order is never used; both files are re-read with the
encoding and delimiter recorded at registration.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from analysis.comparison import matching
from core.exceptions import PlatformError

_ROUND = 12


def _read_header(inputs: dict) -> list[str]:
    try:
        frame = pd.read_csv(inputs["path"], nrows=0,
                            encoding=inputs.get("encoding") or "utf-8",
                            sep=inputs.get("delimiter") or ",")
    except Exception as exc:  # noqa: BLE001 - surfaced as guidance
        raise PlatformError(
            "The CSV header could not be read - check the file and "
            "re-register the dataset on the Data Sources page.",
            detail=f"{type(exc).__name__}") from exc
    return [str(c) for c in frame.columns]


def _iter_chunks(path: str, cfg, inputs: dict):
    try:
        yield from pd.read_csv(path, chunksize=cfg.chunk_size,
                               encoding=inputs.get("encoding") or "utf-8",
                               sep=inputs.get("delimiter") or ",")
    except pd.errors.EmptyDataError as exc:
        raise PlatformError(
            "The CSV file is empty - there is nothing to compare.",
            detail=str(path)) from exc
    except UnicodeDecodeError as exc:
        raise PlatformError(
            "The CSV file could not be decoded with its registered "
            "encoding - re-register the dataset.",
            detail=str(path)) from exc


# ---------------------------------------------------------------------- #
# Streaming statistics (chunk-mergeable)
# ---------------------------------------------------------------------- #
class NumericAccumulator:
    """count/mean/std/min/max/unique merged chunk by chunk (exact)."""

    def __init__(self, unique_cap: int):
        self.n = 0
        self.mean = 0.0
        self.m2 = 0.0
        self.minimum: float | None = None
        self.maximum: float | None = None
        self.missing = 0
        self.nonfinite = 0
        self.unique_cap = unique_cap
        self._unique: set | None = set()
        self.unique_overflow = False

    def update(self, series: pd.Series) -> None:
        numeric = pd.to_numeric(series, errors="coerce")
        self.missing += int(numeric.isna().sum())
        values = numeric.dropna().to_numpy(dtype="float64")
        finite = values[np.isfinite(values)]
        self.nonfinite += int(len(values) - len(finite))
        if not len(finite):
            return
        chunk_mean = float(finite.mean())
        chunk_m2 = float(((finite - chunk_mean) ** 2).sum())
        chunk_n = int(len(finite))
        if self.n == 0:
            self.n, self.mean, self.m2 = chunk_n, chunk_mean, chunk_m2
        else:
            total = self.n + chunk_n
            delta = chunk_mean - self.mean
            self.m2 += chunk_m2 + delta * delta * self.n * chunk_n / total
            self.mean += delta * chunk_n / total
            self.n = total
        chunk_min, chunk_max = float(finite.min()), float(finite.max())
        self.minimum = (chunk_min if self.minimum is None
                        else min(self.minimum, chunk_min))
        self.maximum = (chunk_max if self.maximum is None
                        else max(self.maximum, chunk_max))
        if self._unique is not None and not self.unique_overflow:
            self._unique.update(finite.tolist())
            if len(self._unique) > self.unique_cap:
                self._unique = None
                self.unique_overflow = True

    def to_dict(self) -> dict:
        std = None
        if self.n > 1:
            std = float(np.sqrt(self.m2 / (self.n - 1)))
        unique = None
        if self._unique is not None:
            unique = len(self._unique)
        return {
            "count": self.n,
            "missing": self.missing,
            "nonfinite_excluded": self.nonfinite,
            "mean": None if self.n == 0 else round(self.mean, _ROUND),
            "std": None if std is None else round(std, _ROUND),
            "min": self.minimum,
            "max": self.maximum,
            "unique": unique,
        }


class CategoricalAccumulator:
    """Value counts for one column (exact up to ``cap`` distinct values)."""

    def __init__(self, cap: int):
        self.cap = cap
        self.counts: dict = {}
        self.total = 0
        self.missing = 0
        self.truncated = False

    def update(self, series: pd.Series) -> None:
        self.missing += int(series.isna().sum())
        for value in series:
            if pd.isna(value):
                continue
            key = matching.normalize_value(value)
            if key is None:
                continue
            self.total += 1
            seen = self.counts.get(key)
            if seen is None:
                if len(self.counts) >= self.cap:
                    self.truncated = True
                    continue
                self.counts[key] = 1
            else:
                self.counts[key] = seen + 1

    def to_dict(self) -> dict:
        return {"count": self.total, "missing": self.missing,
                "unique": None if self.truncated else len(self.counts)}


def _is_numeric(series: pd.Series) -> bool:
    return bool(pd.api.types.is_numeric_dtype(series.dtype))


class StatsSide:
    """Per-column statistics of one dataset, fed chunk by chunk."""

    def __init__(self, columns, cfg):
        self.columns = list(columns)
        self.unique_cap = cfg.max_keys
        self.numeric: dict = {c: NumericAccumulator(self.unique_cap)
                              for c in self.columns}
        self.categorical: dict = {c: CategoricalAccumulator(self.unique_cap)
                                  for c in self.columns}

    def feed(self, frame: pd.DataFrame) -> None:
        for col in self.columns:
            if col not in frame.columns:
                continue
            series = frame[col]
            if _is_numeric(series):
                self.numeric[col].update(series)
            else:
                self.categorical[col].update(series)

    def basis(self, col: str) -> str:
        has_num = self.numeric[col].n > 0
        has_cat = self.categorical[col].total > 0
        if has_num and not has_cat:
            return "numeric"
        if has_cat and not has_num:
            return "categorical"
        if has_num and has_cat:
            return "mixed"
        return "empty"


def _round(value):
    if isinstance(value, float):
        return round(value, _ROUND)
    return value


def _delta(a: dict, b: dict, fields) -> dict:
    out = {}
    for field in fields:
        va, vb = a.get(field), b.get(field)
        if va is None or vb is None or isinstance(va, bool) \
                or isinstance(vb, bool):
            out[field] = None
        else:
            try:
                out[field] = _round(vb - va)
            except TypeError:
                out[field] = None
    return out


class StatsPair:
    """Statistical differences between two datasets (common columns only)."""

    def __init__(self, common_columns, cfg):
        self.cfg = cfg
        self.columns = list(common_columns)[:cfg.stats_max_columns]
        self.skipped = max(0, len(common_columns) - len(self.columns))
        self.a = StatsSide(self.columns, cfg)
        self.b = StatsSide(self.columns, cfg)

    def feed_a(self, frame: pd.DataFrame) -> None:
        self.a.feed(frame)

    def feed_b(self, frame: pd.DataFrame) -> None:
        self.b.feed(frame)

    def _top_values(self, col: str) -> tuple[list, bool]:
        counts_a = self.a.categorical[col]
        counts_b = self.b.categorical[col]
        top = self.cfg.categorical_top_k
        keys_a = sorted(counts_a.counts, key=lambda k: (-counts_a.counts[k],
                                                        str(k)))[:top]
        keys_b = sorted(counts_b.counts, key=lambda k: (-counts_b.counts[k],
                                                        str(k)))[:top]
        union = list(dict.fromkeys(list(keys_a) + list(keys_b)))
        rows = []
        for value in union:
            va = counts_a.counts.get(value, 0)
            vb = counts_b.counts.get(value, 0)
            rows.append({"value": value, "a": va, "b": vb,
                         "delta": vb - va})
        rows.sort(key=lambda r: (-(max(r["a"], r["b"])), str(r["value"])))
        return rows, bool(counts_a.truncated or counts_b.truncated)

    def differences(self, kind: str) -> dict:
        columns_out: dict = {}
        warnings: list[dict] = []
        notes = ["full pass over both datasets - statistics are exact "
                 "(not sampled)"]
        delta_fields = ("count", "missing", "mean", "std", "min", "max",
                        "unique")
        for col in self.columns:
            basis_a = self.a.basis(col)
            basis_b = self.b.basis(col)
            if basis_a == basis_b == "numeric":
                da = self.a.numeric[col].to_dict()
                db = self.b.numeric[col].to_dict()
                columns_out[col] = {
                    "basis": "numeric", "a": da, "b": db,
                    "delta": _delta(da, db, delta_fields)}
            elif basis_a == basis_b == "categorical":
                da = self.a.categorical[col].to_dict()
                db = self.b.categorical[col].to_dict()
                top_values, truncated = self._top_values(col)
                entry = {
                    "basis": "categorical", "a": da, "b": db,
                    "delta": _delta(da, db, ("count", "missing", "unique")),
                    "top_values": top_values,
                    "top_k": self.cfg.categorical_top_k,
                }
                if truncated:
                    entry["values_truncated"] = True
                    notes.append(f"column '{col}': value counts capped at "
                                 f"{self.cfg.max_keys:,} distinct values - "
                                 "unique counts are unavailable")
                columns_out[col] = entry
            elif basis_a == "empty" and basis_b == "empty":
                columns_out[col] = {"basis": "empty", "a": {}, "b": {},
                                    "delta": {}}
            else:
                columns_out[col] = {
                    "basis": "mismatch",
                    "a": (self.a.numeric[col].to_dict() if basis_a ==
                          "numeric" else self.a.categorical[col].to_dict()),
                    "b": (self.b.numeric[col].to_dict() if basis_b ==
                          "numeric" else self.b.categorical[col].to_dict()),
                    "a_basis": basis_a, "b_basis": basis_b, "delta": {}}
                warnings.append({
                    "code": "stats_basis_mismatch",
                    "message": (f"column '{col}' has a different value type "
                                f"in the two datasets ({basis_a} vs "
                                f"{basis_b}) - no statistical delta is "
                                "reported for it.")})
        if any(acc.unique_overflow for acc in self.a.numeric.values()) or \
                any(acc.unique_overflow for acc in self.b.numeric.values()):
            notes.append(f"unique-count tracking capped at "
                         f"{self.cfg.max_keys:,} values per column")
        return {"kind": kind, "basis": "exact",
                "columns_analyzed": len(self.columns),
                "columns_skipped": self.skipped,
                "columns": columns_out, "notes": notes,
                "warnings": warnings}


# ---------------------------------------------------------------------- #
# Schema
# ---------------------------------------------------------------------- #
def _schema(kind: str, columns_a, columns_b, dtype_a, dtype_b,
            outcome) -> dict:
    set_a, set_b = set(columns_a), set(columns_b)
    added = [c for c in columns_b if c not in set_a]
    removed = [c for c in columns_a if c not in set_b]
    changes = []
    for col in columns_a:
        if col in set_b and dtype_a and dtype_b:
            da, db = dtype_a.get(col), dtype_b.get(col)
            if da is not None and db is not None and da != db:
                changes.append({"column": col, "a_dtype": da, "b_dtype": db})
    return {
        "kind": kind,
        "status": "ok",
        "columns": {"added": added, "removed": removed,
                    "dtype_changes": changes},
        "row_counts": {"a": outcome.counts.get("a_rows", 0),
                       "b": outcome.counts.get("b_rows", 0)},
        "note": ("column types observed on the first chunk of each file; "
                 "columns with mixed types across chunks may be listed"),
    }


# ---------------------------------------------------------------------- #
# Entry point
# ---------------------------------------------------------------------- #
def compare(in_a: dict, in_b: dict, cfg, stage) -> dict:
    warnings: list[dict] = []
    columns_a = _read_header(in_a)
    columns_b = _read_header(in_b)
    matcher = matching.make_matcher(columns_a, columns_b, cfg.keys, cfg)
    set_b = set(columns_b)
    common = [c for c in columns_a if c in set_b]
    stats = StatsPair(common, cfg) if cfg.stats_enabled else None

    stage("load", 1, 6, "reading dataset A (single pass)")
    dtype_a: dict | None = None
    for chunk in _iter_chunks(in_a["path"], cfg, in_a):
        if dtype_a is None:
            dtype_a = {str(c): str(t)
                       for c, t in zip(chunk.columns, chunk.dtypes)}
        matcher.feed_a(chunk.itertuples(index=False, name=None))
        if stats is not None:
            stats.feed_a(chunk)
    matcher.finish_a()

    dtype_b: dict | None = None
    if matcher.needs_count_pass:
        stage("load", 2, 6, "reading dataset B (pass 1 of 2: key counts)")
        for chunk in _iter_chunks(in_b["path"], cfg, in_b):
            if dtype_b is None:
                dtype_b = {str(c): str(t)
                           for c, t in zip(chunk.columns, chunk.dtypes)}
            matcher.count_b(chunk.itertuples(index=False, name=None))
            if stats is not None:
                stats.feed_b(chunk)
        matcher.finish_counts()
        stage("load", 3, 6, "reading dataset B (pass 2 of 2: matching)")
        for chunk in _iter_chunks(in_b["path"], cfg, in_b):
            matcher.feed_b(chunk.itertuples(index=False, name=None))
    else:
        stage("load", 2, 6, "reading dataset B (single pass)")
        for chunk in _iter_chunks(in_b["path"], cfg, in_b):
            if dtype_b is None:
                dtype_b = {str(c): str(t)
                           for c, t in zip(chunk.columns, chunk.dtypes)}
            matcher.feed_b(chunk.itertuples(index=False, name=None))
            if stats is not None:
                stats.feed_b(chunk)

    if stats is not None:
        stage("statistics", 4, 6, "computing statistical differences")
        statistics = stats.differences("csv")
        warnings.extend(statistics.pop("warnings"))
    else:
        stage("statistics", 4, 6, "statistics disabled for this run")
        statistics = {"kind": "csv", "status": "disabled",
                      "notes": ["statistics disabled in the options"]}
    matcher.observe_dtypes(dtype_a or {}, dtype_b or {})
    outcome = matcher.finish_b()
    schema = _schema("csv", columns_a, columns_b, dtype_a, dtype_b, outcome)

    record_tables = outcome.record_tables()
    return {
        "strategy": outcome.strategy,
        "exactness": outcome.exactness,
        "keys": outcome.keys,
        "counts": outcome.counts,
        "warnings": list(outcome.warnings) + warnings,
        "schema": schema,
        "statistics": statistics,
        "record_tables": record_tables,
        "table_files": [t["file"] for t in record_tables],
        "summary_extra": dict(outcome.summary_extra),
    }
