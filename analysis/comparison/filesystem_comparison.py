"""Filesystem snapshot comparison (Phase 6): metadata-only, path-keyed.

Both sides are compared from their registered scan outputs
(``files_metadata.csv``) - the folders are never re-walked, file
contents are never read, and nothing is written outside the run folder.

Matching keys on the *relative* path inside the recorded scan root
(forward slashes, optional case-folding). Modified detection is purely
metadata: size, timestamps, naming pattern and other recorded fields.
Move/rename detection is a labelled metadata heuristic (name, size and
timestamp evidence) - strong evidence, never proof of identical content.

Memory: one map of dataset A (bounded by ``max_keys``) plus the added
candidates needed for move detection (also bounded).
"""
from __future__ import annotations

import pandas as pd

from analysis.comparison import matching
from analysis.comparison.base import warn
from core.exceptions import PlatformError

_ENCODING = "utf-8-sig"
_PATH = "full_file_path"
_PARENT = "parent_folder"
_COMPARE_FIELDS = (
    "file_name", "file_extension", "file_size_bytes", "creation_timestamp",
    "modified_timestamp", "naming_pattern", "capitalization_style",
    "hidden_file_status", "read_only_status", "file_category",
)
_FIELD_KINDS = {"file_size_bytes": "size",
                "modified_timestamp": "mtime",
                "creation_timestamp": "creation",
                "naming_pattern": "naming"}
_KIND_ORDER = ("size", "mtime", "creation", "naming", "case_change",
               "metadata")
_SAMPLE_LIMIT = 5
_NAMING_SAMPLE_LIMIT = 20
_FOLDER_SAMPLE_LIMIT = 100
_EXTENSION_LIMIT = 1_000


def _header(path: str) -> set[str]:
    try:
        frame = pd.read_csv(path, nrows=0, encoding=_ENCODING)
    except Exception as exc:  # noqa: BLE001 - surfaced as guidance
        raise PlatformError(
            "The folder scan output could not be read - re-register the "
            "folder dataset on the Data Sources page.",
            detail=f"{type(exc).__name__}") from exc
    return {str(c) for c in frame.columns}


def _norm_path(text, root, casefold: bool) -> tuple[str, bool]:
    """(relative path used as the match key, root prefix matched?)."""
    value = str(text).replace("\\", "/")
    matched = True
    if root:
        prefix = str(root).replace("\\", "/").rstrip("/")
        if prefix and value[:len(prefix)].lower() == prefix.lower():
            value = value[len(prefix):].lstrip("/")
        else:
            matched = False
    if casefold:
        value = value.casefold()
    return value, matched


def _rows(path: str, cfg, columns: list[str]):
    try:
        for chunk in pd.read_csv(path, chunksize=cfg.chunk_size,
                                 encoding=_ENCODING, usecols=columns):
            for row in chunk.to_dict("records"):
                yield row
    except pd.errors.EmptyDataError as exc:
        raise PlatformError(
            "The folder scan output is empty - re-register the folder "
            "dataset.", detail=str(path)) from exc


def _fmt(value) -> str:
    safe = matching.normalize_value(value)
    return "" if safe is None else str(safe)


