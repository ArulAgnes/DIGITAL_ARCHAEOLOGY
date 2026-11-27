"""Phase 8 portability tests - the 17 scenarios required by the spec.

Scenario map (Phase 8 section 9):
  1  application root discovery
  2  workspace override using an environment variable
  3  workspace creation in a temporary directory
  4  operation from a directory other than the original workspace
  5  paths containing spaces
  6  paths containing Unicode characters
  7  missing directories
  8  read-only or inaccessible directories
  9  existing SQLite database preservation
 10  existing CSV preservation
 11  Windows launch script validation
 12  missing required dependencies
 13  missing optional scikit-learn behavior
 14  PCA without scikit-learn
 15  t-SNE with and without scikit-learn
 16  temporary file cleanup
 17  no accidental writes to protected research directories

Every test writes only to OS temporary directories (pytest tmp_path or the
system temp dir). The original research database, CSV files, snapshots,
reports, logs and backups are never used as fixtures and never written.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fixtures.synthetic import (PROTECTED_RESEARCH_ROOTS,
                                assert_outside_research_data)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
PY = sys.executable
ENCODING = "utf-8"


def _run(args, *, env_extra=None, cwd=None, timeout=180):
    """Run a subprocess with a clean workspace env unless overridden."""
    env = os.environ.copy()
    env.pop("DIGITAL_ARCHAEOLOGY_HOME", None)
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        for key, value in env_extra.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
    return subprocess.run(
        [str(a) for a in args],
        cwd=str(cwd or PROJECT_ROOT),
        env=env, capture_output=True, text=True,
        encoding=ENCODING, errors="replace", timeout=timeout)


def _config_probe(ws=None, tail=""):
    """Import config in a subprocess and print its path facts as JSON."""
    code = (
        "import config, json; print(json.dumps({"
        "'root': str(config.WORKSPACE_ROOT),"
        "'project': str(config.PROJECT_ROOT),"
        "'data': str(config.DATA_DIR),"
        "'output': str(config.OUTPUT_DIR),"
        "'excl': str(config.WORKSPACE_EXCLUSION),"
        "'env': config.WORKSPACE_ENV_ACTIVE}))"
        + tail
    )
    env = {} if ws is None else {"DIGITAL_ARCHAEOLOGY_HOME": str(ws)}
    return _run([PY, "-c", code], env_extra=env)


# ------------------------------------------------------------------ #
# 1. Application root discovery
# ------------------------------------------------------------------ #
def test_project_root_discovery():
    import config

    assert config.PROJECT_ROOT == Path(config.__file__).resolve().parent
    assert (config.PROJECT_ROOT / "app.py").is_file()
    assert (config.PROJECT_ROOT / "config.py").is_file()

    # Same answer when Python is started from an unrelated directory.
    with tempfile.TemporaryDirectory() as foreign:
        r = _run(
            [PY, "-c",
             "import config; print(config.PROJECT_ROOT)"],
            env_extra={"PYTHONPATH": str(PROJECT_ROOT)}, cwd=foreign)
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == str(config.PROJECT_ROOT)


# ------------------------------------------------------------------ #
# 2. Workspace override using an environment variable
# ------------------------------------------------------------------ #
def test_workspace_env_override(tmp_path):
    import config

    # Pure resolution logic (no import side effects).
    ws = tmp_path / "elsewhere"
    assert config.resolve_workspace_root(
        {"DIGITAL_ARCHAEOLOGY_HOME": str(ws)}) == ws.resolve()
    assert config.resolve_workspace_root({}) == config.PROJECT_ROOT
    # Quoted values (common with Windows set) are tolerated.
    assert config.resolve_workspace_root(
        {"DIGITAL_ARCHAEOLOGY_HOME": f'"{ws}"'}) == ws.resolve()

    # End-to-end through a fresh interpreter.
    r = _config_probe(ws)
    assert r.returncode == 0, r.stderr
    facts = json.loads(r.stdout.strip().splitlines()[-1])
    assert facts["root"] == str(ws.resolve())
    assert facts["data"] == str(ws.resolve() / "data")
    assert facts["output"] == str(ws.resolve() / "output")
    assert facts["excl"] == str(ws.resolve())
    assert facts["env"] is True

    # Default behavior is unchanged when the variable is absent.
    r2 = _config_probe(None)
    facts2 = json.loads(r2.stdout.strip().splitlines()[-1])
    assert facts2["root"] == facts2["project"] == str(PROJECT_ROOT)
    assert facts2["env"] is False


# ------------------------------------------------------------------ #
# 3. Workspace creation in a temporary directory
# ------------------------------------------------------------------ #
def test_workspace_created_in_temp_dir(tmp_path):
    ws = tmp_path / "brand_new_workspace"
    assert not ws.exists()
    r = _config_probe(ws)
    assert r.returncode == 0, r.stderr
    assert ws.is_dir()
    assert (ws / "data" / "raw").is_dir()
    assert (ws / "reports").is_dir()
    assert (ws / "logs").is_dir()
    # Initialization creates directories only - never a database file.
    assert not (ws / "data" / "database" / "digital_archaeology.db").exists()


# ------------------------------------------------------------------ #
# 4. Operation from a directory other than the original workspace
# ------------------------------------------------------------------ #
def test_operation_from_foreign_directory(tmp_path):
    before = sorted(p.name for p in tmp_path.iterdir())

    r = _run([PY, str(PROJECT_ROOT / "run_pipeline.py"), "--help"],
             cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "--input" in r.stdout

    # No-args pipeline prints usage and exits 2 without scanning anything.
    r2 = _run([PY, str(PROJECT_ROOT / "pipeline.py")], cwd=tmp_path)
    assert r2.returncode == 2
    combined = r2.stdout + r2.stderr
    assert "--scan" in combined

    after = sorted(p.name for p in tmp_path.iterdir())
    assert before == after, "running CLIs from a foreign cwd must not write there"
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "logs").exists()


# ------------------------------------------------------------------ #
# 5. Paths containing spaces
# ------------------------------------------------------------------ #
def test_paths_with_spaces(tmp_path):
    ws = tmp_path / "workspace with spaces" / "inner dir"
    r = _config_probe(ws)
    assert r.returncode == 0, r.stderr
    facts = json.loads(r.stdout.strip().splitlines()[-1])
    assert "workspace with spaces" in facts["data"]
    assert Path(facts["data"]).is_dir()
    assert_outside_research_data(Path(facts["data"]))


# ------------------------------------------------------------------ #
# 6. Paths containing Unicode characters
# ------------------------------------------------------------------ #
def test_paths_with_unicode(tmp_path):
    ws = tmp_path / "ünïcode_测试_рабсіх"
    r = _config_probe(ws)
    assert r.returncode == 0, r.stderr
    facts = json.loads(r.stdout.strip().splitlines()[-1])
    assert "测试" in facts["data"]
    assert Path(facts["data"]).is_dir()
    # Round-trip through the real config import in-process as well.
    import config
    resolved = config.resolve_workspace_root(
        {"DIGITAL_ARCHAEOLOGY_HOME": str(ws)})
    assert resolved == ws.resolve()


# ------------------------------------------------------------------ #
# 7. Missing directories
# ------------------------------------------------------------------ #
def test_missing_directories_created(tmp_path):
    ws = tmp_path / "not_created_yet"
    code = (
        "import config, json; config.ensure_platform_dirs(); print(json.dumps({"
        "'raw': (config.RAW_DIR).is_dir(),"
        "'processed': (config.PROCESSED_DIR).is_dir(),"
        "'snapshots': (config.SNAPSHOT_DIR).is_dir(),"
        "'database_dir': (config.DATABASE_DIR).is_dir(),"
        "'reports': (config.REPORTS_DIR).is_dir(),"
        "'charts': (config.CHARTS_DIR).is_dir(),"
        "'logs': (config.LOGS_DIR).is_dir(),"
        "'output': (config.OUTPUT_DIR).is_dir(),"
        "'input_csv': (config.INPUT_CSV_DIR).is_dir()}))"
    )
    r = _run([PY, "-c", code],
             env_extra={"DIGITAL_ARCHAEOLOGY_HOME": str(ws)})
    assert r.returncode == 0, r.stderr
    facts = json.loads(r.stdout.strip().splitlines()[-1])
    assert all(facts.values()), facts
    assert not (ws / "data" / "database" / "digital_archaeology.db").exists()


# ------------------------------------------------------------------ #
# 8. Read-only or inaccessible directories
# ------------------------------------------------------------------ #
def test_inaccessible_directory_reports_clear_error(tmp_path):
    blocker = tmp_path / "blocked_by_file"
    blocker.write_text("not a directory", encoding="utf-8")
    ws = blocker / "workspace"

    r = _config_probe(ws)
    assert r.returncode != 0, "import must fail for an inaccessible workspace"
    err = r.stderr
    assert "Cannot create required directory" in err
    assert "DIGITAL_ARCHAEOLOGY_HOME" in err
    assert str(blocker) in err
    # Actionable, not a bare traceback of an unrelated exception.
    assert "writable" in err


# ------------------------------------------------------------------ #
# 9. Existing SQLite database preservation
# ------------------------------------------------------------------ #
def test_existing_database_preserved(tmp_path):
    ws = tmp_path / "ws_with_db"
    db = ws / "data" / "database" / "digital_archaeology.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    import sqlite3
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE marker (v TEXT)")
    conn.execute("INSERT INTO marker VALUES ('phase8-preservation')")
    conn.commit()
    conn.close()

    before = (db.stat().st_size, db.stat().st_mtime_ns, db.read_bytes()[:64])

    code = ("import config; config.ensure_platform_dirs(); "
            "print('workspace ready')")
    r = _run([PY, "-c", code],
             env_extra={"DIGITAL_ARCHAEOLOGY_HOME": str(ws)})
    assert r.returncode == 0, r.stderr

    after = (db.stat().st_size, db.stat().st_mtime_ns, db.read_bytes()[:64])
    assert before == after, "initialization must never touch an existing DB"

    # And a workspace with no database never gains one during init.
    ws2 = tmp_path / "ws_no_db"
    r2 = _run([PY, "-c", "import config; config.ensure_platform_dirs()"],
              env_extra={"DIGITAL_ARCHAEOLOGY_HOME": str(ws2)})
    assert r2.returncode == 0, r2.stderr
    assert not (ws2 / "data" / "database" / "digital_archaeology.db").exists()


# ------------------------------------------------------------------ #
# 10. Existing CSV preservation
# ------------------------------------------------------------------ #
def test_existing_csv_preserved(tmp_path):
    ws = tmp_path / "ws_with_csv"
    csv = ws / "data" / "raw" / "files_metadata.csv"
    csv.parent.mkdir(parents=True, exist_ok=True)
    payload = "file_id,file_name\r\n1,kept.csv\r\n2,also_kept.csv\r\n"
    csv.write_bytes(payload.encode("utf-8-sig"))
    before = (csv.stat().st_size, csv.stat().st_mtime_ns, csv.read_bytes())

    code = ("import config; config.ensure_platform_dirs(); "
            "import config as c; print(c.FILES_CSV)")
    r = _run([PY, "-c", code],
             env_extra={"DIGITAL_ARCHAEOLOGY_HOME": str(ws)})
    assert r.returncode == 0, r.stderr
    assert str(csv) in r.stdout

    after = (csv.stat().st_size, csv.stat().st_mtime_ns, csv.read_bytes())
    assert before == after, "initialization must never rewrite research CSVs"


# ------------------------------------------------------------------ #
# 11. Windows launch script validation
# ------------------------------------------------------------------ #
def test_windows_launch_scripts_exist_and_are_safe():
    for name in ("install_windows.bat", "run_windows.bat",
                 "check_environment.bat"):
        script = SCRIPTS / name
        assert script.is_file(), f"missing {name}"
        text = script.read_text(encoding="utf-8", errors="ignore")
        assert "%~dp0" in text, f"{name} must locate the project via %~dp0"
        lowered = text.lower()
        for destructive in ("rmdir /s", "rd /s /q", "del /f /s",
                            "remove-item", "format ", "git "):
            assert destructive not in lowered, f"{name} contains {destructive!r}"

    install = (SCRIPTS / "install_windows.bat").read_text(encoding="utf-8")
    assert "python -m venv" in install
    assert "requirements.txt" in install
    assert "keeping it unchanged" in install  # never deletes an existing venv

    run = (SCRIPTS / "run_windows.bat").read_text(encoding="utf-8")
    assert "streamlit run app.py" in run
    assert "--scan" not in run, "the launcher must never trigger a scan"
    assert "localhost:8501" in run

    check = (SCRIPTS / "check_environment.bat").read_text(encoding="utf-8")
    assert "check_environment.py" in check

    assert (SCRIPTS / "check_environment.py").is_file()


def test_check_environment_bat_passes_in_full_install():
    r = _run(["cmd", "/c", str(SCRIPTS / "check_environment.bat")])
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "All required checks passed" in out
    assert "[MISSING]" not in out


# ------------------------------------------------------------------ #
# 12. Missing required dependencies
# ------------------------------------------------------------------ #
def test_missing_required_dependencies_reported():
    # python -S hides site-packages, simulating an environment where the
    # required packages were never installed.
    r = _run([PY, "-S", str(SCRIPTS / "check_environment.py")])
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "[MISSING]" in out
    for package in ("pandas", "numpy", "streamlit", "plotly"):
        assert f"required package '{package}'" in out
    assert "install_windows.bat" in out
    # Optional dependency is reported separately and is not a failure reason
    # by itself: the failure count comes from the four required packages.
    assert "optional scikit-learn" in out


# ------------------------------------------------------------------ #
# 13. Missing optional scikit-learn behavior
# ------------------------------------------------------------------ #
@pytest.fixture()
def small_frame() -> pd.DataFrame:
    rng = np.random.default_rng(3)
    return pd.DataFrame({
        "a": rng.normal(0.0, 1.0, 120),
        "b": rng.normal(5.0, 2.0, 120),
        "c": rng.integers(0, 10, 120).astype(float),
        "g": rng.choice(["x", "y"], 120),
    })


def _block_sklearn(monkeypatch):
    """Simulate an environment without scikit-learn."""
    for name in ("sklearn", "sklearn.manifold", "sklearn.decomposition"):
        monkeypatch.setitem(sys.modules, name, None)


def test_tsne_without_sklearn_gives_install_guidance(monkeypatch,
                                                      small_frame):
    from core.exceptions import PlatformError
    from visualization import VisualizationConfig, build_chart

    _block_sklearn(monkeypatch)
    with pytest.raises(PlatformError) as exc:
        build_chart("tsne_scatter", VisualizationConfig(max_points=500),
                    roles={"columns": ["a", "b"], "color": "g"},
                    frame=small_frame, source_label="synthetic.csv")
    message = exc.value.user_message
    assert "scikit-learn" in message
    assert "PCA" in message
    assert "install" in message.lower()


# ------------------------------------------------------------------ #
# 14. PCA without scikit-learn
# ------------------------------------------------------------------ #
def test_pca_works_without_sklearn(monkeypatch, small_frame):
    from visualization import VisualizationConfig, build_chart

    _block_sklearn(monkeypatch)
    art = build_chart("pca_scatter", VisualizationConfig(max_points=500),
                      roles={"columns": ["a", "b", "c"], "color": "g"},
                      frame=small_frame, source_label="synthetic.csv")
    assert len(art.figure.data) >= 1
    assert art.chart_id


# ------------------------------------------------------------------ #
# 15. t-SNE with scikit-learn (and without -> covered by scenario 13)
# ------------------------------------------------------------------ #
def test_tsne_works_when_sklearn_available(small_frame):
    from visualization import VisualizationConfig, build_chart

    if importlib.util.find_spec("sklearn") is None:
        pytest.skip("scikit-learn not installed in this environment")
    art = build_chart("tsne_scatter", VisualizationConfig(max_points=500),
                      roles={"columns": ["a", "b", "c"], "color": "g"},
                      frame=small_frame, source_label="synthetic.csv")
    assert len(art.figure.data) >= 1


# ------------------------------------------------------------------ #
# 16. Temporary file cleanup
# ------------------------------------------------------------------ #
def test_temp_files_isolated_and_cleaned(tmp_path):
    from ingestion.filesystem_loader import FilesystemLoader

    tree = tmp_path / "tree"
    (tree / "sub").mkdir(parents=True)
    (tree / "sub" / "a.txt").write_text("x", encoding="utf-8")

    loader = FilesystemLoader(tree)  # no context -> system temp output
    loader.scan()
    out = Path(loader.output_dir())
    try:
        assert out.is_dir()
        temp_root = Path(tempfile.gettempdir()).resolve()
        assert out.resolve().is_relative_to(temp_root), (
            f"loader output must live under the OS temp dir, got {out}")
        assert PROJECT_ROOT.resolve() not in out.resolve().parents
        assert_outside_research_data(out)
        assert (out / "files_metadata.csv").is_file()
    finally:
        shutil.rmtree(out, ignore_errors=True)
    assert not out.exists(), "temporary scan output must be removable"


# ------------------------------------------------------------------ #
# 17. No accidental writes to protected research directories
# ------------------------------------------------------------------ #
def _protected_snapshot() -> dict:
    """Map of every protected path (directories and files) to stable facts.

    Includes directories so the check also works in a clean installation
    copy where research files have not been placed yet.
    """
    snapshot = {}
    for root in PROTECTED_RESEARCH_ROOTS:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            st = path.stat()
            snapshot[str(path.relative_to(PROJECT_ROOT))] = (
                path.is_file(), st.st_size, st.st_mtime_ns)
    return snapshot


def test_no_accidental_writes_to_protected_research_dirs(tmp_path):
    before = _protected_snapshot()

    # Portability operations that write: all output lands in temp workspaces.
    ws = tmp_path / "ws_ops"
    r = _run([PY, "-c",
              "import config; config.ensure_platform_dirs(); "
              "print(config.WORKSPACE_ROOT)"],
             env_extra={"DIGITAL_ARCHAEOLOGY_HOME": str(ws)})
    assert r.returncode == 0, r.stderr
    assert str(ws.resolve()) in r.stdout

    # Read-only diagnostics against the real workspace.
    r2 = _run([PY, str(SCRIPTS / "check_environment.py")])
    assert r2.returncode == 0, r2.stdout + r2.stderr

    after = _protected_snapshot()
    assert before == after, (
        "protected research files changed during portability operations")
    # In the original research workspace the protected files must exist.
    if (PROJECT_ROOT / "data" / "database" /
            "digital_archaeology.db").exists():
        assert any(is_file for is_file, _, _ in before.values()), (
            "research database present but no protected files snapshotted")
