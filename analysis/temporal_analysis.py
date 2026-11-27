"""Temporal analysis: how file creation/modification activity is distributed."""
from __future__ import annotations

import pandas as pd


def creation_by_year(files: pd.DataFrame) -> pd.DataFrame:
    if files.empty or "creation_year" not in files:
        return pd.DataFrame(columns=["creation_year", "file_count"])
    d = (files.dropna(subset=["creation_year"])
         .groupby("creation_year").size().reset_index(name="file_count"))
    d["creation_year"] = d["creation_year"].astype(int)
    return d.sort_values("creation_year")


def creation_by_month(files: pd.DataFrame) -> pd.DataFrame:
    if files.empty or "creation_year_month" not in files:
        return pd.DataFrame(columns=["creation_year_month", "file_count"])
    d = (files.dropna(subset=["creation_year_month"])
         .groupby("creation_year_month").size()
         .reset_index(name="file_count"))
    return d.sort_values("creation_year_month")


def modification_by_year(files: pd.DataFrame) -> pd.DataFrame:
    if files.empty or "modified_year" not in files:
        return pd.DataFrame(columns=["modified_year", "file_count"])
    d = (files.dropna(subset=["modified_year"])
         .groupby("modified_year").size().reset_index(name="file_count"))
    d["modified_year"] = d["modified_year"].astype(int)
    return d.sort_values("modified_year")


def modification_by_month(files: pd.DataFrame) -> pd.DataFrame:
    if files.empty or "modified_year_month" not in files:
        return pd.DataFrame(columns=["modified_year_month", "file_count"])
    d = (files.dropna(subset=["modified_year_month"])
         .groupby("modified_year_month").size()
         .reset_index(name="file_count"))
    return d.sort_values("modified_year_month")


def creation_vs_modification(files: pd.DataFrame) -> pd.DataFrame:
    """Yearly comparison of creation-dated vs modification-dated files."""
    if files.empty:
        return pd.DataFrame(columns=["year", "created", "modified"])
    c = creation_by_year(files).rename(
        columns={"creation_year": "year", "file_count": "created"})
    m = modification_by_year(files).rename(
        columns={"modified_year": "year", "file_count": "modified"})
    out = pd.merge(c, m, on="year", how="outer").fillna(0)
    for col in ("created", "modified"):
        out[col] = out[col].astype(int)
    return out.sort_values("year")


def summary(files: pd.DataFrame) -> dict:
    if files.empty:
        return {}
    cy = creation_by_year(files)
    my = modification_by_year(files)
    return {
        "creation_year_min": int(cy["creation_year"].min()) if not cy.empty else None,
        "creation_year_max": int(cy["creation_year"].max()) if not cy.empty else None,
        "peak_creation_year": int(cy.loc[cy["file_count"].idxmax(), "creation_year"])
        if not cy.empty else None,
        "peak_creation_year_count": int(cy["file_count"].max()) if not cy.empty else 0,
        "peak_modification_year": int(my.loc[my["file_count"].idxmax(), "modified_year"])
        if not my.empty else None,
        "peak_modification_year_count": int(my["file_count"].max())
        if not my.empty else 0,
        "files_with_creation_year": int(cy["file_count"].sum()) if not cy.empty else 0,
        "files_with_modified_year": int(my["file_count"].sum()) if not my.empty else 0,
    }
