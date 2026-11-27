# Project structure

What every folder and key file in the repository is for, and how the parts
relate to each other. Paths are relative to the project root (the folder
containing `app.py`). Verified against the workspace on 2026-11-23.

## Top level

| Path | Purpose | Notes |
|---|---|---|
| `app.py` | Streamlit entry point: 12 pages, sidebar, snapshot panel | Launch with `scripts\run_windows.bat` |
| `pipeline.py` | Research CLI — explicit metadata scan + analysis (`--scan`, `--scan-root`, `--yes`) | Without flags prints usage; never scans implicitly |
| `run_pipeline.py` | Platform CLI — detect → inspect → register a dataset (`--input PATH`) | Writes only under `output/` |
| `config.py` | Central paths, workspace resolution, scan behaviour, protected paths | `DIGITAL_ARCHAEOLOGY_HOME` / `DA_SCAN_ROOT` handled here |
| `requirements.txt` | The four runtime dependencies | Unchanged by design |
| `check_fstrings.py` | Developer check: no backslashes in f-string expressions | Run before finishing any change |
| `README.md` | Project overview and quick start | |
| `.gitignore` | Default-deny rules for private research data and outputs | |

## Code packages

| Package | Responsibility | Key files | Relates to |
|---|---|---|---|
| `core/` | Shared domain layer | `dataset.py` (Dataset model, uuid5 identity), `exceptions.py` (redacted errors), `dataset_metadata.py`, `processing_context.py` (per-run output context + **protected-path write guard**) | Imported by everything; the guard is what protects `data/`, `reports/` |
| `ingestion/` | Source detection and loading | `input_detector.py`, `validators.py`, `base_loader.py`, `csv_loader.py`, `sqlite_loader.py`, `filesystem_loader.py`, `dataset_registry.py`, `workflow.py` | Used by Data Sources page, `run_pipeline.py`; produces `Dataset`s for `analysis/` |
| `scanner/` | Metadata-only folder scanning | `filesystem_scanner.py`, `metadata_extractor.py`, `folder_analyzer.py`, `snapshot_manager.py`, `duplicate_detector.py` | Feeds Mode A research pages |
| `analysis/` | All computation | Mode A research modules (`temporal`, `storage`, `filetype`, `naming`, `structural`, `duplicate_analysis`, `report_generator`, …); Mode B engine (`dataset_profiler`, `data_quality`, `statistics`, `distributions`, `correlation`, `outliers`, `frequency_analysis`, `analysis_runner`); `comparison/` (CSV, SQLite, filesystem, metadata comparisons) | Called by dashboard pages and both CLIs; writes to `data/processed` (Mode A) or `output/analysis|comparisons` (platform) |
| `visualization/` | Chart engine (Streamlit-independent) | `chart_registry.py` (57 specs, 3 sections), `chart_factory.py`, `archaeology_visualizations.py`, `dataset_visualizations.py`, `comparison_visualizations.py`, `validation.py`, `data_adapter.py`, `filters.py`, `export.py`, `plotly_helpers.py`, `visualization_config.py` | Consumed by Advanced Visualization page and tests |
| `dashboard/` | The 12 pages | Research: `overview`, `timeline`, `storage`, `filetypes`, `naming`, `duplicates`, `folder_structure`, `findings`; platform: `data_sources`, `dataset_analysis`, `dataset_comparison`, `advanced_visualization`; shared: `common.py` (load/filter/first-run) | All wired in `app.py::PAGES` |
| `scripts/` | Windows helpers | `install_windows.bat`, `run_windows.bat`, `check_environment.bat` + `check_environment.py` | `%~dp0`-rooted, non-destructive |
| `tests/` | 423-test suite | `tests/conftest.py`, `tests/fixtures/synthetic.py`, `tests/test_portability.py` (17 portability scenarios), per-package tests | All writes go to temp dirs; protected roots asserted |

## Data and output locations (workspace)

| Path | Owner | Purpose |
|---|---|---|
| `data/raw/`, `data/processed/` | research | Scan CSVs and derived research tables — **protected** |
| `data/database/` | research | SQLite archive — **protected**, never written by application code |
| `data/snapshots/` | research | Snapshot index used by comparisons — **protected** |
| `reports/` | research | Findings, research report, charts — **protected** (gitignored default-deny) |
| `logs/` | runtime | `run.log`, scan resume state — local only |
| `backups/`, `data/_*_evidence_*/` | research | Incident evidence — never modified |
| `input_data/` | user | Drop zones for your CSV/SQLite/folder inputs (gitignored) |
| `output/` | platform | Registry, datasets, analysis runs, comparisons, chart exports (gitignored, safe to delete) |
| `tests/` | code | Always travels with the code, even with a workspace override |

## Documentation map

| Document | Audience | Content |
|---|---|---|
| `README.md` | everyone | Overview, quick start, modes, links |
| `docs/WINDOWS_INSTALL.md` | new users | Install, verify, launch, troubleshoot |
| `docs/architecture/operating-modes.md` | everyone | The four modes, full reference |
| `docs/architecture/*.md` | contributors | Diagrams A–E |
| `docs/PROJECT_STRUCTURE.md` | contributors | This file |
| `docs/PRIVACY_AND_DATA_PROTECTION.md` | everyone | Privacy model and limits |
| `docs/DATA_INPUT_GUIDE.md`, `docs/COMPARISON_GUIDE.md`, `docs/VISUALIZATION_GUIDE.md` | users | Deep dives per engine |
| `docs/ARCHITECTURE.md` | history | Phase 3-era package reference (banner marks it historical) |
| `reports/PHASE_*` | local only | Phase reports — gitignored, not published |

## Dependency relationships (one line)

```text
app.py / CLIs  →  dashboard, analysis, ingestion, scanner, visualization
every package  →  core (model, exceptions, write guard)
everything     →  config.py (paths + workspace)
outputs        →  data/ + reports/ (research, protected)  |  output/ + input_data/ (platform)
```
