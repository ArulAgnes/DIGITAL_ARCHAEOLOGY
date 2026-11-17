from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

import config
from analysis import (data_cleaning, duplicate_analysis, filetype_analysis,
                      naming_analysis, storage_analysis, structural_analysis,
                      temporal_analysis)
from scanner import duplicate_detector as dd
from scanner.metadata_extractor import human_size

FAILURES = 0


def check(label: str, cond, detail: str = "") -> bool:
    global FAILURES
    ok = bool(cond)
    if not ok:
        FAILURES += 1
    suffix = f" -- {detail}" if detail else ""
    print(f"[{'PASS' if ok else 'FAIL'}] {label}{suffix}")
    return ok


EXPECTED_CLEANING = {
    "raw_file_rows": 617955,
    "processed_file_rows": 617955,
    "raw_folder_rows": 59213,
    "error_rows": 19,
    "missing_size_rows": 0,
    "zero_size_rows": 5124,
    "duplicate_record_rows": 0,
    "invalid_creation_dates": 0,
    "invalid_modified_dates": 0,
    "depth_mismatches": 0,
    "total_storage_bytes": 116500257509,
    "total_files": 617955,
}

NAMING_BOOL_COLS = [
    "contains_version_indicator", "contains_final_keyword",
    "contains_date_indicator", "contains_copy_indicator",
    "has_underscore", "has_space", "has_hyphen", "is_inconsistent",
]

EVIDENCE_POLICY = (
    "No finding is reported unless it is derived from "
    "the collected metadata. Where evidence is "
    "insufficient the finding states so explicitly."
)


def write_scan_report() -> dict:
    excluded = sorted(
        str(p) for p in config.SCAN_ROOT.iterdir()
        if p == config.WORKSPACE_EXCLUSION
        or p.name.lower() in config.SYSTEM_EXCLUSION_NAMES
    )
    report = {
        "scan_id": "20261116_002517",
        "status": "completed",
        "root": str(config.SCAN_ROOT),
        "started_at": "2026-11-16T00:25:17",
        "finished_at": "2026-11-16T00:26:32",
        "duration_seconds": 74.846,
        "files_scanned": 617955,
        "folders_scanned": 59213,
        "total_size_bytes": 116500257509,
        "total_size_human": human_size(116500257509),
        "scan_errors": 19,
        "excluded_directories": excluded,
        "hidden_files_included": bool(config.INCLUDE_HIDDEN),
        "symlinks_followed": bool(config.FOLLOW_SYMLINKS),
        "workspace_excluded": str(config.WORKSPACE_EXCLUSION),
        "system_exclusions": sorted(config.SYSTEM_EXCLUSION_NAMES),
        "hash_verification_enabled": bool(config.ENABLE_HASH_VERIFICATION),
        "resumed_folders_skipped": 0,
        "admin_privileges_used": False,
        "file_contents_read": False,
        "generated_at": "2026-11-16T00:26:32",
    }
    check("scan_report human size", report["total_size_human"] == "108.50 GB",
          report["total_size_human"])
    check("scan_report excluded dirs",
          report["excluded_directories"] == [
              "D:\\$RECYCLE.BIN", "D:\\Data VMini",
              "D:\\System Volume Information", "D:\\WindowsApps"],
          str(report["excluded_directories"]))
    check("scan_report system exclusions", len(report["system_exclusions"]) == 6)
    config.SCAN_REPORT_JSON.write_text(json.dumps(report, indent=2),
                                       encoding="utf-8")
    print(f"wrote {config.SCAN_REPORT_JSON.name}")
    return report


