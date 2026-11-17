"""Bounded data loading for visualizations (Phase 7).

Every loader here is **read-only** and **bounded**: CSV and SQLite
previews read at most ``VisualizationConfig.preview_rows`` rows, SQLite
opens the database in read-only mode, and truncation is always reported
in the returned metadata — never silent.

No loader ever scans the whole drive, modifies a source, or returns an
absolute path inside user-facing messages or exported metadata.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

import config
from core.exceptions import PlatformError, redact_path
from ingestion.dataset_registry import DatasetRegistry
from visualization.validation import column_kind
from visualization.visualization_config import VisualizationConfig


@dataclass
class PreparedData:
    """A frame plus explicit provenance and sampling disclosure."""

    frame: pd.DataFrame
    source_kind: str                 # csv | sqlite | files | folders | ...
    source_label: str                # display name, never an absolute path
    meta: dict = field(default_factory=dict)

    @property
    def kinds(self) -> dict[str, str]:
        return {c: column_kind(self.frame[c].dtype)
                for c in self.frame.columns}

    @property
    def sampling(self) -> dict:
        return dict(self.meta.get("sampling") or
                    {"applied": False, "method": "none",
                     "params": {}, "approximate": False})


def registration_info(dataset_id: str) -> dict:
    """Registry row for ``dataset_id`` or a clear error."""
    row = DatasetRegistry().get(dataset_id)
    if row is None:
        raise PlatformError(
            "That dataset registration could not be found. "
            "Refresh the dataset list and try again.",
            detail=f"registry miss {dataset_id}",
        )
    return row


def _read_metadata_json(dataset_id: str, row: dict) -> dict:
    loc = row.get("metadata_location")
    if not loc:
        return {}
    path = Path(loc)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return payload.get("dataset", {}).get("metadata", {}) or {}


def metadata_column_specs(dataset_id: str) -> list[dict]:
    """Column specs (name/dtype/missing/unique) without loading any rows."""
    row = registration_info(dataset_id)
    meta = _read_metadata_json(dataset_id, row)
    specs = []
    for col in meta.get("columns", []) or []:
        if col.get("name") is None:
            continue
        specs.append({
            "name": str(col["name"]),
            "dtype": str(col.get("dtype", "")),
            "missing_count": col.get("missing_count"),
            "unique_count": col.get("unique_count"),
        })
    if not specs:
        # fall back to an empty spec list; caller decides messaging
        return []
    return specs


def _source_path(row: dict, dataset_id: str) -> Path:
    loc = row.get("source_path")
    if not loc:
        raise PlatformError(
            "This registration does not record where its data came from.",
            detail=f"no source path {dataset_id}",
        )
    path = Path(loc)
    if not path.exists():
        raise PlatformError(
            "The registered source is no longer available at its recorded "
            "location, so its rows cannot be previewed.",
            detail=f"missing source {redact_path(loc)}",
        )
    return path


def load_preview(
    dataset_id: str,
    cfg: VisualizationConfig | None = None,
    *,
    max_rows: int | None = None,
) -> PreparedData:
    """Bounded, read-only preview of a registered CSV or SQLite dataset.

    Raises a clear ``PlatformError`` for filesystem registrations (they
    are visualized in the Archaeology section from scan results instead).
    """
    cfg = cfg or VisualizationConfig()
    limit = int(max_rows or cfg.preview_rows)
    row = registration_info(dataset_id)
    kind = str(row.get("source_type") or "")
    label = str(row.get("name") or dataset_id)
    total_rows = row.get("row_count")

    if kind == "filesystem":
        raise PlatformError(
            "This registration is a filesystem scan, not a table. "
            "Use the Archaeology section to visualize it, or register "
            "a CSV/SQLite source for row-level charts.",
            detail="filesystem preview not supported",
        )
    if kind not in ("csv", "sqlite"):
        raise PlatformError(
            f"Row previews are not supported for '{kind}' sources yet.",
            detail=f"unsupported preview kind {kind}",
        )

    path = _source_path(row, dataset_id)
    meta = _read_metadata_json(dataset_id, row)
    source_info = meta.get("source_info", {}) or {}

    if kind == "csv":
        encoding = str(source_info.get("encoding") or "utf-8")
        sep = str(source_info.get("delimiter") or ",")
        try:
            frame = pd.read_csv(path, encoding=encoding, sep=sep,
                                nrows=limit, low_memory=False)
        except UnicodeDecodeError as exc:
            raise PlatformError(
                f"The CSV could not be decoded with its registered "
                f"encoding ({encoding}).",
                detail=f"csv decode error {type(exc).__name__}",
            ) from exc
        except (pd.errors.EmptyDataError, pd.errors.ParserError,
                pd.errors.DtypeWarning) as exc:
            raise PlatformError(
                "The CSV could not be parsed with its registered "
                "delimiter.",
                detail=f"csv parse error {type(exc).__name__}",
            ) from exc
    else:
        table = str(source_info.get("table") or "")
        if not table:
            raise PlatformError(
                "This SQLite registration does not record which table "
                "was registered, so a preview cannot be built.",
                detail=f"no table recorded {dataset_id}",
            )
        frame = _read_sqlite_preview(path, table, limit)

    displayed = int(len(frame))
    try:
        total = int(total_rows) if total_rows is not None else displayed
    except (TypeError, ValueError):
        total = displayed
    truncated = total > displayed
    sampling = {
        "applied": truncated,
        "method": "first_n_rows" if truncated else "none",
        "params": {"max_rows": limit} if truncated else {},
        "approximate": truncated,
    }
    notes = []
    if truncated:
        notes.append(
            f"Preview shows the first {displayed:,} of {total:,} rows "
            f"(row cap: {limit:,}). Aggregations and distributions that "
            "scan the preview may therefore be approximate — the "
            "disclosure is shown next to every chart."
        )
    notes.append("Preview is read-only; the source is never modified.")

    return PreparedData(
        frame=frame,
        source_kind=kind,
        source_label=label,
        meta={
            "total_rows": total,
            "displayed_rows": displayed,
            "sampling": sampling,
            "notes": notes,
            "fingerprint": row.get("source_fingerprint"),
            "truncated": truncated,
        },
    )


def _read_sqlite_preview(path: Path, table: str, limit: int) -> pd.DataFrame:
    uri = "file:" + path.as_posix() + "?mode=ro"
    quoted = '"' + table.replace('"', '""') + '"'
    try:
        with sqlite3.connect(uri, uri=True) as con:
            return pd.read_sql_query(
                f"SELECT * FROM {quoted} LIMIT {int(limit)}", con
            )
    except sqlite3.Error as exc:
        raise PlatformError(
            "The SQLite table could not be read (read-only preview "
            "failed).",
            detail=f"sqlite preview error {type(exc).__name__}",
        ) from exc


def sample_frame(
    frame: pd.DataFrame,
    max_points: int,
    seed: int,
    *,
    what: str = "points",
) -> tuple[pd.DataFrame, dict]:
    """Deterministic random sample when a frame exceeds ``max_points``.

    Thin delegate to :func:`visualization.filters.sample_frame` (the
    single implementation) with an extra ``target`` label for disclosure.
    """
    from visualization.filters import sample_frame as _sample
    sampled, disclosure = _sample(frame, max_points, seed)
    if disclosure.get("applied"):
        disclosure["params"]["target"] = what
    return sampled, disclosure


def merge_sampling(*disclosures: dict) -> dict:
    """Combine disclosures: applied if any applied; params merged."""
    applied = any(bool(d.get("applied")) for d in disclosures if d)
    approx = any(bool(d.get("approximate")) for d in disclosures if d)
    methods = [str(d.get("method")) for d in disclosures
               if d and d.get("applied")]
    params = {}
    for d in disclosures:
        if d:
            params.update(d.get("params") or {})
    return {
        "applied": applied,
        "method": ", ".join(dict.fromkeys(methods)) if methods else "none",
        "params": params,
        "approximate": approx,
    }


def dataset_options() -> list[tuple[str, str]]:
    """[(dataset_id, label)] for the page selectbox, newest first."""
    rows = DatasetRegistry().list()
    options = []
    for row in rows:
        label = f"{row.get('name')} ({row.get('source_type')})"
        options.append((str(row.get("dataset_id")), str(label)))
    options.sort(key=lambda x: x[1].lower())
    return options


def default_output_dir() -> Path:
    """Configured visualization export root (tests monkeypatch this)."""
    return Path(config.OUTPUT_VISUALIZATIONS_DIR)


def load_column_values(
    dataset_id: str, column: str, *, limit: int = 100,
    max_rows: int | None = None,
) -> dict:
    """Bounded distinct-value list of one column for filter pickers.

    Reads at most ``max_rows`` source rows (default: the preview cap),
    read-only, and reports the window and truncation — never silent.
    Returns ``{values, distinct_in_window, rows_read, truncated}``.
    """
    cfg = VisualizationConfig()
    row = registration_info(dataset_id)
    kind = str(row.get("source_type") or "")
    if kind not in ("csv", "sqlite"):
        raise PlatformError(
            "Filter value lists are only available for CSV and SQLite "
            "datasets.",
            detail=f"values unsupported for {kind}",
        )
    path = _source_path(row, dataset_id)
    meta = _read_metadata_json(dataset_id, row)
    source_info = meta.get("source_info", {}) or {}
    n = int(max_rows or cfg.preview_rows)
    try:
        if kind == "csv":
            encoding = str(source_info.get("encoding") or "utf-8")
            sep = str(source_info.get("delimiter") or ",")
            frame = pd.read_csv(path, encoding=encoding, sep=sep, nrows=n,
                                usecols=lambda c: str(c) == str(column),
                                low_memory=False)
        else:
            table = str(source_info.get("table") or "")
            if not table:
                raise PlatformError(
                    "This SQLite registration does not record which table "
                    "was registered.",
                    detail=f"no table recorded {dataset_id}",
                )
            quoted_table = '"' + table.replace('"', '""') + '"'
            quoted_col = '"' + str(column).replace('"', '""') + '"'
            uri = "file:" + path.as_posix() + "?mode=ro"
            with sqlite3.connect(uri, uri=True) as con:
                frame = pd.read_sql_query(
                    f"SELECT {quoted_col} FROM {quoted_table} LIMIT {n}", con)
    except ValueError:
        raise PlatformError(
            f"The column '{column}' is not present in this dataset.",
            detail="usecols mismatch",
        ) from None
    except (pd.errors.EmptyDataError, pd.errors.ParserError,
            UnicodeDecodeError):
        raise PlatformError(
            "The source could not be read with its registered format "
            "settings.",
            detail=f"values read error {kind}",
        ) from None
    except sqlite3.Error as exc:
        raise PlatformError(
            "The SQLite table could not be read (read-only).",
            detail=f"sqlite values error {type(exc).__name__}",
        ) from exc
    if column not in frame.columns:
        raise PlatformError(
            f"The column '{column}' is not present in this dataset.",
            detail="column missing after read",
        )
    series = frame[column]
    counts = series.dropna().astype(str).value_counts()
    values = [str(v) for v in counts.index[: max(int(limit), 1)]]
    return {
        "values": values,
        "distinct_in_window": int(counts.size),
        "rows_read": int(len(series)),
        "truncated": bool(counts.size > len(values)),
    }
