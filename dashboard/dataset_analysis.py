"""Page 10 - Dataset Analysis (Mode B): verify, configure, run, explore.

Connects the Phase 5 analysis engine (``analysis.analysis_runner``) to the
Streamlit app without touching the eight research pages or Page 9. The
research arguments of ``render`` are accepted for the common page
signature and intentionally unused: this page works only with registered
datasets and their run outputs under ``output/datasets/``.

Safety model: sources are opened read-only, every run re-verifies the
registered fingerprint first, and a changed/missing source refuses the
run with guidance instead of analysing stale data.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

import config
from analysis.analysis_runner import (AnalysisConfig, list_runs, load_run,
                                      run_analysis, verify_source)
from core.exceptions import PlatformError
from core.processing_context import ProcessingContext
from dashboard.common import human_size
from ingestion import workflow
from ingestion.dataset_registry import DatasetRegistry

_DATASET_KEY = "ds5_dataset"    # label of the selected dataset
_VERIFY_KEY = "ds5_verify"      # SourceVerification of the current dataset
_VERIFY_DS_KEY = "ds5_verify_ds"
_RUN_KEY = "ds5_run"            # RunResult of the most recent Start
_RUN_DS_KEY = "ds5_run_ds"
_VIEW_KEY = "ds5_view"          # run_id opened in the viewer
_PICK_KEY = "ds5_pick"          # selectbox state of the run list

_STAT_FIELDS = ("count", "missing", "excluded_nonfinite", "unique",
                "mean", "std", "min", "q1", "median", "q3", "max",
                "range", "variance", "iqr", "mode", "basis")


def _reset_session() -> None:
    for key in list(st.session_state.keys()):
        if str(key).startswith("ds5_"):
            del st.session_state[key]


def _registry_row(ds_id: str) -> dict | None:
    try:
        if not Path(config.REGISTRY_DB).exists():
            return None
        return DatasetRegistry().get(ds_id)
    except PlatformError:
        return None


def _metadata_columns(reg_row: dict | None) -> list[str]:
    if not reg_row:
        return []
    loc = reg_row.get("metadata_location")
    if not loc:
        return []
    try:
        payload = json.loads(Path(str(loc)).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    cols = (payload.get("dataset", {}).get("metadata", {}).get("columns")
            or [])
    return [str(c.get("name")) for c in cols
            if isinstance(c, dict) and c.get("name")]


def _artifact_for(reg_row: dict, ds_id: str) -> Path | None:
    if str(reg_row.get("source_type", "")) == "filesystem":
        return (ProcessingContext().datasets_dir / ds_id
                / "files_metadata.csv")
    return None


def _do_verify(ds_id: str) -> None:
    reg = _registry_row(ds_id)
    if reg is None:
        st.session_state.pop(_VERIFY_KEY, None)
        st.session_state.pop(_VERIFY_DS_KEY, None)
        st.error("This dataset is no longer in the registry - register it "
                 "again on the Data Sources page.")
        return
    with st.spinner("Verifying the registered source (read-only) ..."):
        try:
            ver = verify_source(reg, AnalysisConfig(),
                                artifact=_artifact_for(reg, ds_id))
        except (PlatformError, OSError, ValueError) as exc:
            st.session_state.pop(_VERIFY_KEY, None)
            st.session_state.pop(_VERIFY_DS_KEY, None)
            st.error(f"Verification could not run ({type(exc).__name__}).")
            return
    st.session_state[_VERIFY_KEY] = ver
    st.session_state[_VERIFY_DS_KEY] = ds_id


def _render_verification(ver) -> None:
    if ver.status == "ok":
        st.success("Source verified - registered fingerprint matches the "
                   "current file (size + mtime).")
    elif ver.status == "unverifiable":
        st.warning(ver.guidance())
    else:
        st.error(ver.guidance())
    rows = [{"check": c.get("code"), "ok": c.get("ok"),
             "message": c.get("message")} for c in ver.checks]
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True,
                     use_container_width=True)
    if ver.content_hash:
        st.caption(f"Content SHA-256: `{ver.content_hash}` "
                   f"({ver.content_hash_note})")
    elif ver.content_hash_note:
        st.caption(ver.content_hash_note)
    for note in ver.notes:
        st.caption(note)


def _mode_hint(cfg: AnalysisConfig, reg_row: dict | None) -> str:
    if cfg.sample_rows is not None:
        return f"sample ({cfg.sample_rows:,} rows)"
    rows = (reg_row or {}).get("row_count")
    if rows is not None and int(rows) <= cfg.full_load_max_rows:
        return "full (entire dataset in memory)"
    if rows is None:
        return "streaming (row count unknown at registration)"
    return (f"streaming ({int(rows):,} rows > full-load limit "
            f"{cfg.full_load_max_rows:,})")


def _build_config(columns: list[str]) -> AnalysisConfig | None:
    st.markdown("**Options**")
    left, right = st.columns(2)
    with left:
        if columns:
            group = st.selectbox("Group statistics by", ["(none)"] + columns,
                                 key="ds5_group")
            group_val = None if group == "(none)" else group
        else:
            typed = st.text_input("Group statistics by (column name)",
                                  key="ds5_group_text")
            group_val = (typed or "").strip() or None
            st.caption("Column list not available from registration "
                       "metadata - type a column name to group by.")
        methods = st.multiselect("Correlation methods",
                                 ["pearson", "spearman", "kendall"],
                                 default=["pearson", "spearman"],
                                 key="ds5_methods")
        corr_on = st.checkbox("Compute correlation matrix", value=True,
                              key="ds5_corr_on")
        outliers = st.checkbox("Flag potential outliers (IQR fences)",
                               value=True, key="ds5_outliers")
    with right:
        bins = st.slider("Histogram bins", 5, 100, 20, key="ds5_bins")
        top = st.number_input("Top categories per column", 1, 10_000, 50,
                              step=5, key="ds5_top")
        sample = st.number_input(
            "Sample rows (0 = automatic by size)", 0, 10_000_000, 0,
            step=100_000, key="ds5_sample",
            help="0 selects full or streaming mode automatically; any "
                 "positive value forces approximate sample analysis.")
        k = st.slider("Outlier fence (k x IQR)", 1.0, 3.0, 1.5, 0.25,
                      key="ds5_k")
    with st.expander("Advanced options", expanded=False):
        chunk = st.number_input("Chunk size (streaming)", 1_000, 1_000_000,
                                100_000, step=10_000, key="ds5_chunk")
        full_max = st.number_input("Full-load row limit", 1_000,
                                   100_000_000, 1_000_000, step=100_000,
                                   key="ds5_fullmax")
        seed = st.number_input("Random seed (sampling)", 0, 2_147_483_647, 42,
                               key="ds5_seed")
    try:
        cfg = AnalysisConfig(
            group_by=group_val,
            correlation_methods=tuple(methods) or ("pearson",),
            correlation_enabled=bool(corr_on),
            detect_outliers=bool(outliers),
            histogram_bins=int(bins),
            top_categories=int(top),
            sample_rows=(int(sample) or None),
            outlier_k=float(k),
            chunk_size=int(chunk),
            full_load_max_rows=int(full_max),
            seed=int(seed))
    except ValueError as exc:
        st.error(f"Invalid options: {exc}")
        return None
    return cfg


def _render_stat_table(statistics: dict) -> None:
    records = []
    note_rows = []
    for name, d in statistics.items():
        if name.startswith("_") or not isinstance(d, dict):
            continue
        rec = {"column": name}
        for f in _STAT_FIELDS:
            rec[f] = d.get(f)
        records.append(rec)
        for note in d.get("notes") or []:
            note_rows.append({"column": name, "note": note})
    if records:
        st.dataframe(pd.DataFrame(records), hide_index=True,
                     use_container_width=True)
    else:
        st.info("No numeric columns were analysed in this run.")
    for note in statistics.get("_notes") or []:
        note_rows.append({"run": "-", "note": note})
    if note_rows:
        with st.expander(f"Notes ({len(note_rows)})", expanded=False):
            st.dataframe(pd.DataFrame(note_rows), hide_index=True,
                         use_container_width=True)


def _render_quality_table(rows: list, fields: list[str],
                          empty_msg: str) -> None:
    if not rows:
        st.caption(empty_msg)
        return
    df = pd.DataFrame(rows)
    st.dataframe(df[[f for f in fields if f in df.columns]],
                 hide_index=True, use_container_width=True)


def _render_quality(q: dict) -> None:
    score = q.get("score")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Quality score", "n/a" if score is None else f"{score:.1f}")
    comp = q.get("completeness_pct")
    c2.metric("Completeness", "n/a" if comp is None else f"{comp:.2f}%")
    c3.metric("Duplicate rows", f"{q.get('duplicate_rows', 0):,}")
    c4.metric("Missing cells", f"{q.get('missing_cells', 0):,}")

    if q.get("score_formula"):
        st.code(q["score_formula"], language=None)
    lims = q.get("score_limitations") or []
    if lims:
        with st.expander("What this score does NOT prove", expanded=False):
            for item in lims:
                st.markdown(f"- {item}")

    warnings = q.get("warnings") or []
    if warnings:
        st.markdown("**Warnings**")
        for w in warnings[:30]:
            st.warning(w)
        if len(warnings) > 30:
            st.caption(f"+ {len(warnings) - 30} more warning(s) in "
                       "quality_report.json")

    left, right = st.columns(2)
    with left:
        st.markdown("High-missing columns")
        _render_quality_table(
            q.get("columns_high_missing") or [],
            ["column", "missing", "missing_pct", "severity"],
            "none above the warn threshold")
        st.markdown("Constant columns")
        const = [{"column": c}
                 for c in q.get("constant_columns") or []]
        _render_quality_table(const, ["column"], "none")
        st.markdown("Inconsistent value types")
        incons = [{"column": d.get("column"),
                   "types": ", ".join(f"{k}={v}" for k, v in
                                      (d.get("categories") or {}).items()),
                   "sample_size": d.get("sample_size")}
                  for d in q.get("inconsistent_types") or []]
        _render_quality_table(incons, ["column", "types", "sample_size"],
                              "none detected in sampled values")
    with right:
        st.markdown("Duplicate column groups")
        dup_cols = [{"group": ", ".join(map(str, g))}
                    for g in q.get("duplicate_columns") or []]
        _render_quality_table(dup_cols, ["group"], "none")
        st.markdown("High-cardinality columns")
        _render_quality_table(
            q.get("high_cardinality_columns") or [],
            ["column", "unique"], "none")
        inv = q.get("invalid_values") or {}
        inv_cols = inv.get("columns") or []
        st.markdown("Non-finite (+/-inf) cells")
        if inv.get("nonfinite_cells"):
            st.caption(f"{int(inv['nonfinite_cells']):,} cell(s)")
            _render_quality_table(inv_cols, ["column", "nonfinite"],
                                  "none")
        else:
            st.caption("none")

    out = q.get("potential_outliers") or {}
    out_cols = out.get("columns") or {}
    st.markdown("Potential outliers (statistical flags, never removed)")
    if out_cols:
        st.caption(f"Tukey fences at k={out.get('k')} on basis="
                   f"{out.get('basis')}")
        rows = [{"column": c, "count": v.get("count"), "pct": v.get("pct")}
                for c, v in out_cols.items()]
        st.dataframe(pd.DataFrame(rows), hide_index=True,
                     use_container_width=True)
        for note in out.get("notes") or []:
            st.caption(note)
    else:
        st.caption("no columns flagged")


def _render_distributions(dist: dict) -> None:
    hist = dist.get("histograms") or {}
    cat = dist.get("categorical") or {}
    grouped = dist.get("grouped") or {}
    bases = dist.get("bases") or {}

    if hist:
        st.markdown("**Numerical histograms (finite values only)**")
        col = st.selectbox("Numeric column", list(hist))
        h = hist[col] or {}
        edges = h.get("edges") or []
        counts = h.get("counts") or []
        rows = [{"bin_low": edges[i], "bin_high": edges[i + 1],
                 "count": counts[i]}
                for i in range(len(counts)) if i + 1 < len(edges)]
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True,
                         use_container_width=True)
        else:
            st.info("No finite values to bin.")
        st.caption(
            f"used {h.get('used', 0):,} · missing excluded "
            f"{h.get('excluded_missing', 0):,} · non-finite excluded "
            f"{h.get('excluded_nonfinite', 0):,} · basis "
            f"{bases.get('histograms', h.get('basis', 'n/a'))}")
        for n in h.get("notes") or []:
            st.caption(n)

    if cat:
        st.markdown("**Categorical frequencies (non-missing values)**")
        col = st.selectbox("Categorical column", list(cat))
        d = cat[col] or {}
        m1, m2, m3 = st.columns(3)
        mf = d.get("most_frequent") or {}
        m1.metric("Unique values", "n/a" if d.get("unique") is None
                  else f"{d['unique']:,}")
        m2.metric("Missing", f"{d.get('missing_pct', 0):.2f}%")
        m3.metric("Most frequent",
                  str(mf.get("value", "n/a")) if mf else "n/a")
        tops = d.get("top_categories") or []
        if tops:
            st.dataframe(pd.DataFrame(tops), hide_index=True,
                         use_container_width=True)
        rare = d.get("rare_categories") or []
        if rare:
            with st.expander(
                    f"Rare categories ({len(rare)}"
                    f"{'+' if d.get('rare_truncated') else ''})",
                    expanded=False):
                st.dataframe(pd.DataFrame(rare[:500]), hide_index=True,
                             use_container_width=True)
                if d.get("rare_truncated"):
                    st.caption("list truncated in the run output")
        st.caption(f"basis {d.get('basis', 'n/a')} · top "
                   f"{len(tops)} of {d.get('unique') or 0} shown")
        for n in d.get("notes") or []:
            st.caption(n)

    st.markdown("**Grouped statistics**")
    if grouped.get("groups"):
        st.caption(f"grouped by '{grouped.get('group_by')}'")
        rows = []
        for gvalue, cols in grouped["groups"].items():
            for colname, stats in cols.items():
                rec = {"group": gvalue, "column": colname}
                rec.update(stats)
                rows.append(rec)
        st.dataframe(pd.DataFrame(rows), hide_index=True,
                     use_container_width=True)
        extra = (f"groups: {grouped.get('groups_shown', 0)} shown of "
                 f"{grouped.get('groups_total', 0)} · excluded rows with "
                 f"missing group key: "
                 f"{grouped.get('excluded_rows_missing_group_key', 0)}")
        st.caption(extra)
        for n in grouped.get("notes") or []:
            st.caption(n)
    else:
        for n in grouped.get("notes") or ["no grouping configured"]:
            st.caption(n)

    not_analyzed = dist.get("columns_not_analyzed") or {}
    if not_analyzed:
        st.caption("profiled only (not part of histograms/frequencies): "
                   + ", ".join(not_analyzed))


def _render_correlation(loaded: dict) -> None:
    cdf = loaded.get("correlation_df")
    if cdf is None or cdf.empty:
        st.info("No correlation rows - correlation was disabled, no "
                "numeric columns exist, or the run failed.")
        return
    basis = (loaded.get("summary") or {}).get("correlation_basis", "n/a")
    st.caption(f"pairwise-complete basis: {basis}")
    methods = sorted(str(m) for m in cdf["method"].unique())
    if len(methods) > 1:
        method = st.selectbox("Method", methods)
        sub = cdf[cdf["method"] == method]
    else:
        sub = cdf
        if methods:
            st.caption(f"method: {methods[0]}")
    pivot = sub.pivot_table(index="column_a", columns="column_b",
                            values="value", aggfunc="first")
    st.dataframe(pivot, use_container_width=True)
    with st.expander("Raw correlation rows", expanded=False):
        st.dataframe(sub, hide_index=True, use_container_width=True)


def _render_files_and_progress(loaded: dict) -> None:
    run_id = loaded.get("run_id", "")
    files = loaded.get("files") or {}
    rows = []
    for name in sorted(files):
        path = files[name]
        p = Path(path) if path else None
        ok = bool(p and p.exists())
        rows.append({"file": name, "exists": ok,
                     "bytes": (p.stat().st_size if ok else None)})
    st.markdown("**Run files**")
    st.dataframe(pd.DataFrame(rows), hide_index=True,
                 use_container_width=True)
    for name in sorted(files):
        path = files[name]
        if not path or not Path(path).exists():
            continue
        try:
            data = Path(path).read_bytes()
        except OSError:
            continue
        st.download_button(f"Download {name}", data,
                           file_name=f"{run_id}_{name}",
                           key=f"ds5_dl_{name}")

    fingerprint = loaded.get("fingerprint")
    if fingerprint:
        st.markdown("**Source verification (fingerprint.json)**")
        checks = [{"check": c.get("code"), "ok": c.get("ok"),
                   "message": c.get("message")}
                  for c in fingerprint.get("checks") or []]
        if checks:
            st.dataframe(pd.DataFrame(checks), hide_index=True,
                         use_container_width=True)
        if fingerprint.get("content_hash"):
            st.caption(f"content SHA-256: "
                       f"`{fingerprint.get('content_hash')}`")

    meta = loaded.get("run_metadata") or {}
    if meta:
        st.markdown("**Run metadata**")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Status", meta.get("status", "n/a"))
        m2.metric("Duration", f"{meta.get('duration_seconds', 0):.2f}s")
        m3.metric("Started", str(meta.get("started_at", "n/a")))
        m4.metric("Finished", str(meta.get("finished_at", "n/a")))
        if meta.get("error"):
            st.error(meta["error"])
        st.caption(f"pandas {meta.get('pandas', '?')} · numpy "
                   f"{meta.get('numpy', '?')} · python "
                   f"{meta.get('python', '?')}")
        progress = meta.get("progress") or []
        if progress:
            with st.expander(f"Progress log ({len(progress)} events)",
                             expanded=False):
                st.dataframe(pd.DataFrame(progress), hide_index=True,
                             use_container_width=True)

    errors = loaded.get("errors") or []
    if errors:
        st.markdown("**Errors (errors.json)**")
        st.dataframe(pd.DataFrame(errors), hide_index=True,
                     use_container_width=True)


def _render_summary(loaded: dict) -> None:
    summary = loaded.get("summary") or {}
    meta = loaded.get("run_metadata") or {}
    if summary.get("refused"):
        st.error(summary.get("guidance")
                 or "This run was refused.")
        st.caption(f"reason: {summary.get('reason')}")
        ver = summary.get("verification") or {}
        _render_verification_rows(ver)
        return
    if not summary:
        st.error(meta.get("error") or
                 "This run produced no summary (it may have failed before "
                 "analysis started).")
        return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Mode", str(summary.get("mode", "n/a")))
    c2.metric("Rows analysed", f"{summary.get('rows_analyzed') or 0:,}")
    c3.metric("Columns", f"{summary.get('columns') or 0:,}")
    score = summary.get("quality_score")
    c4.metric("Quality score", "n/a" if score is None else f"{score:.1f}")
    st.caption(
        f"source: {summary.get('source_type')} · verification: "
        f"{summary.get('source_status')} · correlation basis: "
        f"{summary.get('correlation_basis')} · registered rows: "
        f"{summary.get('rows_registered')}")
    warnings = summary.get("warnings") or []
    if warnings:
        st.warning(f"{len(warnings)} quality warning(s) - see the Quality "
                   "tab for details.")
    flags = summary.get("outliers_flagged") or []
    if flags:
        st.caption("columns flagged for potential outliers: "
                   + ", ".join(flags[:15])
                   + (" ..." if len(flags) > 15 else ""))
    for note in summary.get("notes") or []:
        st.caption(note)


def _render_verification_rows(ver: dict) -> None:
    checks = [{"check": c.get("code"), "ok": c.get("ok"),
               "message": c.get("message")}
              for c in ver.get("checks") or []]
    if checks:
        st.dataframe(pd.DataFrame(checks), hide_index=True,
                     use_container_width=True)
    if ver.get("content_hash"):
        st.caption(f"content SHA-256: `{ver.get('content_hash')}`")


def _render_run(ds_id: str) -> None:
    runs = list_runs(ds_id)
    if not runs:
        st.info("No analysis runs yet - verify the source and press "
                "**Start analysis** above.")
        return
    ids = [r.get("run_id") for r in runs]
    labels = {r.get("run_id"):
              f"{r.get('status', '?')} · {r.get('finished_at', '?')} · "
              f"{r.get('run_id')}" for r in runs}
    wanted = st.session_state.get(_VIEW_KEY)
    if wanted in ids:
        if st.session_state.get(_PICK_KEY) != wanted:
            st.session_state[_PICK_KEY] = wanted
    else:
        st.session_state.pop(_PICK_KEY, None)
    idx = ids.index(wanted) if wanted in ids else 0
    picked = st.selectbox("Run to view", ids, index=idx,
                          format_func=lambda i: labels.get(i, str(i)),
                          key=_PICK_KEY)
    if picked != st.session_state.get(_VIEW_KEY):
        st.session_state[_VIEW_KEY] = picked

    try:
        loaded = load_run(ds_id, picked)
    except PlatformError as exc:
        st.error(exc.user_message)
        return

    (tab_sum, tab_stat, tab_qual, tab_dist, tab_corr,
     tab_files) = st.tabs(["Summary", "Statistics", "Quality",
                           "Distributions", "Correlation",
                           "Run files & progress"])
    with tab_sum:
        _render_summary(loaded)
    with tab_stat:
        stats = loaded.get("statistics") or {}
        st.caption(f"basis: {stats.get('basis', 'n/a')}")
        _render_stat_table(stats.get("columns") or {})
        excluded = stats.get("excluded_records") or {}
        if excluded:
            st.markdown("**Excluded records (accounted, never silently "
                        "dropped)**")
            st.json(excluded)
    with tab_qual:
        _render_quality(loaded.get("quality_report") or {})
    with tab_dist:
        _render_distributions(loaded.get("distributions") or {})
    with tab_corr:
        _render_correlation(loaded)
    with tab_files:
        _render_files_and_progress(loaded)


def render(files: pd.DataFrame, folders: pd.DataFrame, findings: dict,
           filters: dict):
    """Common page entry point. Research arguments intentionally unused."""
    st.subheader("Dataset Analysis")
    st.caption(
        "Mode B: analyse any dataset registered on the Data Sources page. "
        "Every run re-verifies the registered source first, works "
        "read-only, and writes its own outputs under "
        "`output/datasets/<dataset_id>/runs/<run_id>/`. The research "
        "dashboard and its data are not affected.")

    history = workflow.history()
    if not history:
        st.info("No datasets registered yet - register one on the Data "
                "Sources page first.")
        return

    labels: dict = {}
    by_id: dict = {}
    for r in history:
        ds = r.get("dataset_id")
        if not ds:
            continue
        label = (f"{r.get('name') or ds[:8]} · {r.get('source_type')} · "
                 f"{r.get('imported_at')} · {ds[:8]}")
        labels[label] = ds
        by_id[ds] = r

    b1, b2 = st.columns([6, 1])
    with b1:
        label = st.selectbox("Dataset", list(labels), key=_DATASET_KEY)
    with b2:
        if st.button("Reset page", key="ds5_reset"):
            _reset_session()
            st.rerun()
    ds_id = labels[label]

    if st.session_state.get("ds5_ds") != ds_id:
        for k in (_VERIFY_KEY, _VERIFY_DS_KEY, _RUN_KEY, _RUN_DS_KEY,
                  _VIEW_KEY, _PICK_KEY):
            st.session_state.pop(k, None)
        st.session_state["ds5_ds"] = ds_id

    reg = _registry_row(ds_id) or by_id.get(ds_id) or {}
    m1, m2, m3, m4 = st.columns(4)
    rows = reg.get("row_count")
    m1.metric("Rows", "unknown" if rows is None else f"{int(rows):,}")
    m2.metric("Columns", reg.get("column_count") or "n/a")
    m3.metric("Size", human_size(reg.get("bytes"))
              if reg.get("bytes") else "n/a")
    m4.metric("Validation", reg.get("validation_status") or "n/a")

    st.markdown("---")
    st.markdown("**Verify source**")
    st.caption("Compares the current file/folder against the fingerprint "
               "recorded at registration (size + mtime, plus a content "
               "SHA-256 when small enough). A changed or missing source "
               "refuses the run.")
    if st.button("Verify source now", key="ds5_verify_btn"):
        _do_verify(ds_id)

    ver = None
    if (st.session_state.get(_VERIFY_DS_KEY) == ds_id):
        ver = st.session_state.get(_VERIFY_KEY)
    ready = ver is not None and ver.ok_to_run
    if ver is not None:
        _render_verification(ver)
    else:
        st.caption("Not verified yet - press **Verify source now** before "
                   "starting an analysis.")

    st.markdown("---")
    columns = _metadata_columns(_registry_row(ds_id))
    cfg = _build_config(columns)
    if cfg is not None:
        st.caption(f"Mode hint: {_mode_hint(cfg, reg)}")

    start_disabled = (cfg is None or not ready)
    if st.button("Start analysis", type="primary", key="ds5_start",
                 disabled=start_disabled):
        with st.spinner("Analysing (local, read-only, sources unchanged) "
                        "..."):
            res = run_analysis(ds_id, cfg)
        st.session_state[_RUN_KEY] = res
        st.session_state[_RUN_DS_KEY] = ds_id
        if res.run_id:
            st.session_state[_VIEW_KEY] = res.run_id
            st.session_state.pop(_PICK_KEY, None)
        st.rerun()
    if not ready:
        st.caption("The Start button unlocks after verification passes.")

    st.markdown("---")
    st.markdown("**Results**")
    if (st.session_state.get(_RUN_DS_KEY) == ds_id
            and st.session_state.get(_RUN_KEY) is not None):
        res = st.session_state[_RUN_KEY]
        if res.ok:
            summary = res.summary or {}
            rows_ok = summary.get("rows_analyzed") or 0
            st.success(
                f"Run {res.run_id} completed ({summary.get('mode', '?')} "
                f"mode, {rows_ok:,} rows).")
        else:
            st.error(res.error or "The run failed.")
    _render_run(ds_id)
