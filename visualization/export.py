"""Visualization exports (Phase 7).

Each *Generate* + export writes a self-contained bundle to
``output/visualizations/<visualization_id>/``:

* ``chart.html`` — standalone HTML with Plotly.js inlined (opens in any
  browser, no server, no personal data),
* ``chart_data.csv`` — the plotted data as a tidy table (bounded by
  ``cfg.export_max_rows``, truncation recorded in metadata),
* ``coordinates.csv`` / other extras — PCA/t-SNE coordinates when
  applicable,
* ``config.json`` — chart id, roles, options and all safety limits,
* ``metadata.json`` — title, notes, exact display/sampling disclosures
  and export accounting.

Nothing outside the visualization directory is ever written; exports
contain labels and ids only — never absolute source paths.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pandas as pd

import config
from core.exceptions import PlatformError, redact_path
from core.processing_context import assert_writable_target
from visualization.chart_factory import ChartArtifact
from visualization.visualization_config import VisualizationConfig

FILE_HTML = "chart.html"
FILE_DATA = "chart_data.csv"
FILE_CONFIG = "config.json"
FILE_METADATA = "metadata.json"

_ABS_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/)")


def _safe_str(value: str) -> str:
    """Labels stay intact; anything that looks like an absolute path is
    reduced to its file name so exports never carry locations."""
    if _ABS_PATH_RE.match(value):
        return redact_path(value)
    return value


def new_visualization_id(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return f"viz_{now:%Y%m%d_%H%M%S}_{uuid4().hex[:6]}"


def _write_json(target: Path, payload: dict) -> None:
    assert_writable_target(target)
    text = json.dumps(payload, indent=2, sort_keys=True,
                      ensure_ascii=False, default=str)
    if "C:" in text and "\\Users\\" in text:
        # defence in depth — exports must never leak personal paths
        raise PlatformError(
            "Export blocked: the generated metadata contained an "
            "absolute user path.",
            detail="path leaked into export",
        )
    target.write_text(text, encoding="utf-8")


def export_visualization(
    artifact: ChartArtifact,
    *,
    cfg: VisualizationConfig,
    source: dict,
    section: str,
    out_root: Path | None = None,
    now: datetime | None = None,
) -> dict:
    """Write one visualization bundle; returns its export manifest.

    ``source`` must contain only safe fields (ids/kinds/labels), never
    absolute paths.
    """
    if not isinstance(artifact, ChartArtifact):
        raise PlatformError(
            "Nothing to export — generate a visualization first.",
            detail="export without artifact",
        )
    viz_id = new_visualization_id(now)
    root = Path(out_root) if out_root is not None else Path(
        config.OUTPUT_VISUALIZATIONS_DIR)
    target = root / viz_id
    assert_writable_target(target / FILE_HTML)
    target.mkdir(parents=True, exist_ok=True)

    # 1) standalone HTML
    from visualization.plotly_helpers import figure_to_html
    html = figure_to_html(artifact.figure)
    (target / FILE_HTML).write_text(html, encoding="utf-8")

    # 2) chart data CSV (bounded, truncation recorded)
    data = artifact.data if isinstance(artifact.data, pd.DataFrame) \
        else pd.DataFrame()
    exported_rows = int(len(data))
    data_truncated = False
    if exported_rows > cfg.export_max_rows:
        data = data.head(cfg.export_max_rows)
        data_truncated = True
    data_path = target / FILE_DATA
    assert_writable_target(data_path)
    data.to_csv(data_path, index=False, encoding="utf-8")

    # 3) extra exports (coordinates etc.)
    extra_files: dict[str, dict] = {}
    for name, frame in (artifact.extra_exports or {}).items():
        safe_name = Path(str(name)).name
        if not safe_name.lower().endswith(".csv"):
            safe_name = safe_name + ".csv"
        frame = frame if isinstance(frame, pd.DataFrame) \
            else pd.DataFrame()
        extra_rows = int(len(frame))
        if extra_rows > cfg.export_max_rows:
            frame = frame.head(cfg.export_max_rows)
        path = target / safe_name
        assert_writable_target(path)
        frame.to_csv(path, index=False, encoding="utf-8")
        extra_files[safe_name] = {
            "rows": int(len(frame)),
            "rows_total": extra_rows,
            "truncated": extra_rows > int(len(frame)),
        }

    # 4) config
    payload_cfg = {
        "visualization_id": viz_id,
        "section": section,
        "chart_id": artifact.chart_id or "",
        "title": artifact.title,
        "roles": _json_safe(artifact.roles),
        "options": _json_safe(artifact.options),
        "source": _json_safe(source),
        "limits": cfg.to_dict(),
        "created_at": (now or datetime.now()).isoformat(
            timespec="seconds"),
        "seed": cfg.seed,
    }
    _write_json(target / FILE_CONFIG, payload_cfg)

    # 5) metadata (disclosures + accounting)
    display = dict(artifact.display or {})
    sampling = dict(display.get("sampling") or {})
    metadata = {
        "visualization_id": viz_id,
        "chart_id": artifact.chart_id or "",
        "title": artifact.title,
        "notes": [str(n) for n in (artifact.notes or [])],
        "display": {
            "total_rows": display.get("total_rows"),
            "displayed_rows": display.get("displayed_rows"),
            "basis": display.get("basis", ""),
            "approximate": bool(display.get("approximate")),
        },
        "sampling": {
            "applied": bool(sampling.get("applied")),
            "method": str(sampling.get("method", "none")),
            "params": _json_safe(sampling.get("params") or {}),
            "approximate": bool(sampling.get("approximate")),
        },
        "export": {
            "chart_data_rows": int(len(data)),
            "chart_data_rows_total": exported_rows,
            "chart_data_truncated": data_truncated,
            "extras": extra_files,
            "html_bytes": (target / FILE_HTML).stat().st_size,
            "contains_absolute_paths": False,
        },
        "approximate": bool(display.get("approximate")),
    }
    _write_json(target / FILE_METADATA, metadata)

    files = {}
    for path in sorted(target.iterdir()):
        if path.is_file():
            files[path.name] = {"bytes": path.stat().st_size}
    return {
        "visualization_id": viz_id,
        "directory": target,
        "relative_dir": f"output/visualizations/{viz_id}",
        "files": files,
        "chart_data_rows": int(len(data)),
        "chart_data_rows_total": exported_rows,
        "chart_data_truncated": data_truncated,
        "approximate": metadata["approximate"],
        "notes": metadata["notes"],
    }


def _json_safe(value):
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, Path):
        return value.name
    if isinstance(value, str):
        return _safe_str(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)
