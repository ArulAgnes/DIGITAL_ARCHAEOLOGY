# Privacy & Data-Safety Statement

This platform is designed so that **user data stays local, research data
stays untouched, and nothing is executed**.

## What is read

| Source | Read | Never read |
|---|---|---|
| CSV / SQLite you point at | file bytes needed to parse rows/columns (your explicit `--input`) | anything else on disk |
| Folder scan | file/folder **metadata only** (name, size, timestamps, path) | **file contents** - never opened |
| Research data under `data/` | nothing by platform commands | all of it (read-only even for Mode A dashboards) |

Input files are opened in read mode only; SQLite connections use the
`file:...?mode=ro` URI so writes are refused by SQLite itself. Input files
are never executed, imported, or evaluated (detection reads at most the first
64 KB in binary mode).

## What is stored, and where

| Artefact | Location | Contains |
|---|---|---|
| Inspection metadata | `output/metadata/<job>_inspect.json` | schema, row/column counts, warnings |
| Dataset registry | `output/metadata/dataset_registry.db` | **descriptors only**: id, name, source type/path, statuses, counts, size - **never row contents** (covered by `test_registry_stores_descriptors_not_contents`) |
| Folder-scan artifacts | `output/datasets/<id>/` | metadata rows of your own scan |
| Logs | `logs/run.log` | runtime events |

Everything under `output/`, `input_data/*` (except README stubs), `logs/`,
and `data/` is gitignored: private datasets and research data can never be
staged or pushed by accident (`.gitignore`, Phase 2 proposal + Phase 3
`output/` addition).

## What is never done

* Research CSVs, the research SQLite database, snapshots, and evidence
  folders are **never written or deleted** by platform code - enforced by
  `ProcessingContext.assert_writable_target()` and re-verified after every
  implementation step (22-file SHA-256 integrity gate, Phase 3 evidence in
  `data/_phase3_evidence_20261116/`).
* No network access; no telemetry; no model/API calls with your data.
* No personal documents are opened by the tooling (metadata only).
* No full-drive scan ever runs implicitly: `pipeline.py` without flags prints
  usage, and drive roots need `--yes` / `--confirm-drive`.

## Message redaction

User-facing errors print **file names only** (`core.exceptions.redact_path`);
absolute paths appear solely in local logs and in messages about the path the
user typed themselves. Tests assert redaction
(`test_require_raises_redacted_source_not_found`).

## If you contribute data

Place datasets in `input_data/` drop zones (gitignored). Remove them any time
with a normal file delete; the registry row can be ignored or removed by
deleting `output/metadata/dataset_registry.db`.
