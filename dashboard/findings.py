"""Page 8 - Research findings (auto-generated from collected data)."""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from dashboard.common import human_size


def render(files: pd.DataFrame, folders: pd.DataFrame, findings: dict,
           filters: dict):
    st.subheader("Research Findings")

    if not findings:
        st.warning("No findings file found. Run `python pipeline.py "
                   "--analyse-only` to generate findings.json.")
        return

    scan = findings.get("scan", {})
    temporal = findings.get("temporal", {})
    storage = findings.get("storage", {})
    ftypes = findings.get("filetypes", {})
    naming = findings.get("naming", {})
    dup = findings.get("duplicates", {})
    struct = findings.get("structure", {})
    clean = findings.get("cleaning", {})

    st.markdown("### Main findings")

    if scan:
        default_root = "D:" + "\\"
        root_label = scan.get("root") or default_root
        st.markdown(
            f"A metadata scan of `{root_label}` completed "
            f"**{scan.get('status', 'n/a')}** on "
            f"{scan.get('started_at', 'n/a')}, recording "
            f"**{scan.get('files_scanned', 0):,} files** across "
            f"**{scan.get('folders_scanned', 0):,} folders** occupying "
            f"**{human_size(scan.get('total_size_bytes', 0))}**, with "
            f"**{scan.get('scan_errors', 0):,} recorded errors** in "
            f"{scan.get('duration_seconds', 0):.0f} seconds.")

    if temporal:
        st.markdown(
            f"**File creation trends.** File-system creation dates span "
            f"{temporal.get('creation_year_min', 'n/a')} to "
            f"{temporal.get('creation_year_max', 'n/a')}. The peak creation "
            f"year is **{temporal.get('peak_creation_year', 'n/a')}** with "
            f"{temporal.get('peak_creation_year_count', 0):,} files, while "
            f"peak modification activity falls in "
            f"**{temporal.get('peak_modification_year', 'n/a')}** "
            f"({temporal.get('peak_modification_year_count', 0):,} files). "
            "These are record dates on this volume; Windows changes creation "
            "time when files are copied, so they are not verified authoring "
            "dates.")

    if storage:
        st.markdown(
            f"**Storage.** Mean file size {human_size(storage.get('mean_bytes', 0))}, "
            f"median {human_size(storage.get('median_bytes', 0))}, largest "
            f"{human_size(storage.get('max_bytes', 0))}, total "
            f"{human_size(storage.get('total_bytes', 0))}.")

    if ftypes:
        st.markdown(
            f"**Most frequently used file types.** "
            f"{ftypes.get('unique_extensions', 0)} unique extensions and "
            f"{ftypes.get('unique_categories', 0)} categories appear. The "
            f"most common extension is "
            f"`{ftypes.get('most_common_extension', 'n/a')}` "
            f"({ftypes.get('most_common_extension_count', 0):,} files); the "
            f"largest storage consumer is "
            f"`{ftypes.get('largest_storage_extension', 'n/a')}` "
            f"({human_size(ftypes.get('largest_storage_extension_bytes', 0))}).")

    if naming:
        st.markdown(
            f"**Naming consistency.** Of {naming.get('total_files_analysed', 0):,} "
            f"file names, {naming.get('version_indicator_count', 0):,} "
            f"({naming.get('version_indicator_pct', 0)}%) carry version "
            f"tokens, {naming.get('final_or_latest_count', 0):,} "
            f"({naming.get('final_or_latest_pct', 0)}%) contain final/latest "
            f"keywords, {naming.get('date_indicator_count', 0):,} contain "
            f"date-like tokens and {naming.get('copy_indicator_count', 0):,} "
            f"carry copy indicators. "
            f"{naming.get('mixed_capitalization_count', 0):,} names use mixed "
            f"capitalisation; {naming.get('inconsistent_files', 0):,} "
            f"({naming.get('inconsistent_pct', 0)}%) trigger at least one of "
            "the nine documented rules.")

    if dup:
        st.markdown(
            f"**Duplicate analysis.** {dup.get('candidate_groups', 0):,} "
            "metadata-level candidate groups were found "
            f"({dup.get('candidate_extra_copies', 0):,} extra copies, up to "
            f"{human_size(dup.get('candidate_potential_bytes', 0))}). "
            + ("SHA-256 verification was enabled: "
               f"{dup.get('verified_groups', 0):,} verified groups."
               if dup.get("hash_verification_enabled") else
               "SHA-256 verification was **not** enabled, so none of these "
               "are confirmed duplicates.")
            + " No file was deleted.")

    if struct:
        st.markdown(
            f"**Structure.** {struct.get('total_folders', 0):,} folders, "
            f"maximum depth {struct.get('max_depth', 'n/a')}, mean depth "
            f"{struct.get('avg_depth', 'n/a')}, "
            f"{struct.get('empty_folders', 0):,} empty folders and "
            f"{struct.get('deep_folders_ge8', 0):,} folders at depth ≥ 8.")

    st.markdown("### Key observations")
    obs = []
    if not files.empty:
        top_cat = (files.groupby("file_category")["file_size_bytes"].sum()
                   .sort_values(ascending=False))
        if not top_cat.empty:
            share = top_cat.iloc[0] / top_cat.sum() * 100
            obs.append(
                f"Storage is concentrated: `{top_cat.index[0]}` alone accounts "
                f"for {share:.1f}% of the measured bytes under the current "
                "data.")
        top_root = (files.groupby("root_folder")["file_size_bytes"].sum()
                    .sort_values(ascending=False))
        if not top_root.empty:
            share = top_root.iloc[0] / top_root.sum() * 100
            obs.append(
                f"The largest top-level grouping `{top_root.index[0]}` holds "
                f"{share:.1f}% of scanned bytes.")
        if "creation_year" in files:
            yr = files["creation_year"].dropna()
            if not yr.empty:
                obs.append(
                    f"Creation-dated files cluster around "
                    f"{int(yr.mode().iloc[0])} (mode year); "
                    f"{int((files['creation_year'].isna()).sum()):,} files "
                    "have no usable creation date.")
        zero = int((files["file_size_bytes"] == 0).sum())
        if zero:
            obs.append(f"{zero:,} files are zero bytes.")
    if dup.get("candidate_groups", 0) == 0:
        obs.append("No metadata-level duplicate candidates were detected.")
    if struct.get("empty_folders", 0):
        obs.append(f"{struct['empty_folders']:,} folders contain no direct "
                   "files - possible scaffolding or abandoned structure.")
    for o in obs:
        st.markdown(f"- {o}")

    st.markdown("### Largest storage-consuming categories")
    if not files.empty:
        d = (files.groupby("file_category")
             .agg(files=("file_size_bytes", "size"),
                  size_bytes=("file_size_bytes", "sum"))
             .reset_index().sort_values("size_bytes", ascending=False))
        d["size"] = d["size_bytes"].apply(human_size)
        st.dataframe(d[["file_category", "files", "size"]],
                     use_container_width=True, hide_index=True)

    st.markdown("### Limitations of the collected data")
    st.markdown(
        "- Windows creation time is a volume record date, not a proven "
        "original authoring date.\n"
        "- File contents were never read; subject matter is unknown.\n"
        "- One scan = one cross-section; change over time requires further "
        "snapshots.\n"
        "- Duplicate candidates are metadata matches only unless SHA-256 "
        "verification is explicitly enabled.\n"
        "- Permission-denied and locked paths are excluded from totals and "
        "logged in `data/raw/scan_errors.csv`.\n"
        "- Symlinks/junctions are not followed; linked content is invisible "
        "to this scan.\n"
        "- Top-level folders are *inferred* groupings, not verified projects."
    )

    st.markdown("### Data quality checks")
    st.markdown(
        f"- Raw rows: {clean.get('raw_file_rows', 'n/a'):,} | processed: "
        f"{clean.get('processed_file_rows', 'n/a'):,}"
        if isinstance(clean.get("raw_file_rows"), int) else
        f"- Raw rows: {clean.get('raw_file_rows', 'n/a')}")
    for k in ("missing_size_rows", "zero_size_rows", "duplicate_record_rows",
              "invalid_creation_dates", "invalid_modified_dates",
              "depth_mismatches", "error_rows"):
        v = clean.get(k, None)
        if v is not None:
            st.markdown(f"- {k}: **{v:,}**" if isinstance(v, int)
                        else f"- {k}: **{v}**")

    with st.expander("Raw findings.json"):
        st.json(json.dumps(findings, indent=2, default=str))
