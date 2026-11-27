"""General Dataset Analysis runner (Mode B, Phase 5).

Orchestrates a complete, reproducible analysis of one REGISTERED dataset:

    verify source -> load (full / sample / streaming) -> profile ->
    statistics -> quality -> distributions -> correlation -> write outputs

Guarantees:
* The source is re-verified (size + mtime fingerprint, content hash where
  practical) before anything runs; a changed or missing source is REFUSED
  with guidance to re-inspect/re-register - never silently analyzed.
* SQLite stays read-only; no code path writes to a source.
* Every run is written separately to
  ``output/datasets/<dataset_id>/runs/<run_id>/`` as stable JSON/CSV.
* Streaming mode never materializes the full dataset; in-memory mode only
  loads up to ``full_load_max_rows`` (or an explicit ``sample_rows`` cap)
  and says so in the outputs.
* JSON outputs are sanitized (no NaN/inf) with sorted keys so identical
  data + identical config reproduce byte-identical result files.

Excluded-record accounting is explicit everywhere: statistics exclude NaN
and +/-inf (counted per column); histograms exclude non-finite (counted);
grouped statistics drop NaN group keys (counted); correlation uses
pairwise-complete rows (rows_total / rows_complete reported).
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd

import config
from analysis import correlation as corr_mod
from analysis import distributions as dist_mod
from analysis import frequency_analysis as freq_mod
from analysis import outliers as out_mod
from analysis.data_quality import QualityAccumulator, analyze_quality
from analysis.dataset_profiler import classify_column, profile_dataset
from analysis.statistics import NumericAccumulator, numerical_stats
from core.exceptions import PlatformError
from core.processing_context import ProcessingContext, assert_writable_target
from ingestion.csv_loader import CsvLoader
from ingestion.dataset_registry import DatasetRegistry
from ingestion.sqlite_loader import SqliteLoader

log = logging.getLogger("da.analysis.runner")

FILE_CONFIG = "config.json"
FILE_FINGERPRINT = "fingerprint.json"
FILE_SUMMARY = "summary.json"
FILE_STATISTICS = "statistics.json"
FILE_QUALITY = "quality_report.json"
FILE_DISTRIBUTIONS = "distributions.json"
FILE_CORRELATION = "correlation.csv"
FILE_ERRORS = "errors.json"
FILE_RUN_META = "run_metadata.json"

RUN_FILE_NAMES = (FILE_CONFIG, FILE_FINGERPRINT, FILE_SUMMARY,
                  FILE_STATISTICS, FILE_QUALITY, FILE_DISTRIBUTIONS,
                  FILE_CORRELATION, FILE_ERRORS, FILE_RUN_META)


# ---------------------------------------------------------------------- #
# Configuration
# ---------------------------------------------------------------------- #
@dataclass
class AnalysisConfig:
    """Options for one analysis run (all values JSON-serializable)."""

    chunk_size: int = 100_000
    sample_rows: int | None = None
    full_load_max_rows: int = 1_000_000
    profile_sample_rows: int = 5
    histogram_bins: int = 20
    top_categories: int = 50
    rare_pct_threshold: float = 0.5
    rare_max_count: int = 5
    high_cardinality_unique: int = 1_000
    max_distinct: int = 50_000
    quantile_sample_limit: int = 1_000_000
    correlation_enabled: bool = True
    correlation_methods: tuple[str, ...] = ("pearson", "spearman")
    max_correlation_columns: int = 100
    correlation_sample_rows: int = 50_000
    group_by: str | None = None
    max_groups: int = 100
    outlier_k: float = 1.5
    detect_outliers: bool = True
    hash_max_bytes: int = 100_000_000
    seed: int = 42

    def __post_init__(self) -> None:
        self.chunk_size = max(1_000, int(self.chunk_size))
        if self.sample_rows is not None:
            self.sample_rows = max(1, int(self.sample_rows))
        self.full_load_max_rows = max(1, int(self.full_load_max_rows))
        self.profile_sample_rows = max(0, int(self.profile_sample_rows))
        self.histogram_bins = min(200, max(1, int(self.histogram_bins)))
        self.top_categories = min(10_000, max(1, int(self.top_categories)))
        self.max_groups = max(1, int(self.max_groups))
        self.outlier_k = float(self.outlier_k)
        self.correlation_methods = tuple(
            str(m) for m in self.correlation_methods) or ("pearson",)
        for m in self.correlation_methods:
            if m not in ("pearson", "spearman", "kendall"):
                raise ValueError(f"unsupported correlation method: {m!r}")

    def to_dict(self) -> dict:
        return {
            "chunk_size": self.chunk_size,
            "sample_rows": self.sample_rows,
            "full_load_max_rows": self.full_load_max_rows,
            "profile_sample_rows": self.profile_sample_rows,
            "histogram_bins": self.histogram_bins,
            "top_categories": self.top_categories,
            "rare_pct_threshold": self.rare_pct_threshold,
            "rare_max_count": self.rare_max_count,
            "high_cardinality_unique": self.high_cardinality_unique,
            "max_distinct": self.max_distinct,
            "quantile_sample_limit": self.quantile_sample_limit,
            "correlation_enabled": self.correlation_enabled,
            "correlation_methods": list(self.correlation_methods),
            "max_correlation_columns": self.max_correlation_columns,
            "correlation_sample_rows": self.correlation_sample_rows,
            "group_by": self.group_by,
            "max_groups": self.max_groups,
            "outlier_k": self.outlier_k,
            "detect_outliers": self.detect_outliers,
            "hash_max_bytes": self.hash_max_bytes,
            "seed": self.seed,
        }


# ---------------------------------------------------------------------- #
# Source verification
# ---------------------------------------------------------------------- #
def _sha256(path: Path, max_bytes: int) -> tuple[str | None, str]:
    size = path.stat().st_size
    if size > max_bytes:
        return None, (f"content hash skipped (file is {size:,} bytes, "
                      f"cap {max_bytes:,}); size+mtime fingerprint used")
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest(), "sha-256 of full content"


@dataclass
class SourceVerification:
    status: str                       # ok | changed | missing |
                                      # artifact_missing | unverifiable
    source_path: str
    checks: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    registered_fingerprint: str | None = None
    current_fingerprint: str | None = None
    content_hash: str | None = None
    content_hash_note: str = ""

    @property
    def ok_to_run(self) -> bool:
        return self.status in ("ok", "unverifiable")

    @property
    def changed(self) -> bool:
        return self.status == "changed"

    def guidance(self) -> str:
        if self.status == "missing":
            return ("The registered source no longer exists - if it moved "
                    "or was replaced, re-register it on the Data Sources "
                    "page before analyzing.")
        if self.status == "changed":
            return ("The source changed after registration - inspect and "
                    "re-register it on the Data Sources page before "
                    "analyzing.")
        if self.status == "artifact_missing":
            return ("The registered folder dataset has no scan output to "
                    "analyze - re-register it on the Data Sources page.")
        return ""

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "source_path": self.source_path,
            "checks": self.checks,
            "notes": self.notes,
            "registered_fingerprint": self.registered_fingerprint,
            "current_fingerprint": self.current_fingerprint,
            "content_hash": self.content_hash,
            "content_hash_note": self.content_hash_note,
            "guidance": self.guidance(),
        }


def verify_source(reg_row: dict, config_obj: AnalysisConfig,
                  artifact: Path | None = None) -> SourceVerification:
    """Compare the registered source with its current state.

    * size + mtime_ns fingerprint vs the one recorded at registration.
    * content SHA-256 for regular files up to ``hash_max_bytes`` (recorded,
      so cross-run tampering is visible in ``fingerprint.json``).
    * for folder datasets *artifact* (the scan-output CSV that will be
      analyzed) must exist and is hashed instead of the directory.
    """
    source = Path(str(reg_row.get("source_path", "")))
    ver = SourceVerification(
        status="ok", source_path=str(source),
        registered_fingerprint=reg_row.get("source_fingerprint") or None)

    if not source.exists():
        ver.status = "missing"
        ver.checks.append({"code": "exists", "ok": False,
                           "message": "source path does not exist"})
        return ver
    ver.checks.append({"code": "exists", "ok": True,
                       "message": "source path exists"})

    try:
        st = source.stat()
    except OSError as exc:
        ver.status = "missing"
        ver.checks.append({"code": "stat", "ok": False,
                           "message": f"cannot stat source ({exc.__class__.__name__})"})
        return ver

    ver.current_fingerprint = f"{st.st_size}:{st.st_mtime_ns}"
    reg_fp = ver.registered_fingerprint
    if reg_fp is None:
        ver.status = "unverifiable"
        ver.notes.append(
            "no fingerprint recorded at registration - current state "
            "recorded now as the future baseline")
        ver.checks.append({"code": "fingerprint", "ok": True,
                           "message": "no registered fingerprint to "
                                      "compare (recorded now)"})
    elif reg_fp == ver.current_fingerprint:
        ver.checks.append({"code": "fingerprint", "ok": True,
                           "message": "size + mtime match registration"})
    else:
        ver.status = "changed"
        ver.checks.append({"code": "fingerprint", "ok": False,
                           "message": "size and/or mtime differ from the "
                                      "registered fingerprint"})

    reg_bytes = reg_row.get("bytes")
    if (reg_bytes is not None and source.is_file()
            and int(reg_bytes) != st.st_size and ver.status == "ok"):
        ver.status = "changed"
        ver.checks.append({"code": "bytes", "ok": False,
                           "message": f"file size {st.st_size:,} differs "
                                      f"from registered {int(reg_bytes):,}"})

    hash_target: Path | None = None
    if artifact is not None:
        if not artifact.exists():
            ver.status = "artifact_missing"
            ver.checks.append({"code": "artifact", "ok": False,
                               "message": "scan output for this dataset "
                                          "does not exist"})
        else:
            hash_target = artifact
            ver.checks.append({"code": "artifact", "ok": True,
                               "message": "scan output exists"})
    elif source.is_file():
        hash_target = source

    if hash_target is not None:
        digest, note = _sha256(hash_target, config_obj.hash_max_bytes)
        ver.content_hash = digest
        ver.content_hash_note = note
        ver.checks.append({"code": "content_hash", "ok": True,
                           "message": note})
    else:
        ver.content_hash_note = (
            "directory source - content hashing not applicable; "
            "size+mtime fingerprint used")
        ver.notes.append(ver.content_hash_note)
    return ver


# ---------------------------------------------------------------------- #
# JSON helpers
# ---------------------------------------------------------------------- #
def _json_safe(obj):
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if obj is pd.NaT:
        return None
    if isinstance(obj, float):
        return None if (obj != obj or obj in (float("inf"), float("-inf"))
                        ) else obj
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return _json_safe(float(obj))
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (np.ndarray,)):
        return [_json_safe(v) for v in obj.tolist()]
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.isoformat()
    if obj is pd.NaT:
        return None
    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    return str(obj)


def _write_json(run_dir: Path, name: str, payload) -> None:
    target = run_dir / name
    assert_writable_target(target)
    target.write_text(
        json.dumps(_json_safe(payload), indent=2, sort_keys=True,
                   ensure_ascii=False, allow_nan=False, default=str),
        encoding="utf-8")


# ---------------------------------------------------------------------- #
# Run results
# ---------------------------------------------------------------------- #
@dataclass
class RunResult:
    ok: bool
    run_id: str = ""
    dataset_id: str = ""
    run_dir: str = ""
    error: str = ""
    files: dict = field(default_factory=dict)
    summary: dict = field(default_factory=dict)
    verification: SourceVerification | None = None
    progress: list = field(default_factory=list)


# ---------------------------------------------------------------------- #
# Correlation row reservoir (streaming mode)
# ---------------------------------------------------------------------- #
class _RowReservoir:
    def __init__(self, columns: list[str], max_rows: int, seed: int):
        self.columns = list(columns)
        self.max_rows = max(2, int(max_rows))
        self.rng = np.random.default_rng(seed)
        self.rows: list[np.ndarray] = []
        self.seen = 0

    def update(self, numeric_df: pd.DataFrame) -> None:
        if numeric_df.empty:
            return
        arr = numeric_df.to_numpy(dtype="float64", na_value=np.nan)
        for row in arr:
            row = np.where(np.isinf(row), np.nan, row)
            self.seen += 1
            if len(self.rows) < self.max_rows:
                self.rows.append(row)
            else:
                j = int(self.rng.integers(0, self.seen))
                if j < self.max_rows:
                    self.rows[j] = row

    def to_frame(self) -> pd.DataFrame:
        if not self.rows:
            return pd.DataFrame(columns=self.columns)
        return pd.DataFrame(np.vstack(self.rows), columns=self.columns)


# ---------------------------------------------------------------------- #
# Analysis execution
# ---------------------------------------------------------------------- #
def _choose_mode(reg_row: dict, cfg: AnalysisConfig) -> str:
    if cfg.sample_rows is not None:
        return "sample"
    rows = reg_row.get("row_count")
    if rows is not None and int(rows) <= cfg.full_load_max_rows:
        return "full"
    return "streaming"


def _build_loader(reg_row: dict, cfg: AnalysisConfig,
                  dataset_id: str, ctx: ProcessingContext,
                  ver: SourceVerification
                  ) -> tuple[object, Path, list[str]]:
    """Return (loader, artifact_path, notes) for the registered source."""
    kind = str(reg_row.get("source_type", ""))
    notes: list[str] = []
    if kind == "csv":
        artifact = Path(str(reg_row.get("source_path")))
        return CsvLoader(artifact, chunk_size=cfg.chunk_size), artifact, notes
    if kind == "sqlite":
        artifact = Path(str(reg_row.get("source_path")))
        table = _registration_table(reg_row)
        if table:
            notes.append(f"table '{table}' recorded at registration")
        loader = SqliteLoader(artifact, table=table,
                              chunk_size=cfg.chunk_size)
        if table is None:
            notes.append("no table recorded at registration - single-table "
                         "auto-resolution used (multi-table databases need "
                         "re-registration)")
        return loader, artifact, notes
    if kind == "filesystem":
        artifact = ctx.datasets_dir / dataset_id / "files_metadata.csv"
        notes.append(
            "folder datasets are analyzed from their registered scan "
            "output (files_metadata.csv), not by re-walking the folder")
        return CsvLoader(artifact, chunk_size=cfg.chunk_size), artifact, notes
    raise PlatformError(
        "This dataset's source type cannot be analyzed.",
        detail=f"source_type={kind!r}")


def _registration_table(reg_row: dict) -> str | None:
    loc = reg_row.get("metadata_location")
    if not loc:
        return None
    p = Path(str(loc))
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    info = (payload.get("dataset", {}).get("metadata", {})
            .get("source_info") or {})
    table = info.get("table")
    return str(table) if table else None


def _split_columns(df: pd.DataFrame) -> tuple[list[str], list[str], list[str]]:
    numeric: list[str] = []
    categorical: list[str] = []
    other: list[str] = []
    for c in df.columns:
        kind = classify_column(df[c])
        if kind in ("numeric",):
            numeric.append(str(c))
        elif kind in ("categorical", "boolean"):
            categorical.append(str(c))
        else:
            other.append(str(c))
    return numeric, categorical, other


def _categorical_for(df: pd.DataFrame, cols: list[str],
                     cfg: AnalysisConfig) -> dict:
    return {
        c: freq_mod.categorical_stats(
            df[c], c, top=cfg.top_categories,
            rare_pct_threshold=cfg.rare_pct_threshold,
            rare_max_count=cfg.rare_max_count,
            high_cardinality_unique=cfg.high_cardinality_unique).to_dict()
        for c in cols}


def _histograms(df: pd.DataFrame, cols: list[str],
                cfg: AnalysisConfig) -> dict:
    return {c: dist_mod.numerical_histogram(df[c], bins=cfg.histogram_bins,
                                            column=c)
            for c in cols}


def _analyze_in_memory(df: pd.DataFrame, cfg: AnalysisConfig, basis: str,
                       stage) -> dict:
    """Full analysis for an in-memory frame (basis: exact | sample)."""
    numeric, categorical, other = _split_columns(df)
    notes: list[str] = []
    if basis == "sample":
        notes.append(
            f"statistics computed from a sample of {len(df):,} rows - "
            "approximate; use the full mode for exact results")

    stage("profile", message=f"{len(df):,} rows in memory")
    profile = profile_dataset(df, sample_rows=cfg.profile_sample_rows)
    profile["basis"] = basis

    stage("statistics", message=f"{len(numeric)} numeric column(s)")
    statistics = {c: numerical_stats(df[c], c, basis=basis).to_dict()
                  for c in numeric}
    statistics["_notes"] = notes

    stage("quality", message="checks")
    quality = analyze_quality(df, k=cfg.outlier_k,
                              detect_outliers=cfg.detect_outliers,
                              high_cardinality_unique=cfg.high_cardinality_unique)
    quality["basis"] = basis

    stage("distributions", message="histograms + frequencies")
    distributions: dict = {
        "histograms": _histograms(df, numeric, cfg),
        "categorical": _categorical_for(df, categorical, cfg),
        "columns_not_analyzed": {c: "datetime/other - profiled only"
                                 for c in other},
        "bases": {"histograms": basis, "categorical": basis},
        "notes": [],
    }
    if cfg.group_by:
        if cfg.group_by in df.columns:
            distributions["grouped"] = dist_mod.grouped_statistics(
                df, cfg.group_by, numeric_columns=numeric,
                max_groups=cfg.max_groups)
            distributions["grouped"]["basis"] = basis
        else:
            distributions["grouped"] = {
                "notes": [f"group column '{cfg.group_by}' not found"]}
    else:
        distributions["grouped"] = {"notes": ["group_by not configured"]}

    stage("correlation", message="matrix")
    if cfg.correlation_enabled:
        correlation = corr_mod.correlation_matrix(
            df, methods=cfg.correlation_methods,
            max_columns=cfg.max_correlation_columns, basis=basis)
    else:
        correlation = {"basis": "disabled", "methods": {}, "notes":
                       ["correlation disabled in configuration"],
                       "excluded_columns": {}}
    return {"profile": profile, "statistics": statistics,
            "quality": quality, "distributions": distributions,
            "correlation": correlation}


def _analyze_streaming(loader, cfg: AnalysisConfig, stage,
                       progress_load) -> dict:
    """Single-pass chunked analysis - never materializes the full frame."""
    numeric: list[str] = []
    categorical: list[str] = []
    other: list[str] = []
    first_chunk: pd.DataFrame | None = None
    numeric_accs: dict[str, NumericAccumulator] = {}
    value_accs: dict[str, freq_mod.ValueCountAccumulator] = {}
    quality_acc: QualityAccumulator | None = None
    corr_reservoir: _RowReservoir | None = None
    rows_seen = 0
    loader_name = loader.__class__.__name__

    for i, chunk in enumerate(loader.iter_chunks(cfg.chunk_size)):
        if first_chunk is None:
            first_chunk = chunk.copy()
            numeric, categorical, other = _split_columns(chunk)
            quality_acc = QualityAccumulator(
                [str(c) for c in chunk.columns], type_sample=5_000)
            for idx, c in enumerate(numeric):
                numeric_accs[c] = NumericAccumulator(
                    c, max_values=cfg.quantile_sample_limit,
                    seed=cfg.seed + idx)
            for idx, c in enumerate(categorical):
                value_accs[c] = freq_mod.ValueCountAccumulator(
                    c, max_distinct=cfg.max_distinct,
                    seed=cfg.seed + 1000 + idx)
            if cfg.correlation_enabled and len(numeric) >= 2:
                corr_reservoir = _RowReservoir(
                    numeric, cfg.correlation_sample_rows,
                    seed=cfg.seed + 5000)
        rows_seen += len(chunk)
        if quality_acc is not None:
            quality_acc.update(chunk)
        for c, acc in numeric_accs.items():
            acc.update(chunk[c]) if c in chunk.columns else None
        for c, acc in value_accs.items():
            acc.update(chunk[c]) if c in chunk.columns else None
        if corr_reservoir is not None:
            num_view = chunk.reindex(columns=corr_reservoir.columns)
            corr_reservoir.update(num_view.apply(
                pd.to_numeric, errors="coerce"))
        progress_load(rows_seen)
        del chunk

    stage("profile", message=f"{rows_seen:,} rows streamed")
    assert first_chunk is not None and quality_acc is not None, \
        "source yielded no data"
    finalized: dict = {c: acc.finalize() for c, acc in numeric_accs.items()}
    profile: dict = {
        "rows": rows_seen,
        "columns": int(first_chunk.shape[1]),
        "column_names": [str(c) for c in first_chunk.columns],
        "generated_by": "analysis_runner.streaming_profile",
        "basis": "streaming",
        "column_kinds": {"numeric": numeric, "boolean": [],
                         "datetime": [], "categorical": categorical,
                         "other": other},
        "column_detail": {},
        "sample_records": [
            {str(k): _json_safe(v) for k, v in row.items()}
            for row in first_chunk.head(
                max(cfg.profile_sample_rows, 0)).to_dict(orient="records")],
        "notes": [
            "no full frame in memory - per-column memory usage and exact "
            "global memory are not measurable in streaming mode",
            "sample records come from the first chunk only"],
    }
    if cfg.profile_sample_rows:
        profile["column_kinds_note"] = (
            "kinds inferred from the first chunk's dtypes")
    for c in first_chunk.columns:
        c = str(c)
        detail: dict = {"dtype": str(first_chunk[c].dtype),
                        "kind": classify_column(first_chunk[c])}
        if c in numeric_accs:
            acc = numeric_accs[c]
            detail.update({"missing": acc.missing,
                           "unique": finalized[c].unique,
                           "count": acc.count,
                           "excluded_nonfinite": acc.excluded_nonfinite})
        elif c in value_accs:
            acc = value_accs[c]
            detail.update({"missing": acc.missing,
                           "unique": (acc.distinct_exact
                                      and len(acc.counts)) or None})
        profile["column_detail"][c] = detail

    stage("statistics", message=f"{len(numeric)} numeric column(s)")
    statistics: dict = {c: finalized[c].to_dict() for c in numeric
                        if c in finalized}
    statistics["_notes"] = ["streaming aggregation - counts/mean/std/min/"
                            "max exact; quantile basis per column "
                            "(basis field)"]

    stage("quality", message="checks")
    unique_info: dict[str, int | None] = {}
    for c in categorical:
        acc = value_accs[c]
        unique_info[c] = (len(acc.counts) if acc.distinct_exact else None)
    for c in numeric:
        if c in finalized:
            unique_info[c] = finalized[c].unique
    quality = quality_acc.finalize(
        unique_info=unique_info, completeness_pct=None,
        missing_cells=0, total_cells=0, k=cfg.outlier_k,
        high_cardinality_unique=cfg.high_cardinality_unique)
    if cfg.detect_outliers:
        flagged: dict = {}
        any_sample = False
        for c, acc in numeric_accs.items():
            if not acc.values:
                continue
            summary = out_mod.outlier_summary(
                pd.Series(acc.values, dtype="float64"), k=cfg.outlier_k,
                basis=acc.basis, column=c)
            if acc.basis != "exact":
                any_sample = True
            if summary["count"] > 0:
                flagged[c] = {"count": summary["count"],
                              "pct": summary["pct"],
                              "basis": summary["basis"]}
        quality["potential_outliers"] = {
            "k": cfg.outlier_k,
            "basis": "sample" if any_sample else "exact",
            "columns": flagged,
            "notes": ["outliers computed from kept per-column values "
                      "(reservoir when capped)"]}
        if flagged:
            quality.setdefault("warnings", []).append(
                f"{len(flagged)} column(s) with IQR outliers "
                f"(k={cfg.outlier_k}) - review, never auto-removed")

    stage("distributions", message="histograms + frequencies")
    distributions: dict = {
        "histograms": {},
        "categorical": {},
        "columns_not_analyzed": {c: "datetime/other - profiled only"
                                 for c in other},
        "bases": {},
        "notes": ["streaming mode: per-column reservoirs used for "
                  "quantile-based calculations"],
    }
    for c, acc in numeric_accs.items():
        hist = dist_mod.numerical_histogram(
            pd.Series(acc.values, dtype="float64"),
            bins=cfg.histogram_bins, column=c)
        hist["basis"] = acc.basis
        if acc.capped:
            hist["notes"].append(
                "histogram from reservoir sample (values cap reached)")
        distributions["histograms"][c] = hist
    distributions["bases"]["histograms"] = "per-column values reservoir"
    for c, acc in value_accs.items():
        distributions["categorical"][c] = acc.finalize(
            top=cfg.top_categories,
            rare_pct_threshold=cfg.rare_pct_threshold,
            rare_max_count=cfg.rare_max_count,
            high_cardinality_unique=cfg.high_cardinality_unique).to_dict()
    distributions["bases"]["categorical"] = "exact (distinct cap noted)"
    if cfg.group_by:
        distributions["grouped"] = {
            "notes": ["grouped statistics require in-memory data - not "
                      "computed in streaming mode; re-run with a smaller "
                      "sample_rows or full_load_max_rows setting"]}
    else:
        distributions["grouped"] = {"notes": ["group_by not configured"]}

    stage("correlation", message="matrix")
    if cfg.correlation_enabled and corr_reservoir is not None:
        frame = corr_reservoir.to_frame()
        if len(frame) < 2 or frame.shape[1] < 2:
            correlation = {"basis": "reservoir_sample", "methods": {},
                           "notes": ["not enough sampled rows/columns"],
                           "excluded_columns": {}}
        else:
            correlation = corr_mod.correlation_matrix(
                frame, methods=cfg.correlation_methods,
                max_columns=cfg.max_correlation_columns,
                basis="reservoir_sample")
            correlation["sample_rows"] = int(len(frame))
            correlation["notes"].append(
                "correlation computed from a reservoir sample of rows "
                f"(up to {cfg.correlation_sample_rows:,}); "
                "statistics/counts remain exact")
    elif not cfg.correlation_enabled:
        correlation = {"basis": "disabled", "methods": {}, "notes":
                       ["correlation disabled in configuration"],
                       "excluded_columns": {}}
    else:
        correlation = {"basis": "skipped", "methods": {}, "notes":
                       ["fewer than 2 numeric columns"],
                       "excluded_columns": {}}

    return {"profile": profile, "statistics": statistics,
            "quality": quality, "distributions": distributions,
            "correlation": correlation}


def _write_correlation_csv(run_dir: Path, correlation: dict) -> None:
    target = run_dir / FILE_CORRELATION
    assert_writable_target(target)
    rows = []
    for method, payload in (correlation.get("methods") or {}).items():
        cols = payload.get("columns") or []
        matrix = payload.get("matrix") or []
        for i, a in enumerate(cols):
            for j, b in enumerate(cols):
                rows.append({"method": method, "column_a": a, "column_b": b,
                             "value": matrix[i][j]})
    pd.DataFrame(rows, columns=["method", "column_a", "column_b",
                                "value"]).to_csv(target, index=False)


# ---------------------------------------------------------------------- #
# Public API
# ---------------------------------------------------------------------- #
def run_analysis(dataset_id: str, config_obj: AnalysisConfig | None = None,
                 context: ProcessingContext | None = None) -> RunResult:
    """Run one complete analysis job for a registered dataset."""
    cfg = config_obj or AnalysisConfig()
    dataset_id = str(dataset_id)

    reg = _registry_row(dataset_id)
    if reg is None:
        return RunResult(ok=False, dataset_id=dataset_id,
                         error="This dataset is not registered - register "
                               "it on the Data Sources page first.")

    run_id = (f"run_{datetime.now():%Y%m%d_%H%M%S}_"
              f"{uuid4().hex[:6]}")
    ctx = context if context is not None else ProcessingContext(job_id=run_id)
    if ctx.cancelled:
        return RunResult(ok=False, run_id=run_id, dataset_id=dataset_id,
                         error="The job was cancelled before it started.")

    started = datetime.now()
    errors: list[dict] = []
    stage_notes: list[str] = []

    def stage(name: str, current: int = 0, total: int | None = None,
              message: str = "") -> None:
        if ctx.cancelled:
            raise _Cancelled()
        ctx.report_progress(name, current, total, message=message)

    try:
        run_dir = ctx.dataset_dir(dataset_id) / "runs" / run_id
        assert_writable_target(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
    except (PlatformError, OSError) as exc:
        msg = (exc.user_message if isinstance(exc, PlatformError)
               else "Run directory could not be created.")
        return RunResult(ok=False, run_id=run_id, dataset_id=dataset_id,
                         error=msg)

    files: dict[str, str] = {FILE_CONFIG: str(run_dir / FILE_CONFIG)}
    _write_json(run_dir, FILE_CONFIG, {
        "run_id": run_id, "dataset_id": dataset_id,
        "config": cfg.to_dict(), "engine": "analysis_runner"})

    def finish(status: str, error: str = "") -> RunResult:
        meta = {
            "run_id": run_id, "dataset_id": dataset_id, "status": status,
            "error": error,
            "started_at": started.isoformat(timespec="seconds"),
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "duration_seconds": round(
                (datetime.now() - started).total_seconds(), 3),
            "progress": ctx.progress,
            "python": sys.version.split()[0],
            "pandas": pd.__version__, "numpy": np.__version__,
        }
        _write_json(run_dir, FILE_RUN_META, meta)
        if status == "completed":
            ctx.set_status("completed")
        elif status == "cancelled":
            ctx.set_status("cancelled")
        else:
            ctx.set_status("failed")
        files[FILE_RUN_META] = str(run_dir / FILE_RUN_META)
        return RunResult(
            ok=status == "completed", run_id=run_id,
            dataset_id=dataset_id, run_dir=str(run_dir), error=error,
            files=files, summary=summary_box["summary"],
            verification=ver_box["ver"], progress=ctx.progress)

    summary_box: dict = {"summary": {}}
    ver_box: dict = {"ver": None}
    try:
        stage("verify", message="checking the registered source")
        artifact: Path | None = None
        if str(reg.get("source_type", "")) == "filesystem":
            artifact = ctx.datasets_dir / dataset_id / "files_metadata.csv"
        ver = verify_source(reg, cfg, artifact=artifact)
        ver_box["ver"] = ver
        _write_json(run_dir, FILE_FINGERPRINT, ver.to_dict())
        files[FILE_FINGERPRINT] = str(run_dir / FILE_FINGERPRINT)
        if not ver.ok_to_run:
            _write_json(run_dir, FILE_ERRORS, [{
                "stage": "verify", "type": ver.status,
                "message": ver.guidance()}])
            files[FILE_ERRORS] = str(run_dir / FILE_ERRORS)
            refusal_summary = {"refused": True, "reason": ver.status,
                               "guidance": ver.guidance(),
                               "verification": ver.to_dict()}
            _write_json(run_dir, FILE_SUMMARY, refusal_summary)
            files[FILE_SUMMARY] = str(run_dir / FILE_SUMMARY)
            summary_box["summary"] = refusal_summary
            return finish("refused", ver.guidance())
        if ver.status == "unverifiable":
            stage_notes.extend(ver.notes)

        loader, artifact_path, loader_notes = _build_loader(
            reg, cfg, dataset_id, ctx, ver)
        stage_notes.extend(loader_notes)
        mode = _choose_mode(reg, cfg)
        stage("load", message=f"mode={mode}")

        load_total = reg.get("row_count")

        def progress_load(processed: int) -> None:
            stage("load", current=processed,
                  total=int(load_total) if load_total else None,
                  message=f"{processed:,} rows")

        if mode in ("full", "sample"):
            df = _load_frame(loader, cfg, mode)
            if mode == "full":
                stage_notes.append(
                    f"full frame in memory ({len(df):,} rows <= "
                    f"full_load_max_rows={cfg.full_load_max_rows:,})")
            basis = "exact" if mode == "full" else "sample"
            parts = _analyze_in_memory(df, cfg, basis, stage)
            del df
        else:
            stage_notes.append(
                "streaming mode: chunked processing, no full copy")
            parts = _analyze_streaming(loader, cfg, stage, progress_load)

        stage("outputs", message="writing run files")
        _write_json(run_dir, FILE_STATISTICS, {
            "basis": parts["profile"].get("basis"),
            "columns": {k: v for k, v in parts["statistics"].items()},
            "excluded_records": _excluded_records(parts["statistics"])})
        _write_json(run_dir, FILE_QUALITY, parts["quality"])
        _write_json(run_dir, FILE_DISTRIBUTIONS, parts["distributions"])
        _write_correlation_csv(run_dir, parts["correlation"])
        for name in (FILE_STATISTICS, FILE_QUALITY, FILE_DISTRIBUTIONS,
                     FILE_CORRELATION):
            files[name] = str(run_dir / name)

        summary = {
            "dataset_id": dataset_id,
            "source_type": reg.get("source_type"),
            "mode": mode,
            "rows_analyzed": parts["profile"].get("rows"),
            "rows_registered": reg.get("row_count"),
            "columns": parts["profile"].get("columns"),
            "profile": parts["profile"],
            "source_status": ver.status,
            "source_content_hash": ver.content_hash,
            "correlation_basis": parts["correlation"].get("basis"),
            "outliers_flagged": list(
                (parts["quality"].get("potential_outliers") or {})
                .get("columns", {}).keys()),
            "quality_score": parts["quality"].get("score"),
            "quality_score_formula": parts["quality"].get("score_formula"),
            "warnings": (parts["quality"].get("warnings") or []),
            "notes": stage_notes,
        }
        summary_box["summary"] = summary
        _write_json(run_dir, FILE_SUMMARY, summary)
        files[FILE_SUMMARY] = str(run_dir / FILE_SUMMARY)
        _write_json(run_dir, FILE_ERRORS, errors)
        files[FILE_ERRORS] = str(run_dir / FILE_ERRORS)
        log.info("Analysis run %s completed for %s", run_id, dataset_id)
        return finish("completed")

    except _Cancelled:
        errors.append({"stage": "stage_boundary", "type": "cancelled",
                       "message": "job cancelled between stages"})
        _write_json(run_dir, FILE_ERRORS, errors)
        files[FILE_ERRORS] = str(run_dir / FILE_ERRORS)
        return finish("cancelled",
                      "The job was cancelled between stages.")
    except PlatformError as exc:
        errors.append({"stage": "run", "type": type(exc).__name__,
                       "message": exc.user_message})
        _safe_write_errors(run_dir, errors, files)
        summary_box.setdefault("summary", {})
        return finish("failed", exc.user_message)
    except Exception as exc:  # noqa: BLE001 - contained, reported
        errors.append({"stage": "run", "type": type(exc).__name__,
                       "message": str(exc)})
        _safe_write_errors(run_dir, errors, files)
        return finish(
            "failed",
            f"Analysis failed ({type(exc).__name__}). Details are in "
            "errors.json in the run folder.")


class _Cancelled(Exception):
    pass


def _safe_write_errors(run_dir: Path, errors: list, files: dict) -> None:
    try:
        _write_json(run_dir, FILE_ERRORS, errors)
        files[FILE_ERRORS] = str(run_dir / FILE_ERRORS)
    except Exception:  # noqa: BLE001
        pass


def _excluded_records(statistics: dict) -> dict:
    total_missing = 0
    total_nonfinite = 0
    for key, st in statistics.items():
        if key.startswith("_") or not isinstance(st, dict):
            continue
        total_missing += int(st.get("missing") or 0)
        total_nonfinite += int(st.get("excluded_nonfinite") or 0)
    return {
        "missing_values_excluded": total_missing,
        "nonfinite_values_excluded": total_nonfinite,
        "rule": ("NaN and +/-inf are excluded from every numerical "
                 "calculation; counts are summed above (per-column detail "
                 "in each column entry)")}


def _load_frame(loader, cfg: AnalysisConfig, mode: str) -> pd.DataFrame:
    """Full load (mode='full') or bounded sample (mode='sample')."""
    if mode == "full":
        return loader.read_all(cfg.chunk_size)
    limit = cfg.sample_rows or 0
    parts: list[pd.DataFrame] = []
    taken = 0
    for chunk in loader.iter_chunks(cfg.chunk_size):
        need = limit - taken
        if need <= 0:
            break
        if len(chunk) >= need:
            parts.append(chunk.head(need))
            taken += need
            break
        parts.append(chunk)
        taken += len(chunk)
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def _registry_row(dataset_id: str) -> dict | None:
    try:
        if not Path(config.REGISTRY_DB).exists():
            return None
        return DatasetRegistry().get(dataset_id)
    except PlatformError:
        return None


def list_runs(dataset_id: str, limit: int = 50) -> list[dict]:
    """Previous analysis runs (newest first) for one dataset."""
    root = ProcessingContext().datasets_dir / str(dataset_id) / "runs"
    if not root.exists():
        return []
    runs: list[dict] = []
    try:
        entries = sorted(root.iterdir(),
                         key=lambda p: (p.stat().st_mtime, p.name),
                         reverse=True)
    except OSError:
        return []
    for d in entries:
        meta_path = d / FILE_RUN_META
        if not d.is_dir() or not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        runs.append(meta)
        if len(runs) >= limit:
            break
    return runs


def load_run(dataset_id: str, run_id: str) -> dict:
    """Read one run's outputs back (JSON parsed, CSV paths provided)."""
    dataset_id = str(Path(str(dataset_id)).name)
    run_id = str(Path(str(run_id)).name)
    if run_id in ("", ".", "..") or "/" in run_id or "\\" in run_id:
        raise PlatformError("Invalid run identifier.")
    run_dir = ProcessingContext().datasets_dir / dataset_id / "runs" / run_id
    if not run_dir.is_dir():
        raise PlatformError("That analysis run does not exist.")
    out: dict = {"run_id": run_id, "dataset_id": dataset_id,
                 "run_dir": str(run_dir), "files": {}}
    for name in (FILE_CONFIG, FILE_FINGERPRINT, FILE_SUMMARY,
                 FILE_STATISTICS, FILE_QUALITY, FILE_DISTRIBUTIONS,
                 FILE_ERRORS, FILE_RUN_META):
        p = run_dir / name
        out["files"][name] = str(p) if p.exists() else None
        if p.exists() and name.endswith(".json"):
            try:
                out[name.replace(".json", "")] = json.loads(
                    p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                out.setdefault("read_errors", []).append(
                    f"{name}: {exc.__class__.__name__}")
    corr = run_dir / FILE_CORRELATION
    if corr.exists():
        out["files"][FILE_CORRELATION] = str(corr)
        try:
            out["correlation_df"] = pd.read_csv(corr)
        except (OSError, pd.errors.ParserError) as exc:
            out.setdefault("read_errors", []).append(
                f"correlation.csv: {exc.__class__.__name__}")
    return out
