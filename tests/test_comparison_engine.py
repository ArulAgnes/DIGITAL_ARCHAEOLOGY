"""Phase 6 tests: the comparison engine (run_comparison + outputs).

All comparison outputs go to pytest temp dirs (platform_dirs fixture);
the research dataset and the research database are asserted untouched via
stat-only signatures. Sources are only ever opened read-only (SQLite via
``mode=ro``, CSV/folder scan artifacts by read).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import pytest

import config
from analysis.comparison import (ComparisonConfig, comparison_id_for,
                                 list_comparisons, load_comparison,
                                 run_comparison, validate_selection)
from analysis.comparison.filesystem_comparison import _FsDiff
from analysis.comparison.matching import (format_key, key_positions,
                                          normalize_value)
from core.exceptions import PlatformError
from fixtures.synthetic import (assert_outside_research_data, make_sqlite,
                                sha256_file, write_csv)
from ingestion import workflow

RUN_FILES_ALWAYS = ("config.json", "comparison_summary.json",
                    "warnings.json", "run_metadata.json")
RUN_FILES_COMPLETED = ("schema_differences.json",
                       "statistics_differences.json")


@pytest.fixture()
def platform_dirs(tmp_path, monkeypatch):
    out = tmp_path / "platform_out"
    inputs = tmp_path / "inputs"
    monkeypatch.setattr(config, "OUTPUT_DIR", out)
    monkeypatch.setattr(config, "OUTPUT_METADATA_DIR", out / "metadata")
    monkeypatch.setattr(config, "OUTPUT_DATASETS_DIR", out / "datasets")
    monkeypatch.setattr(config, "OUTPUT_COMPARISONS_DIR",
                        out / "comparisons")
    monkeypatch.setattr(config, "REGISTRY_DB",
                        out / "metadata" / "dataset_registry.db")
    monkeypatch.setattr(config, "INPUT_CSV_DIR", inputs)
    return out


def _research_signature() -> tuple | None:
    if not (config.FILES_CSV.exists() and config.FOLDERS_CSV.exists()):
        return None  # clean installation without research data
    out = []
    for path in (config.FILES_CSV, config.FOLDERS_CSV):
        stat = path.stat()
        out.append((str(path), stat.st_size, stat.st_mtime_ns))
    return tuple(out)


def _reg_csv(path: Path, text: str) -> str:
    write_csv(path, text)
    insp = workflow.inspect_csv(str(path))
    assert insp.ok, insp.error
    res = workflow.register(insp)
    assert res.ok, res.error
    return res.dataset_id


def _register_csv(tmp_path, name: str, text: str) -> str:
    return _reg_csv(tmp_path / name, text)


def _reg_folder(root: Path) -> str:
    insp = workflow.inspect_folder(str(root))
    assert insp.ok, insp.error
    res = workflow.register(insp, confirm_drive=False)
    assert res.ok, res.error
    return res.dataset_id


def _read_json(run_dir: Path, name: str) -> dict:
    return json.loads((run_dir / name).read_text(encoding="utf-8"))


def _csv_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return pd.read_csv(path).to_dict("records")


def _codes(res) -> list[str]:
    return [w.get("code") for w in res.warnings]


A_CLASSIC = ("id,score,cat\n"
             "1,10,x\n"
             "2,20,y\n"
             "3,30,z\n")
B_CLASSIC = ("id,score,cat\n"
             "1,10,x\n"
             "2,25,y\n"
             "4,40,x\n")


# ----------------------------------------------------------------- #
# Early validation / input errors
# ----------------------------------------------------------------- #
def test_unknown_kind_and_same_dataset_fail_early(platform_dirs):
    res = run_comparison("parquet", "a", "b")
    assert not res.ok and res.status == ""
    assert "Unknown comparison type" in res.error

    res2 = run_comparison("csv", "same", "same")
    assert not res2.ok
    assert "two different datasets" in res2.error

    res3 = run_comparison("csv", "unregistered_a", "unregistered_b")
    assert not res3.ok
    assert "registered" in res3.error


def test_keyed_comparison_counts_and_files(platform_dirs, tmp_path):
    before = _research_signature()
    ds_a = _register_csv(tmp_path, "a.csv", A_CLASSIC)
    ds_b = _register_csv(tmp_path, "b.csv", B_CLASSIC)

    res = run_comparison("csv", ds_a, ds_b)
    assert res.ok, res.error
    assert res.status == "completed"

    counts = res.summary["counts"]
    assert counts["matched"] == 1
    assert counts["modified"] == 1
    assert counts["added"] == 1
    assert counts["removed"] == 1
    assert (counts["a_rows"] == counts["matched"] + counts["modified"]
            + counts["removed"] + counts["duplicate_key_rows_a"]
            + counts["null_key_rows_a"] + counts["unindexed_rows_a"])
    assert res.summary["strategy"] == "key"
    assert res.summary["exactness"] == "exact"
    assert res.summary["keys"] == ["id"]

    run_dir = Path(res.run_dir)
    assert_outside_research_data(run_dir)
    for name in RUN_FILES_ALWAYS + RUN_FILES_COMPLETED + (
            "records_added.csv", "records_removed.csv",
            "records_modified.csv"):
        assert (run_dir / name).exists(), f"missing {name}"

    modified = _csv_rows(run_dir / "records_modified.csv")
    assert len(modified) == 1
    assert int(modified[0]["key"]) == 2
    assert "score: 20 -> 25" in str(modified[0]["changed_fields"])
    assert int(_csv_rows(run_dir / "records_added.csv")[0]["key"]) == 4
    assert int(_csv_rows(run_dir / "records_removed.csv")[0]["key"]) == 3

    summary = _read_json(run_dir, "comparison_summary.json")
    assert summary["status"] == "completed"
    assert summary["warnings_count"] == len(res.warnings)
    assert summary["datasets"]["a"]["dataset_id"] == ds_a
    assert summary["datasets"]["b"]["dataset_id"] == ds_b

    stages = [e["stage"] for e in res.progress]
    assert stages[0] == "verify"
    assert "load" in stages and "statistics" in stages
    assert stages[-1] == "outputs"

    assert _research_signature() == before


def test_duplicate_keys_counted_separately(platform_dirs, tmp_path):
    ds_a = _register_csv(tmp_path, "dup_a.csv",
                         "id,v\n1,a\n1,b\n2,c\n")
    ds_b = _register_csv(tmp_path, "dup_b.csv", "id,v\n1,a\n2,c\n")
    res = run_comparison("csv", ds_a, ds_b)
    assert res.ok, res.error
    counts = res.summary["counts"]
    assert counts["duplicate_key_rows_a"] == 2
    assert counts["duplicate_key_rows_b"] == 1
    assert counts["matched"] == 1
    assert counts["added"] == 0
    assert counts["removed"] == 0
    assert counts["modified"] == 0
    assert "duplicate_keys" in _codes(res)
    assert (counts["a_rows"] == counts["matched"] + counts["removed"]
            + counts["modified"] + counts["duplicate_key_rows_a"]
            + counts["null_key_rows_a"] + counts["unindexed_rows_a"])


def test_null_keys_counted_separately(platform_dirs, tmp_path):
    ds_a = _register_csv(tmp_path, "null_a.csv", "id,v\n,missing\n2,c\n")
    ds_b = _register_csv(tmp_path, "null_b.csv", "id,v\n,x\n2,c\n")
    res = run_comparison("csv", ds_a, ds_b)
    assert res.ok, res.error
    counts = res.summary["counts"]
    assert counts["null_key_rows_a"] == 1
    assert counts["null_key_rows_b"] == 1
    assert counts["matched"] == 1
    assert "null_keys" in _codes(res)
    assert (counts["a_rows"] == counts["matched"] + counts["modified"]
            + counts["removed"] + counts["duplicate_key_rows_a"]
            + counts["null_key_rows_a"] + counts["unindexed_rows_a"])


def test_no_key_fallback_uses_content_strategy(platform_dirs, tmp_path):
    ds_a = _register_csv(tmp_path, "c_a.csv",
                         "name,amount\nalpha,1\nbeta,2\n")
    ds_b = _register_csv(tmp_path, "c_b.csv",
                         "name,amount\nalpha,1\nbeta,3\ngamma,4\n")
    res = run_comparison("csv", ds_a, ds_b, config_obj=ComparisonConfig(
        keys=()))
    assert res.ok, res.error
    assert res.summary["strategy"] == "content-multiset"
    assert res.summary["exactness"] == "exact"
    assert res.summary["keys"] == []
    counts = res.summary["counts"]
    assert counts["matched"] == 1
    assert counts["modified"] == 0
    assert counts["removed"] == 1
    assert counts["added"] == 2
    assert "no_key_columns" in _codes(res)
    assert len(res.summary["limitations"]) == 3


def test_explicit_missing_key_column_fails_with_guidance(
        platform_dirs, tmp_path):
    ds_a = _register_csv(tmp_path, "m_a.csv", A_CLASSIC)
    ds_b = _register_csv(tmp_path, "m_b.csv", B_CLASSIC)
    res = run_comparison("csv", ds_a, ds_b, config_obj=ComparisonConfig(
        keys=("nope",)))
    assert not res.ok
    assert res.status == "failed"
    assert "nope" in res.error
    assert "failed" in _codes(res)

    run_dir = Path(res.run_dir)
    for name in RUN_FILES_ALWAYS:
        assert (run_dir / name).exists(), f"missing {name}"
    summary = _read_json(run_dir, "comparison_summary.json")
    assert summary["status"] == "failed"
    assert summary["exactness"] == "not-run"
    meta = _read_json(run_dir, "run_metadata.json")
    assert meta["status"] == "failed"
    assert meta["error"] == res.error


def test_schema_dtype_changes_and_stats_basis_mismatch(platform_dirs,
                                                       tmp_path):
    ds_a = _register_csv(tmp_path, "d_a.csv", "id,val\n1,10\n2,20\n")
    ds_b = _register_csv(tmp_path, "d_b.csv",
                         "id,val\n1,ten\n2,twenty\n")
    res = run_comparison("csv", ds_a, ds_b)
    assert res.ok, res.error
    run_dir = Path(res.run_dir)

    schema = _read_json(run_dir, "schema_differences.json")
    changes = schema["columns"]["dtype_changes"]
    assert any(c["column"] == "val" and c["a_dtype"] != c["b_dtype"]
               for c in changes)
    assert schema["row_counts"] == {"a": 2, "b": 2}

    assert "stats_basis_mismatch" in _codes(res)
    counts = res.summary["counts"]
    assert counts["modified"] == 2
    assert counts["added"] == 0
    assert counts["removed"] == 0


def test_statistical_deltas_match_manual_expectation(platform_dirs,
                                                     tmp_path):
    ds_a = _register_csv(tmp_path, "s_a.csv",
                         "id,score,cat\n1,10,x\n2,20,\n3,30,z\n")
    ds_b = _register_csv(tmp_path, "s_b.csv",
                         "id,score,cat\n1,10,x\n2,25,x\n4,40,x\n")
    res = run_comparison("csv", ds_a, ds_b)
    assert res.ok, res.error
    stats = _read_json(Path(res.run_dir), "statistics_differences.json")
    assert stats["basis"] == "exact"

    score = stats["columns"]["score"]
    assert score["basis"] == "numeric"
    assert score["a"]["count"] == 3
    assert score["b"]["count"] == 3
    assert score["a"]["mean"] == pytest.approx(20.0, abs=1e-9)
    assert score["b"]["mean"] == pytest.approx(25.0, abs=1e-9)
    assert score["delta"]["mean"] == pytest.approx(5.0, abs=1e-9)
    assert score["delta"]["max"] == pytest.approx(10.0, abs=1e-9)

    cat = stats["columns"]["cat"]
    assert cat["basis"] == "categorical"
    assert cat["delta"]["missing"] == -1
    assert cat["delta"]["unique"] == -1
    top = {row["value"]: row for row in cat["top_values"]}
    assert top["x"]["a"] == 1 and top["x"]["b"] == 3


def test_statistics_can_be_disabled(platform_dirs, tmp_path):
    ds_a = _register_csv(tmp_path, "n_a.csv", "id,v\n1,1\n")
    ds_b = _register_csv(tmp_path, "n_b.csv", "id,v\n1,2\n")
    res = run_comparison("csv", ds_a, ds_b, config_obj=ComparisonConfig(
        stats_enabled=False))
    assert res.ok, res.error
    stats = _read_json(Path(res.run_dir), "statistics_differences.json")
    assert stats["status"] == "disabled"


def test_export_truncation_keeps_exact_totals(platform_dirs, tmp_path):
    a_text = "id,v\n" + "".join(f"{i},{i}\n" for i in range(1, 4))
    b_text = "id,v\n" + "".join(f"{i},{i}\n" for i in range(1, 11))
    ds_a = _register_csv(tmp_path, "t_a.csv", a_text)
    ds_b = _register_csv(tmp_path, "t_b.csv", b_text)
    res = run_comparison("csv", ds_a, ds_b, config_obj=ComparisonConfig(
        max_export_rows=3))
    assert res.ok, res.error
    counts = res.summary["counts"]
    assert counts["added"] == 7
    assert res.summary["truncated"]["records_added.csv"] == {
        "exported": 3, "total": 7}
    assert "truncated" in _codes(res)
    run_dir = Path(res.run_dir)
    assert len(_csv_rows(run_dir / "records_added.csv")) == 3
    warnings = _read_json(run_dir, "warnings.json")
    assert any("exported 3 of 7" in w["message"] for w in warnings)


def test_key_index_overflow_downgrades_to_approximate(platform_dirs,
                                                      tmp_path):
    n = 1200
    a_text = "id,v\n" + "".join(f"{i},{i}\n" for i in range(1, n + 1))
    ds_a = _register_csv(tmp_path, "ov_a.csv", a_text)
    ds_b = _register_csv(tmp_path, "ov_b.csv", a_text)
    res = run_comparison("csv", ds_a, ds_b, config_obj=ComparisonConfig(
        max_keys=1000))
    assert res.ok, res.error
    assert res.summary["exactness"] == "approximate"
    counts = res.summary["counts"]
    assert counts["unindexed_rows_a"] == 200
    assert counts["added"] == 200
    assert counts["matched"] == 1000
    codes = _codes(res)
    assert "key_index_limit" in codes
    assert "key_count_limit" in codes


def test_chunk_size_does_not_change_results(platform_dirs, tmp_path):
    n = 2500
    text = "id,v,cat\n" + "".join(
        f"{i},{i * 2},{'xy'[i % 2]}\n" for i in range(1, n + 1))
    ds_a = _register_csv(tmp_path, "ch_a.csv", text)
    ds_b = _register_csv(tmp_path, "ch_b.csv", text.replace(
        "5,10,x", "5,999,y"))

    res_small = run_comparison("csv", ds_a, ds_b,
                               config_obj=ComparisonConfig(chunk_size=1000))
    res_big = run_comparison("csv", ds_a, ds_b,
                             config_obj=ComparisonConfig(chunk_size=50000))
    assert res_small.ok and res_big.ok

    dir_a, dir_b = Path(res_small.run_dir), Path(res_big.run_dir)
    for name in ("comparison_summary.json", "schema_differences.json",
                 "statistics_differences.json", "warnings.json",
                 "records_added.csv", "records_removed.csv",
                 "records_modified.csv"):
        assert (dir_a / name).read_bytes() == (dir_b / name).read_bytes(), \
            f"{name} differs between chunk sizes"
    assert res_small.summary["counts"] == res_big.summary["counts"]


def test_repeated_runs_are_byte_reproducible(platform_dirs, tmp_path):
    ds_a = _register_csv(tmp_path, "r_a.csv", A_CLASSIC)
    ds_b = _register_csv(tmp_path, "r_b.csv", B_CLASSIC)
    res1 = run_comparison("csv", ds_a, ds_b)
    res2 = run_comparison("csv", ds_a, ds_b)
    assert res1.ok and res2.ok
    assert res1.comparison_id == res2.comparison_id
    assert res1.run_id != res2.run_id

    dir1, dir2 = Path(res1.run_dir), Path(res2.run_dir)
    for name in ("comparison_summary.json", "schema_differences.json",
                 "statistics_differences.json", "warnings.json",
                 "records_added.csv", "records_removed.csv",
                 "records_modified.csv"):
        assert (dir1 / name).read_bytes() == (dir2 / name).read_bytes(), \
            f"{name} not byte-identical across runs"


def test_changed_source_is_refused_with_evidence(platform_dirs, tmp_path):
    ds_a = _register_csv(tmp_path, "ref_a.csv", A_CLASSIC)
    ds_b = _register_csv(tmp_path, "ref_b.csv", B_CLASSIC)
    (tmp_path / "ref_a.csv").write_text("id,score,cat\n9,99,z\n",
                                        encoding="utf-8")

    res = run_comparison("csv", ds_a, ds_b)
    assert not res.ok
    assert res.status == "refused"
    assert res.summary["status"] == "refused"
    assert res.summary["guidance"]
    assert "a" in res.summary["verification"] and \
        "b" in res.summary["verification"]
    assert any(str(c).startswith("source_") for c in _codes(res))

    run_dir = Path(res.run_dir)
    for name in RUN_FILES_ALWAYS + RUN_FILES_COMPLETED:
        assert (run_dir / name).exists(), f"missing {name}"


def test_validation_detects_changed_source_and_bad_keys(platform_dirs,
                                                        tmp_path):
    ds_a = _register_csv(tmp_path, "v_a.csv", A_CLASSIC)
    ds_b = _register_csv(tmp_path, "v_b.csv", B_CLASSIC)

    good = validate_selection("csv", ds_a, ds_b, ["id"])
    assert good["ok"] is True
    assert good["keys"] == ["id"]
    assert any("key columns: id" in d for d in good["details"])

    auto = validate_selection("csv", ds_a, ds_b, [])
    assert auto["ok"] is True
    assert auto["keys"] == ["id"]
    assert any("auto-detected" in d for d in auto["details"])

    bad = validate_selection("csv", ds_a, ds_b, ["missing_col"])
    assert bad["ok"] is False
    assert any("missing_col" in e for e in bad["errors"])

    same = validate_selection("csv", ds_a, ds_a, [])
    assert same["ok"] is False
    assert any("two different" in e for e in same["errors"])

    wrong = validate_selection("sqlite", ds_a, ds_b, [])
    assert wrong["ok"] is False
    assert any("not a sqlite dataset" in e for e in wrong["errors"])

    missing = validate_selection("csv", "nope_a", "nope_b", [])
    assert missing["ok"] is False
    assert any("registered" in e for e in missing["errors"])

    (tmp_path / "v_a.csv").write_text("id,score,cat\n7,77,q\n",
                                      encoding="utf-8")
    changed = validate_selection("csv", ds_a, ds_b, ["id"])
    assert changed["ok"] is False
    assert changed["errors"]


def test_run_dir_isolated_and_sources_untouched(platform_dirs, tmp_path):
    before = _research_signature()
    p_a = tmp_path / "iso_a.csv"
    p_b = tmp_path / "iso_b.csv"
    write_csv(p_a, A_CLASSIC)
    write_csv(p_b, B_CLASSIC)
    ds_a, ds_b = _reg_csv(p_a, A_CLASSIC), _reg_csv(p_b, B_CLASSIC)
    hash_a, hash_b = sha256_file(p_a), sha256_file(p_b)

    res = run_comparison("csv", ds_a, ds_b)
    assert res.ok, res.error
    assert_outside_research_data(Path(res.run_dir))
    assert sha256_file(p_a) == hash_a
    assert sha256_file(p_b) == hash_b
    assert _research_signature() == before


def test_list_and_load_comparison_roundtrip(platform_dirs, tmp_path):
    ds_a = _register_csv(tmp_path, "l_a.csv", A_CLASSIC)
    ds_b = _register_csv(tmp_path, "l_b.csv", B_CLASSIC)
    assert list_comparisons() == []

    res = run_comparison("csv", ds_a, ds_b)
    assert res.ok, res.error
    history = list_comparisons()
    assert len(history) == 1
    assert history[0]["comparison_id"] == res.comparison_id
    assert history[0]["run_id"] == res.run_id
    assert history[0]["status"] == "completed"

    loaded = load_comparison(res.comparison_id, res.run_id)
    assert loaded["comparison_summary"]["status"] == "completed"
    assert loaded["run_metadata"]["kind"] == "csv"
    assert loaded["tables"]["records_added.csv"]
    assert Path(loaded["files"]["comparison_summary.json"]).exists()

    with pytest.raises(PlatformError):
        load_comparison("..", res.run_id)


def test_comparison_id_is_stable_and_pair_sorted():
    assert comparison_id_for("csv", "aaa", "bbb") == \
        comparison_id_for("csv", "bbb", "aaa")
    assert comparison_id_for("csv", "aaa", "bbb") != \
        comparison_id_for("sqlite", "aaa", "bbb")


# ----------------------------------------------------------------- #
# SQLite comparisons
# ----------------------------------------------------------------- #
def test_sqlite_table_diff_row_diff_and_read_only(platform_dirs,
                                                  tmp_path):
    db_a = make_sqlite(tmp_path / "a.db", {
        "people": (["id INTEGER", "name TEXT"],
                   [(1, "ann"), (2, "bob"), (3, "cid")]),
    })
    db_b = make_sqlite(tmp_path / "b.db", {
        "people": (["id INTEGER", "name TEXT"],
                   [(1, "ann"), (2, "bobby"), (4, "dee")]),
        "extra_t": (["x INTEGER"], [(1,)]),
    })
    hash_a, hash_b = sha256_file(db_a), sha256_file(db_b)

    insp_a = workflow.inspect_sqlite(str(db_a), table="people")
    insp_b = workflow.inspect_sqlite(str(db_b), table="people")
    assert insp_a.ok and insp_b.ok
    ds_a = workflow.register(insp_a).dataset_id
    ds_b = workflow.register(insp_b).dataset_id

    res = run_comparison("sqlite", ds_a, ds_b)
    assert res.ok, res.error
    counts = res.summary["counts"]
    assert counts["matched"] == 1
    assert counts["modified"] == 1
    assert counts["removed"] == 1
    assert counts["added"] == 1
    assert res.summary["tables"] == {"a": "people", "b": "people"}
    assert res.summary["strategy"] == "key"

    schema = _read_json(Path(res.run_dir), "schema_differences.json")
    assert schema["table_list_diff"]["b_only"] == ["extra_t"]
    assert schema["tables"] == {"a": "people", "b": "people"}
    assert schema["row_counts"] == {"a": 3, "b": 3}
    assert schema["columns"]["dtype_changes"] == []

    modified = _csv_rows(Path(res.run_dir) / "records_modified.csv")
    assert len(modified) == 1
    assert "name: bob -> bobby" in str(modified[0]["changed_fields"])

    assert sha256_file(db_a) == hash_a, "sqlite A modified!"
    assert sha256_file(db_b) == hash_b, "sqlite B modified!"


# ----------------------------------------------------------------- #
# Filesystem comparisons
# ----------------------------------------------------------------- #
def _fs_pair(tmp_path: Path) -> tuple[Path, Path]:
    root_a = tmp_path / "tree_a"
    root_b = tmp_path / "tree_b"
    for r in (root_a, root_b):
        r.mkdir(parents=True)
    (root_a / "keep.txt").write_text("same", encoding="utf-8")
    (root_a / "change.txt").write_text("AAAA", encoding="utf-8")
    (root_a / "gone.txt").write_text("x", encoding="utf-8")
    (root_a / "move.dat").write_text("payload", encoding="utf-8")
    (root_a / "Readme.txt").write_text("doc", encoding="utf-8")

    (root_b / "keep.txt").write_text("same", encoding="utf-8")
    (root_b / "change.txt").write_text("BBBBBB", encoding="utf-8")
    (root_b / "new.txt").write_text("y", encoding="utf-8")
    (root_b / "sub").mkdir()
    (root_b / "sub" / "move.dat").write_text("payload", encoding="utf-8")
    (root_b / "readme.txt").write_text("doc", encoding="utf-8")

    fixed = (1_760_000_000, 1_760_000_000)
    for p in (root_a / "keep.txt", root_b / "keep.txt",
              root_a / "move.dat", root_b / "sub" / "move.dat",
              root_a / "Readme.txt", root_b / "readme.txt",
              root_a / "change.txt", root_b / "change.txt"):
        os.utime(p, fixed)
    return root_a, root_b


def test_filesystem_identical_trees_have_no_diffs(platform_dirs,
                                                  tmp_path):
    root_a = tmp_path / "same_a"
    root_b = tmp_path / "same_b"
    for r in (root_a, root_b):
        r.mkdir()
        (r / "one.txt").write_text("1", encoding="utf-8")
        (r / "two.txt").write_text("2", encoding="utf-8")
    os.utime(root_a / "one.txt", (1_760_000_000, 1_760_000_000))
    os.utime(root_b / "one.txt", (1_760_000_000, 1_760_000_000))
    os.utime(root_a / "two.txt", (1_760_000_000, 1_760_000_000))
    os.utime(root_b / "two.txt", (1_760_000_000, 1_760_000_000))

    ds_a, ds_b = _reg_folder(root_a), _reg_folder(root_b)
    res = run_comparison("filesystem", ds_a, ds_b)
    assert res.ok, res.error
    counts = res.summary["counts"]
    assert counts["unchanged"] == 2
    assert counts["added"] == 0
    assert counts["removed"] == 0
    assert counts["modified"] == 0
    assert counts["moved"] == 0
    assert counts["case_changes"] == 0
    assert res.summary["strategy"] == "relative-path"
    assert res.summary["exactness"] == "exact"
    assert res.warnings == []


def test_filesystem_full_journey_counts_and_exports(platform_dirs,
                                                    tmp_path):
    before = _research_signature()
    root_a, root_b = _fs_pair(tmp_path)
    ds_a, ds_b = _reg_folder(root_a), _reg_folder(root_b)

    res = run_comparison("filesystem", ds_a, ds_b)
    assert res.ok, res.error
    counts = res.summary["counts"]
    assert counts["files_a"] == 5
    assert counts["files_b"] == 5
    assert counts["unchanged"] == 1
    assert counts["modified"] == 2
    assert counts["added"] == 1
    assert counts["removed"] == 1
    assert counts["moved"] == 1
    assert counts["case_changes"] == 1
    assert (counts["files_a"] == counts["unchanged"] + counts["modified"]
            + counts["removed"] + counts["moved"] + counts["renamed"]
            + counts["duplicate_paths_a"]
            + counts["unindexed_rows_a"])
    assert (counts["files_b"] == counts["unchanged"] + counts["modified"]
            + counts["added"] + counts["moved"] + counts["renamed"]
            + counts["duplicate_paths_b"])
    assert res.summary["move_detection"] == "analysed"
    assert res.summary["exactness"] == "exact"
    assert any("metadata only" in item
               for item in res.summary["limitations"])

    run_dir = Path(res.run_dir)
    assert_outside_research_data(run_dir)
    added = _csv_rows(run_dir / "added_files.csv")
    removed = _csv_rows(run_dir / "removed_files.csv")
    modified = _csv_rows(run_dir / "modified_files.csv")
    moved = _csv_rows(run_dir / "moved_files.csv")
    assert len(added) == counts["added"] == 1
    assert added[0]["path"].endswith("new.txt")
    assert len(removed) == counts["removed"] == 1
    assert removed[0]["path"].endswith("gone.txt")
    assert len(modified) == counts["modified"] == 2
    case_rows = [r for r in modified
                 if "case_change" in str(r["change_kinds"])]
    assert len(case_rows) == 1
    assert len(moved) == counts["moved"] + counts["renamed"] == 1
    assert moved[0]["change"] == "moved"
    assert moved[0]["path_from"].endswith("move.dat")
    assert str(moved[0]["path_to"]).replace("\\", "/").endswith(
        "sub/move.dat")

    stats = _read_json(run_dir, "statistics_differences.json")
    assert stats["storage"]["a_total_bytes"] == 19
    assert stats["storage"]["b_total_bytes"] == 21
    assert stats["storage"]["delta_bytes"] == 2
    assert stats["extensions"]["distinct_a"] >= 2

    schema = _read_json(run_dir, "schema_differences.json")
    assert schema["folders"]["status"] == "ok"
    assert schema["folders"]["a_only_count"] == 0
    assert any(str(p).endswith("sub")
               for p in schema["folders"]["b_only"])
    assert "file_name" in schema["files"]["columns_compared"]
    assert "full_file_path" not in schema["files"]["columns_compared"]

    assert _research_signature() == before


def test_filesystem_case_only_path_change(platform_dirs, tmp_path):
    root_a = tmp_path / "case_a"
    root_b = tmp_path / "case_b"
    for r in (root_a, root_b):
        r.mkdir()
        (r / "keep.txt").write_text("k", encoding="utf-8")
    (root_a / "Readme.txt").write_text("doc", encoding="utf-8")
    (root_b / "readme.txt").write_text("doc", encoding="utf-8")
    fixed = (1_760_000_000, 1_760_000_000)
    for p in (root_a / "keep.txt", root_b / "keep.txt",
              root_a / "Readme.txt", root_b / "readme.txt"):
        os.utime(p, fixed)

    ds_a, ds_b = _reg_folder(root_a), _reg_folder(root_b)
    res = run_comparison("filesystem", ds_a, ds_b)
    assert res.ok, res.error
    counts = res.summary["counts"]
    assert counts["case_changes"] == 1
    assert counts["added"] == 0
    assert counts["removed"] == 0
    assert counts["modified"] + counts["unchanged"] == 2
    if counts["modified"]:
        modified = _csv_rows(Path(res.run_dir) / "modified_files.csv")
        assert any("case_change" in str(r["change_kinds"])
                   for r in modified)


def test_filesystem_duplicate_basenames_are_informational(
        platform_dirs, tmp_path):
    root_a = tmp_path / "dup_a"
    root_b = tmp_path / "dup_b"
    for r in (root_a, root_b):
        (r / "dir1").mkdir(parents=True)
        (r / "dir2").mkdir()
        (r / "dir1" / "x.txt").write_text("1", encoding="utf-8")
        (r / "dir2" / "x.txt").write_text("2", encoding="utf-8")
        os.utime(r / "dir1" / "x.txt", (1_760_000_000, 1_760_000_000))
        os.utime(r / "dir2" / "x.txt", (1_760_000_000, 1_760_000_000))

    ds_a, ds_b = _reg_folder(root_a), _reg_folder(root_b)
    res = run_comparison("filesystem", ds_a, ds_b)
    assert res.ok, res.error
    stats = _read_json(Path(res.run_dir), "statistics_differences.json")
    assert stats["duplicate_basenames"]["a_groups"] == 1
    assert stats["duplicate_basenames"]["b_groups"] == 1
    assert stats["duplicate_basenames"]["delta"] == 0
    counts = res.summary["counts"]
    assert counts["duplicate_paths_a"] == 0
    assert counts["unchanged"] == 2


def test_filesystem_rename_detection_unit(tmp_path):
    """Unit-level: a same-folder rename pairs via parent evidence."""
    cfg = ComparisonConfig()
    diff = _FsDiff(cfg, ["file_name", "file_size_bytes",
                         "modified_timestamp"], None, None)
    diff.feed_a([{"full_file_path": "P/old.txt", "parent_folder": "P",
                  "file_name": "old.txt", "file_size_bytes": 5,
                  "modified_timestamp": "2026-01-01 00:00:00"}])
    diff.feed_b([{"full_file_path": "P/new.txt", "parent_folder": "P",
                  "file_name": "new.txt", "file_size_bytes": 5,
                  "modified_timestamp": "2026-01-01 00:00:00"}])
    assert diff.detect_moves() == "analysed"
    diff.finish_exports()
    assert diff.counts["renamed"] == 1
    assert diff.counts["added"] == 0
    assert diff.counts["removed"] == 0
    assert len(diff.export["moved"]) == 1
    assert diff.export["moved"][0][0] == "renamed"
    assert diff.export["added"] == []


def test_filesystem_validation_and_refusal_on_missing_artifact(
        platform_dirs, tmp_path):
    root_a, root_b = _fs_pair(tmp_path)
    ds_a, ds_b = _reg_folder(root_a), _reg_folder(root_b)
    ok = validate_selection("filesystem", ds_a, ds_b, [])
    assert ok["ok"] is True
    assert ok["keys"] == []
    assert any("normalized relative path" in d for d in ok["details"])

    artifact = (Path(config.OUTPUT_DIR) / "datasets" / ds_a
                / "files_metadata.csv")
    assert artifact.exists()
    artifact.unlink()
    res = run_comparison("filesystem", ds_a, ds_b)
    assert not res.ok
    assert res.status == "refused" or res.status == "failed"
    assert res.error


# ----------------------------------------------------------------- #
# Unit tests: value normalisation and key helpers
# ----------------------------------------------------------------- #
def test_normalize_value_canonicalises_types():
    assert normalize_value(1.0) == 1
    assert normalize_value(1.5) == 1.5
    assert normalize_value(float("nan")) is None
    assert normalize_value(None) is None
    assert normalize_value(pd.NA) is None
    assert normalize_value(True) is True
    assert normalize_value(3) == 3
    assert normalize_value("3") == "3"
    assert normalize_value(pd.Timestamp("2026-01-02T03:04:05")).startswith(
        "2026-01-02")


def test_key_positions_and_format_key():
    assert key_positions(["id", "v"], ["id"]) == [0]
    with pytest.raises(PlatformError):
        key_positions(["id"], ["missing"])
    assert format_key(None) == ""
    assert format_key((1, None)) == "1 | (missing)"
    long = "x" * 200
    assert len(format_key((long,))) <= 63


def test_matching_normalization_row_and_union():
    from analysis.comparison.matching import normalize_row, \
        _union_columns
    assert normalize_row([1.0, None, "a"]) == (1, None, "a")
    assert _union_columns(["a", "b"], ["b", "c"]) == ["a", "b", "c"]
