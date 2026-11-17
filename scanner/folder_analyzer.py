"""Folder-level structural analysis helpers (built from scanned metadata)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

import config


def load_folders() -> pd.DataFrame:
    if not config.FOLDERS_CSV.exists():
        return pd.DataFrame()
    return pd.read_csv(config.FOLDERS_CSV, encoding="utf-8-sig")


def load_files() -> pd.DataFrame:
    if not config.FILES_CSV.exists():
        return pd.DataFrame()
    return pd.read_csv(config.FILES_CSV, encoding="utf-8-sig")


def folder_summary(folders: pd.DataFrame) -> dict:
    if folders.empty:
        return {}
    return {
        "total_folders": int(len(folders)),
        "max_depth": int(folders["folder_depth"].max()),
        "avg_depth": float(folders["folder_depth"].mean()),
        "empty_folders": int((folders["direct_file_count"] == 0).sum()),
        "max_files_in_folder": int(folders["direct_file_count"].max()),
        "total_recursive_files": int(
            folders.loc[folders["folder_depth"] == 1,
                        "recursive_file_count"].sum())
        if (folders["folder_depth"] == 1).any() else int(
            folders["direct_file_count"].sum()),
    }


def deep_folders(folders: pd.DataFrame, threshold: int = 8) -> pd.DataFrame:
    if folders.empty:
        return folders
    return folders[folders["folder_depth"] >= threshold].copy()


def top_folders(folders: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    if folders.empty:
        return folders
    return folders.nlargest(n, "total_size_bytes")[
        ["folder_path", "folder_name", "folder_depth",
         "recursive_file_count", "total_size_bytes", "total_size_mb"]]


def files_per_folder(folders: pd.DataFrame) -> pd.DataFrame:
    if folders.empty:
        return folders
    out = folders[["folder_path", "folder_depth", "direct_file_count",
                   "recursive_file_count", "total_size_bytes"]].copy()
    return out.sort_values("direct_file_count", ascending=False)
