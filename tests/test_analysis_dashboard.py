"""Tests for data cleaning, derived variables and dashboard data loading."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from analysis import data_cleaning, filetype_analysis, naming_analysis, \
    storage_analysis, structural_analysis, temporal_analysis
from analysis.duplicate_analysis import load_candidates, load_verified


class TestCleaning:
    def _df(self):
        return pd.DataFrame([
            {"file_id": 1, "file_name": "A.TXT", "file_extension": ".TXT",
             "file_size_bytes": 1024, "creation_date": "2024-05-01",
             "modified_date": "2024-06-01", "accessed_date": "",
             "full_file_path": "D:/x/A.TXT", "parent_folder": "D:/x",
             "root_folder": "x", "folder_depth": 1,
             "file_category": "Other", "naming_pattern": "plain",
             "contains_version_indicator": False,
             "contains_final_keyword": False,
             "contains_date_indicator": False,
             "filename_length": 5},
            {"file_id": 2, "file_name": "bad.pdf", "file_extension": ".pdf",
             "file_size_bytes": -5, "creation_date": "not-a-date",
             "modified_date": "2024-06-02", "accessed_date": "",
             "full_file_path": "D:/x/bad.pdf", "parent_folder": "D:/x",
             "root_folder": "x", "folder_depth": 1,
             "file_category": "Documents", "naming_pattern": "plain",
             "contains_version_indicator": False,
             "contains_final_keyword": False,
             "contains_date_indicator": False,
             "filename_length": 7},
            {"file_id": 3, "file_name": "A.TXT", "file_extension": ".TXT",
             "file_size_bytes": 1024, "creation_date": "2024-05-01",
             "modified_date": "2024-06-01", "accessed_date": "",
             "full_file_path": "D:/x/A.TXT", "parent_folder": "D:/x",
             "root_folder": "x", "folder_depth": 1,
             "file_category": "Other", "naming_pattern": "plain",
             "contains_version_indicator": False,
             "contains_final_keyword": False,
             "contains_date_indicator": False,
             "filename_length": 5},
        ])

    def test_extension_normalised_lower(self):
        out = data_cleaning.clean_files(self._df())
        assert set(out["file_extension"]) == {".txt", ".pdf"}
        assert out["file_extension"].str.islower().all()

    def test_path_normalised_to_backslash(self):
        out = data_cleaning.clean_files(self._df())
        assert "/" not in out["full_file_path"].iloc[0]

    def test_negative_size_flagged_not_fixed(self):
        out = data_cleaning.clean_files(self._df())
        bad = out[out["file_id"] == 2].iloc[0]
        assert bool(bad["size_invalid"]) is True
        assert bad["file_size_bytes"] == -5  # original value preserved

    def test_invalid_date_flagged_not_replaced(self):
        out = data_cleaning.clean_files(self._df())
        bad = out[out["file_id"] == 2].iloc[0]
        assert bool(bad["creation_date_invalid"]) is True
        assert bad["creation_date"] == "not-a-date"

    def test_duplicate_record_flagged(self):
        out = data_cleaning.clean_files(self._df())
        assert int(out["is_duplicate_record"].sum()) == 1

    def test_year_month_extracted(self):
        out = data_cleaning.clean_files(self._df())
        ok = out[out["file_id"] == 1].iloc[0]
        assert int(ok["creation_year"]) == 2024
        assert int(ok["creation_month"]) == 5
        assert ok["creation_year_month"] == "2024-05"

    def test_category_remapped_from_extension(self):
        out = data_cleaning.clean_files(self._df())
        assert out[out["file_id"] == 1].iloc[0]["file_category"] == "Documents"

    def test_size_units_consistent(self):
        out = data_cleaning.clean_files(self._df())
        row = out[out["file_id"] == 1].iloc[0]
        assert abs(row["file_size_kb"] - 1.0) < 1e-9
        assert abs(row["file_size_mb"] - (1024 / (1024 * 1024))) < 1e-4


class TestAnalyses:
    def _files(self):
        return pd.DataFrame({
            "file_id": [1, 2, 3, 4],
            "file_name": ["a.py", "b.pdf", "c.mp4", "d.py"],
            "file_extension": [".py", ".pdf", ".mp4", ".py"],
            "file_size_bytes": [100, 200, 300, 400],
            "file_size_mb": [0.0001, 0.0002, 0.0003, 0.0004],
            "creation_year": [2024, 2024, 2025, 2025],
            "creation_month": [1, 6, 3, 3],
            "creation_year_month": ["2024-01", "2024-06", "2025-03",
                                    "2025-03"],
            "modified_year": [2024, 2025, 2025, 2025],
            "modified_year_month": ["2024-01", "2025-01", "2025-03",
                                    "2025-03"],
            "root_folder": ["p1", "p1", "p2", "p2"],
            "file_category": ["Programming", "Documents", "Videos",
                              "Programming"],
            "naming_pattern": ["plain", "plain", "plain", "version"],
            "contains_version_indicator": [False, False, False, True],
            "contains_final_keyword": [False] * 4,
            "contains_date_indicator": [False] * 4,
            "contains_copy_indicator": [False] * 4,
            "has_underscore": [False] * 4,
            "has_space": [False] * 4,
            "has_hyphen": [False] * 4,
            "capitalization_style": ["lower"] * 4,
            "filename_length": [4, 5, 5, 4],
            "full_file_path": [r"D:\p1\a.py", r"D:\p1\b.pdf",
                               r"D:\p2\c.mp4", r"D:\p2\d.py"],
        })

    def test_temporal_year_counts(self):
        d = temporal_analysis.creation_by_year(self._files())
        assert d.set_index("creation_year")["file_count"].to_dict() == {
            2024: 2, 2025: 2}

    def test_storage_by_category_total_conserved(self):
        d = storage_analysis.storage_by_category(self._files())
        assert int(d["total_size_bytes"].sum()) == 1000

    def test_extension_counts(self):
        d = filetype_analysis.extension_counts(self._files())
        assert d.set_index("file_extension")["file_count"][".py"] == 2

    def test_size_stats(self):
        s = storage_analysis.size_stats(self._files())
        assert s["total_bytes"] == 1000
        assert s["max_bytes"] == 400

    def test_structural_summary(self):
        folders = pd.DataFrame({
            "folder_path": [r"D:\a", r"D:\a\b"],
            "folder_depth": [1, 2],
            "direct_file_count": [3, 0],
            "recursive_file_count": [3, 0],
            "total_size_bytes": [10, 0],
        })
        s = structural_analysis.summary(folders)
        assert s["total_folders"] == 2
        assert s["max_depth"] == 2
        assert s["empty_folders"] == 1

    def test_project_groups_labelled_inferred(self):
        g = structural_analysis.project_groups(self._files(), pd.DataFrame())
        assert not g.empty
        assert (g["grouping_label"] ==
                "inferred_top_level_grouping_not_verified_project").all()

    def test_naming_indicator_summary(self, scanner_config):
        # scanner_config redirects config.NAMING_CSV into a temp directory;
        # naming_analysis.run() writes that CSV unconditionally.
        naming, stats = naming_analysis.run(self._files())
        assert stats["total_files_analysed"] == 4
        assert stats["version_indicator_count"] == 1
        project_root = Path(__file__).resolve().parents[1]
        written = scanner_config.NAMING_CSV.resolve()
        assert written.exists()
        assert project_root not in written.parents


class TestDashboardLoading:
    def test_load_data_returns_shapes(self, scanner_config, temp_tree):
        from scanner.filesystem_scanner import FilesystemScanner
        sc = FilesystemScanner(root=Path(temp_tree),
                               workspace=Path(
                                   scanner_config.WORKSPACE_EXCLUSION))
        sc.scan()
        data_cleaning.run()

        import importlib
        common = importlib.import_module("dashboard.common")
        # bypass streamlit cache decorator for the test
        files, folders, errors, findings = (
            pd.read_csv(scanner_config.PROCESSED_FILES_CSV,
                        encoding="utf-8-sig"),
            pd.read_csv(scanner_config.PROCESSED_FOLDERS_CSV,
                        encoding="utf-8-sig"),
            pd.read_csv(scanner_config.ERRORS_CSV, encoding="utf-8-sig"),
            {},
        )
        assert len(files) == 10
        assert len(folders) == 6
        assert "file_category" in files.columns
        assert "folder_depth" in folders.columns

    def test_apply_filters(self):
        import importlib
        common = importlib.import_module("dashboard.common")
        df = pd.DataFrame({
            "creation_year": [2024, 2025, 2024],
            "creation_month": [1, 2, 3],
            "file_extension": [".py", ".py", ".pdf"],
            "file_category": ["Programming", "Programming", "Documents"],
            "root_folder": ["a", "b", "a"],
            "file_size_bytes": [1024 * 1024, 10 * 1024 * 1024,
                                100 * 1024 * 1024],
        })
        f = {"years": [2024], "months": [1, 2, 3], "extensions": [".py"],
             "categories": ["Programming"], "roots": ["a", "b"],
             "size_min": 0, "size_max": 1000}
        out = common.apply_filters(df, f)
        assert len(out) == 1
        assert out.iloc[0]["creation_year"] == 2024

    def test_findings_json_valid(self, scanner_config):
        p = scanner_config.FINDINGS_JSON
        p.write_text(json.dumps({"temporal": {"peak_creation_year": 2024}}),
                     encoding="utf-8")
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["temporal"]["peak_creation_year"] == 2024
