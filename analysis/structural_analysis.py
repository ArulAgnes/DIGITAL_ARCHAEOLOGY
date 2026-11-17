"""Folder structure analysis."""
from __future__ import annotations

import pandas as pd


def depth_distribution(folders: pd.DataFrame) -> pd.DataFrame:
    if folders.empty:
        return pd.DataFrame(columns=["folder_depth", "folder_count"])
    d = (folders.groupby("folder_depth").size()
         .rename("folder_count").reset_index())
    return d.sort_values("folder_depth")


def files_per_folder_dist(folders: pd.DataFrame) -> pd.DataFrame:
    if folders.empty:
        return pd.DataFrame(columns=["file_count_bucket", "folder_count"])
    n = folders["direct_file_count"]
    bins = [-1, 0, 1, 5, 20, 100, 1000, 100_000]
    labels = ["0", "1", "2-5", "6-20", "21-100", "101-1000", ">1000"]
    cats = pd.cut(n, bins=bins, labels=labels)
    d = cats.value_counts().reindex(labels).fillna(0).astype(int)
    return d.rename_axis("file_count_bucket").reset_index(name="folder_count")


def empty_folders(folders: pd.DataFrame) -> pd.DataFrame:
    if folders.empty:
        return folders
    return folders[folders["direct_file_count"] == 0].sort_values(
        "folder_depth", ascending=False)


def deeply_nested(folders: pd.DataFrame, threshold: int = 8) -> pd.DataFrame:
    if folders.empty:
        return folders
    return folders[folders["folder_depth"] >= threshold].sort_values(
        "folder_depth", ascending=False)


def root_folders(folders: pd.DataFrame) -> pd.DataFrame:
    if folders.empty:
        return folders
    return folders[folders["folder_depth"] == 1].sort_values(
        "recursive_file_count", ascending=False)


def treemap_data(folders: pd.DataFrame, files: pd.DataFrame) -> pd.DataFrame:
    """Combined data for hierarchical treemaps (folder size + file size)."""
    if folders.empty:
        return folders
    cols = [c for c in ["folder_path", "folder_name", "folder_depth",
                        "recursive_file_count", "total_size_bytes"]
            if c in folders.columns]
    return folders[cols].copy()


def summary(folders: pd.DataFrame) -> dict:
    if folders.empty:
        return {}
    return {
        "total_folders": int(len(folders)),
        "max_depth": int(folders["folder_depth"].max()),
        "avg_depth": round(float(folders["folder_depth"].mean()), 3),
        "median_depth": float(folders["folder_depth"].median()),
        "empty_folders": int((folders["direct_file_count"] == 0).sum()),
        "deep_folders_ge8": int((folders["folder_depth"] >= 8).sum()),
        "max_direct_files": int(folders["direct_file_count"].max()),
        "avg_files_per_folder": round(float(folders["direct_file_count"].mean()), 3),
        "median_files_per_folder": float(folders["direct_file_count"].median()),
        "root_folder_count": int((folders["folder_depth"] == 1).sum()),
    }


def project_groups(files: pd.DataFrame, folders: pd.DataFrame) -> pd.DataFrame:
    """Top-level groupings (inferred, NOT verified as 'projects')."""
    if files.empty:
        return pd.DataFrame()
    g = files.groupby("root_folder").agg(
        total_files=("file_size_bytes", "size"),
        total_storage_bytes=("file_size_bytes", "sum"),
        unique_extensions=("file_extension", "nunique"),
        categories=("file_category", "nunique"),
    ).reset_index()
    g["total_storage_mb"] = (g["total_storage_bytes"] / (1024 * 1024)).round(3)
    g["total_storage_gb"] = (g["total_storage_bytes"] / (1024 ** 3)).round(4)

    cd = pd.to_datetime(files.get("creation_date"), errors="coerce")
    md = pd.to_datetime(files.get("modified_date"), errors="coerce")
    tmp = pd.DataFrame({"root": files["root_folder"],
                        "c": cd, "m": md})
    rng = tmp.groupby("root").agg(
        creation_min=("c", "min"), creation_max=("c", "max"),
        modified_min=("m", "min"), modified_max=("m", "max")).reset_index()
    g = g.merge(rng, left_on="root_folder", right_on="root", how="left")
    g = g.drop(columns=["root"], errors="ignore")

    if not folders.empty and "folder_depth" in folders:
        fc = folders[folders["folder_depth"] == 1][
            ["folder_name", "recursive_file_count"]].rename(
            columns={"folder_name": "root_folder",
                     "recursive_file_count": "folders_recursive_files"})
        g = g.merge(fc, on="root_folder", how="left")

    top_ext = (files.groupby(["root_folder", "file_extension"]).size()
               .reset_index(name="n")
               .sort_values("n", ascending=False)
               .drop_duplicates("root_folder")
               .rename(columns={"file_extension": "top_extension",
                                "n": "top_extension_count"}))
    g = g.merge(top_ext, on="root_folder", how="left")
    g["grouping_label"] = "inferred_top_level_grouping_not_verified_project"
    return g.sort_values("total_storage_bytes", ascending=False)
