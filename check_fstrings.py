"""Scan Python sources for backslashes inside f-string expressions.

Python < 3.12 forbids backslashes anywhere inside the expression part of an
f-string. This checker reports every offending line so it can be fixed.
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent
TARGETS = [ROOT] + [ROOT / d for d in ("scanner", "analysis", "dashboard",
                                       "tests")]

FSTR_START = re.compile(r"\bf[\"']")
EXPR = re.compile(r"\{([^{}]+)\}")


def check_file(path: pathlib.Path) -> list[str]:
    hits: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:  # noqa: BLE001
        return [f"{path}: UNREADABLE {exc}"]
    for i, line in enumerate(lines, 1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if not FSTR_START.search(line):
            continue
        for m in EXPR.finditer(line):
            if "\\" in m.group(1):
                hits.append(f"{path.relative_to(ROOT)}:{i}: {line.strip()[:140]}")
    return hits


def main() -> int:
    hits: list[str] = []
    for d in TARGETS:
        if not d.exists():
            continue
        for p in sorted(d.glob("*.py")):
            hits.extend(check_file(p))
    if hits:
        print(f"FOUND {len(hits)} offending line(s):")
        for h in hits:
            print("  " + h)
        return 1
    print("CLEAN: no backslashes inside f-string expressions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
