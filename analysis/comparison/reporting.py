"""Comparison report writer (Phase 6): deterministic, bounded outputs.

Every run folder receives the same file set:

* ``comparison_summary.json`` - byte-stable for identical inputs
  (no run id, no timestamps, sorted keys, ``allow_nan=False``)
* ``schema_differences.json`` / ``statistics_differences.json``
* ``warnings.json`` - never silent: bounded exports are listed here with
  their exact totals
* ``records_added.csv`` / ``records_removed.csv`` /
  ``records_modified.csv`` (or the filesystem equivalents)

CSV rows are sorted by their rendered text so identical inputs always
produce byte-identical exports. Nothing is ever written outside the run
directory (``assert_writable_target``).
"""
from __future__ import annotations

import csv
import json

from analysis.comparison.base import (FILE_SCHEMA, FILE_STATS,
                                      FILE_SUMMARY, FILE_WARNINGS,
                                      json_safe, warn, write_json)
from core.processing_context import assert_writable_target

_DEFAULT_TABLE_LIMIT = 10_000


def _fmt(value) -> str:
    safe = json_safe(value)
    if safe is None:
        return ""
    if isinstance(safe, bool):
        return "True" if safe else "False"
    if isinstance(safe, str):
        return safe
    if isinstance(safe, (list, dict)):
        return json.dumps(safe, sort_keys=True, ensure_ascii=False)
    return str(safe)


def _write_table(run_dir, table: dict, limit: int) -> dict:
    """Write one bounded CSV; returns the truncation record (or {})."""
    name = str(table.get("file") or "")
    columns = [str(c) for c in (table.get("columns") or [])]
    rows = list(table.get("rows") or [])
    total = int(table.get("total", len(rows)))
    exported = len(rows)
    if limit >= 0 and exported > limit:
        rows = rows[:limit]
        exported = len(rows)
    rendered = [tuple(_fmt(v) for v in row) for row in rows]
    rendered.sort()                      # byte-reproducible exports
    target = run_dir / name
    assert_writable_target(target)
    with open(target, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        writer.writerows(rendered)
    if exported < total:
        return {"exported": exported, "total": total}
    return {}


def write_run(*, run_dir, kind: str, cfg, comparison_id: str, run_id: str,
              reg_a: dict, reg_b: dict, inputs, parts: dict,
              stage_notes: list, warnings: list) -> dict:
    """Write every output of a completed comparison and return the summary."""
    truncated: dict = {}

    # 1) record tables (bounded exports) - collected first so that the
    #    truncation warnings are part of warnings.json, never silent.
    table_warnings: list[dict] = []
    for table in parts.get("record_tables") or []:
        limit = int(cfg.max_export_rows)
        record = _write_table(run_dir, table, limit)
        if record:
            truncated[str(table.get("file"))] = record
            table_warnings.append(warn(
                "truncated",
                f"{table.get('file')}: exported {record['exported']:,} of "
                f"{record['total']:,} row(s) (bounded export) - the counts "
                "in the summary remain exact."))

    # 2) schema + statistics payloads
    schema = parts.get("schema") or {"kind": kind, "status": "not-run"}
    statistics = parts.get("statistics") or {"kind": kind,
                                             "status": "disabled"}
    write_json(run_dir, FILE_SCHEMA, schema)
    write_json(run_dir, FILE_STATS, statistics)

    # 3) warnings (verification + matching + truncation), then summary.
    #    The truncation warnings are appended to the caller's list so the
    #    returned ComparisonResult and warnings.json always agree.
    warnings.extend(table_warnings)
    all_warnings = list(warnings)
    write_json(run_dir, FILE_WARNINGS, all_warnings)

    summary: dict = {
        "comparison_id": comparison_id,
        "kind": kind,
        "status": "completed",
        "datasets": {
            "a": {"dataset_id": reg_a.get("dataset_id"),
                  "name": reg_a.get("name"),
                  "source_type": reg_a.get("source_type")},
            "b": {"dataset_id": reg_b.get("dataset_id"),
                  "name": reg_b.get("name"),
                  "source_type": reg_b.get("source_type")},
        },
        "strategy": parts.get("strategy", ""),
        "exactness": parts.get("exactness", ""),
        "keys": list(parts.get("keys") or []),
        "counts": dict(parts.get("counts") or {}),
        "truncated": truncated,
        "warnings_count": len(all_warnings),
        "notes": [str(n) for n in (stage_notes or [])],
    }
    for key, value in (parts.get("summary_extra") or {}).items():
        summary.setdefault(key, value)
    write_json(run_dir, FILE_SUMMARY, summary)
    return summary
