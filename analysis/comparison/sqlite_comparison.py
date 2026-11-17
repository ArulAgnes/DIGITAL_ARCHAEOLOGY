"""SQLite-to-SQLite table comparison (Phase 6).

Both databases are opened read-only (``file:...?mode=ro``): no table,
index, temporary file or journal is ever created in a source database,
so file hashes stay identical across a run. The table schema comes from
``PRAGMA table_info`` (declared types), table lists from
``sqlite_master``, and rows are streamed with ``fetchmany`` and fed to
the same matching and statistics code used for CSV comparisons.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from analysis.comparison import matching
from analysis.comparison.csv_comparison import StatsPair
from core.exceptions import PlatformError
from ingestion.sqlite_loader import quote_ident

_TABLE_SAMPLE_LIMIT = 100


def _connect(path) -> sqlite3.Connection:
    uri = f"file:{Path(path).as_posix()}?mode=ro"
    try:
        return sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise PlatformError(
            "The database could not be opened for reading - the file may "
            "be missing, unreadable or not a SQLite database.",
            detail=f"{type(exc).__name__}") from exc


def _tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    return [str(r[0]) for r in rows]


def _table_types(conn: sqlite3.Connection, table: str,
                 dataset_label: str) -> dict:
    rows = conn.execute(
        f"PRAGMA table_info({quote_ident(table)})").fetchall()
    if not rows:
        raise PlatformError(
            f"The table '{table}' does not exist in the {dataset_label} "
            "database - re-register the dataset with a table selected.")
    return {str(r[1]): (str(r[2]).strip() or "BLOB") for r in rows}


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(
        f"SELECT COUNT(*) FROM {quote_ident(table)}").fetchone()[0])


def _rows(conn: sqlite3.Connection, table: str, chunk_size: int):
    cursor = conn.execute(f"SELECT * FROM {quote_ident(table)}")
    try:
        while True:
            batch = cursor.fetchmany(max(1, int(chunk_size)))
            if not batch:
                break
            yield batch
    finally:
        cursor.close()


def _schema(tables_a, tables_b, table_a, table_b, decl_a, decl_b,
            count_a, count_b) -> dict:
    set_a, set_b = set(tables_a), set(tables_b)
    a_only = sorted(set_a - set_b)
    b_only = sorted(set_b - set_a)
    cols_a, cols_b = list(decl_a), list(decl_b)
    set_ca, set_cb = set(cols_a), set(cols_b)
    changes = []
    for name in cols_a:
        if name in set_cb and decl_a[name] != decl_b[name]:
            changes.append({"column": name, "a_type": decl_a[name],
                            "b_type": decl_b[name]})
    return {
        "kind": "sqlite",
        "status": "ok",
        "tables": {"a": table_a, "b": table_b},
        "table_counts": {"a": len(tables_a), "b": len(tables_b),
                         "a_only": len(a_only), "b_only": len(b_only)},
        "table_list_diff": {"a_only": a_only[:_TABLE_SAMPLE_LIMIT],
                            "b_only": b_only[:_TABLE_SAMPLE_LIMIT]},
        "columns": {"added": [c for c in cols_b if c not in set_ca],
                    "removed": [c for c in cols_a if c not in set_cb],
                    "dtype_changes": changes},
        "row_counts": {"a": count_a, "b": count_b},
        "note": "declared column types read from PRAGMA table_info; "
                "table lists read from sqlite_master (read-only)",
    }


def compare(in_a: dict, in_b: dict, cfg, stage) -> dict:
    warnings: list[dict] = []
    table_a, table_b = str(in_a["table"]), str(in_b["table"])
    conn_a = _connect(in_a["path"])
    conn_b = _connect(in_b["path"])
    try:
        stage("load", 1, 6, "reading table schemas (read-only)")
        tables_a = _tables(conn_a)
        tables_b = _tables(conn_b)
        decl_a = _table_types(conn_a, table_a, "first")
        decl_b = _table_types(conn_b, table_b, "second")
        cols_a, cols_b = list(decl_a), list(decl_b)
        count_a = _count(conn_a, table_a)
        count_b = _count(conn_b, table_b)

        matcher = matching.make_matcher(cols_a, cols_b, cfg.keys, cfg)
        set_b = set(cols_b)
        common = [c for c in cols_a if c in set_b]
        stats = StatsPair(common, cfg) if cfg.stats_enabled else None

        stage("load", 2, 6, "reading table A (single pass)")
        for batch in _rows(conn_a, table_a, cfg.chunk_size):
            frame = pd.DataFrame(batch, columns=cols_a)
            matcher.feed_a(frame.itertuples(index=False, name=None))
            if stats is not None:
                stats.feed_a(frame)
        matcher.finish_a()

        if matcher.needs_count_pass:
            stage("load", 3, 6,
                  "reading table B (pass 1 of 2: key counts)")
            for batch in _rows(conn_b, table_b, cfg.chunk_size):
                frame = pd.DataFrame(batch, columns=cols_b)
                matcher.count_b(frame.itertuples(index=False, name=None))
                if stats is not None:
                    stats.feed_b(frame)
            matcher.finish_counts()
            stage("load", 4, 6, "reading table B (pass 2 of 2: matching)")
            for batch in _rows(conn_b, table_b, cfg.chunk_size):
                frame = pd.DataFrame(batch, columns=cols_b)
                matcher.feed_b(frame.itertuples(index=False, name=None))
        else:
            stage("load", 3, 6, "reading table B (single pass)")
            for batch in _rows(conn_b, table_b, cfg.chunk_size):
                frame = pd.DataFrame(batch, columns=cols_b)
                matcher.feed_b(frame.itertuples(index=False, name=None))
                if stats is not None:
                    stats.feed_b(frame)

        if stats is not None:
            stage("statistics", 5, 6, "computing statistical differences")
            statistics = stats.differences("sqlite")
            warnings.extend(statistics.pop("warnings"))
        else:
            stage("statistics", 5, 6, "statistics disabled for this run")
            statistics = {"kind": "sqlite", "status": "disabled",
                          "notes": ["statistics disabled in the options"]}

        matcher.observe_dtypes(decl_a, decl_b)
        outcome = matcher.finish_b()
        schema = _schema(tables_a, tables_b, table_a, table_b,
                         decl_a, decl_b, count_a, count_b)
    finally:
        conn_a.close()
        conn_b.close()

    record_tables = outcome.record_tables()
    return {
        "strategy": outcome.strategy,
        "exactness": outcome.exactness,
        "keys": outcome.keys,
        "counts": outcome.counts,
        "warnings": list(outcome.warnings) + list(warnings),
        "schema": schema,
        "statistics": statistics,
        "record_tables": record_tables,
        "table_files": [t["file"] for t in record_tables],
        "summary_extra": dict(outcome.summary_extra, tables={
            "a": table_a, "b": table_b}),
    }
