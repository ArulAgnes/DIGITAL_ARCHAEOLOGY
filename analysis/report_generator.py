"""Automatic generation of the research report and data dictionary.

Every number written here is read from the collected data (findings.json /
CSVs). Nothing is invented; where evidence is insufficient the text says so.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

import config
from analysis import (filetype_analysis, naming_analysis, storage_analysis,
                      structural_analysis, temporal_analysis)
from analysis.data_cleaning import load_raw

log = logging.getLogger("da.report")


def _load_findings() -> dict:
    if config.FINDINGS_JSON.exists():
        return json.loads(config.FINDINGS_JSON.read_text(encoding="utf-8"))
    return {}


def _human(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or u == "TB":
            return f"{n:,.2f} {u}"
        n /= 1024
    return f"{n:,.2f} TB"


def _md_table(df: pd.DataFrame, max_rows: int = 15) -> str:
    if df is None or df.empty:
        return "*No data available for this table.*\n"
    d = df.head(max_rows).copy()
    rows = [[str(v) for v in row] for row in d.itertuples(index=False,
                                                          name=None)]
    header = "| " + " | ".join(str(c) for c in d.columns) + " |"
    sep = "|" + "|".join(["---"] * len(d.columns)) + "|"
    body = "\n".join("| " + " | ".join(r) + " |" for r in rows)
    note = "" if len(df) <= max_rows else (
        f"\n\n*Table truncated to {max_rows} of {len(df)} rows.*\n")
    return f"{header}\n{sep}\n{body}\n{note}"


def generate_all(findings: dict | None = None) -> None:
    findings = findings or _load_findings()
    generate_report(findings)
    generate_data_dictionary()
    log.info("Reports written to %s", config.REPORTS_DIR)


# --------------------------------------------------------------------------- #
def generate_report(f: dict) -> None:
    files, folders, errors = load_raw()
    files_p = (pd.read_csv(config.PROCESSED_FILES_CSV, encoding="utf-8-sig")
               if config.PROCESSED_FILES_CSV.exists() else files)
    folders_p = (pd.read_csv(config.PROCESSED_FOLDERS_CSV, encoding="utf-8-sig")
                 if config.PROCESSED_FOLDERS_CSV.exists() else folders)

    scan = f.get("scan", {})
    cleaning = f.get("cleaning", {})
    temporal = f.get("temporal", {})
    storage = f.get("storage", {})
    ftypes = f.get("filetypes", {})
    naming = f.get("naming", {})
    dup = f.get("duplicates", {})
    struct = f.get("structure", {})

    created = scan.get("started_at", "unknown")
    n_files = scan.get("files_scanned", cleaning.get("processed_file_rows", 0))
    n_folders = scan.get("folders_scanned", struct.get("total_folders", 0))
    total_bytes = scan.get("total_size_bytes", storage.get("total_bytes", 0))
    n_errors = scan.get("scan_errors", cleaning.get("error_rows", 0))
    duration = scan.get("duration_seconds", 0)

    # pre-computed tables
    cat_table = filetype_analysis.category_distribution(files_p)
    ext_table = filetype_analysis.extension_storage(files_p)
    root_table = storage_analysis.storage_by_root(files_p)
    year_table = temporal_analysis.creation_by_year(files_p)
    top_files = storage_analysis.largest_files(files_p, 10)
    top_folders = storage_analysis.storage_by_folder(folders_p, 10)
    pattern_table = naming_analysis.pattern_distribution(
        naming_analysis.build_naming_table(files_p)) \
        if not files_p.empty else pd.DataFrame()
    depth_table = structural_analysis.depth_distribution(folders_p)
    projects = structural_analysis.project_groups(files_p, folders_p)

    empty_folders = struct.get("empty_folders", 0)
    scan_root = str(scan.get("root", "D:\\"))
    sys_excl = ", ".join("`" + e + "`" for e in scan.get("system_exclusions", []))
    peak_c_year = temporal.get("peak_creation_year", "n/a")
    peak_c_count = temporal.get("peak_creation_year_count", 0)
    peak_m_year = temporal.get("peak_modification_year", "n/a")
    peak_m_count = temporal.get("peak_modification_year_count", 0)

    scan_errors_txt = "none"
    if not errors.empty and "error_type" in errors:
        ec = errors["error_type"].value_counts().head(15)
        scan_errors_txt = "\n".join(f"- `{k}`: {v:,}" for k, v in ec.items())

    # honest limitation statements
    limitations = [
        "Windows file-system **creation time** (`st_ctime` on Windows) reflects "
        "the last time the file record changed on this volume (including when "
        "a file was copied or restored). It is **not** guaranteed to be the "
        "original authoring date of the document.",
        "File contents were never opened, so document subject matter, authors "
        "and in-document dates are unknown.",
        "Only `D:\\` at the moment of the scan was observed: this is a "
        "cross-sectional snapshot, not a continuous historical record.",
        "Duplicate verification by SHA-256 was "
        + ("**enabled** for this run." if dup.get("hash_verification_enabled")
           else "**not enabled** (default). Candidate groups are metadata "
                "matches only and must not be reported as confirmed duplicates."),
        "Inaccessible, permission-denied or locked paths are recorded in "
        f"`reports/scan_report.json` and `data/raw/scan_errors.csv` "
        f"({n_errors:,} recorded events) and are excluded from totals.",
        "Hidden files were "
        + ("**included**." if scan.get("hidden_files_included", True)
           else "**excluded**."),
        "Top-level folders are reported as *inferred groupings*, not verified "
        "projects: no folder was confirmed as a project by any means other "
        "than its name and path.",
        "Symbolic links, junctions and directory reparse points were not "
        "followed, so linked content outside the boundary is not represented.",
    ]

    md = f"""# DIGITAL ARCHAEOLOGY
