"""Two-level duplicate detection.

Level 1 (default, metadata only): same size + same name + same extension
    appearing at different paths -> *duplicate candidates*, never claimed as
    verified duplicates.

Level 2 (opt-in): SHA-256 verification of candidate groups by reading bytes.
    Disabled by default via ``config.ENABLE_HASH_VERIFICATION``.
    Document contents are hashed as raw bytes only and never interpreted.
"""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime

import pandas as pd

import config

log = logging.getLogger("da.duplicates")

CANDIDATE_FIELDS = [
    "duplicate_group_id", "file_name", "file_extension", "file_size_bytes",
    "file_size_mb", "copy_count", "paths", "folders", "level", "status",
]

VERIFIED_FIELDS = [
    "duplicate_group_id", "sha256", "file_name", "file_size_bytes",
    "copy_count", "extra_copies", "extra_copy_bytes", "paths", "status",
]


def find_candidates(files: pd.DataFrame) -> pd.DataFrame:
    """Level 1: metadata-based duplicate candidates."""
    if files.empty:
        return pd.DataFrame(columns=CANDIDATE_FIELDS)

    key = ["file_name", "file_extension", "file_size_bytes"]
    grouped = files.groupby(key, dropna=False)
    rows = []
    gid = 0
    for k, g in grouped:
        if len(g) < 2:
            continue
        paths = g["full_file_path"].tolist()
        if len(set(paths)) < 2:
            continue  # same path recorded twice -> not a cross-path duplicate
        gid += 1
        folders = sorted({str(p).rsplit("\\", 1)[0] for p in paths})
        rows.append({
            "duplicate_group_id": f"DC{gid:06d}",
            "file_name": k[0],
            "file_extension": k[1],
            "file_size_bytes": k[2],
            "file_size_mb": round(float(k[2]) / (1024 * 1024), 4),
            "copy_count": len(paths),
            "paths": " | ".join(paths),
            "folders": " | ".join(folders),
            "level": "L1_metadata_candidate",
            "status": "candidate_not_verified",
        })
    return pd.DataFrame(rows, columns=CANDIDATE_FIELDS)


def _sha256(path: str, max_bytes: int = 0) -> str:
    h = hashlib.sha256()
    read = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
            read += len(chunk)
            if max_bytes and read >= max_bytes:
                break
    return h.hexdigest()


def verify_candidates(candidates: pd.DataFrame,
                      enabled: bool | None = None) -> pd.DataFrame:
    """Level 2: SHA-256 verification (opt-in, off by default)."""
    if enabled is None:
        enabled = config.ENABLE_HASH_VERIFICATION
    if not enabled:
        log.info("Hash verification disabled (default). No file bytes read.")
        return pd.DataFrame(columns=VERIFIED_FIELDS)
    if candidates.empty:
        return pd.DataFrame(columns=VERIFIED_FIELDS)

    rows = []
    for _, c in candidates.iterrows():
        paths = [p.strip() for p in str(c["paths"]).split("|")]
        hashes: dict[str, list[str]] = {}
        for p in paths:
            if not os.path.isfile(p):
                continue
            try:
                before = os.stat(p).st_mtime_ns
                digest = _sha256(p, config.HASH_MAX_BYTES)
                after = os.stat(p).st_mtime_ns
                if before != after:
                    continue  # file changed during hashing -> skip
                hashes.setdefault(digest, []).append(p)
            except (OSError, PermissionError) as exc:
                log.warning("hash failed %s: %s", p, exc)
        for digest, matching in hashes.items():
            if len(matching) < config.HASH_MIN_GROUP_SIZE:
                continue
            size = int(c["file_size_bytes"])
            extra = len(matching) - 1
            rows.append({
                "duplicate_group_id": c["duplicate_group_id"] + "_" + digest[:8],
                "sha256": digest,
                "file_name": c["file_name"],
                "file_size_bytes": size,
                "copy_count": len(matching),
                "extra_copies": extra,
                "extra_copy_bytes": extra * size,
                "paths": " | ".join(matching),
                "status": "verified_exact_duplicate",
            })
    return pd.DataFrame(rows, columns=VERIFIED_FIELDS)


def summarise(candidates: pd.DataFrame, verified: pd.DataFrame) -> dict:
    cand_groups = int(len(candidates))
    cand_copies = int(candidates["copy_count"].sum()) if cand_groups else 0
    cand_extra = cand_copies - cand_groups if cand_groups else 0
    cand_bytes = int(
        (candidates["file_size_bytes"] * (candidates["copy_count"] - 1)).sum()
    ) if cand_groups else 0

    ver_groups = int(len(verified))
    ver_copies = int(verified["copy_count"].sum()) if ver_groups else 0
    ver_extra = int(verified["extra_copies"].sum()) if ver_groups else 0
    ver_bytes = int(verified["extra_copy_bytes"].sum()) if ver_groups else 0

    return {
        "candidate_groups": cand_groups,
        "candidate_extra_copies": cand_extra,
        "candidate_potential_bytes": cand_bytes,
        "verified_groups": ver_groups,
        "verified_extra_copies": ver_extra,
        "verified_extra_bytes": ver_bytes,
        "hash_verification_enabled": bool(config.ENABLE_HASH_VERIFICATION),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def run(files: pd.DataFrame,
        enable_hash: bool | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    cand = find_candidates(files)
    ver = verify_candidates(cand, enabled=enable_hash)
    cand.to_csv(config.DUP_CANDIDATES_CSV, index=False, encoding="utf-8-sig")
    ver.to_csv(config.DUP_VERIFIED_CSV, index=False, encoding="utf-8-sig")
    summary = summarise(cand, ver)
    return cand, ver, summary
