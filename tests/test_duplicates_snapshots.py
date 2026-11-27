"""Tests for duplicate detection (L1 metadata + L2 SHA-256) and snapshots."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from scanner import duplicate_detector as dd
from scanner import snapshot_manager as sm


def _files_df(rows):
    return pd.DataFrame(rows)


class TestDuplicateCandidates:
    def test_same_name_size_ext_different_paths(self):
        df = _files_df([
            {"file_name": "a.txt", "file_extension": ".txt",
             "file_size_bytes": 100, "full_file_path": r"D:\x\a.txt"},
            {"file_name": "a.txt", "file_extension": ".txt",
             "file_size_bytes": 100, "full_file_path": r"D:\y\a.txt"},
        ])
        cand = dd.find_candidates(df)
        assert len(cand) == 1
        assert cand.iloc[0]["copy_count"] == 2
        assert cand.iloc[0]["status"] == "candidate_not_verified"

    def test_different_size_not_candidate(self):
        df = _files_df([
            {"file_name": "a.txt", "file_extension": ".txt",
             "file_size_bytes": 100, "full_file_path": r"D:\x\a.txt"},
            {"file_name": "a.txt", "file_extension": ".txt",
             "file_size_bytes": 200, "full_file_path": r"D:\y\a.txt"},
        ])
        assert dd.find_candidates(df).empty

    def test_same_path_not_cross_duplicate(self):
        df = _files_df([
            {"file_name": "a.txt", "file_extension": ".txt",
             "file_size_bytes": 100, "full_file_path": r"D:\x\a.txt"},
            {"file_name": "a.txt", "file_extension": ".txt",
             "file_size_bytes": 100, "full_file_path": r"D:\x\a.txt"},
        ])
        assert dd.find_candidates(df).empty

    def test_empty_input(self):
        assert dd.find_candidates(pd.DataFrame()).empty

    def test_three_copies_one_group(self):
        df = _files_df([
            {"file_name": "a.txt", "file_extension": ".txt",
             "file_size_bytes": 10, "full_file_path": rf"D:\p{i}\a.txt"}
            for i in range(3)
        ])
        cand = dd.find_candidates(df)
        assert len(cand) == 1
        assert cand.iloc[0]["copy_count"] == 3


class TestSha256Verification:
    def test_hash_matches_hashlib(self, tmp_path):
        p = tmp_path / "f.bin"
        p.write_bytes(b"hello world")
        assert dd._sha256(str(p)) == hashlib.sha256(b"hello world").hexdigest()

    def test_disabled_by_default_returns_empty(self):
        df = _files_df([
            {"file_name": "a.txt", "file_extension": ".txt",
             "file_size_bytes": 10, "full_file_path": r"D:\x\a.txt",
             "duplicate_group_id": "DC000001", "paths": r"D:\x\a.txt"},
        ])
        out = dd.verify_candidates(df, enabled=False)
        assert out.empty
        assert list(out.columns) == dd.VERIFIED_FIELDS

    def test_verified_group_created(self, tmp_path):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_bytes(b"identical bytes")
        b.write_bytes(b"identical bytes")
        cand = _files_df([{
            "duplicate_group_id": "DC000001", "file_name": "a.txt",
            "file_extension": ".txt", "file_size_bytes": 15,
            "paths": f"{a} | {b}",
        }])
        ver = dd.verify_candidates(cand, enabled=True)
        assert len(ver) == 1
        assert ver.iloc[0]["status"] == "verified_exact_duplicate"
        assert ver.iloc[0]["copy_count"] == 2
        assert ver.iloc[0]["extra_copies"] == 1
        assert ver.iloc[0]["sha256"] == hashlib.sha256(
            b"identical bytes").hexdigest()

    def test_different_content_not_verified(self, tmp_path):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_bytes(b"one")
        b.write_bytes(b"two")
        cand = _files_df([{
            "duplicate_group_id": "DC000002", "file_name": "a.txt",
            "file_extension": ".txt", "file_size_bytes": 3,
            "paths": f"{a} | {b}",
        }])
        ver = dd.verify_candidates(cand, enabled=True)
        assert ver.empty

    def test_missing_file_skipped(self, tmp_path):
        a = tmp_path / "exists.txt"
        a.write_bytes(b"data")
        cand = _files_df([{
            "duplicate_group_id": "DC000003", "file_name": "exists.txt",
            "file_extension": ".txt", "file_size_bytes": 4,
            "paths": f"{a} | {tmp_path / 'missing.txt'}",
        }])
        ver = dd.verify_candidates(cand, enabled=True)
        assert ver.empty

    def test_summary_counts(self):
        cand = _files_df([
            {"file_size_bytes": 10, "copy_count": 2},
            {"file_size_bytes": 20, "copy_count": 3},
        ])
        ver = _files_df([
            {"copy_count": 2, "extra_copies": 1, "extra_copy_bytes": 10},
        ])
        s = dd.summarise(cand, ver)
        assert s["candidate_groups"] == 2
        assert s["candidate_extra_copies"] == (2 - 1) + (3 - 1)
        assert s["verified_groups"] == 1
        assert s["verified_extra_bytes"] == 10


class TestSnapshots:
    def _mk_scan_report(self, sid):
        return {"scan_id": sid, "started_at": "2026-01-01T00:00:00",
                "finished_at": "2026-01-01T00:01:00",
                "duration_seconds": 60, "status": "completed",
                "root": "D:\\", "files_scanned": 2, "folders_scanned": 1,
                "total_size_bytes": 300, "errors": 0}

    def test_create_and_list(self, scanner_config, tmp_path):
        files = _files_df([
            {"full_file_path": r"D:\a.txt", "modified_timestamp": "t1",
             "file_size_bytes": 100, "root_folder": "a",
             "file_extension": ".txt"},
            {"full_file_path": r"D:\b.txt", "modified_timestamp": "t2",
             "file_size_bytes": 200, "root_folder": "b",
             "file_extension": ".txt"},
        ])
        folders = _files_df([
            {"folder_path": r"D:\a", "total_size_bytes": 100},
        ])
        sm.create_snapshot(self._mk_scan_report("S1"), files, folders)
        snaps = sm.list_snapshots()
        assert len(snaps) == 1
        assert snaps[0]["snapshot_id"] == "S1"

    def test_compare_detects_new_removed_modified(self, scanner_config):
        f1 = _files_df([
            {"full_file_path": r"D:\a.txt", "modified_timestamp": "t1",
             "file_size_bytes": 100, "root_folder": "r",
             "file_extension": ".txt"},
            {"full_file_path": r"D:\gone.txt", "modified_timestamp": "t1",
             "file_size_bytes": 50, "root_folder": "r",
             "file_extension": ".txt"},
        ])
        f2 = _files_df([
            {"full_file_path": r"D:\a.txt", "modified_timestamp": "t9",
             "file_size_bytes": 100, "root_folder": "r",
             "file_extension": ".txt"},
            {"full_file_path": r"D:\new.txt", "modified_timestamp": "t2",
             "file_size_bytes": 75, "root_folder": "r",
             "file_extension": ".txt"},
        ])
        folders = _files_df([{"folder_path": r"D:\r", "total_size_bytes": 1}])
        sm.create_snapshot(self._mk_scan_report("SA"), f1, folders)
        sm.create_snapshot(self._mk_scan_report("SB"), f2, folders)
        res = sm.compare("SA", "SB")
        assert res["new_files_count"] == 1
        assert res["removed_files_count"] == 1
        assert res["modified_files_count"] == 1
        assert res["file_count_delta"] == 0
        assert res["historical_claim_supported"] is True

    def test_compare_missing_snapshot_returns_empty(self, scanner_config):
        assert sm.compare("nope1", "nope2") == {}
