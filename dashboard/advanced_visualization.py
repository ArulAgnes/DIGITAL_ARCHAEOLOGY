"""Page 12 - Advanced Visualization (Phase 7): three-section chart workbench.

Connects the Phase 7 ``visualization/`` package to the Streamlit app:

* **Archaeology** — charts over the research scan frames (already limited
  by the research sidebar filters) and the stored snapshots,
* **Dataset** — row-level charts from a bounded read-only preview of a
  registered CSV/SQLite dataset, or charts of an existing analysis run,
* **Comparison** — charts of a completed A-vs-B comparison run.

Safety model: nothing is computed until **Generate visualization** is
pressed; previews are bounded and read-only; sampling, truncation,
filters and comparison exactness are always disclosed next to the chart
and recorded in the export bundle. The eight research pages and pages
9-11 are untouched; the common page signature accepts the research
arguments (``files``, ``folders``, ``findings``, ``filters``) and uses
the first two for the Archaeology section only.
"""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from analysis.analysis_runner import list_runs, load_run
from analysis.comparison import list_comparisons, load_comparison
from core.exceptions import PlatformError
from scanner import snapshot_manager
from visualization import data_adapter as adapter
from visualization import filters as flt
from visualization.chart_factory import human_count
from visualization.chart_registry import (MODE_ARCHAEOLOGY, MODE_COMPARISON,
                                          MODE_DATASET, NEEDS_COMPARISON,
                                          NEEDS_FILES, NEEDS_FOLDERS,
                                          NEEDS_PREVIEW, NEEDS_RUN,
                                          NEEDS_SNAPSHOT_DIFF,
                                          NEEDS_SNAPSHOTS, build_chart,
                                          charts_for, get_chart)
from visualization.dataset_visualizations import _profile
from visualization.export import export_visualization
from visualization.validation import (column_kind, kind_from_dtype_text,
                                      kind_matches, validate_chart_request)
from visualization.visualization_config import VisualizationConfig

_SECTION_KEY = "ds7_section"
_CHART_KEY = "ds7_chart"
_DS_KEY = "ds7_ds"
_RUN_KEY = "ds7_run"
_CMP_KEY = "ds7_cmp"
_SNAP_A_KEY = "ds7_snap_a"
_SNAP_B_KEY = "ds7_snap_b"
_ART_KEY = "ds7_artifact"
_SOURCE_KEY = "ds7_source"
_CFG_KEY = "ds7_cfg"
_BUILT_KEY = "ds7_built"
_META_KEY = "ds7_meta_notes"
_ERR_KEY = "ds7_err"
_EXPORT_KEY = "ds7_export"
_CATVAL_KEY = "ds7_cat_values"
_CMP_CACHE_KEY = "ds7_cmp_cache"

_SECTIONS = {
    "Archaeology (scan & snapshots)": MODE_ARCHAEOLOGY,
    "Dataset (rows & analysis runs)": MODE_DATASET,
    "Comparison (A vs B)": MODE_COMPARISON,
}
_SECTION_OF = {v: k for k, v in _SECTIONS.items()}

_NONE = "(none)"

#: profile kind (analysis run) -> registry/validation kind
_PROFILE_KIND = {"numeric": "int", "boolean": "bool",
                 "datetime": "datetime", "categorical": "object",
                 "other": "object"}


def _reset_session() -> None:
    for key in list(st.session_state.keys()):
        if str(key).startswith("ds7_"):
            del st.session_state[key]


def _pop_if_invalid(key: str, options) -> None:
    """Drop a stored widget value that is no longer a valid option."""
    if key in st.session_state and st.session_state[key] not in options:
        del st.session_state[key]


def _date_bound(result, side: str):
    """Normalize st.date_input output (date | None | tuple) to one date."""
    if isinstance(result, (tuple, list)):
        if not result:
            return None
        return result[0] if side == "start" else result[-1]
    if result is None or result == "":
        return None
    return result


def _run_column_kinds(run: dict | None) -> dict[str, str]:
    """Registry kinds for a run's columns (profile detail, mapped)."""
    if not run:
        return {}
    detail = (_profile(run).get("column_detail") or {})
    kinds: dict[str, str] = {}
    for col, info in detail.items():
        if isinstance(info, dict):
            kinds[str(col)] = _PROFILE_KIND.get(
                str(info.get("kind") or ""), "object")
        else:
            kinds[str(col)] = "object"
    return kinds