## Understanding the Evolution of Digital Work Through File-System Metadata

**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**Scan identifier:** `{scan.get('scan_id', 'n/a')}`
**Scan status:** {scan.get('status', 'n/a')}

---

## 1. Abstract

This study collects original file-system metadata from a real Windows
workstation volume (`D:\\`) without reading any document contents, and uses
that metadata to examine how accumulated digital work is structured. Across
**{n_files:,} files** and **{n_folders:,} folders** occupying
**{_human(total_bytes)}**, the analysis describes temporal activity,
storage distribution, file-type usage, naming conventions, duplicate
candidates and folder structure. All figures in this report are derived
exclusively from the collected records; no synthetic data is used. Where the
evidence does not support a conclusion, that is stated explicitly.

## 2. Research Problem

Personal and academic digital work accumulates silently. File systems retain
timestamps, sizes, names and hierarchical placement that together form a
partial, imperfect archive of working behaviour. Without systematic
inspection these signals remain unused, leaving questions about growth,
organisation, redundancy and convention unanswered.

## 3. Research Question

> **What can the structure of our accumulated digital files reveal about how
> our work evolves?**

Sub-questions:

1. How has file creation and modification activity changed over time?
2. How much storage does each folder, category and format consume?
3. Which file types dominate by count and by bytes?
4. How deep and how dense is the folder structure?
5. How consistent are naming conventions according to documented rules?
6. How many duplicate candidates accumulate, and where?

## 4. Research Objectives

1. Collect original, non-synthetic file-system metadata from `D:\\`.
2. Quantify temporal, storage, structural and categorical properties.
3. Apply transparent, pre-declared naming rules to real file names.
4. Identify metadata-level duplicate candidates without reading contents.
5. Present the results in an interactive dashboard with working filters.
6. Document methodology, limitations and privacy considerations precisely.

## 5. Data Collection Methodology

| Aspect | Setting |
|---|---|
| Scan root | `{scan_root}` |
| Workspace excluded | `{scan.get('workspace_excluded', str(config.WORKSPACE_EXCLUSION))}` |
| System exclusions | {sys_excl} |
| Hidden files included | {scan.get('hidden_files_included', True)} |
| Symlinks / junctions followed | {scan.get('symlinks_followed', False)} |
| Administrator privileges used | {scan.get('admin_privileges_used', False)} |
| File contents read | {scan.get('file_contents_read', False)} |
| Resumable walk | yes (completed folders skipped on resume) |
| Collection started | {created} |
| Collection finished | {scan.get('finished_at', 'n/a')} |
| Duration | {duration} s |

