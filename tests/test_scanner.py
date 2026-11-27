"""Tests for the filesystem scanner, CSV export and folder statistics.

All data lives in an OS temp directory created by pytest - never in the
real research dataset.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd

from scanner.filesystem_scanner import FilesystemScanner


def _scan(temp_tree, scanner_config):
    sc = FilesystemScanner(root=Path(temp_tree),
                           workspace=Path(scanner_config.WORKSPACE_EXCLUSION))
    return sc, sc.scan()


class TestScan:
    def test_scan_completes(self, temp_tree, scanner_config):
        sc, res = _scan(temp_tree, scanner_config)
        assert res.status == "completed"
        assert res.files_scanned == 10
        assert res.errors == 0

    def test_folder_counts(self, temp_tree, scanner_config):
        sc, res = _scan(temp_tree, scanner_config)
        # folders: root, projectA, projectA/src, projectB, projectB/docs,
        #          empty_folder
        assert res.folders_scanned == 6

    def test_total_size_matches_files(self, temp_tree, scanner_config):
        sc, res = _scan(temp_tree, scanner_config)
        df = pd.read_csv(scanner_config.FILES_CSV, encoding="utf-8-sig")
        assert int(df["file_size_bytes"].sum()) == res.total_size_bytes
        assert len(df) == res.files_scanned

    def test_file_ids_unique(self, temp_tree, scanner_config):
        sc, res = _scan(temp_tree, scanner_config)
        df = pd.read_csv(scanner_config.FILES_CSV, encoding="utf-8-sig")
        assert df["file_id"].is_unique
        assert len(df) == res.files_scanned

    def test_no_duplicate_paths(self, temp_tree, scanner_config):
        sc, res = _scan(temp_tree, scanner_config)
        df = pd.read_csv(scanner_config.FILES_CSV, encoding="utf-8-sig")
        assert df["full_file_path"].duplicated().sum() == 0

    def test_workspace_excluded(self, temp_tree, scanner_config, monkeypatch):
        # place a folder inside the workspace and confirm it is not scanned
        ws = Path(scanner_config.WORKSPACE_EXCLUSION)
        (ws / "should_not_appear").mkdir(parents=True, exist_ok=True)
        (ws / "should_not_appear" / "secret.txt").write_text("x",
                                                             encoding="utf-8")
        sc, res = _scan(temp_tree, scanner_config)
        df = pd.read_csv(scanner_config.FILES_CSV, encoding="utf-8-sig")
        assert not df["full_file_path"].str.contains(
            "should_not_appear").any()

    def test_empty_folder_recorded(self, temp_tree, scanner_config):
        sc, res = _scan(temp_tree, scanner_config)
        fdf = pd.read_csv(scanner_config.FOLDERS_CSV, encoding="utf-8-sig")
        empty = fdf[fdf["direct_file_count"] == 0]
        assert len(empty) == 1
        assert "empty_folder" in empty.iloc[0]["folder_path"]

    def test_recursive_counts(self, temp_tree, scanner_config):
        sc, res = _scan(temp_tree, scanner_config)
        fdf = pd.read_csv(scanner_config.FOLDERS_CSV, encoding="utf-8-sig")
        root_row = fdf[fdf["folder_depth"] == 0]
        if root_row.empty:
            root_row = fdf[fdf["folder_name"] == temp_tree.name]
        # projectA has 5 files (app.py, app_v2.py, src/main.py,
        # src/final_report.pdf, duplicate_name.txt)
        pa = fdf[fdf["folder_path"].str.endswith("projectA")]
        assert int(pa.iloc[0]["recursive_file_count"]) == 5
        assert int(pa.iloc[0]["recursive_folder_count"]) == 1  # src

    def test_hidden_and_readonly_columns_present(self, temp_tree,
                                                 scanner_config):
        sc, res = _scan(temp_tree, scanner_config)
        df = pd.read_csv(scanner_config.FILES_CSV, encoding="utf-8-sig")
        for col in ("hidden_file_status", "read_only_status",
                    "file_category", "naming_pattern",
                    "contains_version_indicator", "folder_depth",
                    "root_folder", "collection_timestamp"):
            assert col in df.columns, col

    def test_csv_export_readable(self, temp_tree, scanner_config):
        sc, res = _scan(temp_tree, scanner_config)
        with open(scanner_config.FILES_CSV, encoding="utf-8-sig",
                  newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == res.files_scanned
        assert rows[0]["file_name"]

    def test_category_populated(self, temp_tree, scanner_config):
        sc, res = _scan(temp_tree, scanner_config)
        df = pd.read_csv(scanner_config.FILES_CSV, encoding="utf-8-sig")
        cats = set(df["file_category"])
        assert "Programming" in cats
        assert "Documents" in cats
        assert "Images" in cats

    def test_error_file_created_with_header(self, temp_tree, scanner_config):
        sc, res = _scan(temp_tree, scanner_config)
        assert scanner_config.ERRORS_CSV.exists()
        with open(scanner_config.ERRORS_CSV, encoding="utf-8-sig") as fh:
            header = fh.readline()
        assert "error_type" in header

    def test_depth_values_correct(self, temp_tree, scanner_config):
        sc, res = _scan(temp_tree, scanner_config)
        df = pd.read_csv(scanner_config.FILES_CSV, encoding="utf-8-sig")
        src = df[df["file_name"] == "main.py"].iloc[0]
        assert int(src["folder_depth"]) == 2
        top = df[df["file_name"] == "top.txt"].iloc[0]
        # files in the scan root sit in a folder of depth 0
        assert int(top["folder_depth"]) == 0


class TestPermissionResilience:
    def test_missing_root_reports_error(self, scanner_config, tmp_path):
        import config
        sc = FilesystemScanner(root=tmp_path / "does_not_exist",
                               workspace=tmp_path / "ws")
        res = sc.scan()
        assert res.status == "failed"
        assert res.errors >= 1


class TestResume:
    """A leftover resume file from a finished run must never truncate data."""

    @staticmethod
    def _fresh_scan(temp_tree, scanner_config):
        return _scan(temp_tree, scanner_config)

    @staticmethod
    def _all_folder_paths(scanner_config):
        fdf = pd.read_csv(scanner_config.FOLDERS_CSV, encoding="utf-8-sig")
        return sorted(fdf["folder_path"])

    def test_stale_old_format_resume_is_ignored(self, temp_tree,
                                                 scanner_config, monkeypatch):
        sc, res = self._fresh_scan(temp_tree, scanner_config)
        assert res.files_scanned == 10

        done = sorted(str(p).lower()
                      for p in self._all_folder_paths(scanner_config))
        scanner_config.RESUME_FILE.write_text(
            json.dumps({"scan_root": str(temp_tree), "done_folders": done}),
            encoding="utf-8")
        monkeypatch.setattr(scanner_config, "RESUME_ENABLED", True)

        sc2, res2 = self._fresh_scan(temp_tree, scanner_config)
        assert res2.status == "completed"
        assert res2.files_scanned == 10
        assert res2.folders_scanned == 6
        assert res2.skipped_resumed == 0
        assert not scanner_config.RESUME_FILE.exists()

    def test_finished_in_progress_state_starts_fresh(self, temp_tree,
                                                     scanner_config,
                                                     monkeypatch):
        sc, res = self._fresh_scan(temp_tree, scanner_config)
        assert res.files_scanned == 10

        done = sorted(str(p).lower()
                      for p in self._all_folder_paths(scanner_config))
        scanner_config.RESUME_FILE.write_text(
            json.dumps({"status": "in_progress", "scan_root": str(temp_tree),
                        "done_folders": done}),
            encoding="utf-8")
        monkeypatch.setattr(scanner_config, "RESUME_ENABLED", True)

        sc2, res2 = self._fresh_scan(temp_tree, scanner_config)
        assert res2.status == "completed"
        assert res2.files_scanned == 10
        assert res2.folders_scanned == 6
        assert not scanner_config.RESUME_FILE.exists()

    def test_interrupted_resume_appends_without_duplicates(
            self, temp_tree, scanner_config, monkeypatch):
        sc, res = self._fresh_scan(temp_tree, scanner_config)
        assert res.files_scanned == 10
        assert res.folders_scanned == 6

        files_df = pd.read_csv(scanner_config.FILES_CSV, encoding="utf-8-sig")
        folders_df = pd.read_csv(scanner_config.FOLDERS_CSV,
                                 encoding="utf-8-sig")
        root_str = str(temp_tree)
        total_bytes = int(files_df["file_size_bytes"].sum())

        done_paths = [p for p in folders_df["folder_path"] if p != root_str]
        assert len(done_paths) == 5

        # interrupted state: every subtree finished except the root folder;
        # one root file row was lost while the rest had been flushed
        folders_df[folders_df["folder_path"].isin(done_paths)].to_csv(
            scanner_config.FOLDERS_CSV, index=False, encoding="utf-8-sig")
        root_rows = files_df[files_df["folder_path"] == root_str]
        lost_path = root_rows.iloc[-1]["full_file_path"]
        files_df[files_df["full_file_path"] != lost_path].to_csv(
            scanner_config.FILES_CSV, index=False, encoding="utf-8-sig")

        scanner_config.RESUME_FILE.write_text(
            json.dumps({
                "status": "in_progress",
                "scan_id": "interrupted",
                "scan_root": root_str,
                "updated": "2026-11-16T00:00:00",
                "done_folders": sorted(p.lower() for p in done_paths),
            }, indent=1),
            encoding="utf-8")
        monkeypatch.setattr(scanner_config, "RESUME_ENABLED", True)

        sc2 = FilesystemScanner(root=Path(temp_tree),
                                workspace=Path(
                                    scanner_config.WORKSPACE_EXCLUSION))
        res2 = sc2.scan()

        assert res2.status == "completed"
        assert res2.errors == 0
        top_done = sum(1 for p in done_paths
                       if Path(p).parent == Path(temp_tree))
        assert res2.skipped_resumed == top_done
        assert res2.files_scanned == 10
        assert res2.folders_scanned == 6

        out_files = pd.read_csv(scanner_config.FILES_CSV, encoding="utf-8-sig")
        assert len(out_files) == 10
        assert out_files["full_file_path"].duplicated().sum() == 0
        assert out_files["file_id"].is_unique
        assert int(out_files["file_size_bytes"].sum()) == total_bytes

        out_folders = pd.read_csv(scanner_config.FOLDERS_CSV,
                                  encoding="utf-8-sig")
        assert len(out_folders) == 6
        assert out_folders["folder_path"].duplicated().sum() == 0

        root_out = out_folders[out_folders["folder_path"] == root_str]
        assert int(root_out["recursive_file_count"].iloc[0]) == 10
        assert int(root_out["recursive_folder_count"].iloc[0]) == 5
        assert int(root_out["total_size_bytes"].iloc[0]) == total_bytes

        assert not scanner_config.RESUME_FILE.exists()