def _run_methods(run: dict | None) -> list[str]:
    if not run:
        return []
    df = run.get("correlation_df")
    if isinstance(df, pd.DataFrame) and len(df) and "method" in df.columns:
        return [str(m) for m in pd.unique(df["method"])]
    methods = ((run.get("correlation") or {}).get("methods") or {})
    return [str(m) for m in methods]


def _grouped_columns(run: dict | None) -> list[str]:
    groups = (((run or {}).get("distributions") or {})
              .get("grouped") or {}).get("groups") or {}
    cols: list[str] = []
    for payload in groups.values():
        if isinstance(payload, dict):
            for c in payload:
                if c not in cols:
                    cols.append(str(c))
    return cols


def _comparison_kinds(comparison: dict | None) -> dict[str, str]:
    stats = (comparison or {}).get("statistics_differences") or {}
    cols = stats.get("columns") or {}
    kinds: dict[str, str] = {}
    for col, info in cols.items():
        if not isinstance(info, dict):
            continue
        kinds[str(col)] = ("int" if info.get("basis") == "numeric"
                           else "object")
    return kinds


def _role_options(
    name: str, role, spec, *, column_kinds: dict, run, comparison,
) -> list[str] | None:
    """Option list for one role. ``None`` means free-text entry (no
    metadata available)."""
    if spec.needs == NEEDS_RUN and run is not None:
        dists = run.get("distributions") or {}
        if spec.chart_id == "run_histogram":
            return sorted((dists.get("histograms") or {}).keys())
        if spec.chart_id == "run_categorical":
            return sorted((dists.get("categorical") or {}).keys())
        if spec.chart_id == "run_grouped_stats":
            return _grouped_columns(run)
        if name == "method":
            return _run_methods(run)
    if spec.needs == NEEDS_COMPARISON and comparison is not None:
        if name == "column":
            stats = (comparison.get("statistics_differences") or {})
            cols = stats.get("columns") or {}
            if spec.chart_id == "comparison_numeric_delta":
                return sorted(
                    c for c, info in cols.items()
                    if isinstance(info, dict)
                    and info.get("basis") == "numeric"
                    and isinstance(info.get("delta"), dict))
            if spec.chart_id == "comparison_category_change":
                return sorted(
                    c for c, info in cols.items()
                    if isinstance(info, dict)
                    and info.get("basis") == "categorical")
            return sorted(cols)
    if not column_kinds:
        return None                      # free-text fallback
    return sorted(c for c, kind in column_kinds.items()
                  if kind_matches(kind, role.kinds))


def _role_pickers(spec, column_kinds: dict, run, comparison) -> dict:
    """Build the column-role widgets; returns the roles dict."""
    roles: dict = {}
    if not spec.roles:
        return roles
    st.markdown("**Column roles**")
    for name, role in spec.roles.items():
        label = str(role.label or name)
        if not role.required:
            label = f"{label} (optional)"
        key = f"ds7_role_{name}"
        options = _role_options(name, role, spec, column_kinds=column_kinds,
                                run=run, comparison=comparison)
        if options is None:
            # No column metadata: honest free-text entry, strictly
            # validated against the real frame at Generate time.
            if key in st.session_state and not isinstance(
                    st.session_state.get(key), str):
                del st.session_state[key]
            typed = st.text_input(
                label, key=key,
                help="Column metadata is unavailable for this dataset - "
                     "type the exact column name; it is verified when "
                     "the chart is generated.")
            value = (typed or "").strip()
            roles[name] = value or None
            continue
        if not options:
            st.caption(f"No columns in this data match '{label}'.")
            roles[name] = None if not role.multiple else []
            continue
        if role.multiple:
            if key in st.session_state:
                st.session_state[key] = [
                    c for c in (st.session_state.get(key) or [])
                    if c in options]
            chosen = st.multiselect(label, options, key=key)
            roles[name] = list(chosen)
        elif role.required:
            _pop_if_invalid(key, options)
            roles[name] = st.selectbox(label, options, key=key)
        else:
            options = [_NONE] + list(options)
            _pop_if_invalid(key, options)
            picked = st.selectbox(label, options, key=key)
            roles[name] = None if picked == _NONE else picked
    return roles


