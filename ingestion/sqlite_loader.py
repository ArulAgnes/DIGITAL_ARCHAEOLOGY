"""SQLite input loader.

Connections are opened strictly read-only (URI ``mode=ro``); queries are
membership-checked against ``sqlite_master`` and identifiers are quoted, so no
user input can reach an unvalidated SQL identifier. Chunk reads use bound
LIMIT/OFFSET parameters. The research database must never be passed here by
platform code paths.
"""
from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pandas as pd

from core.dataset import (Dataset, DatasetMetadata, SourceType,
                          ValidationStatus)
from core.dataset_metadata import build_metadata, columns_from_dataframe, now_iso
from core.exceptions import (InputValidationError, MalformedDataError,
                             SourceNotReadableError, redact_path)
from ingestion.base_loader import BaseLoader
from ingestion.validators import (ValidationResult, combine,
                                  require, sqlite_has_magic,
                                  validate_exists, validate_is_file,
                                  validate_readable, validate_sqlite_file,
                                  validate_table_name)

log = logging.getLogger("da.ingestion.sqlite")

DEFAULT_CHUNK_SIZE = 100_000
SAMPLE_ROWS = 1_000


def quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


class SqliteLoader(BaseLoader):
    def __init__(self, path, table: str | None = None,
                 chunk_size: int = DEFAULT_CHUNK_SIZE):
        self.path = Path(path)
        self.table = table
        self.chunk_size = int(chunk_size)
        self._conn: sqlite3.Connection | None = None
        self._validated = False

    # ------------------------------------------------------------------ #
    # Connection (read-only)
    # ------------------------------------------------------------------ #
    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            uri = self.path.resolve().as_uri() + "?mode=ro"
            try:
                self._conn = sqlite3.connect(uri, uri=True)
            except sqlite3.Error as exc:
                raise InputValidationError(
                    f"'{redact_path(self.path)}' is not a usable SQLite "
                    "database.",
                    detail=repr(exc)) from exc
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def __enter__(self) -> "SqliteLoader":
        self._connect()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # Table discovery / resolution
    # ------------------------------------------------------------------ #
    def list_tables(self) -> list[str]:
        cur = self._connect().execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name")
        return [row[0] for row in cur.fetchall()]

    def resolve_table(self) -> str:
        tables = self.list_tables()
        if self.table is None:
            if not tables:
                raise InputValidationError(
                    "The database contains no tables.",
                    detail=f"tables={tables}")
            if len(tables) > 1:
                raise InputValidationError(
                    "This database contains several tables; specify which "
                    f"one to use ({len(tables)} tables found).",
                    detail=f"tables={tables}")
            return tables[0]
        name = str(self.table)
        require(validate_table_name(name))
        if name not in tables:
            raise InputValidationError(
                f"Table '{name}' was not found in the database.",
                detail=f"available={tables}")
        return name

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #
    def validate(self) -> list[ValidationResult]:
        results: list[ValidationResult] = [validate_exists(self.path)]
        if results[-1].level == "error":
            self._results = results
            return results
        results.extend([
            validate_is_file(self.path),
            validate_readable(self.path),
            validate_sqlite_file(self.path),
        ])
        if sqlite_has_magic(self.path) and not any(
                r.level == "error" for r in results):
            try:
                with self:
                    tables = self.list_tables()
                    if not tables:
                        results.append(ValidationResult.error(
                            "dataset_empty",
                            "The database contains no tables.",
                            detail=str(self.path)))
                    elif self.table is not None:
                        results.append(validate_table_name(str(self.table)))
                        if (results[-1].ok
                                and str(self.table) not in tables):
                            results.append(ValidationResult.error(
                                "table_not_found",
                                f"Table '{self.table}' was not found in "
                                "the database.",
                                detail=f"available={tables}"))
                    elif len(tables) > 1:
                        results.append(ValidationResult.warning(
                            "multiple_tables",
                            f"The database contains {len(tables)} tables; "
                            "one must be selected before loading.",
                            detail=f"tables={tables}"))
            except sqlite3.DatabaseError as exc:
                results.append(ValidationResult.error(
                    "unsupported_source",
                    f"'{redact_path(self.path)}' is not a valid SQLite "
                    "database file.",
                    detail=repr(exc)))
        self._results = results
        return results

    def _require_valid(self) -> None:
        if not self._validated:
            require(combine(self.validate()))
            self._validated = True

    # ------------------------------------------------------------------ #
    # Metadata (bounded memory)
    # ------------------------------------------------------------------ #
    def load_metadata(self) -> DatasetMetadata:
        self._require_valid()
        table = self.resolve_table()
        conn = self._connect()
        try:
            row_count = conn.execute(
                f"SELECT COUNT(*) FROM {quote_ident(table)}").fetchone()[0]
            info = conn.execute(
                f"PRAGMA table_info({quote_ident(table)})").fetchall()
            sample = pd.read_sql_query(
                f"SELECT * FROM {quote_ident(table)} LIMIT :lim",
                conn, params={"lim": SAMPLE_ROWS})
        except sqlite3.DatabaseError as exc:
            raise MalformedDataError(
                f"Table '{table}' could not be read.",
                detail=repr(exc)) from exc

        declared = {r[1]: r[2] for r in info}
        not_null = {r[1]: bool(r[3]) for r in info}
        columns = columns_from_dataframe(sample)
        for col in columns:
            decl = declared.get(col.name, "")
            if decl:
                col.dtype = decl
            if col.name in not_null:
                col.nullable = not not_null[col.name]

        warnings: list[str] = []
        if row_count == 0:
            warnings.append("table is empty - 0 rows")
        if row_count > len(sample):
            warnings.append(
                f"column statistics computed from the first {len(sample)} "
                f"of {row_count} rows")
        if len(self.list_tables()) > 1:
            warnings.append(f"table '{table}' selected from several tables")

        size = self.path.stat().st_size
        return build_metadata(
            row_count=int(row_count),
            columns=columns,
            file_size_bytes=size,
            source_info={
                "format": "sqlite",
                "table": table,
                "tables": self.list_tables(),
                "sample_rows": len(sample),
                "path_name": redact_path(self.path),
            },
            warnings=warnings,
        )

    # ------------------------------------------------------------------ #
    # Reading (parameterized chunks)
    # ------------------------------------------------------------------ #
    def preview(self, nrows: int = 20) -> pd.DataFrame:
        """Bounded head of the resolved table - read-only, bound parameters."""
        self._require_valid()
        table = self.resolve_table()
        conn = self._connect()
        try:
            return pd.read_sql_query(
                f"SELECT * FROM {quote_ident(table)} LIMIT :lim",
                conn, params={"lim": int(nrows)})
        except (sqlite3.DatabaseError, pd.errors.DatabaseError) as exc:
            raise MalformedDataError(
                f"Table '{table}' could not be read.",
                detail=repr(exc)) from exc

    def iter_chunks(self, chunk_size: int | None = None
                    ) -> Iterator[pd.DataFrame]:
        self._require_valid()
        table = self.resolve_table()
        conn = self._connect()
        size = chunk_size or self.chunk_size
        offset = 0
        while True:
            try:
                chunk = pd.read_sql_query(
                    f"SELECT * FROM {quote_ident(table)} "
                    "LIMIT :lim OFFSET :off",
                    conn, params={"lim": size, "off": offset})
            except (sqlite3.DatabaseError, pd.errors.DatabaseError) as exc:
                raise MalformedDataError(
                    f"Table '{table}' could not be read.",
                    detail=repr(exc)) from exc
            if chunk.empty:
                return
            yield chunk
            if len(chunk) < size:
                return
            offset += size

    # ------------------------------------------------------------------ #
    # Dataset
    # ------------------------------------------------------------------ #
    def to_dataset(self) -> Dataset:
        results = self.validate()
        combined = combine(results)
        if combined.level == "error":
            require(combined)
        ds = Dataset.from_source(SourceType.SQLITE, self.path)
        ds.validation_status = ValidationStatus(
            "ok" if combined.level == "ok"
            else "warning" if combined.level == "warning" else "failed")
        ds.metadata = self.load_metadata()
        ds.notes.append(f"validated at {now_iso()}")
        log.info("SQLite dataset ready: %s rows=%s",
                 redact_path(self.path), ds.metadata.row_count)
        return ds

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
