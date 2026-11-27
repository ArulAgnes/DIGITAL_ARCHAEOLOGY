"""Page 11 - Dataset Comparison (Mode B): validate, run, explore.

Connects the Phase 6 comparison engine (``analysis.comparison``) to the
Streamlit app. The research arguments of ``render`` are accepted for the
common page signature and intentionally unused: this page only compares
datasets registered on the Data Sources page and writes its reports
under ``output/comparisons/<comparison_id>/runs/<run_id>/``.

Safety model: nothing runs automatically. The explicit **Validate keys &
sources** step checks both registered sources (read-only) and the chosen
key columns first; **Start comparison** stays locked until validation
passes for the current selection. The engine re-verifies both sources
again at run time and refuses a changed or missing source.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from analysis.comparison import (ComparisonConfig, list_comparisons,
                                 load_comparison, registration_column_names,
                                 run_comparison, validate_selection)
from core.exceptions import PlatformError
from dashboard.common import human_size
from ingestion import workflow

_KIND_LABELS = {"CSV records": "csv", "SQLite table": "sqlite",
                "Filesystem snapshot": "filesystem"}
_KIND_OF = {v: k for k, v in _KIND_LABELS.items()}

_VIEW_KEY = "ds6_view"        # "comparison_id|run_id" currently displayed
_PICK_KEY = "ds6_pick"        # selectbox state of the run list
_VAL_KEY = "ds6_val"          # last validation result
_VAL_SIG_KEY = "ds6_val_sig"  # inputs the validation was made for
_BANNER_KEY = "ds6_banner"    # (ok, message) of the last Start click
_DS_KEY = "ds6_ds"            # (kind, label_a, label_b) last selection

_TABS_BASE = ["Summary", "Added", "Removed", "Modified", "Schema",
              "Statistics", "Warnings", "Files & downloads"]
_TAB_MOVED = "Moved"


def _reset_session() -> None:
    for key in list(st.session_state.keys()):
        if str(key).startswith("ds6_"):
            del st.session_state[key]


def _dataset_options(kind: str) -> tuple[dict, dict]:
    """label -> dataset_id and dataset_id -> history row for one kind."""
    labels: dict = {}
    by_id: dict = {}
    for row in workflow.history():
        ds_id = row.get("dataset_id")
        if not ds_id or row.get("source_type") != kind:
            continue
        label = (f"{row.get('name') or ds_id[:8]} · "
                 f"{row.get('imported_at')} · {ds_id[:8]}")
        labels[label] = ds_id
        by_id[ds_id] = row
    return labels, by_id


def _build_config() -> ComparisonConfig | None:
    st.markdown("**Comparison options**")
    left, right = st.columns(2)
    with left:
        export_rows = st.number_input(
            "Max rows per export file (0 = counts only)", 0, 10_000_000,
            10_000, step=1_000, key="ds6_export_rows",
            help="Exports are bounded; the summary always keeps exact "
                 "totals and truncation is reported as a warning.")
        stats_on = st.checkbox("Compute statistical differences",
                               value=True, key="ds6_stats_on")
        top_k = st.slider("Top categories per column", 5, 100, 20,
                          key="ds6_top_k")
    with right:
        casefold = st.checkbox(
            "Match file paths case-insensitively", value=True,
            key="ds6_casefold",
            help="Filesystem comparisons only.")
        moves = st.checkbox("Detect moved and renamed files", value=True,
                            key="ds6_moves",
                            help="Filesystem comparisons only - a metadata "
                                 "heuristic, never proof of content.")
    with st.expander("Advanced options", expanded=False):
        adv1, adv2 = st.columns(2)
        with adv1:
            chunk = st.number_input("Chunk size (rows per read)", 1_000,
                                    1_000_000, 100_000, step=10_000,
                                    key="ds6_chunk")
        with adv2:
            max_keys = st.number_input("Key index limit (max distinct "
                                       "keys)", 1_000, 100_000_000,
                                       1_000_000, step=100_000,
                                       key="ds6_max_keys")
    try:
        cfg = ComparisonConfig(
            chunk_size=int(chunk), max_keys=int(max_keys),
            max_export_rows=int(export_rows),
            stats_enabled=bool(stats_on),
            categorical_top_k=int(top_k),
            path_casefold=bool(casefold), detect_moves=bool(moves))
    except ValueError as exc:
        st.error(f"Invalid options: {exc}")
        return None
    st.caption("Config preview: "
               + json.dumps(cfg.to_dict(), sort_keys=True))
    return cfg


def _render_validation(val: dict, sig_matches: bool) -> None:
    if not sig_matches:
        st.caption("The selection changed after validation - press "
                   "**Validate keys & sources** again before starting.")
        return
    for message in val.get("errors") or []:
        st.error(message)
    for message in val.get("warnings") or []:
        st.warning(message)
    if val.get("ok"):
        st.success("Validation passed - both sources verified read-only "
                   "and the comparison is ready to start.")
    for message in val.get("details") or []:
        st.caption(message)


def _record_files(kind: str, tab: str) -> list[str]:
    if kind == "filesystem":
        return {"Added": ["added_files.csv"],
                "Removed": ["removed_files.csv"],
                "Modified": ["modified_files.csv"],
                "Moved": ["moved_files.csv"]}.get(tab, [])
    return {"Added": ["records_added.csv"],
            "Removed": ["records_removed.csv"],
            "Modified": ["records_modified.csv"]}.get(tab, [])


def _count_key(kind: str, tab: str) -> str:
    return {"Added": "added", "Removed": "removed",
            "Modified": "modified"}.get(tab, "")


def _render_records(loaded: dict, kind: str, tab: str) -> None:
    summary = loaded.get("comparison_summary") or {}
    counts = summary.get("counts") or {}
    files = loaded.get("files") or {}
    tables = loaded.get("tables") or {}
    names = _record_files(kind, tab)
    if kind == "filesystem" and tab == "Moved":
        total = (counts.get("moved", 0) + counts.get("renamed", 0))
        st.caption(f"move/rename pairs: {total:,} "
                   "(metadata evidence: name + size + timestamp)")
    else:
        key = _count_key(kind, tab)
        if key:
            st.caption(f"total {tab.lower()} row(s): "
                       f"{counts.get(key, 0):,}")
    if not names:
        st.info("This tab does not apply to filesystem comparisons.")
        return
    for name in names:
        st.markdown(f"**{name}**")
        path = tables.get(name) or files.get(name)
        if not path or not Path(path).exists():
            st.info("No rows were exported for this category (or the run "
                    "was refused/failed before exports were written).")
            continue
        try:
            frame = pd.read_csv(path)
        except (OSError, ValueError) as exc:
            st.error(f"{name} could not be read ({type(exc).__name__}).")
            continue
        st.dataframe(frame, hide_index=True, use_container_width=True)
        st.caption(f"{len(frame):,} row(s) shown"
                   + (" (export bounded - see Summary for totals)"
                      if summary.get("truncated", {}).get(name) else ""))


def _render_summary(loaded: dict) -> None:
    summary = loaded.get("comparison_summary") or {}
    meta = loaded.get("run_metadata") or {}
    status = summary.get("status") or meta.get("status") or "unknown"
    if status == "refused":
        st.error(summary.get("guidance")
                 or "This comparison was refused.")
        st.caption(f"reason: {summary.get('reason')}")
        ver = summary.get("verification") or {}
        for side in ("a", "b"):
            data = ver.get(side) or {}
            st.caption(f"dataset {side.upper()}: verification status "
                       f"{data.get('status', 'n/a')}")
            rows = [{"check": c.get("code"), "ok": c.get("ok"),
                     "message": c.get("message")}
                    for c in data.get("checks") or []]
            if rows:
                st.dataframe(pd.DataFrame(rows), hide_index=True,
                             use_container_width=True)
        return
    if status in ("failed", "cancelled"):
        st.error(meta.get("error")
                 or summary.get("guidance")
                 or f"The comparison {status}.")
        st.caption("see Warnings and Files & downloads for details")
        return
    if not summary:
        st.error("This run produced no summary.")
        return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Status", str(summary.get("status", "n/a")))
    c2.metric("Strategy", str(summary.get("strategy", "n/a")))
    c3.metric("Exactness", str(summary.get("exactness", "n/a")))
    c4.metric("Warnings", f"{summary.get('warnings_count', 0):,}")
    datasets = summary.get("datasets") or {}
    for side in ("a", "b"):
        data = datasets.get(side) or {}
        st.caption(f"dataset {side.upper()}: {data.get('name')} "
                   f"({data.get('source_type')}) · "
                   f"{str(data.get('dataset_id', ''))[:8]}")
    keys = summary.get("keys") or []
    st.caption("match key: "
               + (", ".join(str(k) for k in keys) if keys
                  else "exact row content (no key columns)"))
    counts = summary.get("counts") or {}
    if counts:
        st.markdown("**Counts**")
        st.dataframe(
            pd.DataFrame([{"metric": k, "value": v}
                          for k, v in sorted(counts.items())]),
            hide_index=True, use_container_width=True)
    truncated = summary.get("truncated") or {}
    if truncated:
        st.markdown("**Truncated exports (totals above stay exact)**")
        st.dataframe(
            pd.DataFrame([{"file": k, "exported": v.get("exported"),
                           "total": v.get("total")}
                          for k, v in sorted(truncated.items())]),
            hide_index=True, use_container_width=True)
    for note in summary.get("notes") or []:
        st.caption(note)
    limitations = summary.get("limitations") or []
    if limitations:
        with st.expander("What this comparison does NOT prove",
                         expanded=False):
            for item in limitations:
                st.markdown(f"- {item}")


def _render_schema(loaded: dict) -> None:
    schema = loaded.get("schema_differences") or {}
    if not schema or schema.get("status") == "not-run":
        st.info("Schema information was not produced for this run.")
        return
    st.caption(schema.get("note", ""))
    columns = schema.get("columns") or {}
    if columns:
        st.markdown("**Columns**")
        c1, c2 = st.columns(2)
        with c1:
            st.caption("added in B")
            st.write(columns.get("added") or "none")
            st.caption("removed since A")
            st.write(columns.get("removed") or "none")
        with c2:
            changes = columns.get("dtype_changes") or []
            if changes:
                st.caption("type changes")
                st.dataframe(pd.DataFrame(changes), hide_index=True,
                             use_container_width=True)
            else:
                st.caption("type changes: none")
    if schema.get("tables"):
        tables = schema.get("tables") or {}
        st.markdown("**Tables**")
        st.caption(f"A: {tables.get('a')} · B: {tables.get('b')} · "
                   f"table list diff: {json.dumps(schema.get('table_list_diff') or {}, sort_keys=True)}")
        st.write(schema.get("table_counts") or {})
    folders = schema.get("folders") or {}
    if folders:
        st.markdown("**Folders**")
        if folders.get("status") != "ok":
            st.info(folders.get("note") or "folder metadata unavailable")
        else:
            c1, c2 = st.columns(2)
            with c1:
                st.caption(f"folders only in A: "
                           f"{folders.get('a_only_count', 0):,}")
                for value in folders.get("a_only") or []:
                    st.text(value)
            with c2:
                st.caption(f"folders only in B: "
                           f"{folders.get('b_only_count', 0):,}")
                for value in folders.get("b_only") or []:
                    st.text(value)
    counts = schema.get("row_counts") or {}
    if counts:
        st.caption(f"row counts - A: {counts.get('a', 0):,} · "
                   f"B: {counts.get('b', 0):,}")
    files = schema.get("files") or {}
    if files:
        st.caption("file fields compared: "
                   + ", ".join(files.get("columns_compared") or []))


def _render_statistics(loaded: dict) -> None:
    stats = loaded.get("statistics_differences") or {}
    if not stats or stats.get("status") in ("disabled", "not-run"):
        st.info("Statistics were not computed for this run.")
        for note in stats.get("notes") or []:
            st.caption(note)
        return
    st.caption(f"basis: {stats.get('basis', 'n/a')}")
    storage = stats.get("storage")
    if storage:
        st.markdown("**Storage**")
        c1, c2, c3 = st.columns(3)
        c1.metric("Files A / B",
                  f"{storage.get('a_files', 0):,} / "
                  f"{storage.get('b_files', 0):,}")
        c2.metric("Size A", human_size(storage.get("a_total_bytes", 0)))
        delta = int(storage.get("delta_bytes", 0))
        c3.metric("Size delta",
                  ("+" if delta >= 0 else "")
                  + human_size(abs(delta)))
    columns = stats.get("columns") or {}
    if columns:
        rows = []
        for name, entry in sorted(columns.items()):
            basis = entry.get("basis", "")
            a, b, d = entry.get("a") or {}, entry.get("b") or {}, \
                entry.get("delta") or {}
            rows.append({
                "column": name, "basis": basis,
                "a_count": a.get("count"), "b_count": b.get("count"),
                "count_Δ": d.get("count"),
                "a_missing": a.get("missing"),
                "b_missing": b.get("missing"),
                "missing_Δ": d.get("missing"),
                "mean_Δ": d.get("mean"), "min_Δ": d.get("min"),
                "max_Δ": d.get("max"),
                "a_basis": entry.get("a_basis"),
                "b_basis": entry.get("b_basis"),
            })
        st.markdown("**Per-column differences (B - A)**")
        st.dataframe(pd.DataFrame(rows), hide_index=True,
                     use_container_width=True)
        for name, entry in sorted(columns.items()):
            top = entry.get("top_values") or []
            if not top:
                continue
            with st.expander(f"Top values: {name}", expanded=False):
                st.dataframe(pd.DataFrame(top), hide_index=True,
                             use_container_width=True)
                if entry.get("values_truncated"):
                    st.caption("value counts were capped - see notes")
    ext = stats.get("extensions")
    if ext:
        st.markdown("**Extensions**")
        st.dataframe(pd.DataFrame(ext.get("top") or []), hide_index=True,
                     use_container_width=True)
        st.caption(f"shown {ext.get('shown', 0)} of "
                   f"{max(ext.get('distinct_a', 0), ext.get('distinct_b', 0))} "
                   "distinct extension(s)")
    dup = stats.get("duplicate_basenames")
    if dup:
        st.caption(f"duplicate basenames - A groups: "
                   f"{dup.get('a_groups', 0):,}, B groups: "
                   f"{dup.get('b_groups', 0):,} (informational)")
    naming = stats.get("naming_patterns")
    if naming:
        st.caption(f"naming pattern changed for "
                   f"{naming.get('changed', 0):,} of "
                   f"{naming.get('compared_pairs', 0):,} matched file(s)")
        samples = naming.get("samples") or []
        if samples:
            with st.expander("Naming pattern changes (sample)",
                             expanded=False):
                st.dataframe(pd.DataFrame(samples), hide_index=True,
                             use_container_width=True)
    cases = stats.get("path_case_changes")
    if cases and cases.get("count"):
        st.caption(f"path case-only changes: {cases.get('count'):,}")
    for note in stats.get("notes") or []:
        st.caption(note)


def _render_warnings(loaded: dict) -> None:
    warnings = loaded.get("warnings") or []
    if not warnings:
        st.info("No warnings for this run.")
        return
    for entry in warnings[:50]:
        code = entry.get("code", "warning")
        st.warning(f"[{code}] {entry.get('message', '')}")
    if len(warnings) > 50:
        st.caption(f"+ {len(warnings) - 50} more in warnings.json")


def _render_files(loaded: dict, run_label: str) -> None:
    files = loaded.get("files") or {}
    rows = []
    for name in sorted(files):
        path = files[name]
        target = Path(path) if path else None
        exists = bool(target and target.exists())
        rows.append({"file": name, "exists": exists,
                     "bytes": (target.stat().st_size if exists else None)})
    st.markdown("**Run files**")
    if rows:
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
                           file_name=f"{run_label}_{name}",
                           key=f"ds6_dl_{run_label}_{name}")
    meta = loaded.get("run_metadata") or {}
    if meta:
        st.markdown("**Run metadata**")
        m1, m2, m3 = st.columns(3)
        m1.metric("Status", meta.get("status", "n/a"))
        m2.metric("Duration", f"{meta.get('duration_seconds', 0):.2f}s")
        m3.metric("Type", meta.get("kind", "n/a"))
        if meta.get("error"):
            st.error(meta["error"])
        progress = meta.get("progress") or []
        if progress:
            with st.expander(f"Progress log ({len(progress)} events)",
                             expanded=False):
                st.dataframe(pd.DataFrame(progress), hide_index=True,
                             use_container_width=True)


def _render_view(loaded: dict, run_label: str) -> None:
    kind = ((loaded.get("comparison_summary") or {}).get("kind")
            or (loaded.get("run_metadata") or {}).get("kind") or "csv")
    tabs = list(_TABS_BASE)
    if kind == "filesystem":
        tabs.insert(tabs.index("Schema"), _TAB_MOVED)
    if kind == "filesystem":
        names = tabs
    else:
        names = [t for t in tabs if t != _TAB_MOVED]
    widgets = st.tabs(names)
    for tab, widget in zip(names, widgets):
        with widget:
            if tab == "Summary":
                _render_summary(loaded)
            elif tab in ("Added", "Removed", "Modified", "Moved"):
                _render_records(loaded, kind, tab)
            elif tab == "Schema":
                _render_schema(loaded)
            elif tab == "Statistics":
                _render_statistics(loaded)
            elif tab == "Warnings":
                _render_warnings(loaded)
            else:
                _render_files(loaded, run_label)


def render(files: pd.DataFrame, folders: pd.DataFrame, findings: dict,
           filters: dict):
    """Common page entry point. Research arguments intentionally unused."""
    st.subheader("Dataset Comparison")
    st.caption(
        "Mode B: compare two datasets registered on the Data Sources "
        "page (same source type). Nothing runs automatically - validate "
        "the selection first, then start explicitly. Reports are written "
        "under `output/comparisons/<comparison_id>/runs/<run_id>/`; "
        "sources are opened read-only and never modified.")

    c_left, c_right = st.columns([6, 1])
    with c_right:
        if st.button("Reset page", key="ds6_reset"):
            _reset_session()
            st.rerun()

    all_rows = workflow.history()
    if not all_rows:
        st.info("No datasets registered yet - register at least two on "
                "the Data Sources page first.")
        return

    # -- step 1: comparison type -------------------------------------- #
    with c_left:
        kind_label = st.selectbox("Comparison type", list(_KIND_LABELS),
                                  key="ds6_kind",
                                  help="Both selected datasets must be of "
                                       "this source type.")
    kind = _KIND_LABELS[kind_label]

    labels, _by_id = _dataset_options(kind)
    if len(labels) < 2:
        st.info(f"Comparing {kind_label} needs at least two registered "
                f"{kind} datasets - register another one on the Data "
                "Sources page.")
        return

    # -- steps 2 & 3: dataset A and B --------------------------------- #
    st.markdown("**Datasets**")
    col_a, col_b = st.columns(2)
    with col_a:
        label_a = st.selectbox("Dataset A (first)", list(labels),
                               key=f"ds6_a_{kind}")
    with col_b:
        label_b = st.selectbox("Dataset B (second)", list(labels),
                               index=min(1, len(labels) - 1),
                               key=f"ds6_b_{kind}")
    ds_a, ds_b = labels[label_a], labels[label_b]

    selection = (kind, label_a, label_b)
    if st.session_state.get(_DS_KEY) != selection:
        st.session_state.pop(_VAL_KEY, None)
        st.session_state.pop(_VAL_SIG_KEY, None)
        st.session_state.pop(_BANNER_KEY, None)
        st.session_state[_DS_KEY] = selection
    if ds_a == ds_b:
        st.warning("Pick two different datasets - A and B must differ.")

    # -- step 4: key columns (records only) --------------------------- #
    keys: list[str] = []
    if kind in ("csv", "sqlite"):
        st.markdown("**Record keys**")
        names_a = registration_column_names(ds_a)
        names_b = registration_column_names(ds_b)
        options = list(dict.fromkeys(names_a + names_b))
        if options:
            keys = st.multiselect(
                "Key column(s) used to pair records", options,
                key="ds6_keys",
                help="Leave empty to compare by exact row content (a "
                     "changed record then appears as remove + add).")
        else:
            keys = st.multiselect("Key column(s) used to pair records",
                                  [], key="ds6_keys")
            st.caption("Column names are unavailable from registration "
                       "metadata - validate to let the engine auto-detect "
                       "a key column.")

    sig = (kind, ds_a, ds_b, tuple(keys))

    # -- step 5: validate keys & sources ------------------------------ #
    st.markdown("---")
    st.markdown("**Validate**")
    st.caption("Read-only pre-flight: verifies both registered sources "
               "against their fingerprints and checks that the key "
               "columns exist in both datasets. No comparison runs here.")
    if st.button("Validate keys & sources", key="ds6_validate_btn"):
        st.session_state[_VAL_KEY] = validate_selection(
            kind, ds_a, ds_b, keys)
        st.session_state[_VAL_SIG_KEY] = sig
        st.session_state.pop(_BANNER_KEY, None)

    # -- step 6: validation warnings ---------------------------------- #
    val = st.session_state.get(_VAL_KEY)
    if val is not None:
        _render_validation(val, st.session_state.get(_VAL_SIG_KEY) == sig)

    # -- step 7: config preview --------------------------------------- #
    st.markdown("---")
    cfg = _build_config()

    # -- step 8: start (gated) ---------------------------------------- #
    validated = bool(val and val.get("ok")
                     and st.session_state.get(_VAL_SIG_KEY) == sig)
    start_disabled = cfg is None or not validated
    if st.button("Start comparison", type="primary", key="ds6_start",
                 disabled=start_disabled):
        with st.spinner("Comparing (local, read-only, sources re-verified) "
                        "..."):
            res = run_comparison(kind, ds_a, ds_b, config_obj=cfg)
        if res.run_id:
            st.session_state[_VIEW_KEY] = (
                f"{res.comparison_id}|{res.run_id}")
            st.session_state.pop(_PICK_KEY, None)
        if res.ok:
            counts = res.summary.get("counts") or {}
            st.session_state[_BANNER_KEY] = (
                True, f"Run {res.run_id} completed "
                      f"({res.summary.get('strategy', 'n/a')} strategy, "
                      f"exactness {res.summary.get('exactness', 'n/a')}, "
                      f"{len(res.progress)} progress events). "
                      f"counts: "
                      f"{json.dumps(counts, sort_keys=True)}")
        else:
            st.session_state[_BANNER_KEY] = (
                False, res.error or f"The comparison {res.status}.")
        st.rerun()
    if not start_disabled:
        st.caption("Ready - press **Start comparison** to run.")
    else:
        st.caption("The Start button unlocks after validation passes for "
                   "the current selection.")

    # -- steps 9 & 10: banner, results, history ------------------------ #
    banner = st.session_state.get(_BANNER_KEY)
    if banner:
        if banner[0]:
            st.success(banner[1])
        else:
            st.error(banner[1])

    st.markdown("---")
    runs = list_comparisons()
    if not runs:
        st.info("No comparison runs yet - validate a selection and press "
                "**Start comparison** above.")
        return

    run_ids = [f"{r.get('comparison_id')}|{r.get('run_id')}"
               for r in runs]
    labels_by_id = {
        f"{r.get('comparison_id')}|{r.get('run_id')}":
            f"{r.get('status', '?')} · {r.get('kind', '?')} · "
            f"{r.get('finished_at', '?')} · {str(r.get('run_id', ''))}"
        for r in runs}
    wanted = st.session_state.get(_VIEW_KEY)
    if wanted not in run_ids:
        wanted = run_ids[0]
    if st.session_state.get(_PICK_KEY) != wanted:
        st.session_state[_PICK_KEY] = wanted
    index = run_ids.index(wanted)
    picked = st.selectbox("Comparison run to view", run_ids, index=index,
                          format_func=lambda i: labels_by_id.get(i, i),
                          key=_PICK_KEY)
    if picked != st.session_state.get(_VIEW_KEY):
        st.session_state[_VIEW_KEY] = picked
    comparison_id, run_id = picked.split("|", 1)

    try:
        loaded = load_comparison(comparison_id, run_id)
    except PlatformError as exc:
        st.error(exc.user_message)
        return
    _render_view(loaded, run_id)
