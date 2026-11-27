"""Comparison engine core (Phase 6): config, orchestration, results.

Streamlit-free. Follows ``analysis/analysis_runner.py`` conventions:
stage progress with cancellation at stage boundaries, refusal with
evidence when a registered source changed, contained failures, and
reproducible JSON (sorted keys, ``allow_nan=False``).

Outputs live under ``output/comparisons/<comparison_id>/runs/<run_id>/``
via ``ProcessingContext.comparison_dir`` - never near research data.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pandas as pd

import config
from analysis.analysis_runner import verify_source
from core.exceptions import PlatformError
from core.processing_context import ProcessingContext, assert_writable_target
from ingestion.dataset_registry import DatasetRegistry

log = logging.getLogger("da.comparison")

COMPARE_TYPES = ("csv", "sqlite", "filesystem")

FILE_CONFIG = "config.json"
FILE_SUMMARY = "comparison_summary.json"
FILE_SCHEMA = "schema_differences.json"
FILE_STATS = "statistics_differences.json"
FILE_WARNINGS = "warnings.json"
FILE_META = "run_metadata.json"


class _Cancelled(Exception):
    """Raised at a stage boundary when the job was cancelled."""


# ---------------------------------------------------------------------- #
# Configuration
# ---------------------------------------------------------------------- #
@dataclass
class ComparisonConfig:
    """Options for one comparison run (all values JSON-serializable)."""

    keys: tuple[str, ...] = ()
    chunk_size: int = 100_000
    max_keys: int = 1_000_000
    max_export_rows: int = 10_000
    stats_enabled: bool = True
    stats_max_columns: int = 50
    categorical_top_k: int = 20
    path_casefold: bool = True
    detect_moves: bool = True

    def __post_init__(self) -> None:
        self.keys = tuple(str(k) for k in self.keys)
        self.chunk_size = max(1_000, int(self.chunk_size))
        self.max_keys = max(1_000, int(self.max_keys))
        self.max_export_rows = max(0, int(self.max_export_rows))
        self.stats_max_columns = max(1, int(self.stats_max_columns))
        self.categorical_top_k = max(1, int(self.categorical_top_k))
        if len(self.keys) > 8:
            raise ValueError("at most 8 key columns are supported")

    def to_dict(self) -> dict:
        return {
            "keys": list(self.keys),
            "chunk_size": self.chunk_size,
            "max_keys": self.max_keys,
            "max_export_rows": self.max_export_rows,
            "stats_enabled": self.stats_enabled,
            "stats_max_columns": self.stats_max_columns,
            "categorical_top_k": self.categorical_top_k,
            "path_casefold": self.path_casefold,
            "detect_moves": self.detect_moves,
        }


# ---------------------------------------------------------------------- #
# JSON helpers (reproducible, NaN-safe)
# ---------------------------------------------------------------------- #
def json_safe(obj):
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if obj is pd.NaT:
        return None
    if isinstance(obj, float):
        return None if (obj != obj or obj in (float("inf"), float("-inf"))
                        ) else obj
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (pd.Timestamp,)):
        return obj.isoformat()
    if obj is getattr(pd, "NA", object()):
        return None
    try:
        import numpy as np
        if isinstance(obj, np.generic):
            return json_safe(obj.item())
    except Exception:  # noqa: BLE001 - numpy optional at this point
        pass
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [json_safe(v) for v in obj]
    if hasattr(obj, "isoformat"):
        return str(obj.isoformat())
    return str(obj)


def write_json(run_dir: Path, name: str, payload) -> None:
    target = run_dir / name
    assert_writable_target(target)
    target.write_text(
        json.dumps(json_safe(payload), indent=2, sort_keys=True,
                   allow_nan=False, default=str),
        encoding="utf-8")


def warn(code: str, message: str) -> dict:
    return {"code": str(code), "message": str(message)}


# ---------------------------------------------------------------------- #
# Result
# ---------------------------------------------------------------------- #
@dataclass
class ComparisonResult:
    ok: bool
    comparison_id: str = ""
    run_id: str = ""
    run_dir: str = ""
    kind: str = ""
    error: str = ""
    status: str = ""
    summary: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    files: dict = field(default_factory=dict)
    progress: list = field(default_factory=list)


# ---------------------------------------------------------------------- #
# Registry helpers
# ---------------------------------------------------------------------- #
def _registry_row(dataset_id: str) -> dict | None:
    try:
        if not Path(config.REGISTRY_DB).exists():
            return None
        return DatasetRegistry().get(dataset_id)
    except PlatformError:
        return None


def _registration_metadata(reg_row: dict) -> dict:
    loc = reg_row.get("metadata_location")
    if not loc:
        return {}
    p = Path(str(loc))
    if not p.exists():
        return {}
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload.get("dataset", {}).get("metadata", {}) or {}


def _registration_table(reg_row: dict) -> str | None:
    info = _registration_metadata(reg_row).get("source_info") or {}
    table = info.get("table")
    return str(table) if table else None


def comparison_id_for(kind: str, dataset_a: str, dataset_b: str) -> str:
    """Stable per-pair id: runs of the same comparison group together."""
    def slug(value: str) -> str:
        s = re.sub(r"[^a-z0-9]+", "", str(value).lower())
        return (s[:8] or "00000000")
    a, b = sorted((slug(dataset_a), slug(dataset_b)))
    return f"cmp_{kind}_{a}_{b}"


# ---------------------------------------------------------------------- #
# Inputs
# ---------------------------------------------------------------------- #
def _require_artifact(path: Path, label: str) -> Path:
    if not path.exists():
        raise PlatformError(
            f"The {label} for this dataset is missing - re-register the "
            "dataset on the Data Sources page.",
            detail=str(path))
    return path


def resolve_inputs(kind: str, reg_row: dict, dataset_id: str,
                   ctx: ProcessingContext) -> dict:
    """Resolve both sides' read-only inputs for one registered dataset."""
    source = Path(str(reg_row.get("source_path", "")))
    meta = _registration_metadata(reg_row)
    if kind == "csv":
        info = meta.get("source_info") or {}
        return {"kind": "csv", "path": str(source),
                "encoding": str(info.get("encoding") or "utf-8"),
                "delimiter": str(info.get("delimiter") or ","),
                "name": reg_row.get("name") or dataset_id,
                "source_path": str(source)}
    if kind == "sqlite":
        table = _registration_table(reg_row)
        if table is None:
            table = _single_table_or_none(source)
        if table is None:
            raise PlatformError(
                "This database has several tables and no table was recorded "
                "at registration - re-register it with a table selected.",
                detail=str(source))
        return {"kind": "sqlite", "path": str(source), "table": table,
                "name": reg_row.get("name") or dataset_id,
                "source_path": str(source)}
    # filesystem: analysed from its registered scan output, never re-walked
    files_csv = _require_artifact(
        ctx.datasets_dir / dataset_id / "files_metadata.csv",
        "folder scan output")
    folders_csv = ctx.datasets_dir / dataset_id / "folders_metadata.csv"
    root = (meta.get("source_info") or {}).get("root")
    return {"kind": "filesystem", "files_csv": str(files_csv),
            "folders_csv": str(folders_csv) if folders_csv.exists() else None,
            "root": str(root) if root else None,
            "name": reg_row.get("name") or dataset_id,
            "source_path": str(source)}


