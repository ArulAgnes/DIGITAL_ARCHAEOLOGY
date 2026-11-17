"""Tests for metadata extraction, classification and naming rules."""
from __future__ import annotations

from scanner.metadata_extractor import (
    classify_file,
    extract_naming_features,
    folder_depth,
    human_size,
    normalize_extension,
    naming_inconsistencies,
    root_folder_of,
)
from pathlib import Path


class TestExtensionNormalization:
    def test_simple(self):
        assert normalize_extension("Report.PDF") == ".pdf"

    def test_multi_part(self):
        assert normalize_extension("archive.tar.gz") == ".gz"

    def test_no_extension(self):
        assert normalize_extension("README") == ""

    def test_hidden_file(self):
        assert normalize_extension(".gitignore") == ""


class TestClassification:
    def test_programming(self):
        assert classify_file("main.py") == "Programming"

    def test_document(self):
        assert classify_file("thesis.docx") == "Documents"

    def test_spreadsheet(self):
        assert classify_file("data.xlsx") == "Spreadsheets"

    def test_presentation(self):
        assert classify_file("slides.pptx") == "Presentations"

    def test_image(self):
        assert classify_file("photo.JPEG") == "Images"

    def test_video(self):
        assert classify_file("clip.mp4") == "Videos"

    def test_audio(self):
        assert classify_file("song.mp3") == "Audio"

    def test_dataset(self):
        assert classify_file("table.parquet") == "Datasets"

    def test_archive(self):
        assert classify_file("backup.zip") == "Archives"

    def test_executable(self):
        assert classify_file("setup.exe") == "Executables"

    def test_system(self):
        assert classify_file("Thumbs.db") == "System-related"
        assert classify_file("desktop.ini") == "System-related"

    def test_unknown_goes_other(self):
        assert classify_file("mystery.xyz") == "Other"

    def test_every_extension_maps_to_known_category(self):
        from scanner.metadata_extractor import EXTENSION_CATEGORY_MAP, CATEGORY_ORDER
        for ext, cat in EXTENSION_CATEGORY_MAP.items():
            assert cat in CATEGORY_ORDER, f"{ext} -> {cat} not in categories"


class TestFolderDepth:
    def test_root_child(self, temp_tree):
        root = Path(temp_tree)
        assert folder_depth(root / "projectA", root) == 1

    def test_nested(self, temp_tree):
        root = Path(temp_tree)
        assert folder_depth(root / "projectA" / "src", root) == 2

    def test_root_itself(self, temp_tree):
        root = Path(temp_tree)
        assert folder_depth(root, root) == 0

    def test_outside_root(self, temp_tree):
        root = Path(temp_tree)
        assert folder_depth(Path("C:/other/place"), root) == 0


class TestRootFolder:
    def test_top_level(self, temp_tree):
        root = Path(temp_tree)
        p = root / "projectA" / "src" / "main.py"
        assert root_folder_of(p, root) == "projectA"

    def test_root_file(self, temp_tree):
        root = Path(temp_tree)
        assert root_folder_of(root / "top.txt", root) == "(scan root)"


class TestNamingFeatures:
    def test_version_v2(self):
        f = extract_naming_features("report_v2.pdf")
        assert f["contains_version_indicator"] is True

    def test_version_word(self):
        f = extract_naming_features("report version 3.docx")
        assert f["contains_version_indicator"] is True

    def test_no_version(self):
        f = extract_naming_features("report.pdf")
        assert f["contains_version_indicator"] is False

    def test_final(self):
        f = extract_naming_features("essay_final.pdf")
        assert f["contains_final_keyword"] is True

    def test_final2(self):
        f = extract_naming_features("essay final2.pdf")
        assert f["contains_final_keyword"] is True

    def test_latest(self):
        f = extract_naming_features("latest_plan.docx")
        assert f["contains_final_keyword"] is True

    def test_date_iso(self):
        f = extract_naming_features("log-2026-07-27.txt")
        assert f["contains_date_indicator"] is True

    def test_bare_year(self):
        f = extract_naming_features("thesis2024.pdf")
        assert f["contains_date_indicator"] is True

    def test_copy_indicator(self):
        f = extract_naming_features("photo (1).jpg")
        assert f["contains_copy_indicator"] is True

    def test_underscore_space_hyphen(self):
        f = extract_naming_features("my_file name-x.txt")
        assert f["has_underscore"] and f["has_space"] and f["has_hyphen"]

    def test_filename_length(self):
        f = extract_naming_features("abc.txt")
        assert f["filename_length"] == 7

    def test_mixed_capitalisation_flagged(self):
        f = extract_naming_features("rEsUmE doc.pdf")
        assert f["capitalization_style"] == "mixed"

    def test_lower_capitalisation(self):
        f = extract_naming_features("report.pdf")
        assert f["capitalization_style"] == "lower"

    def test_title_capitalisation(self):
        f = extract_naming_features("Final Report.pdf")
        assert f["capitalization_style"] == "title"


class TestNamingInconsistencies:
    def test_issues_listed(self):
        row = extract_naming_features("Final_Report v2 (1).pdf")
        issues = naming_inconsistencies(row)
        assert "final_or_latest_keyword" in issues
        assert "version_indicator" in issues
        assert "copy_indicator" in issues

    def test_clean_name_has_no_issues(self):
        row = extract_naming_features("cleanname.pdf")
        assert naming_inconsistencies(row) == []


class TestHumanSize:
    def test_bytes(self):
        assert human_size(512) == "512.00 B"

    def test_kb(self):
        assert human_size(2048) == "2.00 KB"

    def test_gb(self):
        assert human_size(3 * 1024 ** 3) == "3.00 GB"
