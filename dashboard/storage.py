"""Page 3 - Storage analytics."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from dashboard.common import human_size


def render(files: pd.DataFrame, folders: pd.DataFrame, findings: dict,
           filters: dict):
    st.subheader("Storage Analytics")

    if files.empty:
        st.info("No file records for the current filters.")
        return

    total = int(files["file_size_bytes"].sum())
    mean_s = float(files["file_size_bytes"].mean())
    med_s = float(files["file_size_bytes"].median())
    mx_s = int(files["file_size_bytes"].max())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total (filtered)", human_size(total))
    c2.metric("Mean file size", human_size(mean_s))
    c3.metric("Median file size", human_size(med_s))
    c4.metric("Largest file", human_size(mx_s))

    st.caption("Folder-level charts use the root-folder filter only "
               "(folder rows have no year/month attributes).")

    left, right = st.columns(2)

    with left:
        st.markdown("**Storage by root folder (GB)**")
        d = (files.groupby("root_folder")["file_size_bytes"].sum()
             .div(1024 ** 3).reset_index(name="size_gb")
             .sort_values("size_gb", ascending=False).head(20))
        fig = px.bar(d, x="size_gb", y="root_folder", orientation="h",
                     labels={"size_gb": "GB", "root_folder": ""})
        fig.update_layout(height=520, showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    with right:
        st.markdown("**Storage by extension (top 15, GB)**")
        d = (files.groupby("file_extension")["file_size_bytes"].sum()
             .div(1024 ** 3).reset_index(name="size_gb")
             .sort_values("size_gb", ascending=False).head(15))
        d["file_extension"] = d["file_extension"].replace("", "(none)")
        fig = px.bar(d, x="size_gb", y="file_extension", orientation="h",
                     color="size_gb", color_continuous_scale="Blues",
                     labels={"size_gb": "GB", "file_extension": ""})
        fig.update_layout(height=520, coloraxis_showscale=False)
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("**Storage by category (GB)**")
    d = (files.groupby("file_category")["file_size_bytes"].sum()
         .div(1024 ** 3).reset_index(name="size_gb")
         .sort_values("size_gb", ascending=False))
    fig = px.bar(d, x="file_category", y="size_gb", text="size_gb",
                 color="file_category", labels={"size_gb": "GB",
                                                "file_category": ""})
    fig.update_layout(height=400, showlegend=False, xaxis_title="")
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("**Treemap of storage distribution (root folder → category)**")
    tm = (files.groupby(["root_folder", "file_category"])["file_size_bytes"]
          .sum().div(1024 ** 3).reset_index(name="size_gb"))
    tm = tm[tm["size_gb"] > 0]
    if not tm.empty:
        fig = px.treemap(tm, path=["root_folder", "file_category"],
                         values="size_gb", color="size_gb",
                         color_continuous_scale="Viridis",
                         labels={"size_gb": "GB"})
        fig.update_layout(height=560)
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("**Largest files vs largest folders**")
    col1, col2 = st.columns(2)
    with col1:
        tf = files.nlargest(10, "file_size_bytes")
        tf = tf.sort_values("file_size_bytes")
        fig = px.bar(tf, x="file_size_mb", y="file_name", orientation="h",
                     labels={"file_size_mb": "MB", "file_name": ""})
        fig.update_layout(height=440, showlegend=False)
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        if not folders.empty:
            roots = filters.get("roots") or None
            fr = folders
            if roots:
                fr = fr[fr["folder_name"].isin(roots) |
                        fr["folder_path"].str.contains(
                            "|".join(roots), regex=True, na=False)]
            top = fr.nlargest(10, "total_size_bytes").sort_values(
                "total_size_bytes")
            fig = px.bar(top, x="total_size_mb", y="folder_name",
                         orientation="h",
                         labels={"total_size_mb": "MB", "folder_name": ""})
            fig.update_layout(height=440, showlegend=False)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No folder data.")

    if not folders.empty:
        st.markdown("**Top folders by recursive size**")
        cols = ["folder_path", "folder_depth", "recursive_file_count",
                "total_size_mb"]
        st.dataframe(folders.nlargest(20, "total_size_bytes")[cols],
                     use_container_width=True, hide_index=True)
