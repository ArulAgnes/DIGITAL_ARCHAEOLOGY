"""Duplicate analysis.

Mode A (research): wrappers over ``scanner.duplicate_detector`` operating on
the Digital Archaeology research outputs - unchanged.

Mode B (general datasets): ``record_duplicates`` reports duplicate RECORDS
of any tabular DataFrame. Nothing is ever removed - findings are for review.
"""
from __future__ import annotations

import pandas as pd

import config
from scanner import duplicate_detector as dd


def load_candidates() -> pd.DataFrame:
    if config.DUP_CANDIDATES_CSV.exists():
        return pd.read_csv(config.DUP_CANDIDATES_CSV, encoding="utf-8-sig")
    return pd.DataFrame(columns=dd.CANDIDATE_FIELDS)


def load_verified() -> pd.DataFrame:
    if config.DUP_VERIFIED_CSV.exists():
        return pd.read_csv(config.DUP_VERIFIED_CSV, encoding="utf-8-sig")
    return pd.DataFrame(columns=dd.VERIFIED_FIELDS)


def duplicate_by_folder(candidates: pd.DataFrame,
                        files: pd.DataFrame) -> pd.DataFrame:
    """Where do candidate copies live? (folder of each candidate path)."""
    if candidates.empty:
        return pd.DataFrame(columns=["parent_folder", "candidate_copies"])
    rows = []
    for _, c in candidates.iterrows():
        for p in str(c["paths"]).split("|"):
            p = p.strip()
            rows.append({"path": p,
                         "parent_folder": p.rsplit("\\", 1)[0]
                         if "\\" in p else p})
    if not rows:
        return pd.DataFrame(columns=["parent_folder", "candidate_copies"])
    df = pd.DataFrame(rows)
    d = (df.groupby("parent_folder").size()
         .rename("candidate_copies").reset_index())
    return d.sort_values("candidate_copies", ascending=False)


def summary(candidates: pd.DataFrame, verified: pd.DataFrame) -> dict:
    return dd.summarise(candidates, verified)


def run(files: pd.DataFrame, enable_hash: bool | None = None):
    return dd.run(files, enable_hash=enable_hash)


# ---------------------------------------------------------------------- #
# Mode B: duplicate records in a general dataset (Phase 5)
# ---------------------------------------------------------------------- #
def record_duplicates(df: pd.DataFrame) -> dict:
    """Exact full-row duplicate report. Rows are never dropped or changed.

    * ``duplicate_rows`` - rows beyond the first occurrence of identical
      rows (pandas ``duplicated()`` semantics).
    * ``rows_in_duplicate_groups`` - all rows that have at least one
      identical twin (``duplicated(keep=False)``).
    * ``duplicate_row_groups`` - number of distinct duplicated patterns,
      estimated via 64-bit row hashes (collisions are negligible for this
      purpose and are not deduplicated further).
    """
    if df.empty:
        return {"duplicate_rows": 0, "rows_in_duplicate_groups": 0,
                "duplicate_row_groups": 0, "rows": 0,
                "notes": ["dataset is empty"]}
    dup_beyond_first = int(df.duplicated().sum())
    in_groups = df[df.duplicated(keep=False)]
    rows_in_groups = int(len(in_groups))
    groups: int = 0
    if rows_in_groups:
        try:
            hashes = pd.util.hash_pandas_object(
                in_groups, index=False)
            groups = int(hashes.nunique())
        except (TypeError, ValueError):
            groups = -1
    notes: list[str] = []
    if groups == -1:
        groups = 0
        notes.append("distinct group count unavailable (unhashable values)")
    return {"duplicate_rows": dup_beyond_first,
            "rows_in_duplicate_groups": rows_in_groups,
            "duplicate_row_groups": groups,
            "rows": int(len(df)),
            "notes": notes}
