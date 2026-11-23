"""DIGITAL ARCHAEOLOGY - interactive Streamlit dashboard.

Launch:  streamlit run app.py
"""
from __future__ import annotations

import json
from datetime import datetime

import pandas as pd
import streamlit as st

import config
from dashboard import (duplicates, filetypes, findings as findings_page,
                       folder_structure, naming, overview, storage, timeline)
from dashboard import data_sources, dataset_analysis, dataset_comparison
from dashboard import advanced_visualization
from dashboard.common import (apply_filters, ensure_data, human_size,
                              load_data, sidebar_filters)
from scanner import snapshot_manager

st.set_page_config(
    page_title="Digital Archaeology",
    page_icon="🏺",
    layout="wide",
    initial_sidebar_state="expanded",
)

PAGES = {
    "Overview": overview.render,
    "File Creation Timeline": timeline.render,
    "Storage Analytics": storage.render,
    "File Type Analysis": filetypes.render,
    "Naming Pattern Analysis": naming.render,
    "Duplicate Analysis": duplicates.render,
    "Folder Structure": folder_structure.render,
    "Research Findings": findings_page.render,
    # Phase 4: additive page for the reusable ingestion workflow. The eight
    # research pages above are unchanged.
    "Data Sources": data_sources.render,
    # Phase 5: additive page for the reusable analysis engine (Mode B).
    "Dataset Analysis": dataset_analysis.render,
    # Phase 6: additive page for the two-dataset comparison engine (Mode B).
    "Dataset Comparison": dataset_comparison.render,
    # Phase 7: additive page for the visualization workbench. The eight
    # research pages above are unchanged.
    "Advanced Visualization": advanced_visualization.render,
}


def _header(files: pd.DataFrame, folders: pd.DataFrame, findings: dict):
    st.title("DIGITAL ARCHAEOLOGY")
    st.markdown(
        "**Understanding the Evolution of Digital Work Through "
        "File-System Metadata**")
    scan = findings.get("scan", {})
    if scan:
        root_label = scan.get("root") or "unknown root"
        st.caption(
            f"Source: `{root_label}` · scan "
            f"`{scan.get('scan_id', 'n/a')}` · {scan.get('status', 'n/a')} · "
            f"{scan.get('files_scanned', len(files)):,} files · "
            f"{scan.get('folders_scanned', len(folders)):,} folders · "
            f"{human_size(scan.get('total_size_bytes', 0))} · "
            f"collected {scan.get('started_at', 'n/a')} · "
            "metadata only, no file contents read")
    else:
        st.caption(f"Loaded {len(files):,} file records.")


