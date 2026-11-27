# Privacy and Data Protection

How the Digital Archaeology platform handles your data. Everything in this
document is based on code inspection of the current implementation
(verified 2026-11-23). No security guarantee beyond what is stated here is
claimed.

Companion documents: the short statement in [PRIVACY.md](PRIVACY.md) and
the boundary diagram
[architecture/privacy-boundaries.md](architecture/privacy-boundaries.md).

## 1. Local processing

- The application runs entirely on your machine: a local Streamlit process
  serving `http://localhost:8501` to your local browser.
- **No network access from application code.** There is no cloud client,
  no API integration, no telemetry, no analytics and no model calls.
  (The only network operation in the whole project is `pip install` while
  following the installation guide — it downloads *packages*, never your
  data.)
- No automatic full-drive scan exists: `pipeline.py` without flags prints
  usage (exit 2), whole-drive roots additionally require `--yes`, and the
  dashboard launch script never triggers a scan.

## 2. What is read

| Source | What is read | What is never read |
|---|---|---|
| Folder tree (research scan / filesystem dataset) | **Metadata only**: name, size, timestamps, path | **File contents** — never opened |
| CSV you point at | Bytes needed to parse rows/columns, bounded (`nrows` / `usecols` for previews) | Rest of your disk |
| SQLite you point at | Table listing + selected query results through `file:...?mode=ro` | Writes are refused by SQLite itself |
| Research data under `data/` | Read-only by the 8 research pages | Never written by platform code |

Input files are never executed, imported or evaluated. Source detection
reads at most the first 64 KB of a file in binary mode, only to classify
it.

## 3. What is stored, and where

| Artefact | Location | Contains |
|---|---|---|
| Dataset registry | `output/metadata/dataset_registry.db` | **Descriptors only**: id, name, source type/path, statuses, counts, size — never row contents (asserted by `test_registry_stores_descriptors_not_contents`) |
| Inspection metadata | `output/metadata/*_inspect.json` | Schema, row/column counts, warnings |
| Analysis runs | `output/analysis/` | Statistics/quality artifacts derived from your data |
| Comparisons | `output/comparisons/` | Diff summaries and bounded record exports |
| Chart exports | `output/visualizations/` | HTML/CSV bundles with provenance; no absolute paths (re-checked by `visualization/export.py`) |
| Your inputs | `input_data/` drop zones | Your own files, removable at any time |
| Runtime log | `logs/run.log` | Local events (contains paths — stays local) |

Everything under `data/`, `output/`, `logs/`, `backups/`, `*.db`,
`input_data/*` (except README stubs) and `reports/*` (default-deny) is
covered by `.gitignore`, so private data can never be staged or pushed by
accident once the repository is tracked.

## 4. Research-data preservation

- The original research database
  (`data/database/digital_archaeology.db`), research CSVs, snapshots,
  evidence folders, historical reports and backups are **protected**: the
  platform never writes, moves, renames or deletes them.
- Enforcement: `core.processing_context.ProcessingContext` refuses
  writes to protected paths, and a **22-file SHA-256 integrity gate**
  re-verifies that every protected file is byte-identical after each
  development phase (baseline:
  `data/_phase3_evidence_20261116/integrity_before_phase3.txt`).
- Platform initialization creates only missing directories
  (`mkdir(exist_ok=True)`) — it never deletes or overwrites anything and
  never recreates the research database.

## 5. Workspace configuration and privacy

- `DIGITAL_ARCHAEOLOGY_HOME` re-resolves where `data/`, `reports/`,
  `logs/`, `output/` and `input_data/` are looked up. It **never** moves
  or copies existing data.
- If an override would hide existing research data, the sidebar shows an
  explicit warning naming both locations — switching away from your data
  is never silent.
- Tests write exclusively to OS temporary directories and assert they stay
  outside every protected root.

## 6. Sensitive-file considerations

- Folder scans traverse names, sizes and timestamps of everything under
  the chosen root (except Windows system locations such as
  `System Volume Information`, `$Recycle.Bin`, `Recovery`). File names
  themselves may be sensitive: scan outputs are local and gitignored.
- User-facing error messages print **file names only**
  (`core.exceptions.redact_path`); absolute paths appear solely in the
  local log and in messages about a path you typed yourself.
- Charts and exports never embed absolute paths.
- If you contribute datasets, place them in `input_data/`; delete them
  normally when done. Removing `output/metadata/dataset_registry.db`
  clears the registry.

## 7. Known privacy limitations (honest list)

1. **Local file names are visible to the tool** — metadata scanning means
   names, sizes and timestamps are read; anyone with access to your
   machine can see scan outputs (they are ordinary local files).
2. **No encryption at rest** — outputs are plain CSV/JSON/SQLite on your
   disk; use OS-level encryption (BitLocker etc.) if that matters.
3. **No access control inside the app** — whoever can run the app can see
   the workspace it points at.
4. **`logs/run.log` contains local paths** — keep it local (it is
   gitignored); do not share it verbatim if paths are sensitive.
5. **Dependency installation needs the internet** once; the application
   itself does not.
6. **Optional scikit-learn** is fetched from PyPI if you choose to install
   it; it does not transmit your data.
7. **This document describes the code as it exists**; it is not an
   external security audit and no penetration testing was performed.