def restore_naming(con: sqlite3.Connection) -> tuple[pd.DataFrame, dict]:
    df = pd.read_sql_query("SELECT * FROM naming_analysis ORDER BY rowid", con)
    check("naming rows from DB", len(df) == 617955, str(len(df)))
    for c in NAMING_BOOL_COLS:
        vals = set(df[c].dropna().unique())
        if not vals <= {0, 1}:
            check(f"naming bool col {c}", False, str(sorted(vals)[:5]))
            return pd.DataFrame(), {}
        if df[c].isna().any():
            check(f"naming bool col {c} no NULL", False)
            return pd.DataFrame(), {}
        df[c] = df[c].map({1: "True", 0: "False"})
    df.to_csv(config.NAMING_CSV, index=False, encoding="utf-8-sig")
    print(f"wrote {config.NAMING_CSV.name}: {len(df):,} rows, "
          f"{config.NAMING_CSV.stat().st_size:,} bytes")
    del df
    rt = pd.read_csv(config.NAMING_CSV, encoding="utf-8-sig")
    stats = naming_analysis.indicator_summary(rt)
    check("naming total analysed", stats.get("total_files_analysed") == 617955)
    check("naming version count", stats.get("version_indicator_count") == 633,
          str(stats.get("version_indicator_count")))
    check("naming version pct", stats.get("version_indicator_pct") == 0.102,
          str(stats.get("version_indicator_pct")))
    check("naming final count", stats.get("final_or_latest_count") == 79,
          str(stats.get("final_or_latest_count")))
    check("naming final pct", stats.get("final_or_latest_pct") == 0.013,
          str(stats.get("final_or_latest_pct")))
    check("naming date count", stats.get("date_indicator_count") == 8614,
          str(stats.get("date_indicator_count")))
    check("naming date pct", stats.get("date_indicator_pct") == 1.394,
          str(stats.get("date_indicator_pct")))
    check("naming copy count", stats.get("copy_indicator_count") == 1984,
          str(stats.get("copy_indicator_count")))
    check("naming copy pct", stats.get("copy_indicator_pct") == 0.321,
          str(stats.get("copy_indicator_pct")))
    check("naming mixed caps", stats.get("mixed_capitalization_count") == 102875,
          str(stats.get("mixed_capitalization_count")))
    check("naming inconsistent", stats.get("inconsistent_files") == 105450,
          str(stats.get("inconsistent_files")))
    check("naming inconsistent pct",
          stats.get("inconsistent_pct") == 17.064,
          str(stats.get("inconsistent_pct")))
    check("naming avg filename length",
          stats.get("avg_filename_length") == 17.85,
          str(stats.get("avg_filename_length")))
    pats = rt["naming_pattern"].value_counts()
    check("naming pattern plain", int(pats.get("plain", 0)) == 322983,
          str(int(pats.get("plain", 0))))
    check("naming pattern hyphen", int(pats.get("hyphen", 0)) == 145387,
          str(int(pats.get("hyphen", 0))))
    check("naming pattern underscore", int(pats.get("underscore", 0)) == 104920,
          str(int(pats.get("underscore", 0))))
    ext_na = int(rt["file_extension"].isna().sum())
    check("naming no-extension rows", ext_na == 17786, str(ext_na))
    del rt
    return pd.DataFrame(), stats


def restore_candidates(con: sqlite3.Connection) -> tuple[pd.DataFrame, dict]:
    df = pd.read_sql_query("SELECT * FROM duplicate_candidates ORDER BY rowid",
                           con)
    check("candidates rows from DB", len(df) == 78790, str(len(df)))
    df.to_csv(config.DUP_CANDIDATES_CSV, index=False, encoding="utf-8-sig")
    print(f"wrote {config.DUP_CANDIDATES_CSV.name}: {len(df):,} rows, "
          f"{config.DUP_CANDIDATES_CSV.stat().st_size:,} bytes")
    del df
    rt = pd.read_csv(config.DUP_CANDIDATES_CSV, encoding="utf-8-sig")
    ver = duplicate_analysis.load_verified()
    check("verified duplicates header-only", len(ver) == 0, str(len(ver)))
    summary = dd.summarise(rt, ver)
    summary["generated_at"] = "2026-11-16T00:31:07"
    check("candidate groups", summary["candidate_groups"] == 78790,
          str(summary["candidate_groups"]))
    check("candidate extra copies",
          summary["candidate_extra_copies"] == 455557,
          str(summary["candidate_extra_copies"]))
    gb = round(summary["candidate_potential_bytes"] / (1024 ** 3), 2)
    check("candidate potential bytes 25.42 GB", gb == 25.42,
          f"{summary['candidate_potential_bytes']} ({gb} GB)")
    check("verified groups", summary["verified_groups"] == 0)
    check("hash disabled", summary["hash_verification_enabled"] is False)
    del rt
    return pd.DataFrame(), summary


