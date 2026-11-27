"""Naming pattern analysis using the documented, transparent rules.

Rules (all applied to the file name stem, case-insensitive):

R1  version indicator   : ``v1``, ``v2``, ``version 3`` as a standalone token
R2  final indicator     : token ``final`` or ``final2``
R3  latest indicator    : token ``latest``
R4  date indicator      : ISO-like 2026-07-27, 27-07-2026, or a bare 4-digit
                          year token 19xx/20xx
R5  copy indicator      : ``(1)``, ``copy``, ``duplicate``, ``backup``
R6  separators          : underscore / space / hyphen present
R7  capitalisation      : lower, upper, title, mixed  (mixed = inconsistent)
R8  very long           : stem longer than 80 characters
R9  repeated whitespace or 3+ consecutive separators

A file containing "final" is NOT automatically considered wrong - it is only
counted as an *indicator* of versioning behaviour.
"""
from __future__ import annotations

import pandas as pd

import config
from scanner.metadata_extractor import naming_inconsistencies

RULES = {
    "R1": "version indicator: v1/v2/version 3 as standalone token",
    "R2": "final indicator: token 'final' or 'finalN'",
    "R3": "latest indicator: token 'latest'",
    "R4": "date indicator: 2026-07-27 / 27-07-2026 / bare 19xx-20xx year",
    "R5": "copy indicator: (1), copy, duplicate, backup",
    "R6": "separator usage: underscore / space / hyphen",
    "R7": "capitalisation: lower | upper | title | mixed (mixed = inconsistent)",
    "R8": "very long: stem > 80 characters",
    "R9": "repeated whitespace or 3+ consecutive separators",
}


def build_naming_table(files: pd.DataFrame) -> pd.DataFrame:
    if files.empty:
        return pd.DataFrame()
    cols = [c for c in [
        "file_id", "file_name", "file_extension", "full_file_path",
        "root_folder", "file_category", "file_size_bytes",
        "naming_pattern", "contains_version_indicator",
        "contains_final_keyword", "contains_date_indicator",
        "contains_copy_indicator", "has_underscore", "has_space",
        "has_hyphen", "capitalization_style", "filename_length",
        "creation_date", "modified_date",
    ] if c in files.columns]
    df = files[cols].copy()
    df["naming_issues"] = df.apply(
        lambda r: ";".join(naming_inconsistencies(r)), axis=1)
    df["inconsistency_count"] = (
        df["naming_issues"].apply(lambda s: 0 if not s else len(s.split(";"))))
    df["is_inconsistent"] = df["inconsistency_count"] > 0
    return df


def pattern_distribution(naming: pd.DataFrame) -> pd.DataFrame:
    if naming.empty:
        return pd.DataFrame(columns=["naming_pattern", "file_count"])
    d = (naming["naming_pattern"].value_counts()
         .rename_axis("naming_pattern").reset_index(name="file_count"))
    total = d["file_count"].sum()
    d["count_pct"] = (d["file_count"] / total * 100).round(3)
    return d


def indicator_summary(naming: pd.DataFrame) -> dict:
    if naming.empty:
        return {}
    n = len(naming)
    def pct(col):
        return round(float(naming[col].sum()) / n * 100, 3) if n else 0.0
    return {
        "total_files_analysed": n,
        "version_indicator_count": int(naming["contains_version_indicator"].sum()),
        "version_indicator_pct": pct("contains_version_indicator"),
        "final_or_latest_count": int(naming["contains_final_keyword"].sum()),
        "final_or_latest_pct": pct("contains_final_keyword"),
        "date_indicator_count": int(naming["contains_date_indicator"].sum()),
        "date_indicator_pct": pct("contains_date_indicator"),
        "copy_indicator_count": int(naming["contains_copy_indicator"].sum()),
        "copy_indicator_pct": pct("contains_copy_indicator"),
        "underscore_count": int(naming["has_underscore"].sum()),
        "space_count": int(naming["has_space"].sum()),
        "hyphen_count": int(naming["has_hyphen"].sum()),
        "mixed_capitalization_count": int(
            (naming["capitalization_style"] == "mixed").sum()),
        "inconsistent_files": int(naming["is_inconsistent"].sum()),
        "inconsistent_pct": round(float(naming["is_inconsistent"].sum()) / n * 100, 3)
        if n else 0.0,
        "avg_filename_length": round(float(naming["filename_length"].mean()), 2),
        "max_filename_length": int(naming["filename_length"].max()),
        "rules": RULES,
    }


def issue_counts(naming: pd.DataFrame) -> pd.DataFrame:
    if naming.empty:
        return pd.DataFrame(columns=["issue", "file_count"])
    from collections import Counter
    c: Counter = Counter()
    for s in naming["naming_issues"]:
        if s:
            for issue in str(s).split(";"):
                c[issue] += 1
    out = pd.DataFrame(sorted(c.items(), key=lambda kv: -kv[1]),
                       columns=["issue", "file_count"])
    return out


def repeated_patterns(naming: pd.DataFrame, top: int = 25) -> pd.DataFrame:
    """Most repeated filename patterns (case-insensitive, extension stripped)."""
    if naming.empty:
        return pd.DataFrame(columns=["name_pattern", "file_count"])
    stems = (naming["file_name"].astype(str)
             .str.rsplit(".", n=1).str[0].str.lower().str.strip())
    d = stems.value_counts().rename_axis("name_pattern").reset_index(
        name="file_count")
    return d[d["file_count"] > 1].head(top)


def run(files: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    naming = build_naming_table(files)
    naming.to_csv(config.NAMING_CSV, index=False, encoding="utf-8-sig")
    stats = indicator_summary(naming)
    return naming, stats
