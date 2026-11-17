# Dataset Comparison Guide

How the comparison engine (Phase 6) works, what it proves, and what it
deliberately never claims. Run everything from `<PROJECT_ROOT>` with the
project virtualenv Python.

## What a comparison is

Compare **two datasets that are already registered on the Data Sources
page** and produce a self-contained, reproducible report under:

```text
output/comparisons/<comparison_id>/runs/<run_id>/
```

* CSV vs CSV (record rows), SQLite vs SQLite (one registered table each),
  filesystem snapshot vs filesystem snapshot (scan metadata).
* Both datasets must be of the **same source type**; they must differ.
* Sources are opened **read-only** (SQLite via `mode=ro`, CSV/scan files
  by read). A source that changed or disappeared after registration
  **refuses** the run with guidance instead of comparing stale data.

## Quick start (dashboard)

1. Open the **Dataset Comparison** page.
2. Pick the comparison type (CSV / SQLite / Filesystem).
3. Pick dataset A and dataset B (same type, different datasets).
4. For CSV/SQLite: pick the key column(s), or leave empty for exact
   content matching.
5. Press **Validate keys & sources** - a read-only pre-flight that
   verifies both fingerprints and checks the key columns.
6. Review the options (export bound, statistics, path case-folding,
   move detection, chunk size, key index limit).
7. Press **Start comparison** (locked until validation passes).
8. Explore the tabs: Summary, Added, Removed, Modified, Schema,
   Statistics, Warnings, Files & downloads (filesystem adds Moved).
9. Re-open any past run from **Comparison run to view**.

Nothing runs automatically: loading the page never starts a comparison
or a validation.

## Quick start (programmatic)

```python
from analysis.comparison import (
    ComparisonConfig, run_comparison, validate_selection,
    list_comparisons, load_comparison)

check = validate_selection("csv", ds_a, ds_b, ["id"])
if check["ok"]:
    result = run_comparison("csv", ds_a, ds_b,
                            config_obj=ComparisonConfig())
    if result.ok:
        print(result.summary["counts"], result.run_dir)
```

`run_comparison` returns `ComparisonResult` with `ok`, `status`
(`completed | refused | failed | cancelled`), `summary`, `warnings`,
`files`, `progress` and `error` (public guidance, no full paths).

## Matching strategies

### `key` strategy (record types)

* Rows are paired by one to eight key columns. Row order is **never**
  used for matching.
* Paired rows whose other columns are equal are **matched**; rows that
  differ are **modified** (the changed fields are exported as
  `col: old -> new`).
* Rows with a missing key value, or a key value that occurs more than
  once on either side, are counted in their own categories
  (`null_key_rows_*`, `duplicate_key_rows_*`) - never silently dropped,
  never guessed.
* Accounting invariant:
  `a_rows = matched + modified + removed + duplicate_key_rows_a +
  null_key_rows_a + unindexed_rows_a`.
* Values are canonicalised first: `1` and `1.0` compare equal, missing
  matches missing.

### `content-multiset` strategy (fallback)

Used when no usable key column is selected or detected (and for
explicit empty key lists):

* Rows are compared by exact content only. A changed record therefore
  appears as **one removal plus one addition** - records cannot be
  paired honestly.
* The strategy is always labelled in the summary and always carries a
  `no_key_columns` warning plus a `limitations` list.

If an auto-detected key no longer fits the data, the run falls back to
content matching with an `auto_key_unusable` warning; an **explicitly**
selected key that does not exist fails the run with guidance.

## Filesystem comparison (metadata only)

* Match key: normalized **relative path inside each dataset's recorded
  scan root** (forward slashes, optional case-folding - default on).
  Folders are never re-walked, file contents are never read.
* **Modified** means a recorded metadata field changed:
  `file_size_bytes`, `creation_timestamp`, `modified_timestamp`,
  `naming_pattern`, `capitalization_style`, `hidden_file_status`,
  `read_only_status`, `file_category`.
* **Move/rename detection** is a labelled heuristic on metadata
  evidence (same name + size + timestamp, then same parent + size +
  timestamp). It is *strong evidence, never proof* that the content is
  identical. The summary field `move_detection` says
  `analysed | disabled | skipped`.
* Case-only path changes are counted in `case_changes` (samples in the
  statistics file). Duplicate paths and duplicate basenames are counted
  and reported, never paired.