def _snapshot_panel(findings: dict):
    with st.expander("Historical snapshots", expanded=False):
        snaps = snapshot_manager.list_snapshots()
        if not snaps:
            st.info("No snapshots stored yet.")
        else:
            st.dataframe(
                pd.DataFrame([
                    {"snapshot_id": s.get("snapshot_id"),
                     "created_at": s.get("created_at"),
                     "files": s.get("total_files"),
                     "folders": s.get("total_folders"),
                     "storage": human_size(s.get("total_storage_bytes", 0)),
                     "errors": s.get("scan_errors"),
                     "valid": s.get("valid", True) is not False}
                    for s in snaps]),
                use_container_width=True, hide_index=True)

            n_invalid = sum(1 for s in snaps
                            if s.get("valid", True) is False)
            if n_invalid:
                st.warning(
                    f"{n_invalid} snapshot(s) are marked invalid (clobbered "
                    "scan of 2026-11-16 00:47:55). They are kept as evidence "
                    "but excluded from comparisons.")

            valid_ids = [s["snapshot_id"] for s in snaps
                         if s.get("valid", True) is not False]
            if len(valid_ids) < 2:
                st.caption("A historical comparison needs at least two valid "
                           "snapshots; only one valid snapshot exists so far.")
            else:
                c1, c2 = st.columns(2)
                with c1:
                    base = st.selectbox("Baseline snapshot", valid_ids,
                                        index=0)
                with c2:
                    comp = st.selectbox("Comparison snapshot", valid_ids,
                                        index=len(valid_ids) - 1)
                if st.button("Compare snapshots", type="primary"):
                    res = snapshot_manager.compare(base, comp)
                    if not res:
                        st.error("Comparison unavailable - a snapshot file is "
                                 "missing, unreadable or marked invalid.")
                    else:
                        k1, k2, k3, k4 = st.columns(4)
                        k1.metric("File count Δ", f"{res['file_count_delta']:+,}",
                                  f"{res['file_count_before']:,} → "
                                  f"{res['file_count_after']:,}")
                        k2.metric("Storage Δ",
                                  human_size(res["storage_delta_bytes"]),
                                  f"{human_size(res['storage_before_bytes'])} → "
                                  f"{human_size(res['storage_after_bytes'])}")
                        k3.metric("New files", f"{res['new_files_count']:,}")
                        k4.metric("Removed files",
                                  f"{res['removed_files_count']:,}")
                        m1, m2 = st.columns(2)
                        m1.metric("Modified files",
                                  f"{res['modified_files_count']:,}")
                        m2.metric("New folders",
                                  f"{res['new_folders_count']:,}")
                        with st.expander("New files (sample)"):
                            for p in res.get("new_files_sample", [])[:50]:
                                st.text(p)
                        with st.expander("Removed files (sample)"):
                            for p in res.get("removed_files_sample", [])[:50]:
                                st.text(p)
                        st.caption(
                            "Historical change claims are only presented "
                            "because two real snapshots exist.")

        st.markdown("---")
        st.markdown("**Create a new metadata snapshot**")
        st.caption("Runs a fresh read-only scan of the configured root, "
                   "re-runs the analyses and stores a snapshot for later "
                   "comparison. No file is modified.")
        if st.button("Create new snapshot", type="secondary"):
            with st.spinner("Scanning (read-only) ..."):
                try:
                    import pipeline
                    pipeline.setup_logging()
                    rep = pipeline.run_scan()
                    pipeline.run_analysis(rep)
                    st.success(
                        f"Snapshot stored: {rep['scan_id']} - "
                        f"{rep['files_scanned']:,} files, "
                        f"{human_size(rep['total_size_bytes'])}.")
                    st.cache_data.clear()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Snapshot creation failed: {exc}")


def _sidebar_workspace():
    """Display the active workspace and any override warning (Phase 8)."""
    desc = config.workspace_description()
    st.sidebar.caption(f"Workspace: `{desc['root']}`")
    if desc["env_active"]:
        st.sidebar.caption(f"Environment override: `{desc['env_var']}`")
    if desc["notice"]:
        st.sidebar.warning(desc["notice"])


def _sidebar_modes():
    """Short explanation of each application mode (Phase 8 UX)."""
    with st.sidebar.expander("Modes & pages", expanded=False):
        st.markdown(
            "**Digital Archaeology** - the eight research pages over the "
            "historical metadata scan: Overview, File Creation Timeline, "
            "Storage Analytics, File Type Analysis, Naming Pattern "
            "Analysis, Duplicate Analysis, Folder Structure, Research "
            "Findings.")
        st.markdown(
            "**Data Sources** - inspect and register new CSV, SQLite or "
            "folder inputs; metadata only, no file contents are read.")
        st.markdown(
            "**Dataset Analysis** - profile a registered dataset: "
            "statistics, data quality and distributions.")
        st.markdown(
            "**Dataset Comparison** - compare two datasets or two "
            "historical snapshots.")
        st.markdown(
            "**Advanced Visualization** - chart workbench (scatter, bar, "
            "histogram, pie, heatmap, PCA, t-SNE and more) for the "
            "currently loaded data.")


def main():
    # Workspace info first: it must be visible even on the first-run page
    # (before any data exists) so an override is never silent.
    _sidebar_workspace()

    if not ensure_data():
        st.stop()

    files, folders, errors, findings = load_data()
    _header(files, folders, findings)

    filters = sidebar_filters(files)
    files_f = apply_filters(files, filters)

    st.sidebar.markdown("---")
    page = st.sidebar.radio("Pages", list(PAGES.keys()))
    _sidebar_modes()
    st.sidebar.markdown("---")
    _snapshot_panel(findings)

    st.sidebar.markdown("---")
    st.sidebar.caption(
        "Privacy: metadata only · no contents read · local storage only · "
        "no cloud upload.")

    PAGES[page](files_f, folders, findings, filters)

    st.sidebar.markdown("---")
    st.sidebar.caption(
        f"Rows in view: {len(files_f):,} / {len(files):,} · "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M')}")


if __name__ == "__main__":
    main()
