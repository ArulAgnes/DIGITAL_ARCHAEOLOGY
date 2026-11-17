"""SQLite persistence for the collected metadata (local storage only)."""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

import config

log = logging.getLogger("da.database")

TABLES = {
    "files": config.FILES_CSV,
    "folders": config.FOLDERS_CSV,
    "scan_errors": config.ERRORS_CSV,
    "duplicate_candidates": config.DUP_CANDIDATES_CSV,
    "verified_duplicates": config.DUP_VERIFIED_CSV,
    "naming_analysis": config.NAMING_CSV,
    "snapshots": None,  # built from the snapshot index
}


def build() -> Path:
    """Create/replace the SQLite database from the CSV outputs."""
    config.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if config.DATABASE_PATH.exists():
        config.DATABASE_PATH.unlink()

    conn = sqlite3.connect(str(config.DATABASE_PATH))
    try:
        for name, path in TABLES.items():
            if path is None:
                continue
            if not Path(path).exists():
                log.info("skip %s (missing %s)", name, path)
                continue
            df = pd.read_csv(path, encoding="utf-8-sig")
            if df.empty:
                log.info("skip %s (empty)", name)
                continue
            df.to_sql(name, conn, index=False, if_exists="replace")
            log.info("table %s: %d rows", name, len(df))

        # snapshot index
        try:
            from scanner import snapshot_manager
            snaps = snapshot_manager.list_snapshots()
            if snaps:
                pd.DataFrame(snaps).to_sql("snapshots", conn, index=False,
                                           if_exists="replace")
                log.info("table snapshots: %d rows", len(snaps))
        except Exception as exc:  # noqa: BLE001
            log.warning("snapshots table skipped: %s", exc)

        # convenience view
        conn.execute("DROP VIEW IF EXISTS files_by_category")
        conn.execute(
            "CREATE VIEW files_by_category AS "
            "SELECT file_category, COUNT(*) AS file_count, "
            "SUM(file_size_bytes) AS total_size_bytes "
            "FROM files GROUP BY file_category")
        conn.execute("DROP VIEW IF EXISTS files_by_year")
        conn.execute(
            "CREATE VIEW files_by_year AS "
            "SELECT substr(creation_date,1,4) AS creation_year, "
            "COUNT(*) AS file_count, SUM(file_size_bytes) AS total_size_bytes "
            "FROM files GROUP BY substr(creation_date,1,4)")
        conn.commit()

        meta = pd.DataFrame([{
            "key": "generated_at",
            "value": datetime.now().isoformat(timespec="seconds")},
            {"key": "row_count_files",
             "value": str(conn.execute("SELECT COUNT(*) FROM files")
                          .fetchone()[0])}])
        meta.to_sql("metadata", conn, index=False, if_exists="replace")
        conn.commit()
    finally:
        conn.close()
    log.info("SQLite database written: %s", config.DATABASE_PATH)
    return config.DATABASE_PATH


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    build()