class _FsDiff:
    """Stateful path-keyed comparison of two files_metadata.csv outputs."""

    def __init__(self, cfg, fields, root_a, root_b):
        self.cfg = cfg
        self.fields = list(fields)
        self.root_a, self.root_b = root_a, root_b
        self.casefold = bool(cfg.path_casefold)
        self.counts = {
            "files_a": 0, "files_b": 0, "unchanged": 0, "modified": 0,
            "added": 0, "removed": 0, "moved": 0, "renamed": 0,
            "case_changes": 0, "duplicate_paths_a": 0,
            "duplicate_paths_b": 0, "root_prefix_mismatch_a": 0,
            "root_prefix_mismatch_b": 0, "unindexed_rows_a": 0,
        }
        self.index: dict = {}
        self.ambiguous: set = set()
        self.added_map: dict = {}
        self.overflow_a = False
        self.moves_overflow = False
        self.samples = {"dup_a": [], "dup_b": []}
        self.naming_changes = 0
        self.naming_samples: list = []
        self.case_samples: list = []
        self.size = {"a": 0, "b": 0}
        self.extensions = {"a": {}, "b": {}}
        self.basenames = {"a": {}, "b": {}}
        self.ext_truncated = False
        self.base_truncated = False
        self.export = {"added": [], "removed": [], "modified": [],
                       "moved": []}

    # -- helpers -------------------------------------------------------- #
    def _entry(self, row: dict, rel: str) -> dict:
        entry = {_PATH: row.get(_PATH), _PARENT: row.get(_PARENT),
                 "__rel": rel}
        for field in self.fields:
            entry[field] = row.get(field)
        return entry

    def _bump_side(self, side: str, row: dict) -> None:
        size = matching.normalize_value(row.get("file_size_bytes"))
        if isinstance(size, (int, float)):
            self.size[side] += int(size)
        ext = _fmt(row.get("file_extension")).lower()
        counts = self.extensions[side]
        if ext in counts:
            counts[ext] += 1
        elif len(counts) < _EXTENSION_LIMIT:
            counts[ext] = 1
        else:
            self.ext_truncated = True
        name = _fmt(row.get("file_name")).lower()
        bases = self.basenames[side]
        if name in bases:
            bases[name] += 1
        elif len(bases) <= self.cfg.max_keys:
            bases[name] = 1
        else:
            self.base_truncated = True

    def _keep(self, category: str, row: tuple) -> None:
        bucket = self.export[category]
        if len(bucket) < self.cfg.max_export_rows:
            bucket.append(row)

    def _drop_from_export(self, category: str, path) -> None:
        bucket = self.export[category]
        for pos, row in enumerate(bucket):
            if row and row[0] == path:
                del bucket[pos]
                return

    def _sample(self, bucket: str, text: str) -> None:
        samples = self.samples[bucket]
        if len(samples) < _SAMPLE_LIMIT and text not in samples:
            samples.append(text)

    def _field_row(self, entry: dict) -> tuple:
        return tuple(entry.get(f) for f in self.fields)

    # -- pass A --------------------------------------------------------- #
    def feed_a(self, rows) -> None:
        for row in rows:
            self.counts["files_a"] += 1
            self._bump_side("a", row)
            key, matched = _norm_path(row.get(_PATH), self.root_a,
                                      self.casefold)
            rel, _ = _norm_path(row.get(_PATH), self.root_a, False)
            if not matched:
                self.counts["root_prefix_mismatch_a"] += 1
            if key in self.index:
                self.counts["duplicate_paths_a"] += 1
                self._sample("dup_a", _fmt(key))
                self.index.pop(key, None)
                self.ambiguous.add(key)
                continue
            if key in self.ambiguous:
                self.counts["duplicate_paths_a"] += 1
                self._sample("dup_a", _fmt(key))
                continue
            if len(self.index) >= self.cfg.max_keys:
                self.overflow_a = True
                self.counts["unindexed_rows_a"] += 1
                continue
            self.index[key] = self._entry(row, rel)

    # -- pass B --------------------------------------------------------- #
    def _changed(self, entry_a: dict, entry_b: dict
                 ) -> tuple[list, bool, list]:
        real: list = []
        case_only: list = []
        path_differs = entry_a.get("__rel") != entry_b.get("__rel")
        for field in self.fields:
            va, vb = entry_a.get(field), entry_b.get(field)
            if matching.normalize_value(va) == matching.normalize_value(vb):
                continue
            if (path_differs and isinstance(va, str) and isinstance(vb, str)
                    and va.lower() == vb.lower()):
                case_only.append((field, va, vb))
                continue
            real.append((field, va, vb))
        kinds = {_FIELD_KINDS.get(field, "metadata")
                 for field, _, _ in real}
        ordered = [k for k in _KIND_ORDER if k in kinds]
        details = [f"{field}: {_fmt(va)} -> {_fmt(vb)}"
                   for field, va, vb in real + case_only]
        return ordered, (path_differs or bool(case_only)), details

    def feed_b(self, rows) -> None:
        for row in rows:
            self.counts["files_b"] += 1
            self._bump_side("b", row)
            key, matched = _norm_path(row.get(_PATH), self.root_b,
                                      self.casefold)
            rel, _ = _norm_path(row.get(_PATH), self.root_b, False)
            if not matched:
                self.counts["root_prefix_mismatch_b"] += 1
            entry = self._entry(row, rel)
            if key in self.ambiguous:
                self.counts["duplicate_paths_b"] += 1
                self._sample("dup_b", _fmt(key))
                continue
            known = self.index.pop(key, None)
            if known is not None:
                self._classify_pair(known, entry)
                continue
            if key in self.added_map:
                self.counts["duplicate_paths_b"] += 1
                self._sample("dup_b", _fmt(key))
                continue
            self.counts["added"] += 1
            row_out = (entry.get(_PATH),) + self._field_row(entry)
            self._keep("added", row_out)
            if not self.moves_overflow:
                if len(self.added_map) >= self.cfg.max_keys:
                    self.moves_overflow = True
                else:
                    self.added_map[key] = entry

    def _classify_pair(self, entry_a: dict, entry_b: dict) -> None:
        real_kinds, case_differs, details = self._changed(entry_a, entry_b)
        if case_differs:
            self.counts["case_changes"] += 1
            if len(self.case_samples) < _SAMPLE_LIMIT:
                self.case_samples.append(
                    f"{entry_a.get('__rel')} -> {entry_b.get('__rel')}")
        if not real_kinds:
            self.counts["unchanged"] += 1
            return
        kinds = list(real_kinds)
        if case_differs:
            kinds.append("case_change")
        kinds = [k for k in _KIND_ORDER if k in set(kinds)]
        if "naming" in kinds:
            self.naming_changes += 1
            if len(self.naming_samples) < _NAMING_SAMPLE_LIMIT:
                self.naming_samples.append(
                    {"path": _fmt(entry_b.get(_PATH)),
                     "a": _fmt(entry_a.get("naming_pattern")),
                     "b": _fmt(entry_b.get("naming_pattern"))})
        self.counts["modified"] += 1
        self._keep("modified",
                   (entry_b.get(_PATH), ", ".join(kinds),
                    "; ".join(details)) + self._field_row(entry_b))

    # -- move / rename detection --------------------------------------- #
    def detect_moves(self) -> str:
        if not self.cfg.detect_moves:
            return "disabled"
        if self.moves_overflow:
            return "skipped"
        if not self.added_map or not self.index:
            return "analysed"
        self._pair_by(
            lambda e: (_fmt(e.get("file_name")).lower(),
                       _fmt(e.get("file_size_bytes")),
                       _fmt(e.get("modified_timestamp"))),
            count_key="moved")
        self._pair_by(
            lambda e: (_fmt(e.get(_PARENT)).replace("\\", "/").lower(),
                       _fmt(e.get("file_size_bytes")),
                       _fmt(e.get("modified_timestamp"))),
            count_key="renamed", require_different_name=True)
        return "analysed"

    def _pair_by(self, key_fn, count_key: str,
                 require_different_name: bool = False) -> int:
        removed, added = self.index, self.added_map

        def singletons(source: dict) -> dict:
            groups: dict = {}
            for key, entry in source.items():
                groups.setdefault(key_fn(entry), []).append(key)
            return {k: v[0] for k, v in groups.items() if len(v) == 1}

        rem_single = singletons(removed)
        add_single = singletons(added)
        paired = 0
        for evidence, rem_key in rem_single.items():
            add_key = add_single.get(evidence)
            if add_key is None:
                continue
            rem_entry = removed.get(rem_key)
            add_entry = added.get(add_key)
            if rem_entry is None or add_entry is None:
                continue
            if require_different_name and (
                    _fmt(rem_entry.get("file_name")).lower()
                    == _fmt(add_entry.get("file_name")).lower()):
                continue
            del removed[rem_key]
            del added[add_key]
            self.counts["added"] -= 1
            self.counts[count_key] += 1
            self._drop_from_export("added", add_entry.get(_PATH))
            self._keep("moved",
                       (count_key, rem_entry.get(_PATH),
                        add_entry.get(_PATH), add_entry.get("file_name"),
                        add_entry.get("file_size_bytes"),
                        add_entry.get("modified_timestamp")))
            paired += 1
        return paired

    # -- leftovers become removed rows --------------------------------- #
    def finish_exports(self) -> None:
        for _key, entry in self.index.items():
            self.counts["removed"] += 1
            self._keep("removed", (entry.get(_PATH),)
                       + self._field_row(entry))


