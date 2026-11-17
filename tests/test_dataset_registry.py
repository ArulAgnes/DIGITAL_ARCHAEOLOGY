"""Dataset registry tests - registry lives outside the research database."""
from __future__ import annotations

import json

import pytest

import config
from core.dataset import Dataset, ProcessingStatus, SourceType, ValidationStatus
from core.exceptions import PlatformError
from fixtures.synthetic import assert_outside_research_data, write_csv
from ingestion.dataset_registry import DatasetRegistry


@pytest.fixture()
def registry(tmp_path):
    db = tmp_path / "registry.db"
    reg = DatasetRegistry(db)
    assert_outside_research_data(db)
    return reg


def _dataset(tmp_path, name="d.csv") -> Dataset:
    p = write_csv(tmp_path / name, "a,b\n1,2\n")
    ds = Dataset.from_source(SourceType.CSV, p)
    ds.metadata.row_count = 1
    ds.metadata.column_count = 2
    ds.metadata.file_size_bytes = p.stat().st_size
    return ds


def test_register_and_get_roundtrip(registry, tmp_path):
    ds = _dataset(tmp_path)
    registry.register(ds, metadata_location=tmp_path / "meta.json")
    row = registry.get(ds.dataset_id)
    assert row is not None
    assert row["name"] == ds.name
    assert row["source_type"] == "csv"
    assert row["row_count"] == 1
    assert row["processing_status"] == "registered"


def test_register_is_upsert_not_duplicate(registry, tmp_path):
    ds = _dataset(tmp_path)
    registry.register(ds)
    registry.register(ds)
    assert registry.count() == 1


def test_list_orders_and_returns_dicts(registry, tmp_path):
    ds1 = _dataset(tmp_path, "one.csv")
    ds2 = _dataset(tmp_path, "two.csv")
    registry.register(ds1)
    registry.register(ds2)
    rows = registry.list()
    assert len(rows) == 2
    assert all(isinstance(r, dict) for r in rows)


def test_update_status_changes_fields(registry, tmp_path):
    ds = _dataset(tmp_path)
    registry.register(ds)
    assert registry.update_status(ds.dataset_id,
                                  ProcessingStatus.COMPLETED,
                                  ValidationStatus.OK)
    row = registry.get(ds.dataset_id)
    assert row["processing_status"] == "completed"
    assert row["validation_status"] == "ok"
    assert not registry.update_status("no-such-id", "completed")


def test_registry_refuses_research_database_path():
    with pytest.raises(PlatformError):
        DatasetRegistry(config.DATABASE_PATH)
    with pytest.raises(PlatformError):
        DatasetRegistry(config.DATABASE_PATH.parent / "sub" / ".." /
                        "digital_archaeology.db")


def test_default_registry_path_is_platform_output(registry):
    assert config.REGISTRY_DB != config.DATABASE_PATH
    assert str(config.REGISTRY_DB).startswith(str(config.OUTPUT_DIR))
    default = DatasetRegistry()
    assert default.db_path == config.REGISTRY_DB
    assert_outside_research_data(default.db_path)


def test_registry_stores_descriptors_not_contents(registry, tmp_path):
    p = tmp_path / "secret.csv"
    p.write_text("salary,employee\n99999,alice\n", encoding="utf-8")
    ds = Dataset.from_source(SourceType.CSV, p)
    registry.register(ds)
    row = registry.get(ds.dataset_id)
    flat = json.dumps(row)
    assert "alice" not in flat
    assert "99999" not in flat
    assert row["source_path"] == str(p)  # documented registry field


def test_metadata_location_recorded(registry, tmp_path):
    ds = _dataset(tmp_path)
    loc = tmp_path / "meta.json"
    loc.write_text("{}", encoding="utf-8")
    registry.register(ds, loc)
    assert registry.get(ds.dataset_id)["metadata_location"] == str(loc)
