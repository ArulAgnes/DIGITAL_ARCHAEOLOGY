"""Shared data loading and filtering for all dashboard pages."""
from __future__ import annotations

import json
import math
from functools import lru_cache

import pandas as pd
import streamlit as st

import config

_FILE_NUMERIC = [
    "file_id", "file_size_bytes", "file_size_kb", "file_size_mb",
    "folder_depth", "filename_length", "creation_year", "creation_month",
    "modified_year", "modified_month", "computed_depth",
]
_FILE_FILL_ZERO = ["file_size_bytes", "file_size_kb", "file_size_mb"]
_FILE_BOOL = [
    "hidden_file_status", "read_only_status", "contains_version_indicator",
    "contains_final_keyword", "contains_date_indicator",
    "contains_copy_indicator", "has_underscore", "has_space", "has_hyphen",
    "size_invalid", "size_zero", "is_duplicate_record",
    "creation_date_invalid", "modified_date_invalid", "depth_mismatch",
]
_FOLDER_NUMERIC = [
    "folder_depth", "direct_file_count", "recursive_file_count",
    "direct_subfolder_count", "recursive_folder_count",
    "total_size_bytes", "total_size_mb",
]
_FOLDER_FILL_ZERO = ["total_size_bytes", "total_size_mb"]


def _coerce_bool(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series
    return series.astype(str).str.strip().str.lower().isin(
        ("true", "1", "1.0"))


def _coerce_frame(df: pd.DataFrame, numeric: list[str],
                  fill_zero: list[str], bools: list[str]) -> pd.DataFrame:
    """Force stable dtypes no matter how the CSV was written.

    Numeric columns can arrive as text (for example while another process is
    rewriting the file), which used to break arithmetic on the pages.
    """
    if df.empty:
        return df
    for c in numeric:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in fill_zero:
        if c in df.columns:
            df[c] = df[c].fillna(0)
    for c in bools:
        if c in df.columns:
            df[c] = _coerce_bool(df[c])
    return df


@st.cache_data(show_spinner=False, ttl=300)
def load_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    """Load processed files, folders, errors and findings (cached)."""
    files = (pd.read_csv(config.PROCESSED_FILES_CSV, encoding="utf-8-sig")
             if config.PROCESSED_FILES_CSV.exists() else
             (pd.read_csv(config.FILES_CSV, encoding="utf-8-sig")
              if config.FILES_CSV.exists() else pd.DataFrame()))
    folders = (pd.read_csv(config.PROCESSED_FOLDERS_CSV, encoding="utf-8-sig")
               if config.PROCESSED_FOLDERS_CSV.exists() else
               (pd.read_csv(config.FOLDERS_CSV, encoding="utf-8-sig")
                if config.FOLDERS_CSV.exists() else pd.DataFrame()))
    errors = (pd.read_csv(config.ERRORS_CSV, encoding="utf-8-sig")
              if config.ERRORS_CSV.exists() else pd.DataFrame())
    files = _coerce_frame(files, _FILE_NUMERIC, _FILE_FILL_ZERO, _FILE_BOOL)
    folders = _coerce_frame(folders, _FOLDER_NUMERIC, _FOLDER_FILL_ZERO, [])
    findings = {}
    if config.FINDINGS_JSON.exists():
        try:
            findings = json.loads(config.FINDINGS_JSON.read_text(encoding="utf-8"))
        except Exception:
            findings = {}
    return files, folders, errors, findings


def ensure_data() -> bool:
    if config.PROCESSED_FILES_CSV.exists() or config.FILES_CSV.exists():
        return True
    st.error("No scan data found. Run `python pipeline.py --scan` first.")
    return False


def apply_filters(df: pd.DataFrame, f: dict) -> pd.DataFrame:
    """Apply the global sidebar filters to a file-level DataFrame."""
    if df.empty:
        return df
    out = df

    if f.get("years") and "creation_year" in out:
        out = out[out["creation_year"].isin(f["years"])]

    if f.get("months") and "creation_month" in out:
        out = out[out["creation_month"].isin(f["months"])]

    if f.get("extensions") and "file_extension" in out:
        out = out[out["file_extension"].isin(f["extensions"])]

    if f.get("categories") and "file_category" in out:
        out = out[out["file_category"].isin(f["categories"])]

    if f.get("roots") and "root_folder" in out:
        out = out[out["root_folder"].isin(f["roots"])]

    if f.get("size_min") is not None and "file_size_bytes" in out:
        out = out[out["file_size_bytes"] >= f["size_min"] * 1024 * 1024]
    if f.get("size_max") is not None and "file_size_bytes" in out:
        out = out[out["file_size_bytes"] <= f["size_max"] * 1024 * 1024]

    return out


def human_size(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or u == "TB":
            return f"{n:,.2f} {u}"
        n /= 1024
    return f"{n:,.2f} TB"


def sidebar_filters(files: pd.DataFrame) -> dict:
    """Build the global filter set in the sidebar and return the selection."""
    st.sidebar.header("Filters")

    if files.empty:
        st.sidebar.warning("No data loaded.")
        return {}

    years = sorted([int(y) for y in files["creation_year"].dropna().unique()]) \
        if "creation_year" in files else []
    months = sorted([int(m) for m in files["creation_month"].dropna().unique()]) \
        if "creation_month" in files else []
    extensions = sorted(files["file_extension"].fillna("").unique().tolist())
    categories = sorted(files["file_category"].dropna().unique().tolist())
    roots = sorted(files["root_folder"].dropna().unique().tolist())

    sel_years = st.sidebar.multiselect("Year", years, default=years)
    sel_months = st.sidebar.multiselect(
        "Month", months, default=months,
        format_func=lambda m: f"{int(m):02d}")
    sel_ext = st.sidebar.multiselect("Extension", extensions, default=extensions)
    sel_cat = st.sidebar.multiselect("Category", categories, default=categories)
    sel_root = st.sidebar.multiselect("Root folder", roots, default=roots)

    max_mb = 1.0
    if "file_size_mb" in files.columns:
        try:
            candidate = float(files["file_size_mb"].max())
        except (TypeError, ValueError):
            candidate = float("nan")
        if math.isfinite(candidate) and candidate > 0:
            max_mb = max(round(candidate, 2), 0.1)
    size_min, size_max = st.sidebar.slider(
        "File size (MB)", min_value=0.0,
        max_value=max_mb, value=(0.0, max_mb),
        step=0.1)

    reset = st.sidebar.button("Reset filters")
    if reset:
        st.rerun()

    st.sidebar.markdown("---")
    st.sidebar.caption(
        f"Rows matching: {len(apply_filters(files, _sel(sel_years, sel_months, sel_ext, sel_cat, sel_root, size_min, size_max))):,}"
        f" / {len(files):,}")

    return _sel(sel_years, sel_months, sel_ext, sel_cat, sel_root,
                size_min, size_max)


def _sel(years, months, ext, cat, roots, smin, smax) -> dict:
    return {
        "years": years, "months": months, "extensions": ext,
        "categories": cat, "roots": roots,
        "size_min": smin, "size_max": smax,
    }