def _options_widgets(spec) -> dict:
    """Chart-specific option widgets (aggregation, bins, ...)."""
    options: dict = {}
    if spec.chart_id in ("bar", "hbar", "grouped_bar", "stacked_bar"):
        choices = ["count", "sum", "mean", "median"]
        default = "mean" if spec.chart_id == "grouped_bar" else "count"
        _pop_if_invalid("ds7_opt_agg", choices)
        options["agg"] = st.selectbox(
            "Aggregation", choices,
            index=choices.index(default), key="ds7_opt_agg",
            help="Applies when a numeric value column is chosen; "
                 "'count' is used when no value column is selected.")
    if spec.chart_id == "histogram":
        options["bins"] = int(st.number_input(
            "Bins", 5, 200, value=30, key="ds7_opt_bins"))
    if spec.chart_id == "scatter":
        options["trendline"] = bool(st.checkbox(
            "Linear fit (trendline)", value=False, key="ds7_opt_trendline"))
    if spec.chart_id == "size_distribution":
        options["log_x"] = bool(st.checkbox(
            "Logarithmic size axis", value=True, key="ds7_opt_log_x",
            help="Recommended for long-tailed file sizes (zeros are "
                 "excluded from a log axis and reported on the chart)."))
    return options


def _limits_widgets() -> VisualizationConfig:
    with st.expander("Chart limits & sampling", expanded=False):
        st.caption(
            "Every limit is enforced with a disclosed, seeded random "
            "sample - never a silent truncation. Seed is fixed (42) so "
            "charts are reproducible.")
        c1, c2 = st.columns(2)
        max_points = int(c1.number_input(
            "Max points (scatter-like charts)", 100, 200_000, 5_000,
            key="ds7_max_points"))
        top_n = int(c2.number_input(
            "Top-N categories", 2, 500, 20, key="ds7_top_n"))
        c3, c4 = st.columns(2)
        preview_rows = int(c3.number_input(
            "Preview row cap", 1_000, 5_000_000, 100_000,
            key="ds7_preview_rows"))
        hist_bins = int(c4.number_input(
            "Default histogram bins", 5, 200, 30, key="ds7_hist_bins"))
    return VisualizationConfig(
        max_points=max_points, top_n=top_n, preview_rows=preview_rows,
        histogram_bins=hist_bins, seed=42)


def _category_values(ds_id: str, column: str, cfg: VisualizationConfig) -> dict:
    """Cached bounded value list for the category filter."""
    cache = st.session_state.setdefault(_CATVAL_KEY, {})
    sig = f"{ds_id}|{column}|{cfg.preview_rows}"
    if sig not in cache:
        try:
            cache[sig] = adapter.load_column_values(
                ds_id, column, limit=100, max_rows=cfg.preview_rows)
        except PlatformError as exc:
            cache[sig] = {"error": exc.user_message, "values": []}
    return cache[sig]


