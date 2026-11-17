"""Page 7 - Folder structure."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from analysis.structural_analysis import (depth_distribution,
                                          empty_folders,
                                          files_per_folder_dist,
                                          deeply_nested)


def render(files: pd.DataFrame, folders: pd.DataFrame, findings: dict,
           filters: dict):
    st.subheader("Folder Structure")

    if folders.empty:
        st.info("No folder records available.")
        return

    roots = filters.get("roots") or []
    fr = folders
    if roots:
        mask = pd.Series(False, index=folders.index)
        for r in roots:
            mask |= folders["folder_path"].str.contains(
                rf"\{r}(\\|$)|^{r}$", case=False, regex=True, na=False)
        fr = folders[mask]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Folders (filtered roots)", f"{len(fr):,}")
    c2.metric("Max depth", f"{int(fr['folder_depth'].max()) if not fr.empty else 0:,}")
    c3.metric("Mean depth",
              f"{float(fr['folder_depth'].mean()) if not fr.empty else 0:.2f}")
    c4.metric("Empty folders",
              f"{int((fr['direct_file_count'] == 0).sum()) if not fr.empty else 0:,}")

    st.caption("Folder rows have no year/month attributes; year, month, "
               "extension, category and size filters apply to file charts "
               "only. Root-folder filter applies here.")

    st.markdown("**Folder depth distribution**")
    d = depth_distribution(fr)
    if not d.empty:
        fig = px.bar(d, x="folder_depth", y="folder_count", text="folder_count",
                     labels={"folder_depth": "Depth", "folder_count": "Folders"})
        fig.update_layout(height=380, showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    left, right = st.columns(2)
    with left:
        st.markdown("**Files per folder (distribution)**")
        d = files_per_folder_dist(fr)
        fig = px.bar(d, x="file_count_bucket", y="folder_count",
                     text="folder_count",
                     labels={"file_count_bucket": "Direct files",
                             "folder_count": "Folders"})
        fig.update_layout(height=380, showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    with right:
        st.markdown("**Folder storage treemap (top levels)**")
        tm = fr.nlargest(400, "total_size_bytes").copy()
        tm = tm[tm["total_size_bytes"] > 0]
        if not tm.empty:
            tm["label"] = tm["folder_name"].fillna(tm["folder_path"])
            fig = px.treemap(tm, path=["folder_path"], values="total_size_bytes",
                             color="total_size_bytes",
                             color_continuous_scale="Blues",
                             hover_data={"total_size_mb": ":.2f"})
            fig.update_layout(height=380)
            st.plotly_chart(fig, use_container_width=True)

    st.markdown("**Hierarchical folder tree (sunburst, top 300 by size)**")
    tree = fr.nlargest(300, "total_size_bytes").copy()
    if not tree.empty:
        parts = (tree["folder_path"].str.replace("D:\\", "", regex=False)
                 .str.split("\\"))
        max_len = max(len(p) for p in parts)
        for i in range(max_len):
            tree[f"L{i}"] = parts.apply(
                lambda p, i=i: p[i] if i < len(p) else None)
        lvl_cols = [f"L{i}" for i in range(max_len)]
        fig = px.sunburst(tree, path=lvl_cols, values="total_size_bytes",
                          color="folder_depth", color_continuous_scale="Viridis")
        fig.update_layout(height=600)
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("**Empty folders**")
    ef = empty_folders(fr)
    if ef.empty:
        st.success("No empty folders under the current root selection.")
    else:
        st.dataframe(
            ef[["folder_path", "folder_depth", "direct_subfolder_count"]]
            .head(100), use_container_width=True, hide_index=True)
        st.caption(f"Showing {min(100, len(ef)):,} of {len(ef):,} empty folders.")

    st.markdown("**Deeply nested folders (depth ≥ 8)**")
    dn = deeply_nested(fr, 8)
    if dn.empty:
        st.info("No folders at depth ≥ 8 under the current selection.")
    else:
        st.dataframe(
            dn[["folder_path", "folder_depth", "direct_file_count",
                "recursive_file_count", "total_size_mb"]].head(100),
            use_container_width=True, hide_index=True)

    st.markdown("**Folder records (sorted by direct files)**")
    cols = ["folder_path", "folder_depth", "direct_file_count",
            "recursive_file_count", "direct_subfolder_count",
            "recursive_folder_count", "total_size_mb"]
    st.dataframe(fr.sort_values("direct_file_count", ascending=False)[cols]
                 .head(300), use_container_width=True, hide_index=True)
