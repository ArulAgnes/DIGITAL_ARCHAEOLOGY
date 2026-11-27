"""Page 9 - Data Sources: select, validate, preview, confirm, register.

Connects the Phase 3 ingestion stack (validators, loaders, registry,
ProcessingContext) to the Streamlit app without touching the eight research
pages. The research arguments of ``render`` are accepted for the common page
signature and intentionally unused: this page works only with user-selected
sources and platform outputs under ``output/``.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

import config
from dashboard.common import human_size
from ingestion import workflow

_INSPECTION_KEY = "ds4_inspection"
_RESULT_KEY = "ds4_result"


def _reset_session() -> None:
    for key in list(st.session_state.keys()):
        if str(key).startswith("ds4_"):
            del st.session_state[key]


def _rel(path_str: str) -> str:
    try:
        return str(Path(path_str).relative_to(config.PROJECT_ROOT)
                   ).replace("\\", "/")
    except Exception:  # noqa: BLE001
        return str(path_str)


def _resolve_csv_input(upload, path_text: str) -> str:
    """Uploaded files are stored once as local copies; paths used as typed."""
    if upload is not None:
        signature = (upload.name, upload.size)
        if st.session_state.get("ds4_upload_sig") != signature:
            saved = workflow.save_upload(upload.name, upload.getvalue())
            st.session_state["ds4_upload_sig"] = signature
            st.session_state["ds4_upload_path"] = str(saved)
        return st.session_state["ds4_upload_path"]
    return (path_text or "").strip()


def _run_inspection(kind: str, resolved: str) -> None:
    with st.spinner("Inspecting (read-only, bounded) ..."):
        if kind == "CSV":
            insp = workflow.inspect_csv(resolved)
        elif kind == "SQLite":
            insp = workflow.inspect_sqlite(resolved)
        else:
            insp = workflow.inspect_folder(resolved)
    st.session_state[_INSPECTION_KEY] = insp
    st.session_state.pop(_RESULT_KEY, None)


def _render_validations(insp) -> None:
    errors = [v for v in insp.validations if v["level"] == "error"]
    warnings = [v for v in insp.validations if v["level"] == "warning"]
    for v in errors:
        st.error(v["message"])
    for v in warnings:
        st.warning(v["message"])


def _render_columns(insp) -> None:
    if not insp.columns:
        return
    st.markdown("**Columns**")
    st.dataframe(pd.DataFrame(insp.columns), hide_index=True,
                 use_container_width=True)


def _render_preview(insp) -> None:
    if insp.preview is None:
        return
    st.markdown("**Preview**")
    rows = st.number_input("Preview rows", min_value=5, max_value=200,
                           value=20, step=5, key="ds4_preview_rows")
    st.dataframe(insp.preview.head(int(rows)), hide_index=True,
                 use_container_width=True)
    st.caption(f"Bounded preview ({len(insp.preview)} rows read at most) - "
               "large files are never fully loaded just to show a preview.")


def _render_folder_details(insp) -> None:
    st.markdown("**Directory**")
    st.code(str(insp.path), language=None)
    e1, e2, e3 = st.columns(3)
    e1.metric("Top-level entries", f"{insp.extra.get('entry_count', 0):,}")
    e2.metric("Drive root", "yes" if insp.needs_drive_confirm else "no")
    e3.metric("Large scan", "yes" if insp.needs_large_ack else "no")
    entries = insp.extra.get("entries_preview") or []
    if entries:
        with st.expander("Top-level entries (first 50, no scan yet)"):
            st.dataframe(pd.DataFrame(entries), hide_index=True,
                         use_container_width=True)


def _render_sqlite_table_picker(insp) -> bool:
    """Returns True while the source still needs a table selection."""
    if not insp.extra.get("needs_table"):
        return False
    tables = insp.extra.get("tables", [])
    st.markdown("**Table selection**")
    st.selectbox("Table", tables, key="ds4_table")
    if st.button("Preview selected table", key="ds4_table_btn"):
        table = st.session_state.get("ds4_table")
        if table:
            _run_sqlite_with_table(str(insp.path), table)
        st.rerun()
    st.info("This database contains several tables - select one to "
            "preview and register it.")
    return True


def _run_sqlite_with_table(path: str, table: str) -> None:
    with st.spinner("Loading table (read-only) ..."):
        st.session_state[_INSPECTION_KEY] = workflow.inspect_sqlite(
            path, table=table)
    st.session_state.pop(_RESULT_KEY, None)


def _render_register(insp) -> None:
    st.markdown("---")
    st.markdown("**Confirmation & registration**")
    if insp.kind == "Folder":
        confirm = st.checkbox(
            "I confirm a metadata-only scan of this folder. File contents "
            "are never read; outputs go to output/datasets/.",
            key="ds4_confirm")
        drive_ack = True
        large_ack = True
        if insp.needs_drive_confirm:
            drive_ack = st.checkbox(
                f"I understand this scans an ENTIRE DRIVE ({insp.path}) "
                "and it is explicitly intended.",
                key="ds4_drive")
        if insp.needs_large_ack:
            large_ack = st.checkbox(
                "I understand this may be a large scan that can take a "
                "long time.",
                key="ds4_large")
        st.caption("You may still reset before registering. The scanner "
                   "does not support mid-scan cancellation - this is not "
                   "claimed.")
    else:
        confirm = st.checkbox(
            f"I confirm registering this {insp.kind} dataset. The source "
            "is opened read-only and is never modified.",
            key="ds4_confirm")
        drive_ack = True
        large_ack = True

    ready = bool(confirm and drive_ack and large_ack)
    if st.button("Register dataset", type="primary", disabled=not ready,
                 key="ds4_register"):
        with st.spinner("Registering (processing job) ..."):
            res = workflow.register(
                insp,
                confirm_drive=bool(insp.kind == "Folder" and drive_ack))
        st.session_state[_RESULT_KEY] = res
        st.rerun()


def _render_result() -> None:
    res = st.session_state.get(_RESULT_KEY)
    if res is None:
        return
    if not res.ok:
        st.error(res.error)
        return
    st.success("Dataset registered.")
    s = res.summary
    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Rows", "n/a" if s.get("row_count") is None
              else f"{s['row_count']:,}")
    r2.metric("Columns", s.get("column_count") or 0)
    r3.metric("Validation", s.get("validation_status", ""))
    r4.metric("Duration", f"{s.get('duration_seconds', 0):.2f}s")
    scan = s.get("scan") or {}
    if scan:
        f1, f2, f3 = st.columns(3)
        f1.metric("Files scanned", f"{scan.get('files_scanned', 0):,}")
        f2.metric("Folders scanned", f"{scan.get('folders_scanned', 0):,}")
        f3.metric("Scan errors", f"{scan.get('errors', 0):,}")
    st.caption(f"Job `{res.job_id}` · dataset `{res.dataset_id}`")
    st.caption(f"Metadata: `{_rel(res.metadata_path)}` · registry: "
               "`output/metadata/dataset_registry.db` · history below.")


def _render_history() -> None:
    with st.expander("Dataset history", expanded=False):
        rows = workflow.history()
        if not rows:
            st.caption("No datasets registered yet.")
            return
        df = pd.DataFrame(rows)
        wanted = [c for c in ("imported_at", "name", "source_type",
                              "row_count", "column_count",
                              "validation_status", "processing_status",
                              "dataset_id")
                  if c in df.columns]
        st.dataframe(df[wanted], hide_index=True, use_container_width=True)
        st.caption("Descriptors only - the registry never stores dataset "
                   "contents.")


def render(files: pd.DataFrame, folders: pd.DataFrame, findings: dict,
           filters: dict):
    """Common page entry point. Research arguments intentionally unused."""
    st.subheader("Data Sources")
    st.caption(
        "Load your own data locally: CSV, SQLite or a folder scan. "
        "Sources are opened read-only, previews are bounded, and every "
        "registration runs as its own job under `output/`. The research "
        "dashboard and its data are not affected.")

    kind = st.radio("Source type", workflow.SOURCE_CHOICES,
                    horizontal=True, key="ds4_kind")

    upload = None
    if kind == "CSV":
        upload = st.file_uploader(
            "CSV file - upload a copy (stored under input_data/csv/uploads/) "
            "or type a path below",
            type=["csv"], key="ds4_upload")
        path_text = st.text_input("CSV file path", key="ds4_path_csv",
                                  placeholder=r"D:\data\example.csv")
    elif kind == "SQLite":
        path_text = st.text_input("SQLite database path", key="ds4_path_db",
                                  placeholder=r"D:\data\example.db")
    else:
        path_text = st.text_input("Folder path", key="ds4_path_folder",
                                  placeholder=r"D:\projects\my_folder")

    b1, b2 = st.columns([1, 1])
    inspect_clicked = b1.button("Inspect source", type="primary",
                                key="ds4_inspect")
    if b2.button("Reset workflow", key="ds4_reset"):
        _reset_session()
        st.rerun()

    if inspect_clicked:
        resolved = (_resolve_csv_input(upload, path_text)
                    if kind == "CSV" else (path_text or "").strip())
        if not resolved:
            st.error("Enter a path (or choose a file to upload) first.")
            st.session_state.pop(_INSPECTION_KEY, None)
            st.session_state.pop(_RESULT_KEY, None)
        else:
            _run_inspection(kind, resolved)

    insp = st.session_state.get(_INSPECTION_KEY)
    if insp is None:
        st.info("Select a source and press **Inspect source**.")
        _render_history()
        return

    if not insp.ok:
        st.error(insp.error)
        _render_validations(insp)
        _render_history()
        return

    st.success(f"{insp.kind} validated - {insp.detection}")
    _render_validations(insp)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Rows", "scan pending" if insp.kind == "Folder"
              else ("n/a (large file)" if insp.row_count is None
                    else f"{insp.row_count:,}"))
    m2.metric("Columns", insp.column_count or 0)
    m3.metric("Size", human_size(insp.file_size)
              if insp.file_size is not None else "n/a")
    m4.metric("Validation", insp.validation_level.upper())

    if insp.kind == "Folder":
        _render_folder_details(insp)
    else:
        _render_columns(insp)

    if insp.kind == "SQLite" and _render_sqlite_table_picker(insp):
        _render_history()
        return

    _render_preview(insp)

    for w in insp.warnings:
        st.warning(w)

    _render_register(insp)
    _render_result()
    _render_history()