def _filters_ui(spec, column_kinds: dict, ds_id: str | None,
                cfg: VisualizationConfig) -> dict:
    """Row-filter widgets (Dataset preview charts only)."""
    values = {"date_col": None, "date_start": None, "date_end": None,
              "num_col": None, "num_low": None, "num_high": None,
              "cat_col": None, "cat_sel": [], "missing_col": None,
              "drop_missing": False}
    if spec.needs != NEEDS_PREVIEW or not ds_id or not column_kinds:
        return values
    with st.expander("Row filters (applied at Generate time)", expanded=False):
        st.caption(
            "Filters run on the bounded preview after it is read; every "
            "applied filter and its removed-row count is listed with the "
            "result. Category values are read from the first preview "
            "rows only.")
        temporal = sorted(c for c, k in column_kinds.items()
                          if k in ("datetime", "date"))
        numeric = sorted(c for c, k in column_kinds.items()
                         if k in ("int", "float"))
        categorical = sorted(c for c, k in column_kinds.items()
                             if k in ("object", "category", "bool"))
        if temporal:
            _pop_if_invalid("ds7_f_date_col", [_NONE] + temporal)
            values["date_col"] = st.selectbox(
                "Date column", [_NONE] + temporal, key="ds7_f_date_col")
            if values["date_col"] != _NONE:
                d1, d2 = st.columns(2)
                start = d1.date_input("From", value=None, key="ds7_f_d1")
                end = d2.date_input("To", value=None, key="ds7_f_d2")
                values["date_start"] = _date_bound(start, "start")
                values["date_end"] = _date_bound(end, "end")
            else:
                values["date_col"] = None
        if numeric:
            _pop_if_invalid("ds7_f_num_col", [_NONE] + numeric)
            values["num_col"] = st.selectbox(
                "Numeric range column", [_NONE] + numeric,
                key="ds7_f_num_col")
            if values["num_col"] != _NONE:
                n1, n2 = st.columns(2)
                low = n1.number_input("Minimum", value=None,
                                      key="ds7_f_num_low")
                high = n2.number_input("Maximum", value=None,
                                       key="ds7_f_num_high")
                values["num_low"] = None if low is None else float(low)
                values["num_high"] = None if high is None else float(high)
            else:
                values["num_col"] = None
        if categorical:
            _pop_if_invalid("ds7_f_cat_col", [_NONE] + categorical)
            values["cat_col"] = st.selectbox(
                "Category column", [_NONE] + categorical,
                key="ds7_f_cat_col")
            if values["cat_col"] != _NONE:
                listing = _category_values(ds_id, values["cat_col"], cfg)
                if listing.get("error"):
                    st.error(listing["error"])
                    values["cat_col"] = None
                else:
                    opts = listing["values"]
                    if st.session_state.get("ds7_f_cat_sel") is None:
                        st.session_state["ds7_f_cat_sel"] = []
                    st.session_state["ds7_f_cat_sel"] = [
                        v for v in (st.session_state.get("ds7_f_cat_sel")
                                    or []) if v in opts]
                    picked = st.multiselect(
                        "Keep only these values (empty = keep all)",
                        opts, key="ds7_f_cat_sel")
                    values["cat_sel"] = list(picked)
                    if listing.get("truncated"):
                        st.caption(
                            f"showing the {len(opts)} most frequent of "
                            f"{listing['distinct_in_window']:,} distinct "
                            f"values in the first "
                            f"{listing['rows_read']:,} preview rows.")
                    if not picked:
                        values["cat_col"] = None
            else:
                values["cat_col"] = None
        if column_kinds:
            _pop_if_invalid("ds7_f_missing_col",
                            [_NONE] + sorted(column_kinds))
            values["missing_col"] = st.selectbox(
                "Missing-value policy column", [_NONE] + sorted(column_kinds),
                key="ds7_f_missing_col")
            if values["missing_col"] != _NONE:
                values["drop_missing"] = bool(st.checkbox(
                    "Drop rows where this column is missing",
                    value=False, key="ds7_f_drop"))
            else:
                values["missing_col"] = None
                values["drop_missing"] = False
    return values


def _apply_filters(frame: pd.DataFrame, values: dict
                   ) -> tuple[pd.DataFrame, list[str], int]:
    """Apply the section filters; returns (frame, notes, dropped_rows)."""
    notes: list[str] = []
    dropped = 0

    def _add(res) -> None:
        nonlocal dropped, frame
        frame = res.frame
        dropped += int(res.dropped_rows or 0)
        notes.extend(res.applied or [])

    if values.get("date_col"):
        _add(flt.apply_date_range(frame, values["date_col"],
                                  values.get("date_start"),
                                  values.get("date_end")))
    if values.get("num_col"):
        _add(flt.apply_numeric_range(frame, values["num_col"],
                                     values.get("num_low"),
                                     values.get("num_high")))
    if values.get("cat_col") and values.get("cat_sel"):
        _add(flt.apply_categories(frame, values["cat_col"],
                                  values["cat_sel"]))
    if values.get("missing_col") and values.get("drop_missing"):
        _add(flt.apply_missing_policy(frame, values["missing_col"], True))
    return frame, notes, dropped


def _signature(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, default=str)


