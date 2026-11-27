"""Data quality foundation: evidence-based checks on any tabular DataFrame.

Findings are derived only from the data itself - nothing is invented or
imputed, and NOTHING is ever cleaned, deleted, imputed or replaced: reports
are proposals for human review only.

Two entry points:

* ``assess_quality(df)`` - Phase 3 compact summary (unchanged behaviour).
* ``analyze_quality(df)`` / ``QualityAccumulator`` (Phase 5) - full report
  with missing values, duplicate rows/columns, invalid values, inconsistent
  types, constant columns, high-cardinality columns and potential outliers.

QUALITY SCORE - exact formula (identical in both entry points):

    completeness_pct = 100 * (1 - missing_cells / total_cells)
    score = clamp_0_100(
        completeness_pct
        -  5 * (columns with >= 20% missing, warn or fail severity)
        - 10 * (constant columns)
        -100 * min(1, duplicate_rows / rows)
    )

SCORE LIMITATIONS (read before quoting the number):
* The weights (5 / 10 / 100) are platform heuristics, not a standard.
* A single number hides per-column detail; always read the warnings.
* No semantic, domain or validity rules are evaluated.
* Not comparable across datasets with different schemas.
* Duplicate detection is full-row only (exact), subject to the row-hash cap
  in streaming mode.
"""
from __future__ import annotations

import hashlib
import logging
import math

import numpy as np
import pandas as pd

log = logging.getLogger("da.analysis.quality")

MISSING_WARN_PCT = 20.0
MISSING_FAIL_PCT = 80.0
DEFAULT_HIGH_CARDINALITY_UNIQUE = 1_000
DEFAULT_TYPE_SAMPLE = 5_000
DEFAULT_DUP_HASH_CAP = 5_000_000
OUTLIER_K = 1.5

SCORE_FORMULA = (
    "score = clamp_0_100(completeness_pct "
    "- 5 * columns_missing_ge_20pct "
    "- 10 * constant_columns "
    "- 100 * min(1, duplicate_rows / rows))"
)
SCORE_LIMITATIONS = [
    "weights (5/10/100) are platform heuristics, not a standard",
    "a single number hides per-column detail - read the warnings",
    "no semantic, domain or validity rules are evaluated",
    "not comparable across datasets with different schemas",
    "duplicate detection is full-row only (exact; subject to row-hash cap "
    "when streaming)",
]


def assess_quality(df: pd.DataFrame) -> dict:
    result: dict = {
        "rows": int(len(df)),
        "columns": int(len(df.columns)),
        "warnings": [],
        "generated_by": "data_quality",
    }
    if df.empty:
        result["warnings"].append("dataset is empty")
        result["completeness_pct"] = None
        result["score"] = 0
        return result

    total_cells = int(df.shape[0] * df.shape[1]) or 1
    missing_cells = int(df.isna().sum().sum())
    completeness = max(0.0, 100.0 * (1 - missing_cells / total_cells))
    result["completeness_pct"] = round(completeness, 2)
    result["missing_cells"] = missing_cells

    high_missing: list[dict] = []
    for name in df.columns:
        pct = float(df[name].isna().mean()) * 100
        if pct >= MISSING_FAIL_PCT:
            high_missing.append({"column": str(name),
                                 "missing_pct": round(pct, 2),
                                 "severity": "fail"})
            result["warnings"].append(
                f"column '{name}' is {pct:.0f}% missing")
        elif pct >= MISSING_WARN_PCT:
            high_missing.append({"column": str(name),
                                 "missing_pct": round(pct, 2),
                                 "severity": "warn"})
            result["warnings"].append(
                f"column '{name}' is {pct:.0f}% missing")
    result["columns_high_missing"] = high_missing

    constant_cols = [str(c) for c in df.columns if df[c].nunique(dropna=True) <= 1]
    result["constant_columns"] = constant_cols
    if constant_cols:
        result["warnings"].append(
            f"{len(constant_cols)} constant column(s): "
            + ", ".join(constant_cols[:5]))

    dupes = int(df.duplicated().sum())
    result["duplicate_rows"] = dupes
    if dupes:
        result["warnings"].append(f"{dupes} duplicate row(s)")

    score = completeness
    score -= 5 * len(high_missing)
    score -= 10 * len(constant_cols)
    if df.shape[0]:
        score -= 100.0 * min(1.0, dupes / df.shape[0])
    result["score"] = round(max(0.0, min(100.0, score)), 1)
    log.info("Quality assessed: score=%s warnings=%d", result["score"],
             len(result["warnings"]))
    return result


