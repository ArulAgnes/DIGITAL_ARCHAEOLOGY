# Architecture

Reusable data-analysis platform built in Phase 3 on top of the existing
Digital Archaeology project. All paths below are relative to
`<PROJECT_ROOT>`; no personal or machine-specific paths are used in this
document.

## Layer overview

```text
                    +--------------------------------------+
   CLI              | run_pipeline.py   pipeline.py        |
                    +-------------------+------------------+
                                        |
        +---------------+---------------+---------------+
        |               |                               |
   +----v-----+   +-----v------+   +--------------------+----+
   | ingestion|   |  analysis  |   | dashboard (Streamlit)   |
   | loaders  |   | profiler   |   | app.py + 8 pages        |
   | detector |   | quality    |   | (Mode A, untouched)     |
   | registry |   | (Mode B)   |   +-------------------------+
   +----+-----+   +-----+------+
        |               |
   +----v---------------v---------------------------------+
   | core: Dataset model, exceptions, metadata, context    |
   +----+--------------------------------------------------+
        |
   +----v--------------------------------------------------+
   | scanner/filesystem_scanner (single-pass, metadata)    |
   +-------------------------------------------------------+
```

## Packages

| Package | Contents | Responsibility |
|---|---|---|
| `core/` | `dataset.py`, `exceptions.py`, `dataset_metadata.py`, `processing_context.py` | One `Dataset` object for any source; redacted, typed errors; per-run output context with research-data write guard |
| `ingestion/` | `input_detector.py`, `validators.py`, `base_loader.py`, `csv_loader.py`, `sqlite_loader.py`, `filesystem_loader.py`, `dataset_registry.py` | Detect -> validate -> inspect (chunked) -> register |
| `analysis/` | existing Mode A modules + `dataset_profiler.py`, `data_quality.py` | Mode A untouched; Mode B foundations operate on any tabular frame |
| `scanner/` | `filesystem_scanner.py` (+ add. `outputs`/`enable_resume` params) | Single-pass metadata scan; reused by the filesystem loader |
| `dashboard/`, `app.py` | 8 pages via one cached `load_data()` | Research UI; **no Phase 3 changes** |

## Dataset lifecycle

```text
path --detect()--> DetectionResult (csv | sqlite | filesystem | rejected)
     --validate()--> ValidationResult list (ok | warning | error)
     --load_metadata()--> DatasetMetadata (chunked; bounded memory)
     --to_dataset()--> Dataset (stable uuid5 id, fingerprint, statuses)
     --registry.register()--> output/metadata/dataset_registry.db
```

* **Identity**: `uuid5(namespace, source_type + resolved path)` - stable,
  never derived from the file name alone.
* **Change detection**: `source_fingerprint = size:mtime_ns` (no content read).
* **Statuses**: `ValidationStatus` (`pending|ok|warning|failed`) and
  `ProcessingStatus` (`registered|inspected|processing|completed|failed|cancelled`).

## Data locations

| Location | Owner | Policy |
|---|---|---|
| `data/raw`, `data/processed`, `data/database/digital_archaeology.db`, `data/snapshots`, research `reports/*` | research (Mode A) | **protected**: never written by platform code; enforced by `ProcessingContext.is_protected_research_path()` and asserted in tests |
| `output/datasets`, `output/analysis`, `output/reports`, `output/metadata` | platform | created **lazily** (never at import time) by `ProcessingContext.ensure_dirs()`; gitignored |
| `input_data/{csv,databases,metadata}` | user drop zones | gitignored except README stubs |

## Configuration side-effect policy

`config.py` keeps its original import-time directory loop (9 research/test
dirs, relied on by existing code). Phase 3 constants (`INPUT_*`, `OUTPUT_*`,
`REGISTRY_DB`, `PLATFORM_DIRS`) are **not** part of that loop; they are created
only when `config.ensure_platform_dirs()` or a `ProcessingContext` asks for
them.

## Compatibility rules

1. `FilesystemScanner(root, workspace, outputs=None, enable_resume=None)` -
   defaults reproduce the historical behaviour exactly (config paths, config
   resume switch). Custom `outputs` **force resume off** so dataset scans can
   never share state with research scans.
2. `pipeline.py` keeps `--scan/--analyse-only/--hash/--no-report`; running
   with no flags now prints usage (exit 2) instead of silently scanning.
   Whole-drive roots additionally require `--yes` (`--scan-root` selects the
   target). *Justification: Phase 3 §12 + audit R3.*
3. Dashboard: `app.py` and `dashboard/*` unchanged; regression-tested by
   import checks on all 8 pages.

## Extension points

* **New source type**: subclass `ingestion.base_loader.BaseLoader`
  (`validate` / `load_metadata` / `iter_chunks` / `to_dataset`), add a branch
  in `ingestion.input_detector.detect()` + `SourceType` enum value, export in
  `ingestion/__init__.py`.
* **New validation**: add a function in `ingestion/validators.py` returning
  `ValidationResult`; map new error codes in `_CODE_TO_EXCEPTION` if a new
  exception type is needed.
* **Mode B analyses**: build on `analysis.dataset_profiler` /
  `analysis.data_quality`; consume any `Dataset`/DataFrame, write under
  `output/analysis/` (Phase 5).

## Testing model

* `tests/fixtures/synthetic.py` provides CSV/SQLite/tree builders and
  `assert_outside_research_data()` - every write-capable test asserts its
  outputs land outside the protected roots.
* `tests/conftest.py::scanner_config` continues to redirect all legacy
  `config` paths into pytest temp dirs for Mode A tests.
* The full suite (207 tests) runs in under 5 seconds; 91 of those are the
  pre-existing Mode A regression tests.