Every file record stores: identifier, name, extension, size in bytes/kb/mb,
creation and modification timestamps (and dates), accessed date where safely
available, full path, parent folder, root folder, folder depth, hidden and
read-only flags, category, naming features and the collection timestamp.
Folder records store depth, direct and recursive file/folder counts and total
size. Failures are written to `data/raw/scan_errors.csv` rather than dropped.

## 6. Actual Data Source

Real files and folders on the local Windows volume `D:\\` of the researcher's
own laptop, enumerated with `os.scandir` and `os.stat` on
{created[:10] if created != 'unknown' else 'the collection date'}.
No cloud service, external API or website was contacted. No third-party data
was merged in.

## 7. Variables and Definitions

A complete data dictionary is provided in `reports/data_dictionary.md`.
Principal variables:

| Variable | Type | Definition |
|---|---|---|
| `file_size_bytes` | integer | Apparent file size from the file system |
| `creation_timestamp` | string (ISO) | Windows record change time (see limitations) |
| `modified_timestamp` | string (ISO) | Last write time |
| `folder_depth` | integer | Levels below the scan root (root children = 1) |
| `file_category` | string | Extension-mapped category (transparent table) |
| `naming_pattern` | string | Documented separator/keyword signature |
| `contains_version_indicator` | boolean | Rule R1 matched |
| `contains_final_keyword` | boolean | Rule R2 or R3 matched |

## 8. Data Preprocessing

- Extension values lower-cased and normalised to a leading dot.
- Path separators normalised to `\\`.
- Size columns recomputed from bytes so units stay consistent.
- Timestamps parsed to datetime; unparseable values are **flagged**, never
  replaced with invented values.
- Exact duplicate *records* (same path recorded twice) flagged and excluded
  from analytical totals.
- Folder depth recomputed from the path and compared with the scanned depth.
- Missing-value and invalid-timestamp counts reported in `findings.json`.

Preprocessing summary:

| Check | Value |
|---|---|
| Raw file rows | {cleaning.get('raw_file_rows', len(files)):,} |
| Processed file rows | {cleaning.get('processed_file_rows', len(files_p)):,} |
| Missing/invalid sizes | {cleaning.get('missing_size_rows', 0):,} |
| Zero-byte files | {cleaning.get('zero_size_rows', 0):,} |
| Duplicate records | {cleaning.get('duplicate_record_rows', 0):,} |
| Invalid creation dates | {cleaning.get('invalid_creation_dates', 0):,} |
| Invalid modified dates | {cleaning.get('invalid_modified_dates', 0):,} |
| Depth mismatches | {cleaning.get('depth_mismatches', 0):,} |
| Recorded scan errors | {cleaning.get('error_rows', n_errors):,} |

## 9. Analysis Methodology

- **Temporal** - counts grouped by creation year/month and modified
  year/month; creation and modification series compared side by side.
- **Storage** - byte sums grouped by root folder, extension, category and
  folder; mean/median/max computed on the real size distribution.
- **File types** - extension and category counts with percentage shares.
- **Structure** - depth distribution, files per folder, empty and deeply
  nested folders, inferred top-level groupings.
- **Naming** - nine documented rules (R1-R9) applied to file-name stems.
- **Duplicates** - Level 1 metadata candidates (name + extension + size +
  distinct path); Level 2 SHA-256 verification only if explicitly enabled.

## 10. Visualization Methodology

Interactive dashboard built with Streamlit and Plotly. Eight pages (Overview,
Timeline, Storage, File Types, Naming, Duplicates, Folder Structure,
Findings) share global filters for year, month, extension, category, root
folder and file-size range; every chart is recomputed from the filtered
frame. Charts display counts or bytes with explicit units and never plot
values that were not measured.

## 11. Findings

### 11.1 Scale of the collection

- Files scanned: **{n_files:,}**
- Folders scanned: **{n_folders:,}**
- Total storage: **{_human(total_bytes)}**
- Unique extensions: **{ftypes.get('unique_extensions', 'n/a')}**
- Maximum folder depth: **{struct.get('max_depth', 'n/a')}**
- Average folder depth: **{struct.get('avg_depth', 'n/a')}**
- Empty folders: **{empty_folders:,}**

### 11.2 Temporal activity

