"""Dataset registry: a small SQLite index of known datasets.

The registry ALWAYS lives under ``output/metadata/`` and must never be the
research database (``data/database/digital_archaeology.db``). Only dataset
*descriptors* are stored (paths, counts, statuses) - never dataset contents.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

import config
from core.dataset import Dataset, ProcessingStatus, ValidationStatus
from core.exceptions import PlatformError, RegistryError, redact_path

log = logging.getLogger("da.ingestion.registry")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    dataset_id       TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    source_type      TEXT NOT NULL,
    source_path      TEXT NOT NULL,
    imported_at      TEXT NOT NULL,
    metadata_location TEXT,
    validation_status TEXT NOT NULL,
    processing_status TEXT NOT NULL,
    source_fingerprint TEXT,
    row_count        INTEGER,
    column_count     INTEGER,
    bytes            INTEGER,
    notes            TEXT
)
"""

_ROW_KEYS = ("dataset_id", "name", "source_type", "source_path",
             "imported_at", "metadata_location", "validation_status",
             "processing_status", "source_fingerprint", "row_count",
             "column_count", "bytes", "notes")


class DatasetRegistry:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else config.REGISTRY_DB
        if self.db_path.resolve() == Path(config.DATABASE_PATH).resolve():
            raise PlatformError(
                "The dataset registry must not use the research database.",
                detail=str(self.db_path))
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(str(self.db_path))

    def _ensure_schema(self) -> None:
        try:
            with self._connect() as conn:
                conn.execute(_SCHEMA)
        except sqlite3.Error as exc:
            raise RegistryError(
                "The dataset registry could not be initialised.",
                detail=repr(exc)) from exc

    # ------------------------------------------------------------------ #
    def register(self, dataset: Dataset,
                 metadata_location: str | Path | None = None) -> str:
        payload = {
            "dataset_id": dataset.dataset_id,
            "name": dataset.name,
            "source_type": dataset.source_type.value,
            "source_path": str(dataset.source_path),
            "imported_at": datetime.now().isoformat(timespec="seconds"),
            "metadata_location": str(metadata_location) if metadata_location
            else None,
            "validation_status": dataset.validation_status.value,
            "processing_status": dataset.processing_status.value,
            "source_fingerprint": dataset.source_fingerprint,
            "row_count": dataset.metadata.row_count,
            "column_count": dataset.metadata.column_count,
            "bytes": dataset.metadata.file_size_bytes,
            "notes": "\n".join(dataset.notes) or None,
        }
        sql = (
            "INSERT INTO datasets ("
            + ", ".join(_ROW_KEYS) + ") VALUES ("
            + ", ".join("?" for _ in _ROW_KEYS) + ") "
            "ON CONFLICT(dataset_id) DO UPDATE SET "
            "name=excluded.name, "
            "metadata_location=excluded.metadata_location, "
            "validation_status=excluded.validation_status, "
            "processing_status=excluded.processing_status, "
            "source_fingerprint=excluded.source_fingerprint, "
            "row_count=excluded.row_count, "
            "column_count=excluded.column_count, "
            "bytes=excluded.bytes, "
            "notes=excluded.notes"
        )
        try:
            with self._connect() as conn:
                conn.execute(sql, tuple(payload[k] for k in _ROW_KEYS))
        except sqlite3.Error as exc:
            raise RegistryError(
                "The dataset could not be registered.",
                detail=repr(exc)) from exc
        log.info("Registered dataset %s (%s)",
                 redact_path(dataset.source_path), dataset.dataset_id)
        return dataset.dataset_id

    def get(self, dataset_id: str) -> dict | None:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM datasets WHERE dataset_id = ?",
                (dataset_id,)).fetchone()
        return dict(row) if row else None

    def list(self) -> list[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM datasets ORDER BY imported_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def update_status(self, dataset_id: str,
                      processing_status: str | ProcessingStatus,
                      validation_status: str | ValidationStatus | None = None
                      ) -> bool:
        proc = (processing_status.value if isinstance(
            processing_status, ProcessingStatus) else str(processing_status))
        params: list = [proc, dataset_id]
        sql = ("UPDATE datasets SET processing_status = ? "
               "WHERE dataset_id = ?")
        if validation_status is not None:
            val = (validation_status.value if isinstance(
                validation_status, ValidationStatus)
                else str(validation_status))
            sql = ("UPDATE datasets SET processing_status = ?, "
                   "validation_status = ? WHERE dataset_id = ?")
            params = [proc, val, dataset_id]
        with self._connect() as conn:
            cur = conn.execute(sql, tuple(params))
        return cur.rowcount > 0

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]
