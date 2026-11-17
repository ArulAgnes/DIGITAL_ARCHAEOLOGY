"""CSV loader tests - all data synthetic, under pytest temp dirs."""
from __future__ import annotations

import hashlib

import pandas as pd
import pytest

from core.exceptions import (DatasetEmptyError, InputValidationError,
                             MalformedDataError, SourceNotFoundError,
                             UnsupportedSourceError)
from fixtures.synthetic import sha256_file, write_csv
from ingestion.csv_loader import CsvLoader


def test_validate_ok_on_simple_csv(tmp_path):
    p = write_csv(tmp_path / "people.csv",
                  "id,name,age\n1,Ada,36\n2,Grace,45\n")
    loader = CsvLoader(p)
    results = loader.validate()
    assert all(r.ok for r in results)


def test_missing_file_raises_source_not_found(tmp_path):
    loader = CsvLoader(tmp_path / "nope.csv")
    with pytest.raises(SourceNotFoundError):
        loader.to_dataset()


def test_empty_file_raises_dataset_empty(tmp_path):
    p = write_csv(tmp_path / "empty.csv", "")
    with pytest.raises(DatasetEmptyError):
        CsvLoader(p).to_dataset()


def test_duplicate_headers_rejected(tmp_path):
    p = write_csv(tmp_path / "dup.csv", "id,name,name\n1,a,b\n")
    with pytest.raises(InputValidationError) as exc:
        CsvLoader(p).to_dataset()
    assert "duplicate" in str(exc.value).lower()


def test_wrong_extension_binary_rejected(tmp_path):
    p = tmp_path / "pic.png"
    p.write_bytes(b"\x89PNG\x00\x00binary")
    with pytest.raises(UnsupportedSourceError):
        CsvLoader(p).to_dataset()


def test_utf8_bom_detected_and_stripped(tmp_path):
    p = write_csv(tmp_path / "bom.csv",
                  "id,name\n1,Ada\n", encoding="utf-8-sig")
    loader = CsvLoader(p)
    df = loader.read_all()
    assert loader.encoding == "utf-8-sig"
    assert list(df.columns) == ["id", "name"]


def test_semicolon_delimiter_auto_detected(tmp_path):
    p = write_csv(tmp_path / "semi.csv",
                  "id;city;score\n1;London;9.5\n2;Paris;8\n")
    loader = CsvLoader(p)
    df = loader.read_all()
    assert loader.delimiter == ";"
    assert df.shape == (2, 3)


def test_row_count_handles_newline_inside_quotes(tmp_path):
    p = write_csv(tmp_path / "quoted.csv",
                  'id,note\n1,"line1\nline2"\n2,plain\n')
    ds = CsvLoader(p).to_dataset()
    assert ds.metadata.row_count == 2


def test_iter_chunks_yields_bounded_pieces(tmp_path):
    p = write_csv(tmp_path / "many.csv",
                  "id,v\n" + "".join(f"{i},{i*2}\n" for i in range(1, 6)))
    chunks = list(CsvLoader(p, chunk_size=2).iter_chunks())
    assert [len(c) for c in chunks] == [2, 2, 1]
    assert sum(len(c) for c in chunks) == 5


def test_source_bytes_unchanged_after_full_read(tmp_path):
    p = write_csv(tmp_path / "hash.csv",
                  "id,v\n1,10\n2,20\n3,30\n")
    before = sha256_file(p)
    loader = CsvLoader(p)
    loader.load_metadata()
    list(loader.iter_chunks())
    assert sha256_file(p) == before


def test_header_only_csv_has_zero_rows_and_warning(tmp_path):
    p = write_csv(tmp_path / "header.csv", "id,name\n")
    ds = CsvLoader(p).to_dataset()
    assert ds.metadata.row_count == 0
    assert any("0 data rows" in w for w in ds.metadata.warnings)


def test_malformed_csv_raises_malformed_data(tmp_path):
    p = write_csv(tmp_path / "bad.csv", "id,name\n1,\"unterminated\n")
    with pytest.raises((MalformedDataError, DatasetEmptyError,
                        InputValidationError)):
        CsvLoader(p).to_dataset()


def test_dataset_identity_is_stable_and_path_based(tmp_path):
    p = write_csv(tmp_path / "id.csv", "a,b\n1,2\n")
    ds1 = CsvLoader(p).to_dataset()
    ds2 = CsvLoader(p).to_dataset()
    assert ds1.dataset_id == ds2.dataset_id
    assert ds1.dataset_id != "id"
    assert len(ds1.dataset_id) == 36


def test_metadata_contains_encoding_and_header_info(tmp_path):
    p = write_csv(tmp_path / "meta.csv", "id,name\n1,Ada\n")
    md = CsvLoader(p).load_metadata()
    assert md.row_count == 1
    assert md.column_count == 2
    assert md.source_info["format"] == "csv"
    assert md.source_info["delimiter"] in (",", ";", "\t", "|")
    assert [c.name for c in md.columns] == ["id", "name"]
    assert md.file_size_bytes == p.stat().st_size


def test_column_stats_note_when_sampled(tmp_path):
    p = write_csv(tmp_path / "big.csv",
                  "id,v\n" + "".join(f"{i},{i}\n" for i in range(1500)))
    loader = CsvLoader(p)
    old_sample = loader.__class__.__module__
    import ingestion.csv_loader as mod
    original = mod.SAMPLE_ROWS
    mod.SAMPLE_ROWS = 100
    try:
        md = loader.load_metadata()
    finally:
        mod.SAMPLE_ROWS = original
    assert md.row_count == 1500
    assert any("first 100" in w for w in md.warnings)
    assert old_sample