- Creation years observed: {temporal.get('creation_year_min', 'n/a')} - {temporal.get('creation_year_max', 'n/a')}
- Peak creation year: **{peak_c_year}** ({peak_c_count:,} files)
- Peak modification year: **{peak_m_year}** ({peak_m_count:,} files)

*Interpretation caution:* these are file-system record dates on this volume,
not verified authoring dates.

{_md_table(year_table)}

### 11.3 Storage distribution

- Mean file size: **{_human(storage.get('mean_bytes', 0))}**
- Median file size: **{_human(storage.get('median_bytes', 0))}**
- Largest file: **{_human(storage.get('max_bytes', 0))}**

{_md_table(root_table, 15)}

### 11.4 File types

- Most common extension: **{ftypes.get('most_common_extension', 'n/a')}** ({ftypes.get('most_common_extension_count', 0):,} files)
- Largest storage-consuming extension: **{ftypes.get('largest_storage_extension', 'n/a')}** ({_human(ftypes.get('largest_storage_extension_bytes', 0))})

{_md_table(ext_table, 15)}

{_md_table(cat_table, 15)}

### 11.5 Naming consistency

Rules applied: R1 version, R2 final, R3 latest, R4 date, R5 copy,
R6 separators, R7 capitalisation, R8 very long, R9 repeated separators.

- Files analysed: **{naming.get('total_files_analysed', 0):,}**
- Version indicator: **{naming.get('version_indicator_count', 0):,}** ({naming.get('version_indicator_pct', 0)}%)
- Final/latest keyword: **{naming.get('final_or_latest_count', 0):,}** ({naming.get('final_or_latest_pct', 0)}%)
- Date indicator: **{naming.get('date_indicator_count', 0):,}** ({naming.get('date_indicator_pct', 0)}%)
- Copy indicator: **{naming.get('copy_indicator_count', 0):,}** ({naming.get('copy_indicator_pct', 0)}%)
- Mixed capitalisation: **{naming.get('mixed_capitalization_count', 0):,}**
- Files with at least one flagged issue: **{naming.get('inconsistent_files', 0):,}** ({naming.get('inconsistent_pct', 0)}%)
- Mean filename length: **{naming.get('avg_filename_length', 'n/a')}** chars

A file containing "final" is counted as an *indicator* only; it is not
automatically classified as an error.

{_md_table(pattern_table, 15)}

### 11.6 Duplicates

- Candidate duplicate groups (Level 1, metadata): **{dup.get('candidate_groups', 0):,}**
- Extra copies implied by candidates: **{dup.get('candidate_extra_copies', 0):,}**
- Potential bytes in extra candidate copies: **{_human(dup.get('candidate_potential_bytes', 0))}**
- Verified duplicate groups (Level 2, SHA-256): **{dup.get('verified_groups', 0):,}**
- Hash verification enabled: **{dup.get('hash_verification_enabled', False)}**

Candidates are **not** confirmed duplicates. No file was deleted.

### 11.7 Folder structure and top-level groupings

{_md_table(depth_table, 20)}

Inferred top-level groupings (name/path based, **not** verified projects):

{_md_table(projects.head(10), 10) if not projects.empty else '*No groupings available.*'}

### 11.8 Largest files

{_md_table(top_files, 10)}

### 11.9 Largest folders

{_md_table(top_folders, 10)}

## 12. Tables and Charts

All charts are produced interactively by the dashboard
(`streamlit run app.py`). Static equivalents can be exported from
`reports/charts/`.

## 13. Limitations

{chr(10).join('- ' + x for x in limitations)}

### Scan errors recorded

{scan_errors_txt}

## 14. Privacy and Ethical Considerations

- Only file-system metadata was collected; **no document contents were read**.
- No data was uploaded to any cloud service, external API or website.
- No file was modified, renamed, moved or deleted.
- No administrator privileges were used.
- The project workspace was excluded from its own scan.
- System and recovery locations were excluded and listed in the scan report.
- Duplicate detection defaults to metadata comparison; byte-level hashing is
  opt-in and was {'enabled' if dup.get('hash_verification_enabled') else 'not enabled'}.
- The dataset stays in local storage under `data/` and can be deleted by the
  owner at any time.

## 15. Conclusion

