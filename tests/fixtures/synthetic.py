"""Builders for synthetic test data + research-data safety assertions.

Everything here writes only into pytest-provided temp directories. The
22 protected research files must be byte-identical before/after any test.
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

PROTECTED_RESEARCH_ROOTS: tuple[Path, ...] = (
    PROJECT_ROOT / "data" / "raw",
    PROJECT_ROOT / "data" / "processed",
    PROJECT_ROOT / "data" / "snapshots",
    PROJECT_ROOT / "data" / "database",
    PROJECT_ROOT / "reports",
    PROJECT_ROOT / "logs",
    PROJECT_ROOT / "backups",
)


def assert_outside_research_data(path) -> None:
    """Fail loudly if *path* would write into protected research data."""
    resolved = Path(path).resolve()
    for root in PROTECTED_RESEARCH_ROOTS:
        root_r = root.resolve()
        assert resolved != root_r and root_r not in resolved.parents, (
            f"{path} resolves inside protected research data ({root_r})")


def write_csv(path: Path, text: str, encoding: str = "utf-8") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode(encoding))
    assert_outside_research_data(path)
    return path


def make_sqlite(path: Path,
                tables: dict[str, tuple[list[str], list[tuple]]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(str(path))
    try:
        for table, (columns, rows) in tables.items():
            col_sql = ", ".join(
                f"{c.split()[0]} {c.split()[1]}" if len(c.split()) > 1 else c
                for c in columns)
            conn.execute(f'CREATE TABLE "{table}" ({col_sql})')
            if rows:
                names = [c.split()[0] for c in columns]
                marks = ", ".join("?" for _ in names)
                conn.executemany(
                    f'INSERT INTO "{table}" ({", ".join(names)}) '
                    f"VALUES ({marks})", rows)
        conn.commit()
    finally:
        conn.close()
    assert_outside_research_data(path)
    return path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def tree_snapshot(root: Path) -> dict[str, str]:
    """relative path -> sha256 (contents) for every file under *root*."""
    snap: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            snap[str(p.relative_to(root)).lower()] = sha256_file(p)
    return snap


def count_entries(root: Path) -> tuple[int, int]:
    files = sum(1 for p in root.rglob("*") if p.is_file())
    dirs = sum(1 for p in root.rglob("*") if p.is_dir())
    return files, dirs
