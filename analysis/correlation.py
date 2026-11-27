"""Correlation matrices (Mode B, Phase 5).

Conventions (documented):
* Only numeric columns participate (booleans excluded); each excluded
  column is listed with its reason.
* Non-finite values (+/-inf) are replaced by NaN before computation.
* Missing data: pandas pairwise-complete semantics - each cell uses the
  rows where BOTH columns are non-null. ``rows_total`` and
  ``rows_complete`` (all finite in every included column) are reported so
  the basis of every matrix is visible.
* Columns with zero variance (constant) or no values cannot correlate and
  are excluded with a reason.
* ``basis`` states whether the matrix used the full data (``exact``) or a
  sample (``sample``) - callers doing streaming analysis must label it.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger("da.analysis.correlation")

DEFAULT_METHODS = ("pearson", "spearman")
MAX_CORRELATION_COLUMNS = 100


def _numeric_columns(df: pd.DataFrame) -> tuple[list[str], dict[str, str]]:
    included: list[str] = []
    reasons: dict[str, str] = {}
    for c in df.columns:
        s = df[c]
        if not pd.api.types.is_numeric_dtype(s):
            reasons[str(c)] = "not numeric"
            continue
        if pd.api.types.is_bool_dtype(s):
            reasons[str(c)] = "boolean treated as categorical"
            continue
        included.append(str(c))
    return included, reasons


def correlation_matrix(df: pd.DataFrame,
                       methods: tuple[str, ...] = DEFAULT_METHODS,
                       max_columns: int = MAX_CORRELATION_COLUMNS,
                       basis: str = "exact") -> dict:
    """Correlation matrices for all usable numeric columns."""
    out: dict = {
        "basis": basis,
        "methods": {},
        "excluded_columns": {},
        "notes": [],
        "rows_total": int(len(df)),
    }
    if df.empty:
        out["notes"].append("dataset is empty")
        return out

    numeric, reasons = _numeric_columns(df)
    usable: list[str] = []
    for c in numeric:
        s = pd.to_numeric(df[c], errors="coerce").replace(
            [np.inf, -np.inf], np.nan)
        if not s.notna().any():
            reasons[c] = "no values"
            continue
        if s.nunique(dropna=True) <= 1:
            reasons[c] = "constant (zero variance)"
            continue
        usable.append(c)

    if len(usable) > max_columns:
        dropped = usable[int(max_columns):]
        usable = usable[: int(max_columns)]
        for c in dropped:
            reasons[c] = f"column limit ({max_columns})"
        out["notes"].append(
            f"limited to {max_columns} numeric columns")

    out["excluded_columns"] = reasons
    if len(usable) < 2:
        out["notes"].append(
            "fewer than 2 usable numeric columns - no matrix computed")
        return out

    work = df[usable].apply(pd.to_numeric, errors="coerce").replace(
        [np.inf, -np.inf], np.nan)
    out["rows_complete"] = int(work.notna().all(axis=1).sum())
    out["notes"].append(
        "pairwise-complete observations (each pair uses rows where both "
        "columns are non-null)")

    for method in methods:
        try:
            m = work.corr(method=method)
        except Exception as exc:  # noqa: BLE001 - recorded, not raised
            out["methods"][method] = {
                "error": f"{type(exc).__name__}: computation failed"}
            continue
        matrix: list[list] = []
        for _i in range(len(usable)):
            row: list = []
            for _j in range(len(usable)):
                v = m.iloc[_i, _j]
                if v is None or pd.isna(v):
                    row.append(None)
                else:
                    row.append(round(float(v), 6))
            matrix.append(row)
        out["methods"][method] = {"columns": list(usable),
                                  "matrix": matrix}
    return out