def _single_table_or_none(source: Path) -> str | None:
    """Single-table auto-resolution (read-only) for older registrations."""
    import sqlite3
    if not source.exists():
        return None
    uri = f"file:{source.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return None
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return rows[0][0] if len(rows) == 1 else None


# ---------------------------------------------------------------------- #
# Key resolution
# ---------------------------------------------------------------------- #
def _header_columns(kind: str, inputs: dict) -> list[str]:
    """Column names of one side, read without loading any data rows."""
    if kind == "csv":
        try:
            frame = pd.read_csv(inputs["path"], nrows=0,
                                encoding=inputs.get("encoding") or "utf-8",
                                sep=inputs.get("delimiter") or ",")
        except Exception as exc:  # noqa: BLE001 - surfaced as guidance
            raise PlatformError(
                "The CSV header could not be read - check the file and "
                "re-register the dataset on the Data Sources page.",
                detail=f"{type(exc).__name__} at {inputs['path']}")
        return [str(c) for c in frame.columns]

    import sqlite3
    from ingestion.sqlite_loader import quote_ident
    table = str(inputs["table"])
    uri = f"file:{Path(inputs['path']).as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise PlatformError(
            "The database could not be opened for reading.",
            detail=f"{type(exc).__name__}") from exc
    try:
        rows = conn.execute(
            f"PRAGMA table_info({quote_ident(table)})").fetchall()
    except sqlite3.Error as exc:
        raise PlatformError(
            "The table schema could not be read from this database.",
            detail=f"{type(exc).__name__}") from exc
    finally:
        conn.close()
    if not rows:
        raise PlatformError(
            f"The table '{table}' does not exist in this database - "
            "re-register the dataset with a table selected.")
    return [str(r[1]) for r in rows]