def _folder_paths(path, root, casefold: bool) -> set | None:
    if not path:
        return None
    try:
        columns = {str(c) for c in
                   pd.read_csv(path, nrows=0, encoding=_ENCODING).columns}
        if "folder_path" not in columns:
            return None
        out: set = set()
        for chunk in pd.read_csv(path, encoding=_ENCODING,
                                 usecols=["folder_path"], chunksize=100_000):
            for value in chunk["folder_path"]:
                key, _ = _norm_path(value, root, casefold)
                out.add(key)
        return out
    except (OSError, ValueError, pd.errors.ParserError):
        return None


def _schema(diff: _FsDiff, fields, header_a, header_b, folders) -> dict:
    payload = {
        "kind": "filesystem",
        "status": "ok",
        "files": {
            "columns_compared": list(fields),
            "a_only_columns": sorted(header_a - header_b),
            "b_only_columns": sorted(header_b - header_a),
        },
        "row_counts": {"a": diff.counts["files_a"],
                       "b": diff.counts["files_b"]},
        "note": "folder differences come from the registered scan outputs "
                "only - the folders are never re-walked",
    }
    if folders is None:
        payload["folders"] = {"status": "skipped",
                              "note": "folder metadata "
                                      "(folders_metadata.csv) is not "
                                      "available for both datasets"}
    else:
        set_a, set_b = folders
        a_only = sorted(set_a - set_b)
        b_only = sorted(set_b - set_a)
        payload["folders"] = {
            "status": "ok",
            "a_only_count": len(a_only),
            "b_only_count": len(b_only),
            "a_only": a_only[:_FOLDER_SAMPLE_LIMIT],
            "b_only": b_only[:_FOLDER_SAMPLE_LIMIT],
        }
    return payload


