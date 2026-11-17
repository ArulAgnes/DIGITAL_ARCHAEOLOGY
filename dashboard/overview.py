"""Page 1 - Overview KPIs and summary charts."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from dashboard.common import human_size


def _kpi(label: str, value: str, delta: str = ""):
    st.metric(label, value, delta)


def render(files: pd.DataFrame, folders: pd.DataFrame, findings: dict,
           filters: dict):
    st.subheader("Overview")

    dup = findings.get("duplicates", {})
    struct = findings.get("structure", {})

    total_files = len(files)
    total_folders = len(folders) if not folders.empty else struct.get("total_folders", 0)
    total_storage = int(files["file_size_bytes"].sum()) if total_files else 0
    uniq_ext = files["file_extension"].nunique() if total_files else 0
    cand_groups = dup.get("candidate_groups", 0)
    ver_groups = dup.get("verified_groups", 0)
    max_depth = int(folders["folder_depth"].max()) if not folders.empty \
        else struct.get("max_depth", 0)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Files Scanned", f"{total_files:,}")
    c2.metric("Total Folders", f"{total_folders:,}")
    c3.metric("Total Storage", human_size(total_storage))
    c4.metric("Unique File Extensions", f"{uniq_ext:,}")

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Duplicate Candidates", f"{cand_groups:,}")
    c6.metric("Verified Duplicate Groups", f"{ver_groups:,}")
    c7.metric("Maximum Folder Depth", f"{max_depth:,}")
    c8.metric("Scan Errors", f"{findings.get('scan', {}).get('scan_errors', 0):,}")

    st.caption("Filters above apply to every chart on this page.")

    if files.empty:
        st.info("No file records for the current filters.")
        return

    left, right = st.columns(2)

    with left:
        st.markdown("**File category distribution (count)**")
        cat = (files.groupby("file_category").size()
               .reset_index(name="file_count")
               .sort_values("file_count", ascending=False))
        fig = px.bar(cat, x="file_category", y="file_count",
                     color="file_category", text="file_count",
                     labels={"file_category": "Category",
                             "file_count": "Files"})
        fig.update_layout(showlegend=False, height=420,
                          xaxis_title="", yaxis_title="Files")
        st.plotly_chart(fig, use_container_width=True)

    with right:
        st.markdown("**Storage distribution by category (GB)**")
        stg = (files.groupby("file_category")["file_size_bytes"].sum()
               .reset_index())
        stg.columns = ["file_category", "size_bytes"]
        if float(stg["size_bytes"].sum()) <= 0:
            st.info("No storage to display for the current filters.")
        else:
            stg["size_gb"] = stg["size_bytes"] / (1024 ** 3)
            stg = stg.sort_values("size_gb", ascending=False)
            fig = px.pie(stg, names="file_category", values="size_gb",
                         hole=0.45, labels={"size_gb": "GB"})
            fig.update_traces(textposition="inside", textinfo="percent+label")
            fig.update_layout(height=420)
            st.plotly_chart(fig, use_container_width=True)

    if not folders.empty:
        st.markdown("**Top 10 largest folders (GB)**")
        tf = folders.nlargest(10, "total_size_bytes").copy()
        tf["size_gb"] = tf["total_size_bytes"] / (1024 ** 3)
        tf = tf.sort_values("size_gb")
        fig = px.bar(tf, x="size_gb", y="folder_name", orientation="h",
                     color="size_gb", color_continuous_scale="Viridis",
                     hover_data={"folder_path": True, "size_gb": ":.3f"},
                     labels={"size_gb": "GB", "folder_name": ""})
        fig.update_layout(height=460, coloraxis_showscale=False,
                          xaxis_title="Gigabytes")
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("**Top 10 largest files**")
    top = files.nlargest(10, "file_size_bytes")[
        ["file_name", "file_extension", "file_size_mb", "file_category",
         "full_file_path", "modified_date"]].rename(
        columns={"file_size_mb": "Size (MB)"})
    st.dataframe(top, use_container_width=True, hide_index=True)
