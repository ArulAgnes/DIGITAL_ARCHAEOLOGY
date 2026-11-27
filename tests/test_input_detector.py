"""Input detector tests - extension + content sniffing, never executes files."""
from __future__ import annotations

from pathlib import Path

from core.dataset import SourceType
from ingestion.csv_loader import CsvLoader
from ingestion.filesystem_loader import FilesystemLoader
from ingestion.input_detector import detect
from ingestion.sqlite_loader import SqliteLoader
from fixtures.synthetic import make_sqlite, write_csv


def test_detects_csv(tmp_path):
    p = write_csv(tmp_path / "a.csv", "id,name\n1,Ada\n")
    res = detect(p)
    assert res.detected
    assert res.source_type is SourceType.CSV
    assert res.suggested_loader() is CsvLoader


def test_detects_sqlite_by_content_not_extension(tmp_path):
    db = make_sqlite(tmp_path / "weird.txt",
                     {"t": (["id INTEGER"], [(1,)])})
    res = detect(db)
    assert res.detected
    assert res.source_type is SourceType.SQLITE
    assert any("SQLite" in w for w in res.warnings)
    assert res.suggested_loader() is SqliteLoader


def test_detects_folder(tmp_path):
    (tmp_path / "mydir").mkdir()
    res = detect(tmp_path / "mydir")
    assert res.detected
    assert res.source_type is SourceType.FILESYSTEM
    assert res.suggested_loader() is FilesystemLoader


def test_missing_path_reports_not_found(tmp_path):
    res = detect(tmp_path / "ghost.csv")
    assert not res.detected
    assert res.status == "not_found"
    assert res.suggested_loader() is None


def test_db_extension_with_invalid_content_rejected(tmp_path):
    fake = write_csv(tmp_path / "fake.db", "definitely not sqlite\n")
    res = detect(fake)
    assert not res.detected
    assert res.status == "unsupported"
    assert "SQLite" in res.reason


def test_binary_file_rejected(tmp_path):
    p = tmp_path / "blob.bin"
    p.write_bytes(b"\x00\x01\x02\x03binary\x00stuff")
    res = detect(p)
    assert not res.detected
    assert res.status == "unsupported"


def test_single_line_text_without_rows_rejected(tmp_path):
    p = write_csv(tmp_path / "line.data", "just one line")
    res = detect(p)
    assert not res.detected
    assert res.status == "unsupported"


def test_folder_requested_for_file_is_unsupported(tmp_path):
    p = write_csv(tmp_path / "a.csv", "x,y\n1,2\n")
    res = detect(p, source_type="filesystem")
    assert not res.detected
    assert res.status == "unsupported"


def test_csv_requested_for_folder_is_unsupported(tmp_path):
    (tmp_path / "d").mkdir()
    res = detect(tmp_path / "d", source_type="csv")
    assert not res.detected
    assert res.status == "unsupported"


def test_hint_conflicts_with_sqlite_content(tmp_path):
    db = make_sqlite(tmp_path / "x.db", {"t": (["id INTEGER"], [(1,)])})
    res = detect(db, source_type="csv")
    assert not res.detected
    assert "CSV" in res.reason or "csv" in res.reason


def test_hint_auto_and_aliases(tmp_path):
    p = write_csv(tmp_path / "a.csv", "x,y\n1,2\n")
    assert detect(p, source_type="auto").detected
    assert detect(p, source_type="csv").detected
    assert detect(p, source_type=None).detected
    (tmp_path / "d").mkdir()
    assert detect(tmp_path / "d", source_type="dir").detected
    assert detect(tmp_path / "d", source_type="folder").detected
