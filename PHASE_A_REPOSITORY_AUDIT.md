# Phase A — Repository access and architecture audit

Audit date: 2026-11-17. Scope: inspection and planning only. See the five companion documents for architecture, historical claims, gap assessment, desktop design, and execution gates.

## Access and baseline

* Public GitHub repository `ArulAgnes/DIGITAL_ARCHAEOLOGY` was accessed successfully via the GitHub API. Default branch `main` resolves to `9fe94b23eda5251761f188910593ba9e4940469b`; the current working branch is `arena/1d3114c2-digital-archaeology` at that same commit. The remote recursive tree was inspected and matched the tracked local listing.
* Local workspace listing, Git status, tracked paths and ignored status were checked separately. There was no pre-existing untracked or ignored local work in this sandbox. This is not evidence about files on the user's Windows machine or other checkouts.
* There is **no root README.md** in this checkout; there is a `requirements.txt`, no tracked `.bat` launchers, and no tracked research CSV, Parquet or SQLite datasets. `input_data/*/README.md` are instructions, not datasets. The historical data described in reports were not available here, so their present state cannot be verified. No user drive was enumerated.

## Actual code and documents inspected

Inspection went beyond a listing: entry points `app.py`, `config.py`, `pipeline.py`, `run_pipeline.py`; scanner `filesystem_scanner.py`, `metadata_extractor.py`, `duplicate_detector.py`, `database_builder.py`, `snapshot_manager.py`; `core/dataset.py` and `processing_context.py`; `ingestion/filesystem_loader.py`, `dataset_registry.py`, `workflow.py` and input guides; `analysis/analysis_runner.py`, `analysis/comparison/base.py`, analysis module public APIs; `visualization/chart_registry.py`, `export.py`, helper and adapter references; `dashboard/common.py` and page wiring; test fixtures, scanner, ingestion, analysis, comparison and visualization test declarations, with representative safety tests checked in detail. Documents reviewed: `docs/ARCHITECTURE.md`, `docs/PHASE_3_IMPLEMENTATION.md`, `docs/DATA_INPUT_GUIDE.md`, `docs/PRIVACY.md`, `docs/COMPARISON_GUIDE.md`, `reports/PROJECT_AUDIT.md`, `reports/PHASE_2_AUDIT.md`, `reports/data_dictionary.md`, and `.gitignore`. `visualization/` exists; there is no `database/` or desktop module. The companion documents name specific functions, mechanisms and limitations; this is not an execution-based certification of every module.

## Principal findings

1. The existing application is Streamlit with the original eight pages **plus four** newer pages (12 page entries), not a PySide6 desktop application. `app.py::main` calls `ensure_data()` before page navigation, so a fresh installation with no legacy scan data cannot reach the newer ingestion pages via that path.
2. The scan code uses metadata operations and CSV row writers, but cannot cancel an active folder walk, materializes each directory (`list(it)`), uses recursion with a depth limit, and lacks the required complete discovery/skipped/error counters. See `scanner/filesystem_scanner.py:226-270,430-526` and `ingestion/filesystem_loader.py:139-165`. A fatal walk exception is currently followed by `_finish("completed", ...)`, so status is not a reliable success indicator in that case.
3. Strict privacy cannot be claimed for all existing workflows: `pipeline.py --hash` enables raw-byte file hashing in `scanner/duplicate_detector.py`; `analysis/analysis_runner.py::verify_source` hashes eligible registered input files and scan-output artifacts. Explicit CSV/SQLite import necessarily parses metadata dataset bytes, but arbitrary scanned documents must never be opened or hashed by the desktop path. Separate and enforce those trust boundaries.
4. `FOLLOW_SYMLINKS=False` exists, and links handled as files are skipped; the directory branch lacks an explicit universal Windows reparse/junction check before descent. Do not assume the comments prove junction protection on Windows. Windows long-path, link, permission, and race handling remain unverified.
5. Same-path filesystem registrations use a stable source-derived ID, upsert the registry and reuse output paths; `_RowWriter` opens new outputs with `w`. Historical snapshots may be overwritten on re-registration. Existing legacy `snapshot_manager` is a separate mechanism, not proof that platform artifacts are immutable.
6. The Phase 3 report's test and integrity numbers are historical claims, not verified in this sandbox. Phase 4–7-labelled implementation and tests exist, contradicting any unqualified claim that Phase 4 has not started; their completion and tests are not independently established. See `PHASE_HISTORY_RECONCILIATION.md`.
7. `D:\Data VMini` lies **inside** `D:\`. Excluding it from traversal prevents self-collection but does not meet the earlier literal rule “never save application-generated files inside the scanned source directory.” The confirmed configuration and strict containment rule cannot both hold. See the decision gate below.

## Source-protection decision gate

Recommended strict-compliance choice: put **all** new app-owned outputs on a volume or folder outside the scan root, e.g. a user-selected folder on another volume, and still exclude all app-owned paths. Alternative: explicitly approve `D:\Data VMini` as an excluded app-owned enclave inside `D:\`, accepting that this is an exception to the earlier prohibition, not claiming literal compliance. Refuse equal/ancestor/overlap or uncertain path relationships until policy is decided; account for Windows case, drive aliases, reparse points, UNC/extended paths and concurrent path substitution. If the output and source have an approved enclave relationship, do not write anywhere else under the source and prune the enclave before enumeration; enforce and test this throughout all writers, including temp/cache/log/report/database paths. A source selected inside the enclave must be refused. Existing research data must never be rewritten by the new app.

Also require explicit metadata-dataset import selection: CSV/Parquet parsing reads the selected metadata file, not scanned document contents. The import path must not allow a general-purpose content preview to masquerade as a filesystem scan.

## Tests and verification this phase

* No dependencies installed. `python -B` (3.11.2, Linux) static-parsed the chart registry and package declarations: 57 chart spec entries; this is an inventory, not evidence that the 65 requested research charts are implemented or meaningful. Python module discovery found no `pytest`, pandas, NumPy, Streamlit, Plotly, PySide6, DuckDB, PyArrow, or scikit-learn in this environment.
* No project imports or test execution: imports of `config.py` create directories, and the historical `reports/PROJECT_AUDIT.md` documents a test-induced overwrite in another environment. Consequently there is **no new pass/skip count or integrity-gate result**. No Windows-specific behavior was tested. No scanned source files were touched. GitHub tree and commit were verified; source code was inspected read-only.
* Phase A deliverables created: the six requested root-level Markdown audit documents. Existing files modified: none. No application code or data created. This documentation-only creation is the necessary exception to “do not modify files” in order to produce the explicitly requested deliverables; it changes no existing file.

## Approval requested

Please approve or revise the proposed Phase B plan **and** decide the output-location conflict before any implementation. Stop here: no Phase B or drive scan has been started.
