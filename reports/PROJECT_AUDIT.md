# PROJECT_AUDIT — Digital Archaeology

**Date:** 2026-11-16 (audit window 02:30–02:40 local)
**Scope:** read-only inspection of `D:\Data VMini` before any Phase 2–10 modifications
**Method:** small commands, ≤60 s each; datasets inspected for names/sizes/row counts only — no CSV or DB content printed
**Dashboard check:** basic reachability **PASSED** (`http://127.0.0.1:8501`, page title *Digital Archaeology*).
Individual dashboard pages were **not** visually verified and are not claimed to be.

---

## 1. Existing architecture

Single-folder Python project, 43 `.py` files (incl. 4 pre-edit backups), Python 3.11.9, venv at `.venv`.

### Entry points

| File | Role |
|---|---|
| `app.py` (182 lines) | Streamlit dashboard. 8 pages via `PAGES` dict, sidebar filters, snapshot panel (scan/compare/create buttons). |
| `pipeline.py` (228 lines) | CLI pipeline `scan → clean → analyse → report`. Flags: `--scan`, `--analyse-only`, `--hash`, `--no-report`. **Default with no args = full D: scan** (risk #3). |
| `config.py` (93 lines) | All paths derived from `PROJECT_ROOT` (portable); scan root from env `DA_SCAN_ROOT` (default `D:\`); creates output dirs on import. Exclusions, resume, hashing, output-file constants. |

### Packages

| Package | Modules (lines) | Role |
|---|---|---|
| `scanner/` | `filesystem_scanner` (532), `metadata_extractor` (274), `snapshot_manager` (161), `duplicate_detector` (138), `database_builder` (89), `folder_analyzer` (44) | Metadata collection, stat/naming extraction, folder aggregation, CSV row-writer with resume, SHA-256 optional verification, CSV→SQLite load, snapshot create/compare. |
| `analysis/` | `report_generator` (438), `data_cleaning` (161), `structural_analysis` (92), `naming_analysis` (129), `storage_analysis` (67), `temporal_analysis` (61), `filetype_analysis` (61), `duplicate_analysis` (47 wrapper) | Cleaning (flags, never overwrites bad values), findings JSON, markdown report. |
| `dashboard/` | `common` (154) + 8 pages (73–172 lines each) | Page renderers; one cached `load_data()`; coercion of size/bool columns added 2026-11-16. |
| `scripts/` | `rebuild_reports`, `restore_raw_from_db`, `verify_db` | One-off recovery utilities from today's clobber recovery. **Hard-coded absolute paths** (risk #7). |
| `tests/` | `conftest` + 4 test files, **91 tests** | Fixtures redirect all `config` paths into temp dirs (`scanner_config`). |

Backups/evidence: `backups/pre_fix_20261116/` (4 pre-edit copies), `data/_clobber_evidence_004755/`, `reports/*.clobbered`.

### Data flow

```
scan root ──scanner──▶ data/raw/*.csv ──data_cleaning──▶ data/processed/*.csv
     │                                                          │
     ├──▶ reports/scan_report.json                              ├──▶ reports/findings.json
     │                                                          ├──▶ reports/research_report.md
     └──▶ database_builder ──▶ data/database/digital_archaeology.db
                                   │
snapshot_manager ◀── run_analysis ──┘        dashboard (app.py) ◀── processed CSVs + findings.json
```

## 2. Existing dashboard pages (8)

`Overview` · `File Creation Timeline` · `Storage Analytics` · `File Type Analysis` · `Naming Pattern Analysis` · `Duplicate Analysis` · `Folder Structure` · `Research Findings`

- Data: single cached `load_data()` reads processed CSVs + `findings.json`; filters (year/month/extension/category/root/size) applied once in `app.py`.
- Sidebar: snapshot list + compare (only valid snapshots), "Create new snapshot" (runs a **full D:** scan inline — risk #5).
- Reachability **PASSED**; per-page rendering not yet verified (deferred).

## 3. Scanner and database design

**Scanner** — single-pass recursive `os.scandir` walk; CSV row-writer with 20 k-row batching; per-level exclusions (workspace self, `SYSTEM_EXCLUSION_NAMES` = System Volume Information, $Recycle.Bin, Recovery, WindowsApps, Config.MSI); `FOLLOW_SYMLINKS=False`; `MAX_DEPTH=64`; hidden files included; errors captured (original scan: 19); resume file `logs/scan_resume.json` **fixed today** (status-gated load, append + path dedupe, done-set integrity, cleared on success; stale file moved to evidence).

**Original dataset (verified):** 617,955 files · 59,213 folders · 116,500,257,509 bytes (108.50 GB) · 19 errors · scan 00:25:17→00:26:32 (74.8 s), hash verification off.

**SQLite** — `database_builder.build()` **unlinks and rebuilds** the DB from CSVs (risk #4). Current DB (untouched since 00:32:29, 675,295,232 B = 644.01 MB), read-only inspected:

| Table | Rows |
|---|---|
| `files` | 617,955 |
| `folders` | 59,213 |
| `duplicate_candidates` | 78,790 |
| `naming_analysis` | 617,955 |
| `scan_errors` | 19 |
| `snapshots` | 1 |
| `metadata` | 2 |

Views: `files_by_category`, `files_by_year`. No indexes. `verified_duplicates` table absent (header-only CSV skipped by builder). The DB was the authoritative source for today's recovery and contains a complete copy of `naming_analysis` (usable for repair, risk #1).

**Snapshots** — index `snapshots.json` + slim JSON + detail JSON (original detail: 176.59 MB). Comparison via `snapshot_manager.compare()` (path-keyed new/removed/modified counts). Snapshot `20261116_004755` is marked `valid: false` (clobber evidence) and excluded from the UI.

**Duplicate detection** — size/extension/name grouping → candidates (78,790 groups; 455,557 extra copies; 25.42 GB); SHA-256 verification optional, currently off (`verified_duplicates.csv` = header only, correct).

**Naming analysis** — rules R1–R9 documented in `analysis/naming_analysis.py`; features extracted at scan time; `run()` writes the naming table CSV.

## 4. Current reusable features

- ✅ Config-driven, project-relative paths (`config.py`), env-overridable scan root.
- ✅ Portable CSV ingestion pipeline (clean → analyse → findings → report).
- ✅ SQLite ingestion (read-only restore path proven by recovery; DB builder present).
- ✅ Snapshot create/list/compare with validity flagging.
- ✅ CSV schema/data dictionary (`reports/data_dictionary.md`).
- ✅ 91-test suite with temp-dir isolation (except one leaking test — risk #1).
- ✅ Windows batch friend-workflow: **absent** (no `run_pipeline.py`, no `.bat`, no README yet).

## 5. Test status

- **91 passed in 5.22 s** (fresh run during this audit, `pytest tests -q`).
- ⚠ That run had a **side effect**: it re-wrote the live `data/processed/naming_analysis.csv` (risk #1). Every future run repeats this until the leaking test is fixed.
- Compilation: all 43 project `.py` files compile clean; `check_fstrings.py` clean.

## 6. Git and remote status

| Item | Status |
|---|---|
| Local repo | **None** — no `.git` in `D:\Data VMini` |
| Branch / remote (local) | Not applicable yet |
| `.gitignore` | **Missing** |
| `README.md` / `LICENSE` | **Missing** (only `reports/*.md` research files) |
| `git` client | 2.54.0.windows.1 available |
| Remote `https://github.com/ArulAgnes/DIGITAL_ARCHAEOLOGY.git` | **Reachable, EMPTY** (0 refs — first push will create history; no conflict risk) |

## 7. Bugs and risks

**R1 — ACTIVE: test suite writes into the real dataset (actualised today).**
`tests/test_analysis_dashboard.py:161` `test_naming_indicator_summary` calls `naming_analysis.run()` **without** the `scanner_config` fixture; `analysis/naming_analysis.py:127` unconditionally writes `config.NAMING_CSV`. During this audit's pytest run (02:35:51) the live `data/processed/naming_analysis.csv` was overwritten: **165,442,803 bytes (617,955 rows, rebuilt 01:48) → 774 bytes (4 fixture rows)**. Confirmed content = test fixture (`D:\p1\a.py` …). Impact: dashboard unaffected (naming page computes in memory); **any future `database_builder.build()` would ingest the damaged file**; every pytest run repeats the clobber. *Repair (awaiting approval):* (a) fix the test to request `scanner_config`, (b) regenerate the CSV via `scripts/rebuild_reports.py` (idempotent) or restore the `naming_analysis` table from the DB, (c) re-run suite and verify mtime unchanged.

**R2 — historical clobber (recovered, disclosed).** 2026-11-16 00:47:55 scan honoured a stale resume file → 76-file fragment overwrote raw/processed CSVs, `findings.json`, `scan_report.json`. Recovered today from the SQLite archive (raw restored 01:36; processed/findings/scan_report rebuilt 01:47–01:48; all cross-checks passed). Evidence preserved in `data/_clobber_evidence_004755/`. Snapshot 004755 marked invalid. Caveats: nested `excluded_directories` entries and the nested findings `generated_at` were reconstructed, not recoverable.

**R3 — dangerous default:** `python pipeline.py` with no args triggers a **full D: scan** (`pipeline.py:211–213`). Needs an explicit confirmation gate for the portable release (esp. whole-drive).

**R4 — destructive DB builder:** `database_builder.build()` deletes the 644 MB DB before rebuilding, no backup/confirmation.

**R5 — dashboard "Create new snapshot"** runs a full D: scan + full analysis inline (≈75 s+, button-driven; this path triggered R2 historically). Does not generate the research report (inconsistent with CLI path).

**R6 — dependency hygiene:** `requirements.txt` unpinned (`pandas>=2.0`, `numpy>=1.24`, `streamlit>=1.30`, `plotly>=5.18`); pytest not listed. Installed reality: pandas 3.0.5, streamlit 1.67.0, plotly 7.1.0, pytest 8.4.2. Pandas 3 str-dtype behaviour already caused the dashboard crash fixed today — pin what is tested.

**R7 — hard-coded absolute paths** in `scripts/rebuild_reports.py` (`"D:\\Data VMini"`), `restore_raw_from_db.py`, `verify_db.py`. Must not ship as-is (Phase 7).

**R8 — SQLite at scale:** no indexes, timestamps stored as TEXT, `verified_duplicates` table missing; fine for one-off research, weak for repeated comparisons.

**R9 — config side effect:** `config.py` creates directories at import time.

**R10 — verification gap:** dashboard pages beyond basic reachability not visually verified; Streamlit data load holds ~800 MB of CSVs in memory per session (friend-laptop risk).

**R11 — repo hygiene for push:** `.bak` files, `backups/`, `__pycache__/`, `.pytest_cache/`, and evidence dirs containing real personal paths must never be staged (Phase 8).

## 8. Recommended implementation sequence

0. **Repair gate (before any further work, needs approval):** fix leaking test (R1) → regenerate `naming_analysis.csv` → re-run suite → verify no file mtimes change. *Do not run `database_builder.build()` until repaired.*
1. **Phase 1 sign-off:** this report; per-page dashboard verification can be folded into Phase 5 testing.
2. **Privacy first (Phase 8 early):** write `.gitignore` + classify every path (code/docs vs personal data) **before** `git init`, so no personal dataset can ever be staged.
3. **Portability groundwork (Phase 7):** scan-root parameter + drive-scan confirmation gate (R3), remove/parameterise hard-coded script paths (R7), sample config with safe defaults.
4. **Phase 2:** `input_data/` workspace + ingestion layer (CSV/SQLite readers, chunked, read-only) + Mode A/B detection with manual schema mapping.
5. **Phase 3:** `run_pipeline.py` CLI (wraps existing `pipeline.py` + new ingestion), `setup_windows.bat`, `run_windows.bat`, pinned `requirements.txt`.
6. **Phase 4:** comparison engine (extend `snapshot_manager.compare` → `comparison/` module: FS + tabular, documented matching strategy, optional hashing).
7. **Phase 5:** dashboard additions (Explorer, Dataset Profiling, Comparison Studio, Data Quality) without breaking the existing 8 pages.
8. **Phase 9:** tests for everything new; keep the 91 existing green (after R1 fix).
9. **Phase 6:** README + Mermaid/SVG diagrams + real screenshots (after Phase 5, so images show the final dashboard).
10. **Phase 10:** `git init`, staged commit (code/docs/assets only), push to the empty remote — never force-push, never stage datasets.

---
*Audit performed read-only. The single dataset change disclosed (R1) was caused by the project's own test suite during the audit's required test execution, not by any audit command; no audit command wrote to, moved, or deleted any dataset, snapshot, report, or database file.*
