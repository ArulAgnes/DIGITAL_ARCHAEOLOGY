"""Dataset model tests: stable identity, JSON-safety, status enums."""
from __future__ import annotations

import json

from core.dataset import (ColumnMetadata, Dataset, DatasetMetadata,
                          ProcessingStatus, SourceType, ValidationStatus,
                          make_dataset_id, source_fingerprint)
from fixtures.synthetic import write_csv


def test_dataset_id_stable_for_same_source(tmp_path):
    p = write_csv(tmp_path / "a.csv", "x,y\n1,2\n")
    assert make_dataset_id(SourceType.CSV, p) == \
        make_dataset_id(SourceType.CSV, p)


def test_dataset_id_differs_by_type_and_path(tmp_path):
    p = write_csv(tmp_path / "a.csv", "x,y\n1,2\n")
    q = write_csv(tmp_path / "b.csv", "x,y\n1,2\n")
    id_csv = make_dataset_id(SourceType.CSV, p)
    id_sqlite = make_dataset_id(SourceType.SQLITE, p)
    id_other = make_dataset_id(SourceType.CSV, q)
    assert id_csv != id_sqlite
    assert id_csv != id_other
    assert len(id_csv) == 36


def test_dataset_id_case_insensitive_on_windows_path(tmp_path):
    p = write_csv(tmp_path / "MixedCase.csv", "x,y\n1,2\n")
    upper = p.with_name("MIXEDCASE.CSV")
    try:
        same = upper.exists()
    except OSError:
        same = False
    if same:
        assert make_dataset_id(SourceType.CSV, p) == \
            make_dataset_id(SourceType.CSV, upper)


def test_source_fingerprint_changes_with_content(tmp_path):
    import time
    p = write_csv(tmp_path / "f.csv", "x\n1\n")
    fp1 = source_fingerprint(p)
    time.sleep(0.05)  # cross the filesystem timestamp tick (same size)
    p.write_bytes(b"x\n2\n")
    fp2 = source_fingerprint(p)
    assert fp1 != fp2
    assert fp1.split(":")[0].isdigit()


def test_from_source_defaults_and_json_roundtrip(tmp_path):
    p = write_csv(tmp_path / "d.csv", "a,b\n1,2\n")
    ds = Dataset.from_source(SourceType.CSV, p)
    assert ds.source_path == p
    assert ds.processing_status is ProcessingStatus.REGISTERED
    assert ds.validation_status is ValidationStatus.PENDING
    assert ds.metadata.row_count is None
    payload = json.dumps(ds.to_dict())
    assert "a,b" not in payload
    back = json.loads(payload)
    assert back["source_type"] == "csv"
    assert back["dataset_id"] == ds.dataset_id


def test_metadata_to_dict_serializable():
    md = DatasetMetadata(
        row_count=10, column_count=1,
        columns=[ColumnMetadata(name="a", dtype="int64",
                                missing_count=0, unique_count=10)],
        file_size_bytes=100, source_info={"format": "csv"},
        warnings=["w"], collected_at="2026-11-16T00:00:00")
    payload = json.dumps(md.to_dict())
    back = json.loads(payload)
    assert back["columns"][0]["name"] == "a"
    assert back["warnings"] == ["w"]


def test_enum_values_are_platform_safe_strings():
    assert SourceType.CSV.value == "csv"
    assert SourceType.SQLITE.value == "sqlite"
    assert SourceType.FILESYSTEM.value == "filesystem"
    assert ValidationStatus.FAILED.value == "failed"
    assert ProcessingStatus.COMPLETED.value == "completed"
