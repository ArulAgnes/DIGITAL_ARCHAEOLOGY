"""SQLite loader tests - read-only guarantees, safe identifiers, chunks."""
from __future__ import annotations

import sqlite3

import pytest

from core.exceptions import (DatasetEmptyError, InputValidationError,
                             UnsupportedSourceError)
from fixtures.synthetic import make_sqlite, sha256_file, write_csv
from ingestion.sqlite_loader import SqliteLoader

PEOPLE_TABLE = {
    "people": (
        ["id INTEGER PRIMARY KEY", "name TEXT NOT NULL", "score REAL"],
        [(1, "Ada", 9.5), (2, "Grace", 8.0), (3, "Alan", 7.25),
         (4, "Edsger", 9.0), (5, "Barbara", 8.5)],
    )
}
MULTI_TABLE = {
    "people": PEOPLE_TABLE["people"],
    "tasks": (["id INTEGER", "title TEXT"],
              [(1, "write"), (2, "test")]),
}


@pytest.fixture()
def people_db(tmp_path):
    return make_sqlite(tmp_path / "people.db", PEOPLE_TABLE)


@pytest.fixture()
def multi_db(tmp_path):
    return make_sqlite(tmp_path / "multi.db", MULTI_TABLE)


def test_single_table_auto_resolved(people_db):
    loader = SqliteLoader(people_db)
    assert loader.resolve_table() == "people"
    loader.close()


def test_metadata_row_count_and_declared_dtypes(people_db):
    md = SqliteLoader(people_db).load_metadata()
    assert md.row_count == 5
    assert md.column_count == 3
    dtypes = {c.name: c.dtype for c in md.columns}
    assert dtypes["name"] == "TEXT"
    assert dtypes["score"] == "REAL"
    assert md.source_info["table"] == "people"


def test_multi_table_requires_selection(multi_db):
    loader = SqliteLoader(multi_db)
    results = loader.validate()
    assert any(r.level == "warning" and r.code == "multiple_tables"
               for r in results)
    with pytest.raises(InputValidationError) as exc:
        loader.to_dataset()
    assert "specify" in str(exc.value).lower()
    loader.close()


def test_multi_table_with_explicit_selection(multi_db):
    loader = SqliteLoader(multi_db, table="tasks")
    md = loader.load_metadata()
    assert md.row_count == 2
    assert md.source_info["table"] == "tasks"


def test_unknown_table_rejected(multi_db):
    loader = SqliteLoader(multi_db, table="ghost")
    with pytest.raises(InputValidationError):
        loader.to_dataset()
    loader.close()


def test_injection_style_table_name_blocked_and_db_intact(multi_db):
    loader = SqliteLoader(
        multi_db, table="people\"; DROP TABLE people;--")
    with pytest.raises(InputValidationError):
        loader.to_dataset()
    loader.close()
    with sqlite3.connect(str(multi_db)) as conn:
        n = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    assert n == 5


def test_iter_chunks_bounded(people_db):
    chunks = list(SqliteLoader(people_db, chunk_size=2).iter_chunks())
    assert [len(c) for c in chunks] == [2, 2, 1]
    assert sum(len(c) for c in chunks) == 5


def test_source_db_bytes_unchanged_after_reads(people_db):
    before = sha256_file(people_db)
    loader = SqliteLoader(people_db)
    loader.load_metadata()
    list(loader.iter_chunks())
    loader.close()
    assert sha256_file(people_db) == before


def test_connection_is_read_only(people_db):
    loader = SqliteLoader(people_db)
    conn = loader._connect()
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("CREATE TABLE evil (x INTEGER)")
    loader.close()


def test_not_a_sqlite_file_with_db_extension(tmp_path):
    fake = write_csv(tmp_path / "fake.db", "this is not a database\n")
    with pytest.raises((UnsupportedSourceError, InputValidationError)):
        SqliteLoader(fake).to_dataset()


def test_database_without_tables(tmp_path):
    empty = tmp_path / "empty.db"
    conn = sqlite3.connect(str(empty))
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()
    with pytest.raises(DatasetEmptyError):
        SqliteLoader(empty).to_dataset()


def test_context_manager_closes_connection(people_db):
    with SqliteLoader(people_db) as loader:
        assert loader.list_tables() == ["people"]
        assert loader._conn is not None
    assert loader._conn is None


def test_dataset_statuses_and_identity(people_db):
    ds = SqliteLoader(people_db).to_dataset()
    assert ds.validation_status.value in ("ok", "warning")
    assert ds.source_type.value == "sqlite"
    assert ds.metadata.row_count == 5
    assert len(ds.dataset_id) == 36