# ---------------------------------------------------------------------- #
# Phase 5: full quality report
# ---------------------------------------------------------------------- #
def _score(completeness_pct: float | None, high_missing: int,
           constant: int, dup_rows: int, rows: int) -> float | None:
    if completeness_pct is None:
        return None
    score = float(completeness_pct)
    score -= 5 * high_missing
    score -= 10 * constant
    if rows:
        score -= 100.0 * min(1.0, dup_rows / rows)
    return round(max(0.0, min(100.0, score)), 1)


def _severity(pct: float) -> str:
    if pct >= MISSING_FAIL_PCT:
        return "fail"
    if pct >= MISSING_WARN_PCT:
        return "warn"
    return "info"


def classify_value(v) -> str:
    if isinstance(v, (bool, np.bool_)):
        return "bool"
    if isinstance(v, (int, np.integer)):
        return "int"
    if isinstance(v, (float, np.floating)):
        return "float"
    if isinstance(v, str):
        return "str"
    if isinstance(v, (pd.Timestamp,)) or hasattr(v, "isoformat"):
        return "datetime"
    return "other"


def column_fingerprint_update(hasher, values: pd.Series) -> None:
    """Order-independent-per-element fingerprint: SHA-256 over pandas'
    per-element 64-bit hashes (identical whether computed in one pass or
    chunk by chunk)."""
    h = pd.util.hash_pandas_object(values, index=False).values
    hasher.update(np.asarray(h, dtype="<u8").tobytes())


def column_fingerprint(values: pd.Series) -> str:
    hasher = hashlib.sha256()
    column_fingerprint_update(hasher, values)
    return hasher.hexdigest()


def duplicate_column_groups(fingerprints: dict[str, str]) -> list[list[str]]:
    """Group columns by identical content fingerprint (order preserved)."""
    by_hash: dict[str, list[str]] = {}
    for name, fp in fingerprints.items():
        by_hash.setdefault(fp, []).append(name)
    return sorted((sorted(names) for names in by_hash.values()
                   if len(names) > 1), key=lambda g: g[0])


def _inconsistent_types(series: pd.Series, sample_limit: int) -> dict | None:
    """More than one Python-level type among sampled non-null values of an
    object/string column -> reported (sampling first ``sample_limit``)."""
    if not (pd.api.types.is_object_dtype(series)
            or pd.api.types.is_string_dtype(series)):
        return None
    kinds: dict[str, int] = {}
    seen = 0
    for v in series.dropna():
        kinds[classify_value(v)] = kinds.get(classify_value(v), 0) + 1
        seen += 1
        if seen >= sample_limit:
            break
    if len(kinds) <= 1:
        return None
    return {"categories": dict(sorted(kinds.items())),
            "sample_size": seen}


