"""Phase 7 tests: bounded, read-only data loading for visualizations.

Everything runs against the isolated platform output dir (tmp only).
The research CSV and the research database are asserted untouched via
stat signatures; no absolute path may surface in user-facing messages.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest

import config
from core.exceptions import PlatformError
from fixtures.synthetic import (assert_outside_research_data, make_sqlite,
                                sha256_file, write_csv)
from ingestion import workflow
from visualization import data_adapter as adapter
from visualization.visualization_config import VisualizationConfig

_DRIVE_RE = re.compile(r"[A-Za-z]:[\\/]")


@pytest.fixture()
def platform_dirs(tmp_path, monkeypatch):
    out = tmp_path / "platform_out"
    inputs = tmp_path / "inputs"
    monkeypatch.setattr(config, "OUTPUT_DIR", out)
    monkeypatch.setattr(config, "OUTPUT_METADATA_DIR", out / "metadata")
    monkeypatch.setattr(config, "OUTPUT_DATASETS_DIR", out / "datasets")
    monkeypatch.setattr(config, "REGISTRY_DB",
                        out / "metadata" / "dataset_registry.db")
    monkeypatch.setattr(config, "INPUT_CSV_DIR", inputs)
    monkeypatch.setattr(config, "OUTPUT_VISUALIZATIONS_DIR",
                        out / "visualizations")
    return out


def _research_signature() -> tuple[int, int]:
    stat = config.FILES_CSV.stat()
    return stat.st_size, stat.st_mtime_ns


def _rows(n: int) -> str:
    cats = ["x", "y", "z"]
    return "".join(
        f"{i},{(i % 7) + 1.5},{cats[i % 3]}\n" for i in range(n))


def _register_csv(tmp_path, name: str, text: str) -> str:
    write_csv(tmp_path / name, text)
    insp = workflow.inspect_csv(str(tmp_path / name))
    assert insp.ok, insp.error
    res = workflow.register(insp)
    assert res.ok, res.error
    return res.dataset_id


def _register_sqlite(tmp_path, name: str, table: str,
                     columns: list[str], rows: list[tuple]) -> str:
    db = make_sqlite(tmp_path / name, {table: (columns, rows)})
    insp = workflow.inspect_sqlite(str(db))
    assert insp.ok, insp.error
    res = workflow.register(insp)
    assert res.ok, res.error
    return res.dataset_id


def _register_folder(tmp_path, name: str) -> str:
    root = tmp_path / name
    (root / "sub").mkdir(parents=True, exist_ok=True)
    (root / "a.txt").write_text("aaa", encoding="utf-8")
    (root / "sub" / "b.txt").write_text("bbb", encoding="utf-8")
    insp = workflow.inspect_folder(str(root))
    assert insp.ok, insp.error
    res = workflow.register(insp, confirm_drive=False)
    assert res.ok, res.error
    return res.dataset_id


def _assert_no_path_leak(text: str) -> None:
    assert str(Path.cwd()) not in text
    assert not _DRIVE_RE.search(text), f"drive path leaked: {text!r}"


# ------------------------------------------------------------------ #
# Isolation + registration metadata
# ------------------------------------------------------------------ #
def test_outputs_isolated_and_research_csv_untouched(platform_dirs, tmp_path):
    before = _research_signature()
    ds_id = _register_csv(tmp_path, "iso.csv", "id,score,cat\n" + _rows(30))
    out = adapter.load_preview(ds_id, max_rows=10)
    assert_outside_research_data(Path(config.REGISTRY_DB))
    assert_outside_research_data(
        Path(config.OUTPUT_METADATA_DIR))
    assert adapter.default_output_dir() == Path(
        config.OUTPUT_VISUALIZATIONS_DIR)
    assert_outside_research_data(adapter.default_output_dir())
    assert len(out.frame) == 10
    assert _research_signature() == before


def test_registration_info_miss_raises_clean_error(platform_dirs):
    with pytest.raises(PlatformError) as exc:
        adapter.registration_info("does_not_exist")
    assert "could not be found" in exc.value.user_message
    _assert_no_path_leak(exc.value.user_message)


def test_metadata_column_specs_reads_json_only(platform_dirs, tmp_path,
                                               monkeypatch):
    ds_id = _register_csv(tmp_path, "meta.csv", "id,score,cat\n" + _rows(40))
    specs = adapter.metadata_column_specs(ds_id)
    names = {s["name"] for s in specs}
    assert names == {"id", "score", "cat"}
    assert all("dtype" in s for s in specs)

    def _boom(*_a, **_k):
        raise AssertionError("metadata_column_specs must not read rows")

    monkeypatch.setattr(pd, "read_csv", _boom)
    again = adapter.metadata_column_specs(ds_id)
    assert {s["name"] for s in again} == names


# ------------------------------------------------------------------ #
# load_preview: bounded, read-only, disclosed
# ------------------------------------------------------------------ #
def test_load_preview_csv_is_bounded_and_disclosed(platform_dirs, tmp_path):
    ds_id = _register_csv(tmp_path, "big.csv", "id,score,cat\n" + _rows(500))
    out = adapter.load_preview(
        ds_id, VisualizationConfig(preview_rows=100))
    assert len(out.frame) == 100
    meta = out.meta
    assert meta["total_rows"] == 500
    assert meta["displayed_rows"] == 100
    assert meta["truncated"] is True
    sampling = meta["sampling"]
    assert sampling["applied"] is True
    assert sampling["approximate"] is True
    assert sampling["method"] == "first_n_rows"
    assert sampling["params"]["max_rows"] == 100
    assert any("Preview shows the first 100 of 500" in n
               for n in meta["notes"]), meta["notes"]
    assert out.source_kind == "csv"
    assert not _DRIVE_RE.search(out.source_label)


def test_load_preview_small_csv_reports_no_sampling(platform_dirs, tmp_path):
    ds_id = _register_csv(tmp_path, "small.csv", "id,score,cat\n" + _rows(20))
    out = adapter.load_preview(ds_id)
    assert len(out.frame) == 20
    assert out.meta["truncated"] is False
    assert out.sampling["applied"] is False
    assert any("read-only" in n for n in out.meta["notes"])


def test_load_preview_max_rows_overrides_cap(platform_dirs, tmp_path):
    ds_id = _register_csv(tmp_path, "cap.csv", "id,score,cat\n" + _rows(200))
    out = adapter.load_preview(ds_id, max_rows=25)
    assert len(out.frame) == 25
    assert out.meta["sampling"]["params"]["max_rows"] == 25


def test_load_preview_sqlite_is_read_only(platform_dirs, tmp_path):
    rows = [(i, float(i * 2), ["x", "y", "z"][i % 3])
            for i in range(300)]
    ds_id = _register_sqlite(
        tmp_path, "src.db", "records",
        ["id INTEGER", "score REAL", "cat TEXT"], rows)
    db_path = tmp_path / "src.db"
    before = sha256_file(db_path)
    out = adapter.load_preview(ds_id, max_rows=50)
    assert sha256_file(db_path) == before, "sqlite source was modified"
    assert len(out.frame) == 50
    assert out.source_kind == "sqlite"
    assert out.meta["truncated"] is True
    assert list(out.frame.columns) == ["id", "score", "cat"]


def test_load_preview_filesystem_gives_clear_error(platform_dirs, tmp_path):
    ds_id = _register_folder(tmp_path, "scanroot")
    with pytest.raises(PlatformError) as exc:
        adapter.load_preview(ds_id)
    assert "Archaeology" in exc.value.user_message
    _assert_no_path_leak(exc.value.user_message)


def test_load_preview_missing_source_error_is_path_free(platform_dirs,
                                                        tmp_path):
    ds_id = _register_csv(tmp_path, "vanish.csv",
                          "id,score,cat\n" + _rows(10))
    (tmp_path / "vanish.csv").unlink()
    with pytest.raises(PlatformError) as exc:
        adapter.load_preview(ds_id)
    msg = exc.value.user_message
    assert "no longer available" in msg
    _assert_no_path_leak(msg)
    assert str(tmp_path) not in msg
    assert str(tmp_path) not in (exc.value.detail or "")
    assert not _DRIVE_RE.search(exc.value.detail or "")


# ------------------------------------------------------------------ #
# load_column_values: bounded distinct values for filter pickers
# ------------------------------------------------------------------ #
def _frequency_csv(path: Path) -> str:
    lines = ["id,score,cat"]
    lines += [f"{i},1.0,x" for i in range(30)]
    lines += [f"{100 + i},1.0,y" for i in range(20)]
    lines += [f"{200 + i},1.0,z" for i in range(10)]
    return "\n".join(lines) + "\n"


def test_load_column_values_orders_and_flags_truncation(platform_dirs,
                                                        tmp_path):
    ds_id = _register_csv(tmp_path, "freq.csv", _frequency_csv(
        tmp_path))
    out = adapter.load_column_values(ds_id, "cat", limit=2)
    assert out["values"] == ["x", "y"]
    assert out["distinct_in_window"] == 3
    assert out["truncated"] is True
    assert out["rows_read"] == 60


def test_load_column_values_missing_column(platform_dirs, tmp_path):
    ds_id = _register_csv(tmp_path, "miss.csv", "id,score,cat\n" + _rows(10))
    with pytest.raises(PlatformError) as exc:
        adapter.load_column_values(ds_id, "nope")
    assert "not present" in exc.value.user_message
    _assert_no_path_leak(exc.value.user_message)


def test_load_column_values_sqlite(platform_dirs, tmp_path):
    rows = [(i, float(i), ["x", "y", "z"][i % 3]) for i in range(90)]
    ds_id = _register_sqlite(
        tmp_path, "vals.db", "records",
        ["id INTEGER", "score REAL", "cat TEXT"], rows)
    out = adapter.load_column_values(ds_id, "cat", limit=10)
    assert out["values"] == ["x", "y", "z"]
    assert out["truncated"] is False
    assert out["rows_read"] == 90


def test_load_column_values_rejects_filesystem(platform_dirs, tmp_path):
    ds_id = _register_folder(tmp_path, "scanroot2")
    with pytest.raises(PlatformError) as exc:
        adapter.load_column_values(ds_id, "file_name")
    assert "CSV and SQLite" in exc.value.user_message


# ------------------------------------------------------------------ #
# Options + sampling helpers
# ------------------------------------------------------------------ #
def test_dataset_options_sorted_labels(platform_dirs, tmp_path):
    _register_csv(tmp_path, "a1.csv", "id,score,cat\n" + _rows(5))
    _register_csv(tmp_path, "a2.csv", "id,score,cat\n" + _rows(5))
    options = adapter.dataset_options()
    assert len(options) == 2
    labels = [label for _, label in options]
    assert all(label.endswith("(csv)") for label in labels)
    assert labels == sorted(labels, key=str.lower)


def test_merge_sampling_combines_disclosures():
    a = {"applied": False, "method": "none", "params": {},
         "approximate": False}
    b = {"applied": True, "method": "first_n_rows",
         "params": {"max_rows": 100}, "approximate": True}
    merged = adapter.merge_sampling(a, b)
    assert merged["applied"] is True
    assert merged["approximate"] is True
    assert merged["method"] == "first_n_rows"
    assert merged["params"]["max_rows"] == 100
    assert adapter.merge_sampling(a, a)["applied"] is False


def test_sample_frame_is_deterministic_and_disclosed():
    rng = pd.Series(range(2000))
    frame = pd.DataFrame({"v": rng})
    cfg_free = VisualizationConfig()
    s1, d1 = adapter.sample_frame(frame, 200, cfg_free.seed, what="points")
    s2, d2 = adapter.sample_frame(frame, 200, cfg_free.seed, what="points")
    assert len(s1) == 200
    assert list(s1.index) == list(s2.index)
    assert d1["applied"] is True
    assert d1["approximate"] is True
    assert d1["params"]["seed"] == cfg_free.seed
    assert d1["params"]["target"] == "points"
    assert d2 == d1
    small, dsmall = adapter.sample_frame(frame.head(50), 200,
                                         cfg_free.seed)
    assert dsmall["applied"] is False
    assert len(small) == 50