This project demonstrates a reproducible, privacy-preserving method for
turning a real Windows volume's metadata into structured research data. The
collected {n_files:,} records support descriptive statements about scale,
temporal activity, storage concentration, format usage, naming behaviour and
duplicate candidates on this machine at the time of the scan. Because a
single scan is a cross-section, claims about *change over time* beyond the
observed timestamp distribution require additional snapshots taken on later
dates; the snapshot system is in place to enable exactly that.

## 16. Future Improvements

1. Schedule periodic scans so genuine before/after comparisons become
   possible with the existing snapshot comparator.
2. Opt-in SHA-256 verification to convert candidates into confirmed
   duplicates and quantify exactly recoverable space.
3. Cluster top-level folders into verified projects using explicit
   user-declared mappings rather than name inference.
4. Export static chart images for print publication.
5. Extend category mapping with user-configurable extension rules.

---

*Report generated automatically by `analysis/report_generator.py` from the
collected metadata. Every figure is traceable to `data/raw/*.csv` and
`reports/findings.json`.*
"""
    config.RESEARCH_REPORT.write_text(md, encoding="utf-8")
    log.info("Research report written: %s", config.RESEARCH_REPORT)


# --------------------------------------------------------------------------- #
def generate_data_dictionary() -> None:
    md = """# Data Dictionary

Digital Archaeology - definitions of every variable collected or derived.
All values originate from the local file system of `D:\\`; no document
contents were read.

## 1. `data/raw/files_metadata.csv` (one row per file)

| # | Variable | Data type | Unit / domain | Definition |
|---|---|---|---|---|
| 1 | `file_id` | integer | 1..N | Sequential identifier assigned during the scan |
| 2 | `file_name` | string | | File name with extension, as stored |
| 3 | `file_extension` | string | `.ext` or empty | Lower-case extension including leading dot |
| 4 | `file_size_bytes` | integer | bytes | Apparent size from `os.stat` |
| 5 | `file_size_kb` | float | KB | `file_size_bytes / 1024` |
| 6 | `file_size_mb` | float | MB | `file_size_bytes / 1024^2` |
| 7 | `creation_timestamp` | string (ISO 8601) | local time | Windows record-change time (`st_ctime`) |
| 8 | `creation_date` | string `YYYY-MM-DD` | date | Date part of `creation_timestamp` |
| 9 | `modified_timestamp` | string (ISO 8601) | local time | Last write time (`st_mtime`) |
| 10 | `modified_date` | string `YYYY-MM-DD` | date | Date part of `modified_timestamp` |
| 11 | `accessed_date` | string `YYYY-MM-DD` | date | Last access date where safely available |
| 12 | `full_file_path` | string | absolute path | Complete path of the file |
| 13 | `parent_folder` | string | absolute path | Directory immediately containing the file |
| 14 | `root_folder` | string | name | First path level under the scan root |
| 15 | `folder_depth` | integer | levels | Depth of the containing folder (root children = 1) |
| 16 | `folder_path` | string | absolute path | Same as `parent_folder`; kept for clarity |
| 17 | `hidden_file_status` | boolean | true/false | `FILE_ATTRIBUTE_HIDDEN` set |
| 18 | `read_only_status` | boolean | true/false | `FILE_ATTRIBUTE_READONLY` set |
| 19 | `file_category` | enum | see below | Extension-mapped category |
| 20 | `naming_pattern` | string | see R1-R9 | Concatenated naming signature |
| 21 | `contains_version_indicator` | boolean | | Rule R1 matched (`v1`, `version 2`, ...) |
| 22 | `contains_final_keyword` | boolean | | Rule R2 or R3 matched (`final`, `latest`) |
| 23 | `contains_date_indicator` | boolean | | Rule R4 matched (date-like token) |
| 24 | `contains_copy_indicator` | boolean | | Rule R5 matched (`(1)`, `copy`, `backup`) |
| 25 | `filename_length` | integer | characters | Length of `file_name` |
| 26 | `capitalization_style` | enum | lower/upper/title/mixed/no_letters | Rule R7 result |
| 27 | `has_underscore` | boolean | | `_` present in stem |
| 28 | `has_space` | boolean | | whitespace present in stem |
| 29 | `has_hyphen` | boolean | | `-` present in stem |
| 30 | `collection_timestamp` | string (ISO 8601) | local time | When this row was collected |