* Two row-accounting invariants are checked; a violation downgrades the
  run to `approximate` and raises an `accounting_mismatch` warning.

## Schema comparison

* CSV: columns added/removed, observed type changes (first chunk of
  each file), row counts.
* SQLite: the same, plus table lists (`sqlite_master`), per-table
  counts and `table_list_diff` - all read-only.
* Filesystem: compared field list, column differences between the two
  scan outputs, folder list differences from `folders_metadata.csv`.

## Statistics

* One full pass over both datasets - exact (never sampled), merged
  chunk by chunk.
* Numeric columns: count, missing, mean, std, min, max, unique
  (`delta = B - A`, rounded to 12 decimals).
* Categorical columns: counts, missing, unique plus the top-k values of
  each side with per-value deltas.
* Columns whose type basis differs between the datasets get no delta -
  a `stats_basis_mismatch` warning instead.
* Filesystem statistics: storage totals and delta, extension table,
  duplicate basenames, naming-pattern changes, path case changes.
* Can be disabled entirely (`stats_enabled=False`); the statistics file
  then records `status: disabled`.

## Limits and memory

| Limit | Default | Meaning |
|---|---|---|
| `chunk_size` | 100000 | rows per read while streaming |
| `max_keys` | 1000000 | size of the in-memory A-side index; overflow downgrades exactness to `approximate` with a `key_index_limit` warning |
| `max_export_rows` | 10000 | bound per export CSV; totals in the summary stay exact and the truncation is reported as a `truncated` warning |
| `stats_max_columns` | 50 | per-column statistics cap (excess columns are counted in `columns_skipped`) |
| `categorical_top_k` | 20 | top values listed per categorical column |
| key columns | max 8 | more raises a configuration error |

Memory is O(distinct keys of dataset A) for record comparisons and
O(paths of dataset A) plus bounded move candidates for filesystem
comparisons. Datasets are streamed - neither side is ever fully
materialised in memory.

## Output layout

```text
output/comparisons/<comparison_id>/runs/<run_id>/
  config.json                 run id, pair, full configuration
  comparison_summary.json     byte-stable summary (no run id/timestamps)
  schema_differences.json     column/table/folder differences
  statistics_differences.json exact statistical deltas
  warnings.json               every deviation, including truncations
  run_metadata.json           status, duration, versions, progress log
  records_added.csv           (csv/sqlite) bounded, sorted exports
  records_removed.csv
  records_modified.csv        key + changed_fields + B-side values
  added_files.csv             (filesystem) bounded, sorted exports
  removed_files.csv
  modified_files.csv          path + change_kinds + changed_fields
  moved_files.csv             change + path_from + path_to
```

Even refused and failed runs leave a complete run folder
(`config.json`, `comparison_summary.json`, `warnings.json`,
`run_metadata.json`) so the evidence is never lost.

## Reproducibility

* CSV exports are sorted by rendered text: identical inputs produce
  **byte-identical** export files across runs.
* `comparison_summary.json` contains no run id and no timestamps -
  repeated runs of the same pair are byte-identical.
* JSON is written with sorted keys and `allow_nan=False`.
* Verified by tests: two runs of the same pair and two runs with
  different `chunk_size` values produce byte-equal outputs.

## What this comparison does NOT prove

* Filesystem comparisons are **metadata only**: content changes cannot
  be proven without reading file contents (never done here).
* Move/rename pairs are metadata evidence, not proof of identical
  content.
* Content matching cannot pair records: changes appear as remove+add.
* Duplicate-key and null-key rows are reported separately, not matched.
* Approximate runs (index overflow, accounting mismatch) say so in
  `exactness` and in the warnings.
* Statistics describe each dataset's columns; they are not a hypothesis
  test.

## Safety

* Writes only under `output/comparisons/` - never `data/`, `reports/`,
  `logs/`, `backups/` (blocked by `assert_writable_target`).
* SQLite opened with `file:...?mode=ro`; source SHA-256 hashes are
  asserted unchanged by the test suite.
* Changed/missing sources refuse the run with guidance and recorded
  verification evidence.
* Error messages are public guidance - full paths stay in run metadata
  inside the run folder only.
* No network access, no telemetry, no dependency changes.
