# Data Dictionary

Digital Archaeology - definitions of every variable collected or derived.
All values originate from the local file system of `D:\`; no document
contents were read.

## 1. `data/raw/files_metadata.csv` (one row per file)

| # | Variable | Data type | Unit / domain | Definition |
|---|---|---|---|---|
| 1 | `file_id` | integer | 1..N | Sequential identifier assigned during the scan |
| 2 | `file_name` | string | | File name with extension, as stored |
| 3 | `file_extension` | string | `.ext` or empty | Lower-case extension including leading dot |
| 4 | `file_size_bytes` | integer | bytes | Apparent size from `os.stat` |
| 5 | `file_size_kb` | float | KB | `file_size_bytes / 1024` |
| 6 | `file_size_mb` | float | MB | `file_size_bytes / 1024^2` |
| 7 | `creation_timestamp` | string (ISO 8601) | local time | Windows record-change time (`st_ctime`) |
| 8 | `creation_date` | string `YYYY-MM-DD` | date | Date part of `creation_timestamp` |
| 9 | `modified_timestamp` | string (ISO 8601) | local time | Last write time (`st_mtime`) |
| 10 | `modified_date` | string `YYYY-MM-DD` | date | Date part of `modified_timestamp` |
| 11 | `accessed_date` | string `YYYY-MM-DD` | date | Last access date where safely available |
| 12 | `full_file_path` | string | absolute path | Complete path of the file |
| 13 | `parent_folder` | string | absolute path | Directory immediately containing the file |
| 14 | `root_folder` | string | name | First path level under the scan root |
| 15 | `folder_depth` | integer | levels | Depth of the containing folder (root children = 1) |
| 16 | `folder_path` | string | absolute path | Same as `parent_folder`; kept for clarity |
| 17 | `hidden_file_status` | boolean | true/false | `FILE_ATTRIBUTE_HIDDEN` set |
| 18 | `read_only_status` | boolean | true/false | `FILE_ATTRIBUTE_READONLY` set |
| 19 | `file_category` | enum | see below | Extension-mapped category |
| 20 | `naming_pattern` | string | see R1-R9 | Concatenated naming signature |
| 21 | `contains_version_indicator` | boolean | | Rule R1 matched (`v1`, `version 2`, ...) |
| 22 | `contains_final_keyword` | boolean | | Rule R2 or R3 matched (`final`, `latest`) |
| 23 | `contains_date_indicator` | boolean | | Rule R4 matched (date-like token) |
| 24 | `contains_copy_indicator` | boolean | | Rule R5 matched (`(1)`, `copy`, `backup`) |
| 25 | `filename_length` | integer | characters | Length of `file_name` |
| 26 | `capitalization_style` | enum | lower/upper/title/mixed/no_letters | Rule R7 result |
| 27 | `has_underscore` | boolean | | `_` present in stem |
| 28 | `has_space` | boolean | | whitespace present in stem |
| 29 | `has_hyphen` | boolean | | `-` present in stem |
| 30 | `collection_timestamp` | string (ISO 8601) | local time | When this row was collected |

### `file_category` enum

`Programming`, `Documents`, `Spreadsheets`, `Presentations`, `Images`,
`Videos`, `Audio`, `Datasets`, `Archives`, `Executables`, `System-related`,
`Other`. The mapping is the table `EXTENSION_CATEGORY_MAP` in
`scanner/metadata_extractor.py` and is applied by extension only.

## 2. `data/raw/folders_metadata.csv` (one row per folder)

| # | Variable | Data type | Definition |
|---|---|---|---|
| 1 | `folder_path` | string | Absolute folder path |
| 2 | `folder_name` | string | Final path component |
| 3 | `parent_folder` | string | Parent directory |
| 4 | `folder_depth` | integer | Levels below scan root |
| 5 | `direct_file_count` | integer | Files directly inside |
| 6 | `recursive_file_count` | integer | Files in folder and all descendants |
| 7 | `direct_subfolder_count` | integer | Immediate subfolders |
| 8 | `recursive_folder_count` | integer | All descendant folders |
| 9 | `total_size_bytes` | integer | Recursive size in bytes |
| 10 | `total_size_mb` | float | Recursive size in MB |
| 11 | `collection_timestamp` | string | When this row was collected |

## 3. `data/raw/scan_errors.csv`

| Variable | Type | Definition |
|---|---|---|
| `error_timestamp` | ISO 8601 | When the failure occurred |
| `error_type` | enum | e.g. `PermissionDenied`, `OSError`, `NotFound`, `SymlinkSkipped`, `MaxDepthExceeded` |
| `path` | string | Affected path |
| `operation` | string | `scandir`, `stat`, `write`, ... |
| `message` | string | Truncated OS/Python error text |

Errors are **recorded, not discarded**; affected objects are excluded from
totals and counted in the scan report.

## 4. Derived / processed columns (`data/processed/files_processed.csv`)

| Variable | Type | Definition |
|---|---|---|
| `size_invalid` | boolean | Size missing or negative (flag only) |
| `size_zero` | boolean | Zero-byte file |
| `is_duplicate_record` | boolean | Same `full_file_path` seen earlier in the file |
| `creation_date_parsed` | datetime64 | Parsed creation date (NaT if invalid) |
| `modified_date_parsed` | datetime64 | Parsed modification date |
| `*_invalid` | boolean | Original value present but unparseable |
| `creation_year`, `creation_month`, `creation_year_month` | int/int/str | Extracted date parts |
| `modified_year`, `modified_month`, `modified_year_month` | int/int/str | Extracted date parts |
| `computed_depth` | integer | `full_file_path` level count (verification) |
| `depth_mismatch` | boolean | Scanned depth differs from computed depth |

Original raw values are preserved; cleaning never overwrites them with
invented data.

## 5. Duplicate outputs

| File | Variables |
|---|---|
| `duplicate_candidates.csv` | `duplicate_group_id`, `file_name`, `file_extension`, `file_size_bytes`, `file_size_mb`, `copy_count`, `paths`, `folders`, `level`, `status` |
| `verified_duplicates.csv` | `duplicate_group_id`, `sha256`, `file_name`, `file_size_bytes`, `copy_count`, `extra_copies`, `extra_copy_bytes`, `paths`, `status` |

`status` for candidates is always `candidate_not_verified`. Verified rows
appear only when SHA-256 verification is explicitly enabled.

## 6. Naming rules (as implemented)

| Rule | Meaning |
|---|---|
| R1 | Version indicator: `v1`, `v2.1`, `version 3` as a standalone token |
| R2 | Final indicator: token `final` or `finalN` |
| R3 | Latest indicator: token `latest` |
| R4 | Date indicator: `2026-07-27`, `27-07-2026`, or a bare `19xx`/`20xx` year |
| R5 | Copy indicator: `(1)`, `copy`, `duplicate`, `backup` |
| R6 | Separators present: underscore / space / hyphen |
| R7 | Capitalisation: lower, upper, title, mixed (mixed = flagged) |
| R8 | Very long: stem > 80 characters |
| R9 | Repeated whitespace or 3+ consecutive separators |