### `file_category` enum

`Programming`, `Documents`, `Spreadsheets`, `Presentations`, `Images`,
`Videos`, `Audio`, `Datasets`, `Archives`, `Executables`, `System-related`,
`Other`. The mapping is the table `EXTENSION_CATEGORY_MAP` in
`scanner/metadata_extractor.py` and is applied by extension only.

## 2. `data/raw/folders_metadata.csv` (one row per folder)

| # | Variable | Data type | Definition |
|---|---|---|---|
| 1 | `folder_path` | string | Absolute folder path |
| 2 | `folder_name` | string | Final path component |
| 3 | `parent_folder` | string | Parent directory |
| 4 | `folder_depth` | integer | Levels below scan root |
| 5 | `direct_file_count` | integer | Files directly inside |
| 6 | `recursive_file_count` | integer | Files in folder and all descendants |
| 7 | `direct_subfolder_count` | integer | Immediate subfolders |
| 8 | `recursive_folder_count` | integer | All descendant folders |
| 9 | `total_size_bytes` | integer | Recursive size in bytes |
| 10 | `total_size_mb` | float | Recursive size in MB |
| 11 | `collection_timestamp` | string | When this row was collected |

## 3. `data/raw/scan_errors.csv`

| Variable | Type | Definition |
|---|---|---|
| `error_timestamp` | ISO 8601 | When the failure occurred |
| `error_type` | enum | e.g. `PermissionDenied`, `OSError`, `NotFound`, `SymlinkSkipped`, `MaxDepthExceeded` |
| `path` | string | Affected path |
| `operation` | string | `scandir`, `stat`, `write`, ... |
| `message` | string | Truncated OS/Python error text |

Errors are **recorded, not discarded**; affected objects are excluded from
totals and counted in the scan report.

## 4. Derived / processed columns (`data/processed/files_processed.csv`)

| Variable | Type | Definition |
|---|---|---|
| `size_invalid` | boolean | Size missing or negative (flag only) |
| `size_zero` | boolean | Zero-byte file |
| `is_duplicate_record` | boolean | Same `full_file_path` seen earlier in the file |
| `creation_date_parsed` | datetime64 | Parsed creation date (NaT if invalid) |
| `modified_date_parsed` | datetime64 | Parsed modification date |
| `*_invalid` | boolean | Original value present but unparseable |
| `creation_year`, `creation_month`, `creation_year_month` | int/int/str | Extracted date parts |
| `modified_year`, `modified_month`, `modified_year_month` | int/int/str | Extracted date parts |
| `computed_depth` | integer | `full_file_path` level count (verification) |
| `depth_mismatch` | boolean | Scanned depth differs from computed depth |

Original raw values are preserved; cleaning never overwrites them with
invented data.

## 5. Duplicate outputs

| File | Variables |
|---|---|
| `duplicate_candidates.csv` | `duplicate_group_id`, `file_name`, `file_extension`, `file_size_bytes`, `file_size_mb`, `copy_count`, `paths`, `folders`, `level`, `status` |
| `verified_duplicates.csv` | `duplicate_group_id`, `sha256`, `file_name`, `file_size_bytes`, `copy_count`, `extra_copies`, `extra_copy_bytes`, `paths`, `status` |

`status` for candidates is always `candidate_not_verified`. Verified rows
appear only when SHA-256 verification is explicitly enabled.

## 6. Naming rules (as implemented)

| Rule | Meaning |
|---|---|
| R1 | Version indicator: `v1`, `v2.1`, `version 3` as a standalone token |
| R2 | Final indicator: token `final` or `finalN` |
| R3 | Latest indicator: token `latest` |
| R4 | Date indicator: `2026-07-27`, `27-07-2026`, or a bare `19xx`/`20xx` year |
| R5 | Copy indicator: `(1)`, `copy`, `duplicate`, `backup` |
| R6 | Separators present: underscore / space / hyphen |
| R7 | Capitalisation: lower, upper, title, mixed (mixed = flagged) |
| R8 | Very long: stem > 80 characters |
| R9 | Repeated whitespace or 3+ consecutive separators |

"""
    config.DATA_DICTIONARY.write_text(md, encoding="utf-8")
    log.info("Data dictionary written: %s", config.DATA_DICTIONARY)
