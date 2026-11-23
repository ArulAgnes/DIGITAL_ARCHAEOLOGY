"""run_pipeline.py CLI tests - all outputs redirected to pytest temp dirs."""
from __future__ import annotations

import sqlite3

import pytest

import config
import run_pipeline
from fixtures.synthetic import (assert_outside_research_data, make_sqlite,
                                write_csv)


@pytest.fixture()
def platform_dirs(tmp_path, monkeypatch):
    out = tmp_path / "platform_out"
    monkeypatch.setattr(config, "OUTPUT_DIR", out)
    monkeypatch.setattr(config, "OUTPUT_METADATA_DIR", out / "metadata")
    monkeypatch.setattr(config, "REGISTRY_DB",
                        out / "metadata" / "dataset_registry.db")
    return out


def _research_raw_signature() -> tuple[int, int] | None:
    if not config.FILES_CSV.exists():
        return None  # clean installation without research data
    stat = config.FILES_CSV.stat()
    return stat.st_size, stat.st_mtime_ns


def test_csv_happy_path_inspects_without_register(platform_dirs, tmp_path,
                                                  capsys):
    p = write_csv(tmp_path / "people.csv", "id,name\n1,Ada\n2,Grace\n")
    code = run_pipeline.main(["--input", str(p), "--no-register"])
    out = capsys.readouterr().out
    assert code == 0
    assert "DATASET INSPECTION" in out
    assert "csv" in out
    assert "2" in out
    assert not config.REGISTRY_DB.exists()
    assert (platform_dirs / "metadata").exists()
    for meta in (platform_dirs / "metadata").glob("*_inspect.json"):
        assert_outside_research_data(meta)


def test_csv_register_writes_registry_entry(platform_dirs, tmp_path, capsys):
    p = write_csv(tmp_path / "reg.csv", "a,b\n1,2\n")
    code = run_pipeline.main(["--input", str(p)])
    capsys.readouterr()
    assert code == 0
    assert config.REGISTRY_DB.exists()
    assert_outside_research_data(config.REGISTRY_DB)
    with sqlite3.connect(str(config.REGISTRY_DB)) as conn:
        n = conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]
        stype = conn.execute("SELECT source_type FROM datasets"
                             ).fetchone()[0]
    assert n == 1
    assert stype == "csv"


def test_missing_input_exits_2(platform_dirs, tmp_path, capsys):
    code = run_pipeline.main(["--input", str(tmp_path / "ghost.csv")])
    out = capsys.readouterr().out
    assert code == 2
    assert "does not exist" in out
    assert str(tmp_path) not in out


def test_folder_input_scans_into_platform_dirs(platform_dirs, tmp_path,
                                               capsys):
    root = tmp_path / "tree"
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("one\n", encoding="utf-8")
    (root / "sub" / "b.txt").write_text("two\n", encoding="utf-8")
    before = _research_raw_signature()
    code = run_pipeline.main(["--input", str(root)])
    capsys.readouterr()
    assert code == 0
    assert before == _research_raw_signature()
    datasets_dir = platform_dirs / "datasets"
    assert datasets_dir.exists()
    assert_outside_research_data(datasets_dir)
    produced = list(datasets_dir.glob("*/files_metadata.csv"))
    assert produced


def test_drive_root_exits_2_without_confirmation(platform_dirs, capsys):
    code = run_pipeline.main(["--input", "D:\\"])
    out = capsys.readouterr().out
    assert code == 2
    assert "confirmation" in out.lower()
    assert not (platform_dirs / "datasets").exists()


def test_sqlite_input_with_table_selection(platform_dirs, tmp_path, capsys):
    db = make_sqlite(tmp_path / "x.db", {
        "people": (["id INTEGER", "name TEXT"], [(1, "Ada")]),
        "tasks": (["id INTEGER", "title TEXT"], [(1, "t")]),
    })
    code = run_pipeline.main(["--input", str(db), "--table", "people"])
    out = capsys.readouterr().out
    assert code == 0
    assert "people" in out

    code2 = run_pipeline.main(["--input", str(db)])
    out2 = capsys.readouterr().out
    assert code2 == 2
    assert "table" in out2.lower()


def test_unsupported_binary_exits_2(platform_dirs, tmp_path, capsys):
    p = tmp_path / "blob.bin"
    p.write_bytes(b"\x00\x01\x02binary\x00")
    code = run_pipeline.main(["--input", str(p), "--mode", "csv"])
    out = capsys.readouterr().out
    assert code == 2
    assert "Input rejected" in out


def test_platform_output_never_touches_research_dirs(platform_dirs):
    assert_outside_research_data(platform_dirs)
    assert config.REGISTRY_DB != config.DATABASE_PATH
    assert str(config.REGISTRY_DB).startswith(str(config.OUTPUT_DIR))
