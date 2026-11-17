# Data Input Guide

How to feed your own data into the platform. Run all commands from
`<PROJECT_ROOT>` with the project virtualenv Python.

## Supported inputs

| Type | Accepted | Notes |
|---|---|---|
| CSV | `.csv` (also `.tsv`/`.txt` with a warning) | **Header row required**; duplicate column names rejected; encoding auto-detected (BOM, UTF-8, CP1252, Latin-1); delimiter auto-detected (`, ; tab pipe`) |
| SQLite | `.db`, `.sqlite`, `.sqlite3` (+ content header check) | Opened **read-only**; single-table databases auto-select; multi-table needs `--table` |
| Folder | any directory | Metadata-only scan (no file contents read); whole drives require `--confirm-drive` |

## Quick start

```text
python run_pipeline.py --input path\to\file.csv
python run_pipeline.py --input path\to\db.sqlite --table events
python run_pipeline.py --input path\to\folder
python run_pipeline.py --input path\to\file.csv --no-register   # inspect only
```

Detection is automatic (`--mode auto`); force it with
`--mode csv|sqlite|filesystem` when the content is ambiguous.

## Options

| Option | Meaning |
|---|---|
| `--input PATH` | (required) file or folder to inspect |
| `--mode auto\|csv\|sqlite\|filesystem` | source-type hint (default `auto`) |
| `--table NAME` | SQLite table selection (letters/digits/underscore) |
| `--confirm-drive` | required to scan an entire drive (e.g. `D:\`) |
| `--chunk-size N` | rows per chunk when reading (default 100000) |
| `--no-register` | do not record the dataset in the registry |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success - inspection summary printed |
| 2 | invalid input, missing confirmation, or usage error (message on stdout) |
| 1 | unexpected failure (only the exception type is printed) |
| 130 | cancelled (Ctrl-C) |

## What gets written where

```text
output/
  metadata/<job>_inspect.json        full metadata (columns, counts, warnings)
  metadata/dataset_registry.db       registry index (descriptors only)
  datasets/<dataset_id>/             folder-scan artifacts (files/folders/errors CSV)
  analysis/, reports/                reserved for Phase 5 outputs
```

Public messages are path-redacted (file names only); full paths stay in
details/logs. Nothing under `data/` is ever written by these commands.

## Registry fields

`dataset_id, name, source_type, source_path, imported_at,
metadata_location, validation_status, processing_status,
source_fingerprint, row_count, column_count, bytes, notes`
- descriptors only, never dataset contents; re-running the same input
  upserts the same `dataset_id`.

## Memory model

* CSV/SQLite: read in **chunks** (`--chunk-size`); metadata statistics are
  computed from the first 1000 rows plus an exact chunked row count. Use
  `BaseLoader.read_all()` only when the dataset comfortably fits in memory.
* Folder: the scan streams metadata; the produced CSV can then be chunk-read
  via `FilesystemLoader.iter_chunks()`.

## Limitations (Phase 3)

* CSV files need a header row (headerless files are interpreted with the
  first row as header).
* Folder-scan progress is reported every 200 folders; mid-scan cancellation
  is not supported by the underlying scanner (cancel before starting works).
* Analysis/transform CLI (`--analyse` on user data) arrives in Phase 5.

## Troubleshooting

| Message | Fix |
|---|---|
| `does not exist` | check the path spelling |
| `unsupported extension / not a supported input type` | use `--mode`, or the file is binary/ambiguous |
| `duplicate column names` | fix the CSV header |
| `several tables; specify which one` | add `--table NAME` |
| `requires explicit confirmation (--confirm-drive)` | add `--confirm-drive` (or scan a subfolder instead) |
| `not a readable SQLite database` | file corrupted or wrong type |
