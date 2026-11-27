"""Builders converting raw inspection results into DatasetMetadata."""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from core.dataset import ColumnMetadata, DatasetMetadata


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def columns_from_dataframe(df: pd.DataFrame,
                           missing_counts: dict | None = None
                           ) -> list[ColumnMetadata]:
    missing_counts = missing_counts or {}
    cols: list[ColumnMetadata] = []
    for name in df.columns:
        series = df[name]
        missing = missing_counts.get(str(name))
        if missing is None:
            missing = int(series.isna().sum())
        try:
            unique = int(series.nunique(dropna=True))
        except TypeError:
            unique = None
        cols.append(ColumnMetadata(
            name=str(name),
            dtype=str(series.dtype),
            nullable=bool(series.isna().any()) or missing > 0,
            missing_count=int(missing),
            unique_count=unique,
        ))
    return cols


def empty_metadata(warning: str = "") -> DatasetMetadata:
    md = DatasetMetadata(collected_at=now_iso())
    if warning:
        md.warnings.append(warning)
    return md


def build_metadata(row_count: int | None,
                   columns: list[ColumnMetadata],
                   file_size_bytes: int | None,
                   source_info: dict,
                   warnings: list[str] | None = None) -> DatasetMetadata:
    return DatasetMetadata(
        row_count=row_count,
        column_count=len(columns),
        columns=columns,
        file_size_bytes=file_size_bytes,
        source_info=source_info,
        warnings=list(warnings or []),
        collected_at=now_iso(),
    )
