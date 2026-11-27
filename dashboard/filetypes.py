"""Page 4 - File type analysis."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st


def render(files: pd.DataFrame, folders: pd.DataFrame, findings: dict,
           filters: dict):
    st.subheader("File Type Analysis")

    if files.empty:
        st.info("No file records for the current filters.")
        return

    work = files.copy()
    work["file_extension"] = work["file_extension"].replace("", "(no ext)")

    counts = (work["file_extension"].value_counts()
              .rename_axis("file_extension").reset_index(name="file_count"))
    counts["pct"] = (counts["file_count"] / counts["file_count"].sum() * 100)

    st.markdown("**Extension share (top 12 + other)**")
    top = counts.head(12).copy()
    if len(counts) > 12:
        rest = counts.iloc[12:]
        top = pd.concat([top, pd.DataFrame([{
            "file_extension": "other",
            "file_count": rest["file_count"].sum(),
            "pct": rest["pct"].sum()}])], ignore_index=True)
    fig = px.pie(top, names="file_extension", values="file_count",
                 hole=0.5, labels={"file_count": "Files"})
    fig.update_traces(textposition="inside", textinfo="percent+label")
    fig.update_layout(height=460)
    st.plotly_chart(fig, use_container_width=True)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**File count by extension (top 20)**")
        d = counts.head(20)
        fig = px.bar(d, x="file_count", y="file_extension", orientation="h",
                     labels={"file_count": "Files", "file_extension": ""})
        fig.update_layout(height=520, showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    with c2:
        st.markdown("**Storage by extension (top 20, MB)**")
        d = (work.groupby("file_extension")["file_size_bytes"].sum()
             .div(1024 ** 2).reset_index(name="size_mb")
             .sort_values("size_mb", ascending=False).head(20))
        fig = px.bar(d, x="size_mb", y="file_extension", orientation="h",
                     color="size_mb", color_continuous_scale="Teal",
                     labels={"size_mb": "MB", "file_extension": ""})
        fig.update_layout(height=520, coloraxis_showscale=False)
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("**File size distribution (log scale, bytes)**")
    sizes = work["file_size_bytes"].clip(lower=1)
    fig = px.histogram(work, x=sizes, nbins=60, log_x=True,
                       labels={"x": "File size (bytes, log scale)"},
                       color_discrete_sequence=["#4c78a8"])
    fig.update_layout(height=400, showlegend=False, xaxis_title="File size (bytes, log)",
                      yaxis_title="Files")
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("**File count by category**")
    cat = (work.groupby("file_category").size()
           .reset_index(name="file_count")
           .sort_values("file_count", ascending=False))
    fig = px.bar(cat, x="file_category", y="file_count", text="file_count",
                 color="file_category",
                 labels={"file_count": "Files", "file_category": ""})
    fig.update_layout(height=400, showlegend=False, xaxis_title="")
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("**Summary table (extension × count, %, storage)**")
    d = (work.groupby("file_extension")
         .agg(file_count=("file_size_bytes", "size"),
              total_size_bytes=("file_size_bytes", "sum"))
         .reset_index())
    d["count_pct"] = (d["file_count"] / d["file_count"].sum() * 100).round(3)
    d["size_mb"] = (d["total_size_bytes"] / (1024 ** 2)).round(3)
    d = d.sort_values("file_count", ascending=False)
    st.dataframe(d, use_container_width=True, hide_index=True)