def analyze_quality(df: pd.DataFrame, *, k: float = OUTLIER_K,
                    detect_outliers: bool = True,
                    high_cardinality_unique: int
                    = DEFAULT_HIGH_CARDINALITY_UNIQUE,
                    type_sample: int = DEFAULT_TYPE_SAMPLE) -> dict:
    """Full quality report for an in-memory DataFrame (nothing modified)."""
    result: dict = {
        "generated_by": "data_quality.analyze_quality",
        "rows": int(len(df)),
        "columns": int(df.shape[1]),
        "warnings": [],
        "score_formula": SCORE_FORMULA,
        "score_limitations": list(SCORE_LIMITATIONS),
    }
    if df.empty:
        result["warnings"].append("dataset is empty")
        result["completeness_pct"] = None
        result["score"] = 0
        _empty_sections(result)
        return result

    # --- missing ------------------------------------------------------
    total_cells = int(df.shape[0] * df.shape[1]) or 1
    per_column: list[dict] = []
    missing_cells = 0
    for name in df.columns:
        miss = int(df[name].isna().sum())
        missing_cells += miss
        pct = 100.0 * miss / df.shape[0]
        entry = {"column": str(name), "missing": miss,
                 "missing_pct": round(pct, 4),
                 "severity": _severity(pct)}
        if entry["severity"] in ("warn", "fail"):
            per_column.append(entry)
            result["warnings"].append(
                f"column '{name}' is {pct:.0f}% missing")
    completeness = round(max(0.0, 100.0 * (1 - missing_cells / total_cells)),
                         2)
    result["completeness_pct"] = completeness
    result["missing_cells"] = missing_cells
    result["total_cells"] = total_cells
    result["columns_high_missing"] = per_column

    # --- duplicates ---------------------------------------------------
    dup_rows = int(df.duplicated().sum())
    fps = {str(c): column_fingerprint(df[c]) for c in df.columns}
    dup_cols = duplicate_column_groups(fps)
    result["duplicate_rows"] = dup_rows
    result["duplicate_columns"] = dup_cols
    if dup_rows:
        result["warnings"].append(f"{dup_rows} duplicate row(s)")
    if dup_cols:
        result["warnings"].append(
            f"{len(dup_cols)} duplicate column group(s): "
            + "; ".join(",".join(g) for g in dup_cols[:5]))

    # --- invalid / inconsistent --------------------------------------
    nonfinite_cols: list[dict] = []
    invalid_cells = 0
    for name in df.columns:
        s = df[name]
        if not pd.api.types.is_numeric_dtype(s):
            continue
        arr = s.to_numpy(dtype="float64", na_value=np.nan)
        n_inf = int(np.isinf(arr).sum())
        if n_inf:
            invalid_cells += n_inf
            nonfinite_cols.append({"column": str(name), "nonfinite": n_inf})
    result["invalid_values"] = {"nonfinite_cells": invalid_cells,
                                "columns": nonfinite_cols}
    if invalid_cells:
        result["warnings"].append(
            f"{invalid_cells} non-finite (+/-inf) value(s) in numeric columns")

    inconsistent: list[dict] = []
    for name in df.columns:
        found = _inconsistent_types(df[name], type_sample)
        if found:
            found["column"] = str(name)
            inconsistent.append(found)
            result["warnings"].append(
                f"column '{name}' mixes value types {found['categories']}")
    result["inconsistent_types"] = inconsistent

    # --- constant / high-cardinality ---------------------------------
    constant_cols: list[str] = []
    high_card: list[dict] = []
    for name in df.columns:
        try:
            uniq = int(df[name].nunique(dropna=True))
        except TypeError:
            uniq = None
        if uniq is not None and uniq <= 1:
            constant_cols.append(str(name))
        elif uniq is not None and uniq >= high_cardinality_unique:
            high_card.append({"column": str(name), "unique": uniq})
    result["constant_columns"] = constant_cols
    if constant_cols:
        result["warnings"].append(
            f"{len(constant_cols)} constant column(s): "
            + ", ".join(constant_cols[:5]))
    result["high_cardinality_columns"] = high_card
    for entry in high_card:
        result["warnings"].append(
            f"column '{entry['column']}' is high-cardinality "
            f"({entry['unique']} unique values)")

    # --- outliers (potential, IQR) -----------------------------------
    from analysis.outliers import outlier_report
    if detect_outliers:
        report = outlier_report(df, k=k, basis="exact")
        flagged = {c: {"count": v["count"], "pct": v["pct"]}
                   for c, v in report["columns"].items() if v["count"] > 0}
        result["potential_outliers"] = {"k": float(k), "basis": "exact",
                                        "columns": flagged,
                                        "notes": report["notes"]}
        if flagged:
            result["warnings"].append(
                f"{len(flagged)} column(s) with IQR outliers "
                f"(k={k}) - review, never auto-removed")
    else:
        result["potential_outliers"] = {"k": float(k), "basis": "skipped",
                                        "columns": {}, "notes": []}

    result["score"] = _score(completeness, len(per_column),
                             len(constant_cols), dup_rows, len(df))
    log.info("Quality analysed: score=%s warnings=%d",
             result["score"], len(result["warnings"]))
    return result


