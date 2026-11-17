"""Page 6 - Duplicate analysis (candidates vs verified)."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard.common import human_size


def render(files: pd.DataFrame, folders: pd.DataFrame, findings: dict,
           filters: dict):
    st.subheader("Duplicate Analysis")

    from analysis.duplicate_analysis import (duplicate_by_folder,
                                             load_candidates, load_verified)

    cand = load_candidates()
    ver = load_verified()
    dup = findings.get("duplicates", {})

    st.info(
        "**Level 1 = metadata candidates** (same name + extension + size at "
        "different paths). These are *not* confirmed duplicates.\n\n"
        "**Level 2 = SHA-256 verified** exact duplicates - "
        + ("enabled for this run." if dup.get("hash_verification_enabled")
           else "**not enabled** (default). Run "
                "`python pipeline.py --scan --hash` to verify.")
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Candidate groups (L1)", f"{dup.get('candidate_groups', 0):,}")
    c2.metric("Extra candidate copies",
              f"{dup.get('candidate_extra_copies', 0):,}")
    c3.metric("Verified groups (L2)", f"{dup.get('verified_groups', 0):,}")
    c4.metric("Potential extra-copy storage",
              human_size(dup.get("candidate_potential_bytes", 0)))

    if cand.empty:
        st.success("No metadata-level duplicate candidates under the current "
                   "scan.")
        return

    left, right = st.columns(2)

    with left:
        st.markdown("**Candidate groups by copy count**")
        d = (cand["copy_count"].value_counts().sort_index()
             .rename_axis("copies").reset_index(name="groups"))
        fig = px.bar(d, x="copies", y="groups", text="groups",
                     labels={"copies": "Copies per group",
                             "groups": "Groups"})
        fig.update_layout(height=380, showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    with right:
        st.markdown("**Candidate extra copies by extension**")
        cand = cand.copy()
        cand["extra"] = cand["copy_count"] - 1
        d = (cand.groupby("file_extension")["extra"].sum()
             .reset_index(name="extra_copies")
             .sort_values("extra_copies", ascending=False).head(15))
        d["file_extension"] = d["file_extension"].replace("", "(no ext)")
        fig = px.bar(d, x="file_extension", y="extra_copies",
                     text="extra_copies",
                     labels={"extra_copies": "Extra copies",
                             "file_extension": "Extension"})
        fig.update_layout(height=380, showlegend=False, xaxis_title="")
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("**Candidate storage at risk (MB by extension)**")
    cand["extra_bytes"] = cand["file_size_bytes"] * (cand["copy_count"] - 1)
    d = (cand.groupby("file_extension")["extra_bytes"].sum()
         .div(1024 ** 2).reset_index(name="extra_mb")
         .sort_values("extra_mb", ascending=False).head(15))
    d["file_extension"] = d["file_extension"].replace("", "(no ext)")
    fig = px.bar(d, x="extra_mb", y="file_extension", orientation="h",
                 labels={"extra_mb": "MB", "file_extension": ""})
    fig.update_layout(height=440, showlegend=False)
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("**Verified duplicate groups (SHA-256)**")
    if ver.empty:
        st.warning("No verified duplicates available - hash verification "
                   "was not enabled for this scan.")
    else:
        st.dataframe(
            ver[["duplicate_group_id", "file_name", "file_size_bytes",
                 "copy_count", "extra_copies", "extra_copy_bytes", "status"]],
            use_container_width=True, hide_index=True)

    st.markdown("**Duplicate location table (candidate folders)**")
    loc = duplicate_by_folder(cand, files)
    if not loc.empty:
        st.dataframe(loc.head(50), use_container_width=True, hide_index=True)
    else:
        st.info("No folder-level duplicate locations.")

    st.markdown("**All candidate groups**")
    show = cand[["duplicate_group_id", "file_name", "file_extension",
                 "file_size_bytes", "copy_count", "status", "paths"]].copy()
    st.dataframe(show.head(300), use_container_width=True, hide_index=True)
    st.caption(f"Showing {min(300, len(show)):,} of {len(show):,} candidate "
               "groups. No file is ever deleted automatically.")
