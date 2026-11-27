"""pipeline.py CLI tests: explicit opt-in scan gate (Phase 3, §12) and
dashboard import regression. No analysis or scan may actually run."""
from __future__ import annotations

import importlib

import pytest

import pipeline


class _ScanBomb:
    """Safety net: instantiating a scanner in these tests is a failure."""

    def __init__(self, *args, **kwargs):
        raise AssertionError("FilesystemScanner must not start in this test")


def test_parser_exposes_documented_flags():
    parser = pipeline.build_parser()
    args = parser.parse_args(["--analyse-only", "--no-report", "--hash"])
    assert args.analyse_only and args.no_report and args.hash
    assert not args.scan
    args2 = parser.parse_args(
        ["--scan", "--scan-root", "C:\\somewhere", "--yes"])
    assert args2.scan and args2.yes
    assert args2.scan_root == "C:\\somewhere"


def test_parser_rejects_unknown_flags():
    with pytest.raises(SystemExit) as exc:
        pipeline.build_parser().parse_args(["--nuke"])
    assert exc.value.code == 2


def test_no_flags_prints_usage_and_exits_2(monkeypatch, capsys):
    monkeypatch.setattr(pipeline, "setup_logging", lambda: None)
    with pytest.raises(SystemExit) as exc:
        pipeline.main([])
    assert exc.value.code == 2
    out = capsys.readouterr().out
    assert "--scan" in out
    assert "--analyse-only" in out


def test_scan_on_drive_root_requires_yes(monkeypatch, capsys):
    monkeypatch.setattr(pipeline, "setup_logging", lambda: None)
    monkeypatch.setattr(pipeline.filesystem_scanner,
                        "FilesystemScanner", _ScanBomb)
    with pytest.raises(SystemExit) as exc:
        pipeline.main(["--scan"])
    assert exc.value.code == 2
    out = capsys.readouterr().out
    assert "--yes" in out


def test_scan_without_yes_never_reaches_scanner_for_default_root(
        monkeypatch):
    monkeypatch.setattr(pipeline, "setup_logging", lambda: None)
    monkeypatch.setattr(pipeline.filesystem_scanner,
                        "FilesystemScanner", _ScanBomb)
    with pytest.raises(SystemExit):
        pipeline.main(["--scan"])


def test_analyse_only_parsing_does_not_trigger_scan_gate(monkeypatch):
    calls = {"scan": 0}

    def _fake_run_scan(root=None):
        calls["scan"] += 1
        return {}

    monkeypatch.setattr(pipeline, "setup_logging", lambda: None)
    monkeypatch.setattr(pipeline, "run_scan", _fake_run_scan)

    class _Stop(Exception):
        pass

    monkeypatch.setattr(pipeline, "run_analysis",
                        lambda report=None: (_ for _ in ()).throw(_Stop()))
    with pytest.raises(_Stop):
        pipeline.main(["--analyse-only", "--no-report"])
    assert calls["scan"] == 0


@pytest.mark.parametrize("module_name", [
    "dashboard.overview", "dashboard.storage", "dashboard.timeline",
    "dashboard.filetypes", "dashboard.duplicates", "dashboard.naming",
    "dashboard.findings", "dashboard.folder_structure",
])
def test_dashboard_pages_still_import(module_name):
    module = importlib.import_module(module_name)
    assert callable(getattr(module, "render", None))


def test_app_module_still_imports():
    module = importlib.import_module("app")
    assert callable(getattr(module, "main", None))
