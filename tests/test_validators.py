"""Validator tests - explicit failure messages, path redaction."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.exceptions import (ConfirmationRequiredError, DatasetEmptyError,
                             InputValidationError, SourceNotFoundError,
                             UnsupportedSourceError)
from fixtures.synthetic import make_sqlite, write_csv
from ingestion.validators import (ValidationResult, combine,
                                  is_drive_root, require,
                                  sqlite_has_magic, validate_csv_headers,
                                  validate_drive_confirmation,
                                  validate_exists, validate_extension,
                                  validate_nonempty_file,
                                  validate_readable, validate_table_name)


def test_validate_exists_missing_reports_error(tmp_path):
    res = validate_exists(tmp_path / "missing.csv")
    assert not res.ok
    assert res.code == "source_not_found"


def test_require_raises_redacted_source_not_found(tmp_path):
    deep = tmp_path / "secret_area" / "missing.csv"
    res = validate_exists(deep)
    with pytest.raises(SourceNotFoundError) as exc:
        require(res)
    message = str(exc.value)
    assert "missing.csv" in message
    assert str(tmp_path) not in message


def test_validate_readable_on_file_and_dir(tmp_path):
    p = write_csv(tmp_path / "ok.csv", "a,b\n1,2\n")
    assert validate_readable(p).ok
    assert validate_readable(tmp_path).ok
    assert not validate_readable(tmp_path / "nope.csv").ok


def test_validate_extension_levels(tmp_path):
    assert validate_extension(tmp_path / "a.csv", {".csv"}).level == "ok"
    err = validate_extension(tmp_path / "a.png", {".csv"})
    assert err.level == "error"
    warn = validate_extension(tmp_path / "a.txt", {".csv"},
                              fallback_level="warning")
    assert warn.level == "warning"
    assert warn.ok


def test_validate_nonempty_file_flags_zero_bytes(tmp_path):
    p = write_csv(tmp_path / "zero.csv", "")
    res = validate_nonempty_file(p)
    assert not res.ok
    assert res.code == "dataset_empty"
    with pytest.raises(DatasetEmptyError):
        require(res)


def test_validate_csv_headers_duplicates_and_clean(tmp_path):
    assert validate_csv_headers(["id", "name"]).ok
    dup = validate_csv_headers(["id", "name", "Name"])
    assert not dup.ok
    assert "Name" in dup.message


def test_validate_table_name_rejects_injection_shapes(tmp_path):
    assert validate_table_name("people").ok
    assert validate_table_name("People_2024").ok
    for bad in ("people; DROP TABLE x", 'people"', "2people", "", "a b"):
        res = validate_table_name(bad)
        assert not res.ok, bad
        with pytest.raises(InputValidationError):
            require(res)


def test_is_drive_root_detection():
    assert is_drive_root(Path("D:\\"))
    assert is_drive_root(Path("C:/"))
    assert not is_drive_root(Path("D:\\ExampleWorkspace"))
    assert not is_drive_root(Path("D:\\Users"))


def test_drive_confirmation_required_unless_confirmed(tmp_path):
    res = validate_drive_confirmation(Path("D:\\"), confirmed=False)
    assert not res.ok
    assert res.code == "confirmation_required"
    with pytest.raises(ConfirmationRequiredError):
        require(res)
    assert validate_drive_confirmation(Path("D:\\"), confirmed=True).ok
    assert validate_drive_confirmation(tmp_path, confirmed=False).ok


def test_sqlite_magic_detection(tmp_path):
    db = make_sqlite(tmp_path / "x.db",
                     {"t": (["id INTEGER"], [(1,), (2,)])})
    txt = write_csv(tmp_path / "x.txt", "not a database\n")
    assert sqlite_has_magic(db)
    assert not sqlite_has_magic(txt)


def test_combine_prefers_error_then_warning(tmp_path):
    ok = ValidationResult.success()
    warn = ValidationResult.warning("w", "careful")
    err = ValidationResult.error("source_not_found", "gone")
    assert combine([ok, warn]).level == "warning"
    assert combine([warn, err]).level == "error"
    assert combine([ok, ok]).level == "ok"


def test_unsupported_extension_error_maps_correctly(tmp_path):
    res = validate_extension(tmp_path / "a.zip", {".csv"})
    with pytest.raises(UnsupportedSourceError):
        require(res)
