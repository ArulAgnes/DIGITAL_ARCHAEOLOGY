"""Page 5 - Naming pattern analysis."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from analysis.naming_analysis import (RULES, build_naming_table,
                                      issue_counts, pattern_distribution)


def render(files: pd.DataFrame, folders: pd.DataFrame, findings: dict,
           filters: dict):
    st.subheader("Naming Pattern Analysis")

    if files.empty:
        st.info("No file records for the current filters.")
        return

    naming = build_naming_table(files)
    stats = findings.get("naming", {})

    with st.expander("Documented naming rules (R1-R9)", expanded=False):
        for k, v in RULES.items():
            st.markdown(f"- **{k}** - {v}")
        st.caption("Files containing 'final' are counted as indicators only; "
                   "they are not automatically classified as errors.")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Files analysed", f"{len(naming):,}")
    c2.metric("Version indicators",
              f"{int(naming['contains_version_indicator'].sum()):,}")
    c3.metric("Final/latest keywords",
              f"{int(naming['contains_final_keyword'].sum()):,}")
    c4.metric("Date indicators",
              f"{int(naming['contains_date_indicator'].sum()):,}")

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Copy indicators",
              f"{int(naming['contains_copy_indicator'].sum()):,}")
    c6.metric("Mixed capitalisation",
              f"{int((naming['capitalization_style'] == 'mixed').sum()):,}")
    c7.metric("Files with ≥1 flag",
              f"{int(naming['is_inconsistent'].sum()):,}")
    c8.metric("Mean name length",
              f"{float(naming['filename_length'].mean()):.1f} chars")

    left, right = st.columns(2)

    with left:
        st.markdown("**Naming convention distribution**")
        d = pattern_distribution(naming)
        fig = px.bar(d, x="naming_pattern", y="file_count", text="file_count",
                     labels={"naming_pattern": "Pattern",
                             "file_count": "Files"})
        fig.update_layout(height=420, showlegend=False,
                          xaxis={"tickangle": -35})
        st.plotly_chart(fig, use_container_width=True)

    with right:
        st.markdown("**Capitalisation styles**")
        d = (naming["capitalization_style"].value_counts()
             .rename_axis("style").reset_index(name="file_count"))
        fig = px.pie(d, names="style", values="file_count", hole=0.45)
        fig.update_traces(textinfo="percent+label")
        fig.update_layout(height=420)
        st.plotly_chart(fig, use_container_width=True)

    left, right = st.columns(2)
    with left:
        st.markdown("**Version indicator analysis (R1)**")
        d = (naming.groupby("file_extension")["contains_version_indicator"]
             .sum().reset_index(name="count")
             .sort_values("count", ascending=False).head(15))
        d["file_extension"] = d["file_extension"].replace("", "(no ext)")
        fig = px.bar(d, x="file_extension", y="count", text="count",
                     labels={"count": "Files with version token",
                             "file_extension": "Extension"})
        fig.update_layout(height=400, showlegend=False, xaxis_title="")
        st.plotly_chart(fig, use_container_width=True)

    with right:
        st.markdown("**Final / latest keyword analysis (R2+R3)**")
        d = (naming.groupby("file_extension")["contains_final_keyword"]
             .sum().reset_index(name="count")
             .sort_values("count", ascending=False).head(15))
        d["file_extension"] = d["file_extension"].replace("", "(no ext)")
        fig = px.bar(d, x="file_extension", y="count", text="count",
                     color_discrete_sequence=["#e45756"],
                     labels={"count": "Files with final/latest",
                             "file_extension": "Extension"})
        fig.update_layout(height=400, showlegend=False, xaxis_title="")
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("**Naming inconsistency chart (issue counts)**")
    iss = issue_counts(naming)
    if not iss.empty:
        fig = px.bar(iss, x="file_count", y="issue", orientation="h",
                     labels={"file_count": "Files", "issue": ""})
        fig.update_layout(height=max(320, 28 * len(iss)), showlegend=False)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No naming issues flagged for the current filters.")

    st.markdown("**Searchable filename table**")
    q = st.text_input("Search file names", "")
    view = naming
    if q:
        view = view[view["file_name"].str.contains(q, case=False, na=False)]
    view = view.sort_values("inconsistency_count", ascending=False)
    cols = ["file_name", "file_extension", "naming_pattern",
            "contains_version_indicator", "contains_final_keyword",
            "contains_date_indicator", "contains_copy_indicator",
            "capitalization_style", "filename_length", "naming_issues",
            "full_file_path"]
    st.dataframe(view[cols].head(500), use_container_width=True,
                 hide_index=True)
    st.caption(f"Showing {min(500, len(view)):,} of {len(view):,} rows.")