def resolve_keys(kind: str, in_a: dict, in_b: dict, keys, *,
                 explicit: bool, guess, warnings: list) -> tuple:
    """Validate key columns against both sides before matching starts.

    * filesystem comparisons never use record keys (they match by path).
    * explicitly requested keys must exist on both sides - otherwise the
      run fails with guidance instead of silently degrading.
    * an automatic guess that no longer fits the data falls back to
      exact-content matching with a warning.
    """
    if kind == "filesystem":
        return ()
    cols_a = set(_header_columns(kind, in_a))
    cols_b = set(_header_columns(kind, in_b))
    wanted = tuple(str(k) for k in (keys or ()))
    if wanted:
        missing = sorted({k for k in wanted
                          if k not in cols_a or k not in cols_b})
        if missing:
            if explicit:
                raise PlatformError(
                    "Key column(s) not present in both datasets: "
                    + ", ".join(missing)
                    + ". Adjust the key selection and start the "
                      "comparison again.")
            warnings.append(warn(
                "auto_key_unusable",
                "the automatically suggested key column(s) "
                f"({', '.join(missing)}) do not exist in both datasets - "
                "comparing by exact row content instead."))
            return ()
        return wanted
    guessed = tuple(str(g) for g in (guess or ()))
    if guessed and all(g in cols_a and g in cols_b for g in guessed):
        return guessed
    if guessed:
        warnings.append(warn(
            "auto_key_unusable",
            "the automatically suggested key column(s) "
            f"({', '.join(guessed)}) do not exist in both datasets - "
            "comparing by exact row content instead."))
    return ()