def _empty_sections(result: dict) -> None:
    result.update({
        "missing_cells": 0, "total_cells": 0,
        "columns_high_missing": [], "duplicate_rows": 0,
        "duplicate_columns": [], "invalid_values": {"nonfinite_cells": 0,
                                                    "columns": []},
        "inconsistent_types": [], "constant_columns": [],
        "high_cardinality_columns": [],
        "potential_outliers": {"k": OUTLIER_K, "basis": "exact",
                               "columns": {}, "notes": ["dataset is empty"]},
        "score_formula": SCORE_FORMULA,
        "score_limitations": list(SCORE_LIMITATIONS),
    })


class QualityAccumulator:
    """Streaming equivalent of the non-outlier parts of ``analyze_quality``.

    Exact: rows, missing cells, duplicate rows (SHA/64-bit row hashes -
    identical results to full mode modulo hash collisions), duplicate
    columns (content fingerprints), non-finite counts, type-mix sampling.
    Unique-dependent sections (constant / high-cardinality) use exact
    unique counts supplied by the caller's frequency accumulators.
    """

    def __init__(self, columns: list[str],
                 hash_cap: int = DEFAULT_DUP_HASH_CAP,
                 type_sample: int = DEFAULT_TYPE_SAMPLE):
        self.columns = [str(c) for c in columns]
        self.hash_cap = int(hash_cap)
        self.type_sample = int(type_sample)
        self.rows = 0
        self.missing_by_col: dict[str, int] = {c: 0 for c in self.columns}
        self._hashes: set[int] = set()
        self.duplicate_rows = 0
        self.hash_overflow = False
        self._fingerprints: dict[str, "hashlib._Hash"] = {
            c: hashlib.sha256() for c in self.columns}
        self.nonfinite_by_col: dict[str, int] = {c: 0 for c in self.columns}
        self.per_column_kinds: dict[str, dict[str, int]] = {}
        self.per_column_type_seen: dict[str, int] = {}

    def update(self, chunk: pd.DataFrame) -> None:
        n = len(chunk)
        self.rows += n
        for c in self.columns:
            if c not in chunk.columns:
                continue
            s = chunk[c]
            self.missing_by_col[c] += int(s.isna().sum())
            column_fingerprint_update(self._fingerprints[c], s)
            if pd.api.types.is_numeric_dtype(s):
                arr = s.to_numpy(dtype="float64", na_value=np.nan)
                n_inf = int(np.isinf(arr).sum())
                if n_inf:
                    self.nonfinite_by_col[c] += n_inf
            if (pd.api.types.is_object_dtype(s)
                    or pd.api.types.is_string_dtype(s)):
                self._sample_types(c, s)
        if not self.hash_overflow and n:
            try:
                hs = pd.util.hash_pandas_object(
                    chunk.reindex(columns=self.columns),
                    index=False).to_numpy()
            except (TypeError, ValueError):
                self.hash_overflow = True
                self._hashes.clear()
                self.duplicate_rows = 0
            else:
                if len(self._hashes) + len(hs) > self.hash_cap:
                    self.hash_overflow = True
                    self._hashes.clear()
                    self.duplicate_rows = 0
                else:
                    for h in hs.tolist():
                        if h in self._hashes:
                            self.duplicate_rows += 1
                        else:
                            self._hashes.add(int(h))

    def _sample_types(self, col: str, s: pd.Series) -> None:
        """Classify up to ``type_sample`` non-null values of this column."""
        kinds = self.per_column_kinds.setdefault(col, {})
        seen = self.per_column_type_seen.get(col, 0)
        if seen >= self.type_sample:
            return
        for v in s.dropna().tolist():
            k = classify_value(v)
            kinds[k] = kinds.get(k, 0) + 1
            seen += 1
            if seen >= self.type_sample:
                break
        self.per_column_type_seen[col] = seen

    def finalize(self, *,
                 unique_info: dict[str, int | None],
                 completeness_pct: float | None,
                 missing_cells: int,
                 total_cells: int,
                 k: float = OUTLIER_K,
                 high_cardinality_unique: int
                 = DEFAULT_HIGH_CARDINALITY_UNIQUE) -> dict:
        result: dict = {
            "generated_by": "data_quality.QualityAccumulator",
            "rows": self.rows,
            "columns": len(self.columns),
            "warnings": [],
            "score_formula": SCORE_FORMULA,
            "score_limitations": list(SCORE_LIMITATIONS),
        }
        if self.rows == 0 or not self.columns:
            result["warnings"].append("dataset is empty")
            result["completeness_pct"] = None
            result["score"] = 0
            _empty_sections(result)
            return result

        total_cells = int(self.rows * len(self.columns)) or 1
        missing_cells = sum(self.missing_by_col.values())
        completeness = round(
            max(0.0, 100.0 * (1 - missing_cells / total_cells)), 2)
        per_column: list[dict] = []
        for c in self.columns:
            miss = self.missing_by_col.get(c, 0)
            pct = 100.0 * miss / self.rows
            entry = {"column": c, "missing": miss,
                     "missing_pct": round(pct, 4),
                     "severity": _severity(pct)}
            if entry["severity"] in ("warn", "fail"):
                per_column.append(entry)
                result["warnings"].append(
                    f"column '{c}' is {pct:.0f}% missing")
        result["completeness_pct"] = completeness
        result["missing_cells"] = missing_cells
        result["total_cells"] = total_cells
        result["columns_high_missing"] = per_column

        result["duplicate_rows"] = int(self.duplicate_rows)
        if self.hash_overflow:
            result["warnings"].append(
                "duplicate-row detection skipped: row-hash cap exceeded")
            result["duplicate_rows_skipped"] = True
        fps = {c: h.hexdigest() for c, h in self._fingerprints.items()}
        dup_cols = duplicate_column_groups(fps)
        result["duplicate_columns"] = dup_cols
        if self.duplicate_rows:
            result["warnings"].append(
                f"{self.duplicate_rows} duplicate row(s)")
        if dup_cols:
            result["warnings"].append(
                f"{len(dup_cols)} duplicate column group(s): "
                + "; ".join(",".join(g) for g in dup_cols[:5]))

        nonfinite_cols = [{"column": c, "nonfinite": n}
                          for c, n in self.nonfinite_by_col.items() if n]
        invalid_cells = sum(self.nonfinite_by_col.values())
        result["invalid_values"] = {"nonfinite_cells": invalid_cells,
                                    "columns": nonfinite_cols}
        if invalid_cells:
            result["warnings"].append(
                f"{invalid_cells} non-finite (+/-inf) value(s) in numeric "
                "columns")
        result["inconsistent_types"] = []
        for c, kinds in self.per_column_kinds.items():
            if len(kinds) > 1:
                entry = {"column": c,
                         "categories": dict(sorted(kinds.items())),
                         "sample_size": self.per_column_type_seen.get(c, 0),
                         "basis": f"first {self.type_sample} values per "
                                  "column"}
                result["inconsistent_types"].append(entry)
                result["warnings"].append(
                    f"column '{c}' mixes value types {entry['categories']}")

        constant_cols: list[str] = []
        high_card: list[dict] = []
        unknown_unique: list[str] = []
        for c in self.columns:
            uniq = unique_info.get(c)
            if uniq is None:
                unknown_unique.append(c)
            elif uniq <= 1:
                constant_cols.append(c)
            elif uniq >= high_cardinality_unique:
                high_card.append({"column": c, "unique": uniq})
        result["constant_columns"] = constant_cols
        if constant_cols:
            result["warnings"].append(
                f"{len(constant_cols)} constant column(s): "
                + ", ".join(constant_cols[:5]))
        result["high_cardinality_columns"] = high_card
        for entry in high_card:
            result["warnings"].append(
                f"column '{entry['column']}' is high-cardinality "
                f"({entry['unique']} unique values)")
        if unknown_unique:
            result["notes_unique_unknown"] = (
                "unique count unavailable (value cap exceeded) for: "
                + ", ".join(unknown_unique[:10]))

        result["potential_outliers"] = {"k": float(k), "basis": "deferred",
                                        "columns": {},
                                        "notes": ["merged by the analysis "
                                                  "runner from numerical "
                                                  "accumulators"]}
        result["score"] = _score(completeness, len(per_column),
                                 len(constant_cols), self.duplicate_rows,
                                 self.rows)
        return result
