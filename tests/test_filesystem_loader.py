"""Filesystem loader tests - output isolation, metadata-only, confirmations.

These tests run real scans of tiny synthetic trees inside pytest temp dirs.
Research data must be untouched: output isolation is asserted explicitly.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import config
from core.exceptions import (ConfirmationRequiredError,
                             InputValidationError, PlatformError)
from core.processing_context import ProcessingContext
from fixtures.synthetic import (assert_outside_research_data,
                                count_entries, tree_snapshot)
from ingestion.filesystem_loader import FilesystemLoader


def _research_raw_signature() -> tuple[int, int]:
    stat = config.FILES_CSV.stat()
    return stat.st_size, stat.st_mtime_ns


@pytest.fixture()
def context(tmp_path):
    ctx = ProcessingContext(base_dir=tmp_path / "out")
    yield ctx
    ctx.close()


def test_scan_outputs_isolated_from_research_raw(temp_tree, context):
    before = _research_raw_signature()
    loader = FilesystemLoader(temp_tree, context=context)
    result = loader.scan()
    after = _research_raw_signature()
    assert before == after
    out = loader.output_dir()
    assert (out / "files_metadata.csv").exists()
    assert_outside_research_data(out)
    assert result.files_scanned > 0


def test_source_tree_byte_identical_after_scan(temp_tree, context):
    before = tree_snapshot(temp_tree)
    before_names = sorted(str(p) for p in temp_tree.rglob("*"))
    FilesystemLoader(temp_tree, context=context).scan()
    assert tree_snapshot(temp_tree) == before
    assert sorted(str(p) for p in temp_tree.rglob("*")) == before_names


def test_counts_match_synthetic_tree(temp_tree, context):
    expected_files, expected_subdirs = count_entries(temp_tree)
    result = FilesystemLoader(temp_tree, context=context).scan()
    assert result.files_scanned == expected_files
    assert result.folders_scanned == expected_subdirs + 1
    assert result.status == "completed"
    assert result.errors == 0


def test_metadata_reflects_scan_without_rescanning(temp_tree, context):
    expected_files, _ = count_entries(temp_tree)
    loader = FilesystemLoader(temp_tree, context=context)
    ds = loader.to_dataset()
    assert ds.metadata.row_count == expected_files
    assert ds.metadata.source_info["format"] == "filesystem"
    assert loader.scan_result is not None
    first_scan_id = loader.scan_result.scan_id
    loader.load_metadata()
    assert loader.scan_result.scan_id == first_scan_id


def test_drive_root_requires_confirmation():
    loader = FilesystemLoader(Path("D:\\"), confirm_drive=False)
    with pytest.raises(ConfirmationRequiredError):
        loader.to_dataset()
    assert loader._out_dir is None
    assert loader._scan_result is None


def test_missing_folder_raises_source_not_found(tmp_path, context):
    from core.exceptions import SourceNotFoundError
    loader = FilesystemLoader(tmp_path / "ghost", context=context)
    with pytest.raises(SourceNotFoundError):
        loader.to_dataset()


def test_empty_folder_scan_warns_but_succeeds(tmp_path, context):
    empty = tmp_path / "empty"
    empty.mkdir()
    loader = FilesystemLoader(empty, context=context)
    ds = loader.to_dataset()
    assert ds.metadata.row_count == 0
    assert any("no files" in w for w in ds.metadata.warnings)
    assert ds.validation_status.value == "warning"


def test_context_progress_records_scan_stages(temp_tree, context):
    loader = FilesystemLoader(temp_tree, context=context)
    loader.scan()
    stages = [p["stage"] for p in context.progress]
    assert "scan_done" in stages


def test_cancelled_context_blocks_scan(temp_tree, context):
    context.cancel()
    loader = FilesystemLoader(temp_tree, context=context)
    with pytest.raises(PlatformError):
        loader.scan()


def test_scan_without_context_uses_temp_output(temp_tree):
    import tempfile
    loader = FilesystemLoader(temp_tree)
    result = loader.scan()
    assert result.files_scanned > 0
    out = loader.output_dir()
    assert str(out).lower().startswith(
        str(Path(tempfile.gettempdir())).lower())
    assert_outside_research_data(out)


def test_iter_chunks_reads_scan_output(temp_tree, context):
    loader = FilesystemLoader(temp_tree, context=context, chunk_size=3)
    chunks = list(loader.iter_chunks())
    assert chunks
    assert all(len(c) <= 3 for c in chunks)
    assert sum(len(c) for c in chunks) == loader.scan_result.files_scanned


def test_preview_lists_top_level_without_scanning(temp_tree, context):
    loader = FilesystemLoader(temp_tree, context=context)
    entries = loader.preview()
    names = {e["name"] for e in entries}
    assert {"projectA", "projectB", "empty_folder"} <= names
    assert loader._scan_result is None


@pytest.mark.skipif(not hasattr(Path, "is_symlink"),
                    reason="symlink support unavailable")
def test_symlinked_file_is_not_followed(tmp_path, context):
    target = tmp_path / "realdir"
    target.mkdir()
    real = target / "real.txt"
    real.write_text("data", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    try:
        (root / "link").symlink_to(real)
    except OSError:
        pytest.skip("symlink creation not permitted on this system")
    before = tree_snapshot(tmp_path)
    result = FilesystemLoader(root, context=context).scan()
    assert tree_snapshot(tmp_path) == before
    assert result.status == "completed"
    assert result.files_scanned <= 1


def test_outputs_never_use_research_resume(temp_tree, context, monkeypatch):
    monkeypatch.setattr(config, "RESUME_ENABLED", True)
    loader = FilesystemLoader(temp_tree, context=context)
    loader.scan()
    assert not config.RESUME_FILE.exists()
    assert loader._out_dir is not None
    assert not (loader.output_dir() / "scan_resume.json").exists()