def _statistics(diff: _FsDiff) -> dict:
    ext_a, ext_b = diff.extensions["a"], diff.extensions["b"]
    merged = {e: (ext_a.get(e, 0), ext_b.get(e, 0))
              for e in set(ext_a) | set(ext_b)}
    top_ext = sorted(merged.items(),
                     key=lambda item: (-(abs(item[1][1] - item[1][0])),
                                       item[0]))
    base_a, base_b = diff.basenames["a"], diff.basenames["b"]
    dup_a = sum(1 for c in base_a.values() if c > 1)
    dup_b = sum(1 for c in base_b.values() if c > 1)
    notes = ["storage, extension and duplicate statistics are exact sums "
             "over both scan outputs",
             "duplicate-basename counts are informational only"]
    if diff.ext_truncated:
        notes.append(f"extension counts capped at {_EXTENSION_LIMIT} "
                     "distinct extensions")
    if diff.base_truncated:
        notes.append("duplicate-basename tracking reached the key limit - "
                     "counts may be incomplete")
    return {
        "kind": "filesystem",
        "basis": "exact",
        "storage": {"a_total_bytes": int(diff.size["a"]),
                    "b_total_bytes": int(diff.size["b"]),
                    "delta_bytes": int(diff.size["b"] - diff.size["a"]),
                    "a_files": diff.counts["files_a"],
                    "b_files": diff.counts["files_b"]},
        "extensions": {
            "top": [{"extension": e, "a": a, "b": b, "delta": b - a}
                    for e, (a, b) in top_ext[:50]],
            "distinct_a": len(ext_a), "distinct_b": len(ext_b),
            "shown": min(50, len(top_ext)),
            "truncated": diff.ext_truncated,
        },
        "duplicate_basenames": {"a_groups": dup_a, "b_groups": dup_b,
                                "delta": dup_b - dup_a,
                                "tracked_values": {"a": len(base_a),
                                                   "b": len(base_b)}},
        "naming_patterns": {"compared_pairs": diff.counts["unchanged"]
                            + diff.counts["modified"],
                            "changed": diff.naming_changes,
                            "samples": diff.naming_samples},
        "path_case_changes": {"count": diff.counts["case_changes"],
                              "samples": diff.case_samples},
        "notes": notes,
    }