def _do_generate(*, spec, cfg: VisualizationConfig, roles: dict,
                 options: dict, filter_values: dict, column_kinds: dict,
                 ds_id: str | None, run: dict | None, comparison: dict | None,
                 files: pd.DataFrame, folders: pd.DataFrame,
                 sidebar_filters: dict, snap_a: str | None,
                 snap_b: str | None, signature: str) -> None:
    """Build one chart and store it (the only heavy path on the page)."""
    st.session_state.pop(_ERR_KEY, None)
    st.session_state.pop(_EXPORT_KEY, None)
    inputs: dict = {}
    source: dict = {"section": _spec_mode(spec), "chart_id": spec.chart_id}
    page_notes: list[str] = []
    prepared = None
    frame = None
    filter_notes: list[str] = []
    try:
        if spec.needs == NEEDS_PREVIEW:
            if not ds_id:
                raise PlatformError("Select a dataset first.",
                                    detail="preview without dataset")
            prepared = adapter.load_preview(ds_id, cfg)
            frame = prepared.frame
            frame, filter_notes, dropped = _apply_filters(frame,
                                                          filter_values)
            if dropped:
                filter_notes.append(
                    f"section filters removed {dropped:,} row(s) from the "
                    "preview before charting.")
            if not len(frame):
                raise PlatformError(
                    "No rows are available after filtering. Adjust or "
                    "reset the row filters and try again.",
                    detail="empty after filters")
            inputs["frame"] = frame
            inputs["source_label"] = prepared.source_label
            page_notes.extend(prepared.meta.get("notes") or [])
            if filter_notes:
                source["filters"] = list(filter_notes)
            source.update(
                dataset_id=ds_id, source_kind=prepared.source_kind,
                source_label=prepared.source_label,
                preview_sampling=prepared.sampling)
        elif spec.needs == NEEDS_RUN:
            if run is None:
                raise PlatformError(
                    "No analysis run is selected. Run the analysis on "
                    "the Dataset Analysis page first.",
                    detail="run chart without run")
            inputs["run"] = run
            inputs["source_label"] = "analysis run"
            source.update(dataset_id=ds_id, run_id=run.get("run_id"))
        elif spec.needs in (NEEDS_FILES, NEEDS_FOLDERS, NEEDS_SNAPSHOTS,
                            NEEDS_SNAPSHOT_DIFF):
            if spec.needs == NEEDS_FILES:
                inputs["files"] = files
                page_notes.extend(
                    flt.summarize_filters(sidebar_filters, []))
            if spec.needs == NEEDS_FOLDERS:
                inputs["folders"] = folders
            if spec.needs in (NEEDS_SNAPSHOTS, NEEDS_SNAPSHOT_DIFF):
                inputs["snapshots"] = snapshot_manager.list_snapshots()
            if spec.needs == NEEDS_SNAPSHOT_DIFF:
                if not snap_a or not snap_b:
                    raise PlatformError(
                        "Select two snapshots to compare.",
                        detail="snapshot diff without pair")
                diff = snapshot_manager.compare(snap_a, snap_b)
                if not diff:
                    raise PlatformError(
                        "That snapshot pair could not be compared - a "
                        "snapshot file is missing, unreadable or marked "
                        "invalid.",
                        detail="snapshot compare failed")
                inputs["snapshot_diff"] = diff
            inputs["source_label"] = "research scan"
            source["source_kind"] = "filesystem_scan"
        elif spec.needs == NEEDS_COMPARISON:
            if comparison is None:
                raise PlatformError(
                    "No comparison run is selected. Run a comparison on "
                    "the Dataset Comparison page first.",
                    detail="comparison chart without run")
            inputs["comparison"] = comparison
            inputs["source_label"] = "comparison run"
            source.update(
                comparison_id=comparison.get("comparison_id"),
                run_id=comparison.get("run_id"))
        if filter_notes:
            inputs.setdefault("meta", {})["filters"] = filter_notes

        if spec.needs == NEEDS_PREVIEW:
            real_kinds = {c: column_kind(frame[c].dtype)
                          for c in frame.columns}
            report = validate_chart_request(
                spec.chart_id, real_kinds, roles, needs="preview",
                frame_empty=False, role_kinds=spec.role_kinds,
                role_required=spec.role_required)
            if not report.ok:
                st.session_state[_ERR_KEY] = " ".join(report.errors)
                return

        artifact = build_chart(spec.chart_id, cfg, roles=roles,
                               options=options, **inputs)
        if prepared is not None and prepared.sampling.get("applied"):
            artifact.display["sampling"] = adapter.merge_sampling(
                artifact.display.get("sampling") or {}, prepared.sampling)
            artifact.display["approximate"] = bool(
                artifact.display.get("approximate")) or bool(
                    prepared.sampling.get("approximate"))
            total = prepared.meta.get("total_rows")
            if total:
                artifact.display["total_rows"] = int(total)
        st.session_state[_ART_KEY] = artifact
        st.session_state[_SOURCE_KEY] = source
        st.session_state[_CFG_KEY] = cfg.to_dict()
        st.session_state[_META_KEY] = page_notes + filter_notes
        st.session_state[_BUILT_KEY] = signature
    except PlatformError as exc:
        st.session_state[_ERR_KEY] = exc.user_message
    except Exception as exc:  # noqa: BLE001 - contained, path-free message
        st.session_state[_ERR_KEY] = (
            f"The chart could not be built ({type(exc).__name__}). "
            "Check the selected columns and limits, then try again.")


def _spec_mode(spec) -> str:
    """Single mode string of a chart spec (for export provenance)."""
    return spec.modes[0] if spec.modes else ""


