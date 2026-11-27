"""File-type distribution analysis."""
from __future__ import annotations

import pandas as pd


def extension_counts(files: pd.DataFrame) -> pd.DataFrame:
    if files.empty:
        return pd.DataFrame(columns=["file_extension", "file_count", "count_pct"])
    d = files["file_extension"].replace("", "(no extension)").value_counts()
    out = d.rename_axis("file_extension").reset_index(name="file_count")
    total = out["file_count"].sum()
    out["count_pct"] = (out["file_count"] / total * 100).round(3)
    return out


def extension_storage(files: pd.DataFrame) -> pd.DataFrame:
    if files.empty:
        return pd.DataFrame(columns=["file_extension", "total_size_bytes"])
    d = (files.groupby("file_extension")
         .agg(file_count=("file_size_bytes", "size"),
              total_size_bytes=("file_size_bytes", "sum"),
              avg_size_bytes=("file_size_bytes", "mean"))
         .reset_index())
    d["total_size_mb"] = (d["total_size_bytes"] / (1024 * 1024)).round(3)
    total = d["total_size_bytes"].sum()
    d["storage_pct"] = (d["total_size_bytes"] / total * 100).round(3) if total else 0
    return d.sort_values("total_size_bytes", ascending=False)


def category_distribution(files: pd.DataFrame) -> pd.DataFrame:
    if files.empty:
        return pd.DataFrame(columns=["file_category", "file_count", "total_size_bytes"])
    d = (files.groupby("file_category")
         .agg(file_count=("file_size_bytes", "size"),
              total_size_bytes=("file_size_bytes", "sum"))
         .reset_index())
    total_c = d["file_count"].sum()
    d["count_pct"] = (d["file_count"] / total_c * 100).round(3) if total_c else 0
    total_s = d["total_size_bytes"].sum()
    d["storage_pct"] = (d["total_size_bytes"] / total_s * 100).round(3) if total_s else 0
    d["total_size_mb"] = (d["total_size_bytes"] / (1024 * 1024)).round(3)
    return d.sort_values("file_count", ascending=False)


def size_distribution(files: pd.DataFrame) -> pd.DataFrame:
    """Histogram bins of file sizes in MB."""
    if files.empty:
        return pd.DataFrame(columns=["size_bucket", "file_count"])
    mb = files["file_size_bytes"] / (1024 * 1024)
    bins = [0, 0.01, 0.1, 1, 10, 100, 1000, 10_000, 1e9]
    labels = ["<0.01 MB", "0.01-0.1 MB", "0.1-1 MB", "1-10 MB",
              "10-100 MB", "100MB-1GB", "1-10 GB", ">10 GB"]
    cats = pd.cut(mb, bins=bins, labels=labels, right=False)
    d = cats.value_counts().reindex(labels).fillna(0).astype(int)
    out = d.rename_axis("size_bucket").reset_index(name="file_count")
    return out


def summary(files: pd.DataFrame) -> dict:
    if files.empty:
        return {}
    ec = extension_counts(files)
    es = extension_storage(files)
    return {
        "unique_extensions": int(files["file_extension"].nunique()),
        "unique_categories": int(files["file_category"].nunique()),
        "most_common_extension": str(ec.iloc[0]["file_extension"]) if not ec.empty else None,
        "most_common_extension_count": int(ec.iloc[0]["file_count"]) if not ec.empty else 0,
        "largest_storage_extension": str(es.iloc[0]["file_extension"]) if not es.empty else None,
        "largest_storage_extension_bytes": int(es.iloc[0]["total_size_bytes"]) if not es.empty else 0,
    }
