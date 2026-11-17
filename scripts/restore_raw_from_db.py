"""Recovery: restore raw CSVs from the archived SQLite dataset (read-only).

The original scan CSVs were clobbered at 00:47:56 by a partial rescan that
reused a stale resume file.  The full original dataset (617,955 files) still
exists in data/database/digital_archaeology.db, which is opened read-only
here.  No rescan of D: is performed; nothing synthetic is generated.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd

DB = Path(r"D:\Data VMini\data\database\digital_archaeology.db")
RAW = Path(r"D:\Data VMini\data\raw")

# Raw-scan boolean columns: DB stores 0/1 ints, the original CSVs wrote
# Python bools as True/False strings.
BOOL_FILE_COLS = [
    "hidden_file_status", "read_only_status", "contains_version_indicator",
    "contains_final_keyword", "contains_date_indicator",
    "contains_copy_indicator", "has_underscore", "has_space", "has_hyphen",
]

EXPECTED = {"files": 617955, "folders": 59213, "scan_errors": 19}
EXPECTED_BYTES = 116500257509


def fetch_chunks(cur: sqlite3.Cursor, query: str, chunk: int = 50000):
    cur.execute(query)
    cols = [d[0] for d in cur.description]
    chunks = []
    while True:
        rows = cur.fetchmany(chunk)
        if not rows:
            break
        chunks.append(pd.DataFrame(rows, columns=cols))
    if not chunks:
        return pd.DataFrame(columns=cols)
    return pd.concat(chunks, ignore_index=True)


def main() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = con.cursor()

    print("Reading files table ...")
    files = fetch_chunks(cur, "SELECT * FROM files ORDER BY rowid")
    print("Reading folders table ...")
    folders = fetch_chunks(cur, "SELECT * FROM folders ORDER BY rowid")
    print("Reading scan_errors table ...")
    errors = fetch_chunks(cur, "SELECT * FROM scan_errors ORDER BY rowid")
    con.close()

    ok = True

    # --- row-count / integrity gates (against original scan facts) --------
    for name, df in (("files", files), ("folders", folders),
                     ("scan_errors", errors)):
        exp = EXPECTED[name]
        flag = "OK" if len(df) == exp else "MISMATCH"
        print(f"{name}: {len(df):,} rows (expected {exp:,}) -> {flag}")
        if len(df) != exp:
            ok = False

    total = int(files["file_size_bytes"].sum())
    print(f"sum(file_size_bytes) = {total:,} "
          f"(expected {EXPECTED_BYTES:,}) -> "
          f"{'OK' if total == EXPECTED_BYTES else 'MISMATCH'}")
    if total != EXPECTED_BYTES:
        ok = False

    if not ok:
        print("ABORT: database does not match the original scan - "
              "no CSVs written.")
        return 1

    # --- dtype fixes before writing --------------------------------------
    for c in BOOL_FILE_COLS:
        vals = set(files[c].dropna().unique())
        if not vals <= {0, 1}:
            print(f"ABORT: {c} has unexpected values {sorted(vals)[:5]}")
            return 1
        files[c] = files[c].map({1: "True", 0: "False"})
    files["file_extension"] = files["file_extension"].fillna("")

    # --- write raw CSVs (utf-8-sig, CRLF - same as the scanner) ----------
    RAW.mkdir(parents=True, exist_ok=True)
    for name, df in (("files_metadata.csv", files),
                     ("folders_metadata.csv", folders),
                     ("scan_errors.csv", errors)):
        path = RAW / name
        df.to_csv(path, index=False, encoding="utf-8-sig",
                  lineterminator="\r\n")
        print(f"wrote {path.name}: {len(df):,} rows, "
              f"{path.stat().st_size:,} bytes")

    # --- verify round-trip exactly as the dashboard reads it -------------
    rt = pd.read_csv(RAW / "files_metadata.csv", encoding="utf-8-sig")
    checks = [
        ("rows", len(rt) == EXPECTED["files"]),
        ("sum", int(rt["file_size_bytes"].sum()) == EXPECTED_BYTES),
        ("size dtype numeric", pd.api.types.is_numeric_dtype(
            rt["file_size_bytes"])),
        ("size no NaN", int(rt["file_size_bytes"].isna().sum()) == 0),
        ("distinct paths", rt["full_file_path"].nunique()
         == EXPECTED["files"]),
        ("bool dtype", rt["hidden_file_status"].dtype == bool),
        ("empty extensions", int((rt["file_extension"].isna()
                                  | (rt["file_extension"] == "")).sum())
         == 17786),
        ("file_id seq end", int(rt["file_id"].max())
         == EXPECTED["files"]),
    ]
    for label, passed in checks:
        print(f"round-trip {label}: {'OK' if passed else 'FAIL'}")
        if not passed:
            ok = False

    rf = pd.read_csv(RAW / "folders_metadata.csv", encoding="utf-8-sig")
    print(f"round-trip folders rows: "
          f"{'OK' if len(rf) == EXPECTED['folders'] else 'FAIL'}")
    if len(rf) != EXPECTED["folders"]:
        ok = False

    print("RESULT:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