def _snapshot_pair() -> tuple[str | None, str | None, str]:
    """Snapshot selects for snapshot-diff charts."""
    snaps = snapshot_manager.list_snapshots()
    ids = [str(s.get("snapshot_id")) for s in snaps if s.get("snapshot_id")]
    if len(ids) < 2:
        st.info("This chart needs two stored snapshots. Create snapshots "
                "from the sidebar of the app first.")
        return None, None, ""
    labels = {}
    for s in snaps:
        sid = str(s.get("snapshot_id"))
        labels[sid] = (f"{sid} · {s.get('created_at', '?')} · "
                       f"{int(s.get('total_files') or 0):,} files")
    a_opts = list(ids)
    b_opts = list(ids)
    _pop_if_invalid(_SNAP_A_KEY, a_opts)
    _pop_if_invalid(_SNAP_B_KEY, b_opts)
    c1, c2 = st.columns(2)
    with c1:
        snap_a = st.selectbox("Baseline snapshot", a_opts, index=0,
                              format_func=lambda i: labels.get(i, i),
                              key=_SNAP_A_KEY)
    with c2:
        snap_b = st.selectbox("Comparison snapshot", b_opts,
                              index=len(b_opts) - 1,
                              format_func=lambda i: labels.get(i, i),
                              key=_SNAP_B_KEY)
    return snap_a, snap_b, f"{snap_a}|{snap_b}"


def _render_result(signature: str) -> None:
    err = st.session_state.get(_ERR_KEY)
    if err:
        st.error(err)
    artifact = st.session_state.get(_ART_KEY)
    if artifact is None:
        st.caption("Nothing generated yet - choose a chart and press "
                   "**Generate visualization**.")
        return
    st.markdown("---")
    st.markdown("**Result**")
    if st.session_state.get(_BUILT_KEY) != signature:
        st.caption("Settings changed since this chart was generated - "
                   "press **Generate visualization** to refresh.")
    display = artifact.display or {}
    m1, m2, m3 = st.columns(3)
    m1.metric("Rows total", human_count(display.get("total_rows")))
    m2.metric("Rows displayed", human_count(display.get("displayed_rows")))
    m3.metric("Basis", str(display.get("basis") or "—"))
    sampling = display.get("sampling") or {}
    if sampling.get("applied"):
        params = sampling.get("params") or {}
        seed = params.get("seed")
        st.caption(
            f"Sampling: {sampling.get('method', 'sampled')}"
            + (f" · {params}" if params else "")
            + (f" · seed {seed}" if seed is not None else "")
            + " · figure badge marks the chart approximate."
            if display.get("approximate") else
            f"Row cap applied: {sampling.get('method', 'capped')}.")
    st.plotly_chart(artifact.figure, use_container_width=True,
                    key="ds7_fig",
                    config={"displaylogo": False,
                            "modeBarButtonsToRemove": ["lasso2d",
                                                       "select2d"]})
    for note in artifact.notes or []:
        st.caption(str(note))
    for note in st.session_state.get(_META_KEY) or []:
        st.caption(str(note))

    st.markdown("**Export**")
    e1, e2 = st.columns(2)
    cfg_dict = st.session_state.get(_CFG_KEY) or {}
    try:
        cfg = VisualizationConfig(**cfg_dict)
    except (TypeError, ValueError):
        cfg = VisualizationConfig()
    with e1:
        if st.button("Export bundle (HTML + CSV + metadata)",
                     key="ds7_export_btn"):
            try:
                manifest = export_visualization(
                    artifact, cfg=cfg,
                    source=st.session_state.get(_SOURCE_KEY) or {},
                    section=str((st.session_state.get(_SOURCE_KEY) or {})
                                .get("section") or "dataset"))
                st.session_state[_EXPORT_KEY] = manifest
            except PlatformError as exc:
                st.error(exc.user_message)
            except Exception as exc:  # noqa: BLE001
                st.error(f"Export failed ({type(exc).__name__}).")
    with e2:
        total_rows = int(len(artifact.data)) if isinstance(
            artifact.data, pd.DataFrame) else 0
        csv_df = (artifact.data.head(cfg.export_max_rows)
                  if isinstance(artifact.data, pd.DataFrame)
                  else pd.DataFrame())
        st.download_button(
            "Download chart data (CSV)",
            data=csv_df.to_csv(index=False).encode("utf-8"),
            file_name="chart_data.csv", mime="text/csv",
            key="ds7_dl_csv",
            help=f"{total_rows:,} row(s) in the chart data"
                 + (f"; export cap {cfg.export_max_rows:,}"
                    if total_rows > cfg.export_max_rows else ""))
    if total_rows:
        st.caption(
            f"chart data: {total_rows:,} row(s)"
            + (f" (CSV download capped at {cfg.export_max_rows:,})"
               if total_rows > cfg.export_max_rows else "") + ".")
    manifest = st.session_state.get(_EXPORT_KEY)
    if manifest:
        st.success(
            f"Exported `{manifest['visualization_id']}` to "
            f"`{manifest['relative_dir']}` — "
            f"{', '.join(sorted(manifest.get('files') or {}))} "
            f"({manifest.get('chart_data_rows', 0):,} data rows"
            + (", truncated" if manifest.get("chart_data_truncated")
               else "") + ").")


