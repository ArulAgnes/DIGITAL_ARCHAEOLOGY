"""Data cleaning and feature engineering.

Raw collected values are never overwritten with invented data. Cleaning adds
*new* columns and flags; original columns are preserved.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

import config
from scanner.metadata_extractor import classify_file, normalize_extension

log = logging.getLogger("da.cleaning")

EXPECTED_FILE_COLS = [
    "file_id", "file_name", "file_extension", "file_size_bytes",
    "creation_timestamp", "creation_date", "modified_timestamp",
    "modified_date", "full_file_path", "parent_folder", "root_folder",
    "folder_depth", "file_category", "naming_pattern",
]


def load_raw() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    files = (pd.read_csv(config.FILES_CSV, encoding="utf-8-sig")
             if config.FILES_CSV.exists() else pd.DataFrame())
    folders = (pd.read_csv(config.FOLDERS_CSV, encoding="utf-8-sig")
               if config.FOLDERS_CSV.exists() else pd.DataFrame())
    errors = (pd.read_csv(config.ERRORS_CSV, encoding="utf-8-sig")
              if config.ERRORS_CSV.exists() else pd.DataFrame())
    return files, folders, errors


def clean_files(files: pd.DataFrame) -> pd.DataFrame:
    if files.empty:
        return files
    df = files.copy()
    n0 = len(df)

    # --- normalisation -------------------------------------------------
    df["file_extension"] = df["file_extension"].fillna("").astype(str).str.lower()
    df["file_extension"] = df["file_extension"].apply(
        lambda e: e if e == "" or e.startswith(".") else "." + e)

    df["full_file_path"] = (df["full_file_path"].astype(str)
                            .str.replace("/", "\\", regex=False))
    if "folder_path" not in df.columns:
        df["folder_path"] = df.get("parent_folder", "")
    df["folder_path"] = (df["folder_path"].astype(str)
                         .str.replace("/", "\\", regex=False))

    # --- type coercion -------------------------------------------------
    df["file_size_bytes"] = pd.to_numeric(df["file_size_bytes"], errors="coerce")
    df["folder_depth"] = pd.to_numeric(df["folder_depth"], errors="coerce")

    # --- invalid values flagged, never invented ------------------------
    df["size_invalid"] = df["file_size_bytes"].isna() | (df["file_size_bytes"] < 0)
    df["size_zero"] = df["file_size_bytes"] == 0

    for col in ("creation_date", "modified_date", "accessed_date"):
        if col in df.columns:
            parsed = pd.to_datetime(df[col], errors="coerce")
            df[col + "_parsed"] = parsed
            df[col + "_invalid"] = df[col].notna() & parsed.isna()

    # --- duplicate record check (exact path duplicates) ----------------
    df["is_duplicate_record"] = df.duplicated(subset=["full_file_path"],
                                              keep="first")

    # --- recompute derived size columns (consistent units) -------------
    df["file_size_kb"] = (df["file_size_bytes"] / 1024).round(3)
    df["file_size_mb"] = (df["file_size_bytes"] / (1024 * 1024)).round(4)

    # --- date features -------------------------------------------------
    cd = df.get("creation_date_parsed")
    md = df.get("modified_date_parsed")
    df["creation_year"] = cd.dt.year if cd is not None else np.nan
    df["creation_month"] = cd.dt.month if cd is not None else np.nan
    df["creation_year_month"] = (cd.dt.strftime("%Y-%m")
                                 if cd is not None else None)
    df["modified_year"] = md.dt.year if md is not None else np.nan
    df["modified_month"] = md.dt.month if md is not None else np.nan
    df["modified_year_month"] = (md.dt.strftime("%Y-%m")
                                 if md is not None else None)

    # --- category re-map from normalised extension ---------------------
    df["file_category"] = [classify_file(n) for n in df["file_name"].astype(str)]

    # --- path depth sanity --------------------------------------------
    # depth relative to the scan root = separators in the full path minus
    # separators in the root path itself (e.g. "D:\" contributes 1)
    root = str(config.SCAN_ROOT).rstrip("\\") + "\\"
    root_seps = root.count("\\")
    df["computed_depth"] = (df["full_file_path"].str.count("\\\\")
                            - root_seps)
    df["computed_depth"] = df["computed_depth"].clip(lower=0)
    df["depth_mismatch"] = (
        df["folder_depth"].notna()
        & (df["folder_depth"] != df["computed_depth"])
    )

    log.info("clean_files: %d -> %d rows (%d exact duplicate records removed)",
             n0, len(df), n0 - len(df))
    return df


def clean_folders(folders: pd.DataFrame) -> pd.DataFrame:
    if folders.empty:
        return folders
    df = folders.copy()
    df["folder_path"] = (df["folder_path"].astype(str)
                         .str.replace("/", "\\", regex=False))
    for c in ("direct_file_count", "recursive_file_count",
              "direct_subfolder_count", "recursive_folder_count",
              "total_size_bytes", "folder_depth"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["is_empty_folder"] = df["direct_file_count"] == 0
    df["is_deep_folder"] = df["folder_depth"] >= 8
    return df


def build_features(files: pd.DataFrame, folders: pd.DataFrame) -> dict:
    """Analytical variables requested by the specification."""
    f: dict = {}
    if files.empty:
        return f

    valid = files[~files["is_duplicate_record"]] if "is_duplicate_record" in files else files

    f["files_per_month"] = (valid["creation_year_month"].value_counts()
                            .sort_index() if "creation_year_month" in valid
                            else pd.Series(dtype=int))
    f["files_per_year"] = (valid["creation_year"].value_counts().sort_index()
                           if "creation_year" in valid else pd.Series(dtype=int))
    f["storage_per_extension"] = (valid.groupby("file_extension")["file_size_bytes"]
                                  .sum().sort_values(ascending=False))
    f["files_per_extension"] = valid["file_extension"].value_counts()
    f["storage_per_category"] = (valid.groupby("file_category")["file_size_bytes"]
                                 .sum().sort_values(ascending=False))
    f["files_per_category"] = valid["file_category"].value_counts()

    if not folders.empty:
        f["storage_per_folder"] = folders.sort_values(
            "total_size_bytes", ascending=False)[
            ["folder_path", "total_size_bytes"]]
        f["files_per_folder"] = folders.sort_values(
            "direct_file_count", ascending=False)[
            ["folder_path", "direct_file_count"]]
        f["average_folder_depth"] = float(folders["folder_depth"].mean())
        f["maximum_folder_depth"] = int(folders["folder_depth"].max())

    f["average_file_size"] = float(valid["file_size_bytes"].mean())
    f["median_file_size"] = float(valid["file_size_bytes"].median())
    f["largest_file_size"] = float(valid["file_size_bytes"].max())
    f["total_storage_bytes"] = int(valid["file_size_bytes"].sum())
    f["total_files"] = int(len(valid))
    return f


def run() -> dict:
    files, folders, errors = load_raw()
    files_c = clean_files(files)
    folders_c = clean_folders(folders)

    files_c.to_csv(config.PROCESSED_FILES_CSV, index=False, encoding="utf-8-sig")
    folders_c.to_csv(config.PROCESSED_FOLDERS_CSV, index=False,
                     encoding="utf-8-sig")

    features = build_features(files_c, folders_c)

    report = {
        "raw_file_rows": int(len(files)),
        "processed_file_rows": int(len(files_c)),
        "raw_folder_rows": int(len(folders)),
        "error_rows": int(len(errors)),
        "missing_size_rows": int(files_c["size_invalid"].sum())
        if not files_c.empty else 0,
        "zero_size_rows": int(files_c["size_zero"].sum())
        if not files_c.empty else 0,
        "duplicate_record_rows": int(files_c["is_duplicate_record"].sum())
        if not files_c.empty and "is_duplicate_record" in files_c else 0,
        "invalid_creation_dates": int(files_c["creation_date_invalid"].sum())
        if not files_c.empty and "creation_date_invalid" in files_c else 0,
        "invalid_modified_dates": int(files_c["modified_date_invalid"].sum())
        if not files_c.empty and "modified_date_invalid" in files_c else 0,
        "depth_mismatches": int(files_c["depth_mismatch"].sum())
        if not files_c.empty else 0,
        "total_storage_bytes": features.get("total_storage_bytes", 0),
        "total_files": features.get("total_files", 0),
    }
    log.info("Cleaning report: %s", report)
    return report
