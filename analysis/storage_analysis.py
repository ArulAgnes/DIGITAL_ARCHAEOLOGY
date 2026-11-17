"""Storage analysis: where bytes accumulate."""
from __future__ import annotations

import pandas as pd


def total_storage(files: pd.DataFrame) -> int:
    if files.empty:
        return 0
    return int(files["file_size_bytes"].sum())


def storage_by_root(files: pd.DataFrame) -> pd.DataFrame:
    if files.empty:
        return pd.DataFrame(columns=["root_folder", "file_count", "total_size_bytes", "total_size_mb"])
    d = (files.groupby("root_folder")
         .agg(file_count=("file_id", "count") if "file_id" in files
              else ("file_size_bytes", "size"),
              total_size_bytes=("file_size_bytes", "sum"))
         .reset_index())
    d["total_size_mb"] = (d["total_size_bytes"] / (1024 * 1024)).round(3)
    d["total_size_gb"] = (d["total_size_bytes"] / (1024 ** 3)).round(4)
    return d.sort_values("total_size_bytes", ascending=False)


def storage_by_extension(files: pd.DataFrame) -> pd.DataFrame:
    if files.empty:
        return pd.DataFrame(columns=["file_extension", "file_count", "total_size_bytes"])
    d = (files.groupby("file_extension")
         .agg(file_count=("file_size_bytes", "size"),
              total_size_bytes=("file_size_bytes", "sum"),
              avg_size_bytes=("file_size_bytes", "mean"))
         .reset_index())
    d["total_size_mb"] = (d["total_size_bytes"] / (1024 * 1024)).round(3)
    total = d["total_size_bytes"].sum()
    d["storage_pct"] = (d["total_size_bytes"] / total * 100).round(3) if total else 0
    return d.sort_values("total_size_bytes", ascending=False)


def storage_by_category(files: pd.DataFrame) -> pd.DataFrame:
    if files.empty:
        return pd.DataFrame(columns=["file_category", "file_count", "total_size_bytes"])
    d = (files.groupby("file_category")
         .agg(file_count=("file_size_bytes", "size"),
              total_size_bytes=("file_size_bytes", "sum"))
         .reset_index())
    d["total_size_mb"] = (d["total_size_bytes"] / (1024 * 1024)).round(3)
    total = d["total_size_bytes"].sum()
    d["storage_pct"] = (d["total_size_bytes"] / total * 100).round(3) if total else 0
    return d.sort_values("total_size_bytes", ascending=False)


def largest_files(files: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    if files.empty:
        return files
    cols = ["file_name", "file_extension", "file_size_bytes", "file_size_mb",
            "full_file_path", "file_category"]
    cols = [c for c in cols if c in files.columns]
    return files.nlargest(n, "file_size_bytes")[cols]


def size_stats(files: pd.DataFrame) -> dict:
    if files.empty:
        return {}
    s = files["file_size_bytes"]
    return {
        "total_bytes": int(s.sum()),
        "mean_bytes": float(s.mean()),
        "median_bytes": float(s.median()),
        "max_bytes": int(s.max()),
        "min_bytes": int(s.min()),
        "std_bytes": float(s.std()) if len(s) > 1 else 0.0,
    }


def storage_by_folder(folders: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    if folders.empty:
        return folders
    cols = ["folder_path", "folder_name", "folder_depth",
            "recursive_file_count", "total_size_bytes", "total_size_mb"]
    cols = [c for c in cols if c in folders.columns]
    return folders.nlargest(n, "total_size_bytes")[cols]