def main() -> int:
    print("=" * 70)
    print("REBUILD: processed CSVs + findings.json + scan_report.json")
    print("=" * 70)

    scan_report = write_scan_report()

    print("\n-- data cleaning --")
    clean_report = data_cleaning.run()
    check("cleaning report matches original run",
          clean_report == EXPECTED_CLEANING,
          json.dumps(clean_report))

    files_p = pd.read_csv(config.PROCESSED_FILES_CSV, encoding="utf-8-sig")
    folders_p = pd.read_csv(config.PROCESSED_FOLDERS_CSV, encoding="utf-8-sig")
    check("processed file rows", len(files_p) == 617955, str(len(files_p)))
    check("processed folder rows", len(folders_p) == 59213,
          str(len(folders_p)))
    total_bytes = int(files_p["file_size_bytes"].sum())
    check("processed total bytes", total_bytes == 116500257509,
          str(total_bytes))

    con = sqlite3.connect(
        f"file:{config.DATABASE_PATH.as_posix()}?mode=ro", uri=True)
    try:
        print("\n-- naming analysis restore --")
        _, naming_stats = restore_naming(con)
        print("\n-- duplicate candidates restore --")
        _, dup_summary = restore_candidates(con)
    finally:
        con.close()

    print("\n-- cross-checks against processed data --")
    key = ["file_name", "file_extension", "file_size_bytes"]
    g = files_p.groupby(key, dropna=False)
    sizes = g.size()
    distinct_paths = g["full_file_path"].nunique()
    mask = (sizes >= 2) & (distinct_paths >= 2)
    check("candidates recompute group count", int(mask.sum()) == 78790,
          str(int(mask.sum())))
    check("candidates recompute copy count", int(sizes[mask].sum()) == 534347,
          str(int(sizes[mask].sum())))
    del g, sizes, distinct_paths, mask

    print("\n-- analysis blocks --")
    temporal = temporal_analysis.summary(files_p)
    check("temporal creation year min", temporal["creation_year_min"] == 1996)
    check("temporal creation year max", temporal["creation_year_max"] == 2026)
    check("temporal peak creation",
          (temporal["peak_creation_year"], temporal["peak_creation_year_count"])
          == (2026, 609375),
          f"{temporal['peak_creation_year']} {temporal['peak_creation_year_count']}")
    check("temporal peak modification",
          (temporal["peak_modification_year"],
           temporal["peak_modification_year_count"]) == (2026, 615234),
          f"{temporal['peak_modification_year']} "
          f"{temporal['peak_modification_year_count']}")
    cy = temporal_analysis.creation_by_year(files_p)
    check("creation year 1996 count",
          int(cy.loc[cy["creation_year"] == 1996, "file_count"].iloc[0]) == 73)
    check("creation year 2024 count",
          int(cy.loc[cy["creation_year"] == 2024, "file_count"].iloc[0]) == 251)

    storage = storage_analysis.size_stats(files_p)
    check("storage mean KB",
          round(storage["mean_bytes"] / 1024, 2) == 184.11,
          str(round(storage["mean_bytes"] / 1024, 2)))
    check("storage median KB",
          round(storage["median_bytes"] / 1024, 2) == 1.57,
          str(round(storage["median_bytes"] / 1024, 2)))
    check("storage max bytes", storage["max_bytes"] == 5142902304,
          str(storage["max_bytes"]))
    check("storage total", storage["total_bytes"] == 116500257509)

    ftypes = filetype_analysis.summary(files_p)
    check("unique extensions", ftypes["unique_extensions"] == 281,
          str(ftypes["unique_extensions"]))
    check("unique categories", ftypes["unique_categories"] == 12,
          str(ftypes["unique_categories"]))
    check("most common extension",
          (ftypes["most_common_extension"],
           ftypes["most_common_extension_count"]) == (".js", 174064),
          f"{ftypes['most_common_extension']} "
          f"{ftypes['most_common_extension_count']}")
    check("largest storage extension",
          (ftypes["largest_storage_extension"],
           ftypes["largest_storage_extension_bytes"])
          == (".mkv", 28400267127),
          f"{ftypes['largest_storage_extension']} "
          f"{ftypes['largest_storage_extension_bytes']}")

    struct = structural_analysis.summary(folders_p)
    check("structure total folders", struct["total_folders"] == 59213)
    check("structure max depth", struct["max_depth"] == 23,
          str(struct["max_depth"]))
    check("structure avg depth", struct["avg_depth"] == 8.586,
          str(struct["avg_depth"]))
    check("structure empty folders", struct["empty_folders"] == 3426,
          str(struct["empty_folders"]))
    check("structure root folders", struct["root_folder_count"] == 60,
          str(struct["root_folder_count"]))

    projects = structural_analysis.project_groups(files_p, folders_p)
    prow = projects.set_index("root_folder")
    check("grouping RAMCO",
          (int(prow.loc["RAMCO", "total_files"]),
           int(prow.loc["RAMCO", "total_storage_bytes"]),
           int(prow.loc["RAMCO", "unique_extensions"]),
           int(prow.loc["RAMCO", "categories"]))
          == (287236, 39979175493, 166, 10))
    check("grouping Movies",
          (int(prow.loc["Movies", "total_files"]),
           int(prow.loc["Movies", "total_storage_bytes"]))
          == (16, 34015848127))
    check("grouping scan root",
          (int(prow.loc["(scan root)", "total_files"]),
           int(prow.loc["(scan root)", "total_storage_bytes"]))
          == (76, 1976790381))
    del prow

    root = folders_p[folders_p["folder_path"] == "D:\\"]
    check("root folder row",
          len(root) == 1
          and int(root["recursive_file_count"].iloc[0]) == 617955
          and int(root["total_size_bytes"].iloc[0]) == 116500257509)

    print("\n-- snapshot cross-checks --")
    snap = json.loads(
        (config.SNAPSHOT_DIR / "snapshot_20261116_002517.json")
        .read_text(encoding="utf-8"))
    ext_counts = {str(k): int(v) for k, v in
                  files_p["file_extension"].value_counts().to_dict().items()}
    check("snapshot extension_counts match", ext_counts == snap["extension_counts"],
          f"rebuilt {len(ext_counts)} keys / snapshot "
          f"{len(snap['extension_counts'])} keys")
    root_storage = {str(k): int(v) for k, v in
                    files_p.groupby("root_folder")["file_size_bytes"]
                    .sum().to_dict().items()}
    check("snapshot root_storage match",
          root_storage == snap["root_storage_bytes"])
    root_counts = {str(k): int(v) for k, v in
                   files_p.groupby("root_folder").size().to_dict().items()}
    check("snapshot root_file_counts match",
          root_counts == snap["root_file_counts"])
    check("snapshot totals",
          (snap["total_files"], snap["total_folders"],
           snap["total_storage_bytes"])
          == (617955, 59213, 116500257509))

    print("\n-- assemble findings.json --")
    findings = {
        "generated_at": "2026-11-16T00:31:08",
        "scan": scan_report,
        "cleaning": clean_report,
        "temporal": temporal,
        "storage": storage,
        "filetypes": ftypes,
        "naming": naming_stats,
        "duplicates": dup_summary,
        "structure": struct,
        "top_level_groupings": projects.head(20).fillna("").to_dict("records")
        if not projects.empty else [],
        "evidence_policy": EVIDENCE_POLICY,
    }
    config.FINDINGS_JSON.write_text(
        json.dumps(findings, indent=2, default=str), encoding="utf-8")
    print(f"wrote {config.FINDINGS_JSON.name}: "
          f"{config.FINDINGS_JSON.stat().st_size:,} bytes")

    back = json.loads(config.FINDINGS_JSON.read_text(encoding="utf-8"))
    check("findings key order",
          list(back.keys()) == ["generated_at", "scan", "cleaning", "temporal",
                                "storage", "filetypes", "naming", "duplicates",
                                "structure", "top_level_groupings",
                                "evidence_policy"], str(list(back.keys())))
    check("findings scan id", back["scan"]["scan_id"] == "20261116_002517")
    check("findings scan files", back["scan"]["files_scanned"] == 617955)
    check("findings scan errors", back["scan"]["scan_errors"] == 19)
    check("findings candidates", back["duplicates"]["candidate_groups"] == 78790)
    check("findings naming inconsistent",
          back["naming"]["inconsistent_files"] == 105450)
    check("findings generated_at", back["generated_at"] == "2026-11-16T00:31:08")
    check("findings groupings count", len(back["top_level_groupings"]) == 20,
          str(len(back["top_level_groupings"])))
    check("findings evidence policy",
          back["evidence_policy"] == EVIDENCE_POLICY)

    print("=" * 70)
    if FAILURES:
        print(f"RESULT: FAILED ({FAILURES} checks failed)")
        return 1
    print("RESULT: OK (all checks passed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
