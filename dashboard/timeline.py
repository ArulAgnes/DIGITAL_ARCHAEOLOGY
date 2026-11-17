"""Page 2 - File creation / modification timeline."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


def render(files: pd.DataFrame, folders: pd.DataFrame, findings: dict,
           filters: dict):
    st.subheader("File Creation Timeline")

    if files.empty:
        st.info("No file records for the current filters.")
        return

    temp = findings.get("temporal", {})
    if temp:
        st.caption(
            f"File-system record dates observed: creation "
            f"{temp.get('creation_year_min', 'n/a')}–{temp.get('creation_year_max', 'n/a')}, "
            f"peak creation year {temp.get('peak_creation_year', 'n/a')}. "
            "Windows creation time changes when a file is copied - these are "
            "record dates on this volume, not verified authoring dates.")

    monthly = (files.dropna(subset=["creation_year_month"])
               .groupby("creation_year_month").size()
               .reset_index(name="file_count"))
    yearly = (files.dropna(subset=["creation_year"])
              .groupby("creation_year").size().reset_index(name="file_count"))
    mod_monthly = (files.dropna(subset=["modified_year_month"])
                   .groupby("modified_year_month").size()
                   .reset_index(name="file_count"))
    mod_yearly = (files.dropna(subset=["modified_year"])
                  .groupby("modified_year").size().reset_index(name="file_count"))

    st.markdown("**Monthly file creation (file-system creation date)**")
    if not monthly.empty:
        fig = px.line(monthly, x="creation_year_month", y="file_count",
                      markers=True,
                      labels={"creation_year_month": "Year-Month",
                              "file_count": "Files created"})
        fig.update_layout(height=380, xaxis_title="", yaxis_title="Files",
                          xaxis={"tickangle": -45})
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No creation dates available for these filters.")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Yearly file creation**")
        if not yearly.empty:
            fig = px.bar(yearly, x="creation_year", y="file_count",
                         text="file_count",
                         labels={"creation_year": "Year",
                                 "file_count": "Files"})
            fig.update_layout(height=360, showlegend=False)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No data.")

    with c2:
        st.markdown("**Yearly modification activity**")
        if not mod_yearly.empty:
            fig = px.bar(mod_yearly, x="modified_year", y="file_count",
                         text="file_count", color_discrete_sequence=["#ff7f0e"],
                         labels={"modified_year": "Year",
                                 "file_count": "Files modified"})
            fig.update_layout(height=360, showlegend=False)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No data.")

    st.markdown("**Monthly modification activity**")
    if not mod_monthly.empty:
        fig = px.area(mod_monthly, x="modified_year_month", y="file_count",
                      labels={"modified_year_month": "Year-Month",
                              "file_count": "Files modified"})
        fig.update_layout(height=360, xaxis_title="", yaxis_title="Files",
                          xaxis={"tickangle": -45})
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No modification dates available for these filters.")

    st.markdown("**Creation versus modification (by year)**")
    cmp = pd.merge(
        yearly.rename(columns={"creation_year": "year",
                               "file_count": "created"}),
        mod_yearly.rename(columns={"modified_year": "year",
                                   "file_count": "modified"}),
        on="year", how="outer").fillna(0).sort_values("year")
    if not cmp.empty:
        for col in ("created", "modified"):
            cmp[col] = cmp[col].astype(int)
        fig = go.Figure()
        fig.add_bar(x=cmp["year"], y=cmp["created"], name="Creation-dated")
        fig.add_bar(x=cmp["year"], y=cmp["modified"], name="Modification-dated")
        fig.update_layout(barmode="group", height=400,
                          xaxis_title="Year", yaxis_title="Files",
                          legend_title="")
        st.plotly_chart(fig, use_container_width=True)
        with st.expander("View data table"):
            st.dataframe(cmp, use_container_width=True, hide_index=True)