def compare(in_a: dict, in_b: dict, cfg, stage) -> dict:
    files_a, files_b = str(in_a["files_csv"]), str(in_b["files_csv"])
    header_a, header_b = _header(files_a), _header(files_b)
    if _PATH not in header_a or _PATH not in header_b:
        raise PlatformError(
            "The folder scan output does not contain the expected path "
            "column - re-register the folder dataset on the Data Sources "
            "page.")
    fields = [f for f in _COMPARE_FIELDS
              if f in header_a and f in header_b]
    use_a = [_PATH] + ([_PARENT] if _PARENT in header_a else []) + fields
    use_b = [_PATH] + ([_PARENT] if _PARENT in header_b else []) + fields

    diff = _FsDiff(cfg, fields, in_a.get("root"), in_b.get("root"))
    warnings: list = []

    stage("load", 1, 4, "reading dataset A (folder scan output)")
    diff.feed_a(_rows(files_a, cfg, use_a))
    stage("load", 2, 4, "reading dataset B (folder scan output)")
    diff.feed_b(_rows(files_b, cfg, use_b))

    stage("load", 3, 4, "detecting moved and renamed files")
    move_status = diff.detect_moves()
    diff.finish_exports()

    stage("statistics", 4, 4, "computing folder and metadata statistics")
    folders = None
    folders_a, folders_b = in_a.get("folders_csv"), in_b.get("folders_csv")
    if folders_a and folders_b:
        set_a = _folder_paths(folders_a, in_a.get("root"),
                              cfg.path_casefold)
        set_b = _folder_paths(folders_b, in_b.get("root"),
                              cfg.path_casefold)
        if set_a is not None and set_b is not None:
            folders = (set_a, set_b)

    if diff.overflow_a:
        warnings.append(warn(
            "key_index_limit",
            f"the path index reached its limit of {cfg.max_keys:,} "
            f"entries; {diff.counts['unindexed_rows_a']:,} row(s) of "
            "dataset A could not be indexed, so added/removed counts are "
            "approximate."))
    if diff.moves_overflow:
        warnings.append(warn(
            "moves_skipped",
            "move detection skipped: added files reached the limit of "
            f"{cfg.max_keys:,} - affected files stay in the added/"
            "removed counts."))
    for side in ("a", "b"):
        mismatch = diff.counts[f"root_prefix_mismatch_{side}"]
        if mismatch:
            warnings.append(warn(
                "root_prefix_mismatch",
                f"{mismatch:,} row(s) in dataset {side.upper()} do not "
                "start with the recorded scan root - those paths were "
                "compared in full, which may report them as added/"
                "removed."))
    if diff.counts["duplicate_paths_a"] or diff.counts["duplicate_paths_b"]:
        warnings.append(warn(
            "duplicate_paths",
            "scan output contains duplicate paths: "
            f"{diff.counts['duplicate_paths_a']} row(s) in dataset A, "
            f"{diff.counts['duplicate_paths_b']} in dataset B - they are "
            "counted separately and never paired. examples: "
            + (", ".join(diff.samples["dup_a"] + diff.samples["dup_b"])
               or "n/a")))
    if diff.base_truncated:
        warnings.append(warn(
            "basename_limit",
            "duplicate-basename tracking reached the key limit - its "
            "counts may be incomplete"))

    invariant_a = (diff.counts["files_a"] == diff.counts["unchanged"]
                   + diff.counts["modified"] + diff.counts["removed"]
                   + diff.counts["moved"] + diff.counts["renamed"]
                   + diff.counts["duplicate_paths_a"]
                   + diff.counts["unindexed_rows_a"])
    invariant_b = (diff.counts["files_b"] == diff.counts["unchanged"]
                   + diff.counts["modified"] + diff.counts["added"]
                   + diff.counts["moved"] + diff.counts["renamed"]
                   + diff.counts["duplicate_paths_b"])
    reconciled = invariant_a and invariant_b
    if not reconciled:
        warnings.append(warn(
            "accounting_mismatch",
            "row accounting did not reconcile - duplicate or unreadable "
            "rows may exist in the scan outputs; added/removed counts are "
            "approximate."))
    exactness = ("approximate"
                 if diff.overflow_a or not reconciled else "exact")

    schema = _schema(diff, fields, header_a, header_b, folders)
    statistics = _statistics(diff)

    record_tables = [
        {"file": "added_files.csv", "columns": ["path"] + fields,
         "rows": diff.export["added"],
         "total": diff.counts["added"]},
        {"file": "removed_files.csv", "columns": ["path"] + fields,
         "rows": diff.export["removed"],
         "total": diff.counts["removed"]},
        {"file": "modified_files.csv",
         "columns": ["path", "change_kinds", "changed_fields"] + fields,
         "rows": diff.export["modified"],
         "total": diff.counts["modified"]},
        {"file": "moved_files.csv",
         "columns": ["change", "path_from", "path_to", "file_name",
                     "file_size_bytes", "modified_timestamp"],
         "rows": diff.export["moved"],
         "total": diff.counts["moved"] + diff.counts["renamed"]},
    ]
    return {
        "strategy": "relative-path",
        "exactness": exactness,
        "keys": ["relative path inside the scan root"],
        "counts": diff.counts,
        "warnings": warnings,
        "schema": schema,
        "statistics": statistics,
        "record_tables": record_tables,
        "table_files": [t["file"] for t in record_tables],
        "summary_extra": {
            "move_detection": move_status,
            "match_key": ("normalized relative path (forward slashes"
                          + (", case-folded" if cfg.path_casefold else "")
                          + ") inside each dataset's recorded scan root"),
            "limitations": [
                "metadata only - file contents are never read, so content "
                "changes cannot be proven from this comparison",
                "modified means a recorded metadata field changed (size, "
                "timestamps, naming, flags)",
                "move/rename detection uses metadata evidence (name, "
                "size, timestamp) - strong evidence, not proof that the "
                "content is identical",
            ],
        },
    }