def render(files: pd.DataFrame, folders: pd.DataFrame, findings: dict,
           filters: dict) -> None:
    """Common page entry point (Phase 7 chart workbench)."""
    st.subheader("Advanced Visualization")
    st.caption(
        "Three-section chart workbench over the Phase 7 visualization "
        "engine. Nothing is computed until **Generate visualization** is "
        "pressed; previews are bounded and read-only; sampling, filters "
        "and comparison exactness are always disclosed. The research "
        "pages and pages 9-11 are unaffected.")
    del findings  # research findings are shown by the research pages

    section_label = st.radio("Section", list(_SECTIONS), index=0,
                             horizontal=True, key=_SECTION_KEY)
    mode = _SECTIONS[section_label]
    st.markdown("---")

    cfg = _limits_widgets()
    ds_id: str | None = None
    run: dict | None = None
    comparison: dict | None = None
    run_available = False
    column_kinds: dict[str, str] = {}
    selector_sig = ""
    cmp_kind = ""

    if mode == MODE_ARCHAEOLOGY:
        st.caption(
            f"Charts read the current research scan frames "
            f"({len(files):,} files, {len(folders):,} folders) as passed "
            "by the research sidebar filters above - file charts respect "
            "those filters; snapshot charts read the stored snapshot "
            "index only.")
        column_kinds = {c: column_kind(files[c].dtype)
                        for c in files.columns}
    elif mode == MODE_DATASET:
        options = adapter.dataset_options()
        if not options:
            st.info("No datasets registered yet - register one on the "
                    "Data Sources page first.")
            return
        labels = {label: ds for ds, label in options}
        _pop_if_invalid(_DS_KEY, list(labels))
        label_pick = st.selectbox("Dataset", list(labels), key=_DS_KEY)
        ds_id = labels[label_pick]
        selector_sig = ds_id
        specs = adapter.metadata_column_specs(ds_id)
        if specs:
            column_kinds = {s["name"]: kind_from_dtype_text(s["dtype"])
                            for s in specs}
        else:
            column_kinds = {}
    else:
        runs = list_comparisons()
        if not runs:
            st.info("No comparison runs yet - run a comparison on the "
                    "Dataset Comparison page first.")
            return
        run_keys = [f"{r.get('comparison_id')}|{r.get('run_id')}"
                    for r in runs]
        run_labels = {
            k: (f"{r.get('status', '?')} · {r.get('kind', '?')} · "
                f"{r.get('finished_at', '?')} · {r.get('run_id', '')}")
            for k, r in zip(run_keys, runs)}
        _pop_if_invalid(_CMP_KEY, run_keys)
        pick = st.selectbox(
            "Comparison run", run_keys,
            format_func=lambda k: run_labels.get(k, k), key=_CMP_KEY)
        selector_sig = pick
        cmp_kind = str(next((r.get("kind") or "" for r in runs
                             if f"{r.get('comparison_id')}|"
                             f"{r.get('run_id')}" == pick), "") or "")
        cache = st.session_state.setdefault(_CMP_CACHE_KEY, {})
        if pick not in cache:
            comparison_id, _, run_id = pick.partition("|")
            try:
                cache[pick] = load_comparison(comparison_id, run_id)
            except PlatformError as exc:
                st.error(exc.user_message)
                cache[pick] = None
        comparison = cache.get(pick)
        if comparison is None:
            return
        column_kinds = _comparison_kinds(comparison)

    # ---------------------------------------------------- chart picker
    chart_specs = charts_for(
        mode, kinds=cmp_kind if mode == MODE_COMPARISON and cmp_kind
        else None)
    chart_ids = [s.chart_id for s in chart_specs]
    if not chart_ids:
        st.info("No chart types are available for this selection.")
        return
    _pop_if_invalid(_CHART_KEY, chart_ids)
    chart_id = st.selectbox(
        "Chart type", chart_ids,
        format_func=lambda c: get_chart(c).label, key=_CHART_KEY)
    spec = get_chart(chart_id)
    if mode == MODE_COMPARISON and not cmp_kind:
        st.caption("The comparison kind is unknown - charts limited to "
                   "one kind may return a clear empty state.")
    st.caption(spec.description)

    # ------------------------------------------------- run selection
    if mode == MODE_DATASET and spec.needs == NEEDS_RUN:
        st.markdown("**Analysis run**")
        runs = list_runs(ds_id or "")
        if not runs:
            st.warning("No analysis runs for this dataset - run the "
                       "analysis on the Dataset Analysis page first.")
        else:
            ids = [str(r.get("run_id")) for r in runs]
            run_labels = {
                str(r.get("run_id")):
                    f"{r.get('status', '?')} · {r.get('finished_at', '?')} · "
                    f"{r.get('run_id', '')}" for r in runs}
            _pop_if_invalid(_RUN_KEY, ids)
            run_id = st.selectbox(
                "Run to visualize", ids,
                format_func=lambda i: run_labels.get(i, i), key=_RUN_KEY)
            selector_sig += f"|{run_id}"
            try:
                run = load_run(ds_id or "", run_id)
                run_available = True
            except PlatformError as exc:
                st.error(exc.user_message)
                run = None
            column_kinds = _run_column_kinds(run)
            if spec.chart_id == "run_correlation":
                column_kinds.update({m: "object"
                                     for m in _run_methods(run)})

    # ------------------------------------------- snapshot selection
    snap_a = snap_b = None
    if mode == MODE_ARCHAEOLOGY and spec.needs in (NEEDS_SNAPSHOTS,
                                                   NEEDS_SNAPSHOT_DIFF):
        st.markdown("**Snapshots**")
        if spec.needs == NEEDS_SNAPSHOT_DIFF:
            snap_a, snap_b, pair_sig = _snapshot_pair()
            selector_sig += f"|{pair_sig}"
        else:
            snaps = snapshot_manager.list_snapshots()
            if not snaps:
                st.info("No snapshots stored yet - create one from the "
                        "sidebar of the app first.")

    # ------------------------------------------------- role pickers
    roles = _role_pickers(spec, column_kinds, run, comparison)
    options = _options_widgets(spec)
    filter_values = _filters_ui(spec, column_kinds, ds_id, cfg)

    # pre-flight validation (no data is read for this step)
    eff_kinds = dict(column_kinds)
    if spec.needs == NEEDS_PREVIEW and not eff_kinds:
        # metadata unavailable: permissive pre-flight; Generate verifies
        # strictly against the real frame.
        for value in roles.values():
            for col in (value if isinstance(value, list) else [value]):
                if col:
                    eff_kinds[str(col)] = "object"
    needs_v = ("run" if spec.needs == NEEDS_RUN
               else "preview" if spec.needs == NEEDS_PREVIEW else "none")
    report = validate_chart_request(
        chart_id, eff_kinds, roles,
        run_available=run_available or spec.needs != NEEDS_RUN,
        needs=needs_v, frame_empty=False,
        role_kinds=spec.role_kinds, role_required=spec.role_required)
    for message in report.errors:
        st.error(message)
    for message in report.warnings:
        st.warning(message)

    signature = _signature({
        "section": mode, "chart": chart_id, "selector": selector_sig,
        "roles": roles, "options": options,
        "limits": cfg.to_dict(), "filters": filter_values})

    gcol, rcol = st.columns([2, 1])
    with gcol:
        if st.button("Generate visualization", type="primary",
                     key="ds7_generate", disabled=not report.ok,
                     use_container_width=True):
            _do_generate(
                spec=spec, cfg=cfg, roles=roles, options=options,
                filter_values=filter_values, column_kinds=column_kinds,
                ds_id=ds_id, run=run, comparison=comparison, files=files,
                folders=folders, sidebar_filters=filters, snap_a=snap_a,
                snap_b=snap_b, signature=signature)
    with rcol:
        if st.button("Reset page", key="ds7_reset",
                     use_container_width=True):
            _reset_session()
            st.rerun()
    if not report.ok:
        st.caption("Fix the selections above to enable "
                   "**Generate visualization**.")

    _render_result(signature)