# ---------------------------------------------------------------------- #
# Orchestration
# ---------------------------------------------------------------------- #
def run_comparison(kind: str, dataset_a: str, dataset_b: str,
                   config_obj: ComparisonConfig | None = None,
                   context: ProcessingContext | None = None
                   ) -> ComparisonResult:
    """Run one complete comparison between two registered datasets."""
    cfg = config_obj or ComparisonConfig()
    kind = str(kind)
    dataset_a, dataset_b = str(dataset_a), str(dataset_b)

    def early(error: str) -> ComparisonResult:
        return ComparisonResult(ok=False, kind=kind, error=error)

    if kind not in COMPARE_TYPES:
        return early(f"Unknown comparison type: {kind!r}.")
    if dataset_a == dataset_b:
        return early("Select two different datasets to compare.")
    reg_a = _registry_row(dataset_a)
    reg_b = _registry_row(dataset_b)
    if reg_a is None or reg_b is None:
        return early("Both datasets must be registered on the Data Sources "
                     "page first.")
    for reg in (reg_a, reg_b):
        if str(reg.get("source_type", "")) != kind:
            return early(
                f"This comparison type expects {kind} datasets - both "
                "selected datasets must be of that source type.")
    comparison_id = comparison_id_for(kind, dataset_a, dataset_b)
    run_id = f"run_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:6]}"
    ctx = context if context is not None else ProcessingContext(job_id=run_id)
    if ctx.cancelled:
        return early("The job was cancelled before it started.")

    started = datetime.now()
    warnings: list[dict] = []
    files: dict[str, str] = {}
    stage_notes: list[str] = []
    summary_box: dict = {"summary": {}}
    parts_box: dict = {"parts": None}
    inputs_box: dict = {"a": None, "b": None}

    def stage(name: str, current: int = 0, total: int | None = None,
              message: str = "") -> None:
        if ctx.cancelled:
            raise _Cancelled()
        ctx.report_progress(name, current, total, message=message)

    try:
        run_dir = ctx.comparison_dir(comparison_id) / "runs" / run_id
        assert_writable_target(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
    except (PlatformError, OSError) as exc:
        msg = (exc.user_message if isinstance(exc, PlatformError)
               else "The comparison run directory could not be created.")
        return early(msg)

    files[FILE_CONFIG] = str(run_dir / FILE_CONFIG)
    write_json(run_dir, FILE_CONFIG, {
        "run_id": run_id, "comparison_id": comparison_id, "kind": kind,
        "dataset_a": dataset_a, "dataset_b": dataset_b,
        "config": cfg.to_dict(), "engine": "comparison.base"})

    def finish(status: str, error: str = "") -> ComparisonResult:
        meta = {
            "run_id": run_id, "comparison_id": comparison_id, "kind": kind,
            "dataset_a": dataset_a, "dataset_b": dataset_b,
            "status": status, "error": error,
            "started_at": started.isoformat(timespec="seconds"),
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "duration_seconds": round(
                (datetime.now() - started).total_seconds(), 3),
            "progress": ctx.progress,
            "python": __import__("sys").version.split()[0],
            "pandas": pd.__version__,
        }
        write_json(run_dir, FILE_META, meta)
        if status == "completed":
            ctx.set_status("completed")
        elif status == "cancelled":
            ctx.set_status("cancelled")
        else:
            ctx.set_status("failed")
        files[FILE_META] = str(run_dir / FILE_META)
        summary = summary_box["summary"]
        return ComparisonResult(
            ok=status == "completed", comparison_id=comparison_id,
            run_id=run_id, run_dir=str(run_dir), kind=kind, error=error,
            status=status, summary=summary, warnings=list(warnings),
            files=dict(files), progress=ctx.progress)

    try:
        stage("verify", message="checking both registered sources")
        in_a = resolve_inputs(kind, reg_a, dataset_a, ctx)
        in_b = resolve_inputs(kind, reg_b, dataset_b, ctx)
        inputs_box["a"], inputs_box["b"] = in_a, in_b

        ver_a = _verify_side(reg_a, kind, in_a)
        ver_b = _verify_side(reg_b, kind, in_b)
        not_ok = [(dataset_a, ver_a), (dataset_b, ver_b)]
        not_ok = [(d, v) for d, v in not_ok if not v.ok_to_run]
        if not_ok:
            for ds_id, ver in not_ok:
                warnings.append(warn(
                    f"source_{ver.status}",
                    f"dataset {ds_id}: {ver.guidance()}"))
            write_json(run_dir, FILE_WARNINGS, warnings)
            files[FILE_WARNINGS] = str(run_dir / FILE_WARNINGS)
            refused = {
                "comparison_id": comparison_id, "kind": kind,
                "status": "refused",
                "reason": not_ok[0][1].status,
                "guidance": not_ok[0][1].guidance(),
                "verification": {
                    "a": ver_a.to_dict(), "b": ver_b.to_dict()},
                "counts": {}, "exactness": "not-run",
                "truncated": {}, "warnings_count": len(warnings),
            }
            write_json(run_dir, FILE_SUMMARY, refused)
            write_json(run_dir, FILE_SCHEMA, {"kind": kind,
                                              "status": "not-run"})
            write_json(run_dir, FILE_STATS, {"kind": kind,
                                             "status": "not-run"})
            files[FILE_SUMMARY] = str(run_dir / FILE_SUMMARY)
            files[FILE_SCHEMA] = str(run_dir / FILE_SCHEMA)
            files[FILE_STATS] = str(run_dir / FILE_STATS)
            summary_box["summary"] = refused
            return finish("refused", not_ok[0][1].guidance())
        for ver in (ver_a, ver_b):
            stage_notes.append(f"source verification: {ver.status}")
            stage_notes.extend(ver.notes)

        stage("load", message=f"preparing the {kind} comparison")
        cfg.keys = resolve_keys(
            kind, in_a, in_b, cfg.keys, explicit=bool(cfg.keys),
            guess=tuple(_default_keys(reg_a) or ()), warnings=warnings)
        if kind == "filesystem":
            from analysis.comparison import filesystem_comparison as mod
        elif kind == "csv":
            from analysis.comparison import csv_comparison as mod
        else:
            from analysis.comparison import sqlite_comparison as mod
        parts = mod.compare(in_a, in_b, cfg, stage)
        warnings.extend(parts.get("warnings") or [])
        parts_box["parts"] = parts

        stage("outputs", message="writing comparison report")
        from analysis.comparison import reporting
        summary = reporting.write_run(
            run_dir=run_dir, kind=kind, cfg=cfg, comparison_id=comparison_id,
            run_id=run_id, reg_a=reg_a, reg_b=reg_b, inputs=(in_a, in_b),
            parts=parts, stage_notes=stage_notes, warnings=warnings)
        for name in (FILE_SUMMARY, FILE_SCHEMA, FILE_STATS, FILE_WARNINGS):
            files[name] = str(run_dir / name)
        files.update({k: str(run_dir / k)
                      for k in parts.get("table_files", [])})
        summary_box["summary"] = summary
        log.info("Comparison %s completed (%s)", run_id, kind)
        return finish("completed")

    except _Cancelled:
        warnings.append(warn("cancelled",
                             "job cancelled between stages"))
        write_json(run_dir, FILE_WARNINGS, warnings)
        files[FILE_WARNINGS] = str(run_dir / FILE_WARNINGS)
        summary_box.setdefault("summary", {"status": "cancelled"})
        return finish("cancelled", "The job was cancelled between stages.")
    except PlatformError as exc:
        warnings.append(warn("failed", exc.user_message))
        _failure_outputs(run_dir, warnings, files, summary_box,
                         comparison_id, kind)
        return finish("failed", exc.user_message)
    except Exception as exc:  # noqa: BLE001 - contained, reported
        msg = (f"Comparison failed ({type(exc).__name__}). Details are in "
               "run_metadata.json and warnings.json in the run folder.")
        warnings.append(warn(type(exc).__name__, str(exc)))
        _failure_outputs(run_dir, warnings, files, summary_box,
                         comparison_id, kind)
        return finish("failed", msg)


def _failure_outputs(run_dir: Path, warnings: list, files: dict,
                     summary_box: dict, comparison_id: str,
                     kind: str) -> None:
    try:
        write_json(run_dir, FILE_WARNINGS, warnings)
        files[FILE_WARNINGS] = str(run_dir / FILE_WARNINGS)
        summary = {"comparison_id": comparison_id, "kind": kind,
                   "status": "failed", "counts": {},
                   "exactness": "not-run", "truncated": {},
                   "warnings_count": len(warnings)}
        write_json(run_dir, FILE_SUMMARY, summary)
        files[FILE_SUMMARY] = str(run_dir / FILE_SUMMARY)
        summary_box.setdefault("summary", summary)
    except (PlatformError, OSError):
        pass


def _verify_side(reg_row: dict, kind: str, inputs: dict):
    """Verify one registered source (read-only); caller checks ``ok_to_run``."""
    artifact = None
    if kind == "filesystem":
        artifact = Path(inputs["files_csv"])
    return verify_source(reg_row, config_obj=_default_verify_cfg(),
                         artifact=artifact)


_DEFAULT_VERIFY_CFG = None


def _default_verify_cfg():
    global _DEFAULT_VERIFY_CFG
    if _DEFAULT_VERIFY_CFG is None:
        from analysis.analysis_runner import AnalysisConfig
        _DEFAULT_VERIFY_CFG = AnalysisConfig()
    return _DEFAULT_VERIFY_CFG


def _default_keys(reg_row: dict) -> list[str]:
    """Best-effort key guess: unique id-like column from registration."""
    meta = _registration_metadata(reg_row)
    cols = meta.get("columns") or []
    names = [str(c.get("name")) for c in cols if isinstance(c, dict)]
    for candidate in names:
        if candidate.lower() in ("id", "uuid", "key", "guid",
                                 "record_id", "row_id"):
            return [candidate]
    return []


def registration_column_names(dataset_id: str) -> list[str]:
    """Column names recorded at registration time (never reads source)."""
    reg = _registry_row(str(dataset_id))
    if reg is None:
        return []
    cols = _registration_metadata(reg).get("columns") or []
    return [str(c.get("name")) for c in cols
            if isinstance(c, dict) and c.get("name")]


# ---------------------------------------------------------------------- #
# History
# ---------------------------------------------------------------------- #
def validate_selection(kind: str, dataset_a: str, dataset_b: str, keys
                       ) -> dict:
    """Pre-run validation for the dashboard (read-only, no comparison).

    Returns ``{"ok", "errors", "warnings", "details", "keys"}`` where
    ``keys`` are the record key columns the run would actually use
    (empty for filesystem comparisons).
    """
    out: dict = {"ok": True, "errors": [], "warnings": [],
                 "details": [], "keys": []}

    def fail(message: str) -> None:
        out["ok"] = False
        out["errors"].append(str(message))

    if kind not in COMPARE_TYPES:
        fail(f"Unknown comparison type: {kind!r}.")
        return out
    if not dataset_a or not dataset_b:
        fail("Select two datasets to compare.")
        return out
    if dataset_a == dataset_b:
        fail("Select two different datasets to compare.")
        return out
    reg_a, reg_b = _registry_row(dataset_a), _registry_row(dataset_b)
    if reg_a is None or reg_b is None:
        fail("Both datasets must be registered on the Data Sources page "
             "first.")
        return out
    for label, reg in (("first", reg_a), ("second", reg_b)):
        if str(reg.get("source_type", "")) != kind:
            fail(f"The selected {label} dataset is not a {kind} dataset - "
                 f"choose two {kind} datasets.")
    if not out["ok"]:
        return out

    ctx = ProcessingContext()
    try:
        in_a = resolve_inputs(kind, reg_a, dataset_a, ctx)
        in_b = resolve_inputs(kind, reg_b, dataset_b, ctx)
    except PlatformError as exc:
        fail(exc.user_message)
        return out

    for ds_id, reg, inputs in ((dataset_a, reg_a, in_a),
                               (dataset_b, reg_b, in_b)):
        try:
            ver = _verify_side(reg, kind, inputs)
        except (OSError, ValueError, PlatformError) as exc:
            fail(f"Source verification could not run for dataset "
                 f"{ds_id[:8]} ({type(exc).__name__}).")
            continue
        if ver.ok_to_run:
            out["details"].append(
                f"source verification {ds_id[:8]}: {ver.status}")
        else:
            fail(ver.guidance())

    if kind == "filesystem":
        out["details"].append(
            "files are matched by normalized relative path inside each "
            "dataset's recorded scan root (metadata only)")
        return out

    try:
        cols_a = set(_header_columns(kind, in_a))
        cols_b = set(_header_columns(kind, in_b))
    except PlatformError as exc:
        fail(exc.user_message)
        return out
    wanted = tuple(str(k) for k in (keys or ()))
    if wanted:
        missing = sorted({k for k in wanted
                          if k not in cols_a or k not in cols_b})
        if missing:
            fail("Key column(s) not present in both datasets: "
                 + ", ".join(missing) + ".")
            return out
        out["keys"] = list(wanted)
        out["details"].append("key columns: " + ", ".join(wanted))
    else:
        guess = tuple(_default_keys(reg_a) or ())
        if guess and all(g in cols_a and g in cols_b for g in guess):
            out["keys"] = list(guess)
            out["details"].append(
                "key columns (auto-detected): " + ", ".join(guess))
        else:
            out["warnings"].append(
                "No key columns selected or detected - rows will be "
                "compared by exact content only: a changed record "
                "appears as one removal plus one addition, and row order "
                "is ignored.")
    out["details"].append(
        f"common columns: {len(cols_a & cols_b)} of "
        f"{len(cols_a)} / {len(cols_b)}")
    return out


def list_comparisons(limit: int = 100) -> list[dict]:
    """All comparison runs (newest first), tagged with their comparison_id."""
    try:
        root = ProcessingContext().comparisons_dir
    except PlatformError:
        return []
    if not root.exists():
        return []
    rows: list[dict] = []
    for comp_dir in root.iterdir():
        runs_dir = comp_dir / "runs"
        if not comp_dir.is_dir() or not runs_dir.is_dir():
            continue
        try:
            entries = sorted(runs_dir.iterdir(),
                             key=lambda p: (p.stat().st_mtime, p.name),
                             reverse=True)
        except OSError:
            continue
        for run in entries:
            meta_path = run / FILE_META
            if not run.is_dir() or not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            meta = dict(meta)
            meta.setdefault("comparison_id", comp_dir.name)
            meta["run_dir"] = str(run)
            rows.append(meta)
            if len(rows) >= limit:
                rows.sort(key=lambda r: str(r.get("finished_at", "")),
                          reverse=True)
                return rows
    rows.sort(key=lambda r: str(r.get("finished_at", "")), reverse=True)
    return rows[:limit]


def load_comparison(comparison_id: str, run_id: str) -> dict:
    """Read one comparison run's outputs back (JSON parsed, CSVs listed)."""
    comparison_id = str(Path(str(comparison_id)).name)
    run_id = str(Path(str(run_id)).name)
    bad = ("", ".", "..")
    if (comparison_id in bad or run_id in bad
            or "/" in comparison_id or "\\" in comparison_id
            or "/" in run_id or "\\" in run_id):
        raise PlatformError("Invalid comparison identifier.")
    run_dir = (ProcessingContext().comparisons_dir
               / comparison_id / "runs" / run_id)
    if not run_dir.is_dir():
        raise PlatformError("That comparison run does not exist.")
    out: dict = {"comparison_id": comparison_id, "run_id": run_id,
                 "run_dir": str(run_dir), "files": {}, "tables": {}}
    for name in (FILE_CONFIG, FILE_SUMMARY, FILE_SCHEMA, FILE_STATS,
                 FILE_WARNINGS, FILE_META):
        p = run_dir / name
        out["files"][name] = str(p) if p.exists() else None
        if p.exists() and name.endswith(".json"):
            try:
                out[name.replace(".json", "")] = json.loads(
                    p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                out.setdefault("read_errors", []).append(
                    f"{name}: {type(exc).__name__}")
    for p in sorted(run_dir.glob("*.csv")):
        out["files"][p.name] = str(p)
        out["tables"][p.name] = str(p)
    return out
