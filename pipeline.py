"""End-to-end pipeline: scan -> clean -> analyse -> report.

Usage:
    python pipeline.py --scan [--scan-root PATH] [--yes]
        # metadata scan (whole-drive roots additionally require --yes)
    python pipeline.py --analyse-only   # re-run analyses on existing raw data
    python pipeline.py --hash           # also enable SHA-256 verification
    python pipeline.py --no-report      # skip report generation

Full-drive scanning is an explicit opt-in: running with no flags prints
usage instead of scanning (Phase 3, §12).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

import config
from analysis import (data_cleaning, duplicate_analysis, filetype_analysis,
                      naming_analysis, storage_analysis, structural_analysis,
                      temporal_analysis)
from scanner import filesystem_scanner, snapshot_manager

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
def setup_logging():
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    root = logging.getLogger()
    root.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))
    for h in list(root.handlers):
        root.removeHandler(h)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    fh = logging.FileHandler(config.LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)


log = logging.getLogger("da.pipeline")


# --------------------------------------------------------------------------- #
# Scan phase
# --------------------------------------------------------------------------- #
def run_scan(root: Path | None = None) -> dict:
    scan_root = Path(root) if root else config.SCAN_ROOT
    print("=" * 78)
    print("DIGITAL ARCHAEOLOGY - AUTHORISED METADATA SCAN")
    print("=" * 78)
    print(f"Scan root              : {scan_root}")
    print(f"Workspace exclusion    : {config.WORKSPACE_EXCLUSION}")
    print(f"System exclusions      : {', '.join(sorted(config.SYSTEM_EXCLUSION_NAMES))}")
    print(f"Hidden files included  : {config.INCLUDE_HIDDEN}")
    print(f"Follow symlinks        : {config.FOLLOW_SYMLINKS}")
    print(f"Hash verification      : {config.ENABLE_HASH_VERIFICATION} (opt-in)")
    print("-" * 78)

    if not scan_root.exists():
        print(f"ERROR: scan root {scan_root} is not accessible.")
        sys.exit(2)

    # Safe scope estimate from top-level entries only (no full walk)
    try:
        top = list(scan_root.iterdir())
        top_dirs = sum(1 for p in top if p.is_dir())
        top_files = sum(1 for p in top if p.is_file())
        print(f"Top-level entries      : {len(top)} ({top_dirs} dirs, {top_files} files)")
        print("Estimated scope        : full recursive walk of the selected root")
        print("                         (exact totals reported after the scan)")
    except PermissionError as e:
        print(f"Top-level listing failed: {e}")
    print("-" * 78)

    scanner = filesystem_scanner.FilesystemScanner(root=scan_root)
    t0 = time.time()
    result = scanner.scan()
    elapsed = time.time() - t0

    report = {
        "scan_id": result.scan_id,
        "status": result.status,
        "root": result.root,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "duration_seconds": result.duration_seconds,
        "files_scanned": result.files_scanned,
        "folders_scanned": result.folders_scanned,
        "total_size_bytes": result.total_size_bytes,
        "total_size_human": filesystem_scanner.human_size(result.total_size_bytes)
        if hasattr(filesystem_scanner, "human_size") else "",
        "scan_errors": result.errors,
        "excluded_directories": result.excluded_dirs,
        "hidden_files_included": result.hidden_included,
        "symlinks_followed": config.FOLLOW_SYMLINKS,
        "workspace_excluded": str(config.WORKSPACE_EXCLUSION),
        "system_exclusions": sorted(config.SYSTEM_EXCLUSION_NAMES),
        "hash_verification_enabled": config.ENABLE_HASH_VERIFICATION,
        "resumed_folders_skipped": result.skipped_resumed,
        "admin_privileges_used": False,
        "file_contents_read": False,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        from scanner.metadata_extractor import human_size
        report["total_size_human"] = human_size(result.total_size_bytes)
    except Exception:
        report["total_size_human"] = ""

    config.SCAN_REPORT_JSON.write_text(json.dumps(report, indent=2),
                                       encoding="utf-8")
    log.info("Scan report written: %s", config.SCAN_REPORT_JSON)
    print(f"Files scanned          : {result.files_scanned:,}")
    print(f"Folders scanned        : {result.folders_scanned:,}")
    print(f"Total storage          : {report['total_size_human']}")
    print(f"Scan errors recorded   : {result.errors:,}")
    print(f"Excluded directories   : {len(result.excluded_dirs)}")
    print(f"Duration               : {elapsed:.1f}s")
    print("=" * 78)
    return report


# --------------------------------------------------------------------------- #
# Analysis phase
# --------------------------------------------------------------------------- #
def run_analysis(scan_report: dict | None = None) -> dict:
    log.info("Loading raw metadata ...")
    files, folders, errors = data_cleaning.load_raw()
    if files.empty:
        log.error("No raw file metadata found. Run the scan first.")
        return {}
    log.info("Raw rows: files=%d folders=%d errors=%d",
             len(files), len(folders), len(errors))

    clean_report = data_cleaning.run()
    files_p = pd.read_csv(config.PROCESSED_FILES_CSV, encoding="utf-8-sig")
    folders_p = pd.read_csv(config.PROCESSED_FOLDERS_CSV, encoding="utf-8-sig")

    log.info("Temporal analysis ...")
    temporal = temporal_analysis.summary(files_p)

    log.info("Storage analysis ...")
    storage = storage_analysis.size_stats(files_p)

    log.info("File-type analysis ...")
    ftypes = filetype_analysis.summary(files_p)

    log.info("Naming analysis ...")
    naming_tbl, naming_stats = naming_analysis.run(files_p)

    log.info("Duplicate analysis ...")
    cand, ver, dup_summary = duplicate_analysis.run(
        files_p, enable_hash=config.ENABLE_HASH_VERIFICATION)

    log.info("Structural analysis ...")
    struct = structural_analysis.summary(folders_p)
    projects = structural_analysis.project_groups(files_p, folders_p)

    findings = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "scan": scan_report or json.loads(config.SCAN_REPORT_JSON.read_text(
            encoding="utf-8")) if config.SCAN_REPORT_JSON.exists() else {},
        "cleaning": clean_report,
        "temporal": temporal,
        "storage": storage,
        "filetypes": ftypes,
        "naming": naming_stats,
        "duplicates": dup_summary,
        "structure": struct,
        "top_level_groupings": projects.head(20).fillna("").to_dict("records")
        if not projects.empty else [],
        "evidence_policy": ("No finding is reported unless it is derived from "
                            "the collected metadata. Where evidence is "
                            "insufficient the finding states so explicitly."),
    }
    config.FINDINGS_JSON.write_text(json.dumps(findings, indent=2, default=str),
                                    encoding="utf-8")
    log.info("Findings written: %s", config.FINDINGS_JSON)

    # snapshot of this run
    base_scan = findings["scan"] or {}
    snapshot_manager.create_snapshot(base_scan, files_p, folders_p, errors)

    return findings


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Digital Archaeology pipeline")
    ap.add_argument("--scan", action="store_true",
                    help="Run the metadata scan (explicit opt-in)")
    ap.add_argument("--scan-root", metavar="PATH",
                    help="Scan this folder/drive instead of the configured root")
    ap.add_argument("--yes", action="store_true",
                    help="Confirm a whole-drive scan (required for drive roots)")
    ap.add_argument("--analyse-only", action="store_true",
                    help="Analyse existing raw data without scanning")
    ap.add_argument("--hash", action="store_true",
                    help="Enable SHA-256 exact duplicate verification")
    ap.add_argument("--no-report", action="store_true",
                    help="Skip research report generation")
    return ap


def main(argv: list[str] | None = None):
    ap = build_parser()
    args = ap.parse_args(argv)

    if not (args.scan or args.analyse_only):
        ap.print_usage()
        print("pipeline.py: choose an action: --scan or --analyse-only "
              "(full-drive scans additionally need --yes).")
        sys.exit(2)

    setup_logging()

    if args.hash:
        config.ENABLE_HASH_VERIFICATION = True

    scan_root: Path | None = None
    if args.scan:
        if args.scan_root:
            scan_root = Path(args.scan_root)
        else:
            scan_root = config.SCAN_ROOT
        from ingestion.validators import is_drive_root
        if is_drive_root(scan_root) and not args.yes:
            print(f"ERROR: '{scan_root}' is a whole-drive root. "
                  "Full-drive scans are explicit opt-in: re-run with --yes.")
            sys.exit(2)

    scan_report = None
    if args.scan:
        scan_report = run_scan(scan_root)

    findings = run_analysis(scan_report)

    if not args.no_report and findings:
        try:
            from analysis.report_generator import generate_all
            generate_all(findings)
        except Exception as exc:  # noqa: BLE001
            log.exception("Report generation failed: %s", exc)

    log.info("Pipeline finished.")


if __name__ == "__main__":
    main()
