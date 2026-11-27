# Phase 3 Implementation Notes

Developer-facing record of what was built, which existing files changed, and
why. Verification evidence lives in
`reports/PHASE_3_IMPLEMENTATION_REPORT.md`.

## Built (new files)

```text
core/                       dataset model + safety context
  __init__.py  dataset.py  dataset_metadata.py  exceptions.py  processing_context.py
ingestion/                  input adapters
  __init__.py  base_loader.py  validators.py  input_detector.py
  csv_loader.py  sqlite_loader.py  filesystem_loader.py  dataset_registry.py
analysis/                   Mode B foundations
  dataset_profiler.py  data_quality.py
run_pipeline.py             CLI: detect -> validate -> inspect -> register
input_data/{csv,databases,metadata}/README.md
tests/fixtures/{__init__,synthetic}.py
tests/test_csv_loader.py  test_sqlite_loader.py  test_filesystem_loader.py
tests/test_dataset_model.py  test_validators.py  test_processing_context.py
tests/test_dataset_registry.py  test_input_detector.py
tests/test_profiler_quality.py  test_pipeline_cli.py  test_run_pipeline_cli.py
docs/ARCHITECTURE.md  docs/DATA_INPUT_GUIDE.md  docs/PRIVACY.md
docs/PHASE_3_IMPLEMENTATION.md  reports/PHASE_3_INITIAL_INSPECTION.md
reports/PHASE_3_IMPLEMENTATION_REPORT.md
```

## Existing files changed (justified, behaviour-preserving)

| File | Change | Justification | Regression proof |
|---|---|---|---|
| `scanner/filesystem_scanner.py` | optional `outputs=` + `enable_resume=` constructor params; internal path refs now use `self.out_*` / `self.out_resume`; `_resume_allowed()` gates resume read/write | user-dataset scans must not write research CSVs or share resume state | default constructor identical (reads config at use); scanner tests 17/17 + loader tests |
| `pipeline.py` | `build_parser()`; no-flags = usage exit 2; `--scan-root`; `--yes` drive gate; `run_scan(root=None)` | Phase 3 §12 (full-drive scan explicit opt-in), audit R3 | `test_pipeline_cli.py` incl. scanner-bomb safety net |
| `config.py` | additive platform constants + `ensure_platform_dirs()` **outside** the import-time mkdir loop | new dirs without import side effects | `test_platform_dirs_created_only_on_explicit_request` |
| `.gitignore` | appended `output/` + `data/_phase3_evidence_*/` | platform runtime outputs must never be staged (keeps Phase-2 rules) | n/a (no git operations performed) |

`app.py` and `dashboard/*`: **not modified**; import-regression tested
(8 pages + app).

## Deviations from the proposed target structure

1. **No `database/schema|repositories` package** (Phase 4): the registry is
   self-contained in `ingestion/dataset_registry.py` with its **own** SQLite
   file `output/metadata/dataset_registry.db`; the research database is
   refused by constructor guard + test.
2. **`output/` is created lazily** (by `ProcessingContext` /
   `ensure_platform_dirs()`), not committed, gitignored.
3. **`run_pipeline.py` is inspect-only** (detect/validate/inspect/register);
   the analysis CLI arrives with Phase 5.
4. **`dataset_profiler` / `data_quality` are minimal foundations** (full
   Mode B pipeline in Phase 5).
5. **Cancellation for folder scans** is supported only *before* a scan starts;
   the underlying scanner has no mid-walk cancel hook (documented, not faked).
6. **`requirements.txt` not re-pinned** (Phase 2 pin proposal awaits separate
   approval; not a Phase 3 acceptance item).

## Safety mechanics implemented

* `ProcessingContext` refuses (raises `PlatformError`) any target inside
  `data/raw`, `data/processed`, `data/snapshots`, the research database,
  `reports/`, `logs/` (tested both positively and negatively).
* `FilesystemLoader` forces `enable_resume=False` + custom `outputs`, asserts
  its output dir is outside protected roots, and never touches
  `config.FILES_CSV` (mtime gate in tests).
* SQLite: URI read-only mode, membership check against `sqlite_master`,
  `validate_table_name` regex, quoted identifiers, bound LIMIT/OFFSET.
* CSV: raw-header duplicate detection (pre-pandas mangling), encoding and
  delimiter sniffing, chunked row counts, `read_all` documented as explicit.
* Errors: typed hierarchy with redacted `user_message`; CLI prints no raw
  paths.

## How to verify

```text
python -m pytest tests -q          # 207 passed, 1 skipped expected
python check_fstrings.py           # CLEAN
python -m py_compile <all changed files>
python <temp>/phase3_integrity_gate.py   # 22 protected files identical
```
