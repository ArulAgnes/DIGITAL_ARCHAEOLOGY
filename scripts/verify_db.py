"""Read-only verification of the archived SQLite dataset (no writes)."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DB = Path(r"D:\Data VMini\data\database\digital_archaeology.db")

EXPECTED = {
    "files": 617955,
    "folders": 59213,
    "scan_errors": 19,
}
EXPECTED_BYTES = 116500257509


def main() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = con.cursor()

    tables = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print("tables:", tables)

    ok = True
    for t in ["files", "folders", "scan_errors", "duplicate_candidates",
              "naming_analysis", "snapshots"]:
        if t not in tables:
            print(f"MISSING TABLE: {t}")
            ok = False
            continue
        cols = [c[1] for c in cur.execute(f"PRAGMA table_info({t})")]
        n = cur.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        print(f"{t}: rows={n} cols={len(cols)}")
        if t in EXPECTED:
            exp = EXPECTED[t]
            flag = "OK" if n == exp else f"MISMATCH (expected {exp})"
            print(f"  -> {flag}")
            if n != exp:
                ok = False

    if "files" in tables:
        cols = [c[1] for c in cur.execute("PRAGMA table_info(files)")]
        print("files columns:", cols)
        s = cur.execute("SELECT sum(file_size_bytes) FROM files").fetchone()[0]
        print(f"files sum(file_size_bytes) = {s} "
              f"({'OK' if s == EXPECTED_BYTES else f'MISMATCH expected {EXPECTED_BYTES}'})")
        if s != EXPECTED_BYTES:
            ok = False
        nn = cur.execute(
            "SELECT count(*) FROM files "
            "WHERE file_size_bytes IS NULL OR typeof(file_size_bytes) NOT IN ('integer','real')"
        ).fetchone()[0]
        print(f"non-numeric file_size_bytes: {nn}")
        if nn:
            ok = False
        dist = cur.execute("SELECT count(DISTINCT full_file_path) FROM files").fetchone()[0]
        print(f"distinct full_file_path: {dist}")
        if dist != EXPECTED["files"]:
            ok = False

    if "folders" in tables:
        cols = [c[1] for c in cur.execute("PRAGMA table_info(folders)")]
        print("folders columns:", cols)

    if "scan_errors" in tables:
        cols = [c[1] for c in cur.execute("PRAGMA table_info(scan_errors)")]
        print("scan_errors columns:", cols)

    integrity = cur.execute("PRAGMA quick_check").fetchone()[0]
    print("sqlite quick_check:", integrity)
    if integrity != "ok":
        ok = False

    con.close()
    print("RESULT:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
