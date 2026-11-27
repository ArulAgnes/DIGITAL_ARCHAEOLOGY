"""Platform CLI: detect, inspect, and register input datasets.

Usage:
    python run_pipeline.py --input PATH
        [--mode auto|csv|sqlite|filesystem] [--table NAME]
        [--confirm-drive] [--no-register]

Writes inspection metadata under output/metadata/ and registers the dataset
in output/metadata/dataset_registry.db (never the research database).
Exit codes: 0 success, 2 invalid input/usage, 1 unexpected failure.
"""
from __future__ import annotations

import argparse
import sys

from core.dataset import Dataset
from core.exceptions import PlatformError, redact_path
from core.processing_context import ProcessingContext
from ingestion.dataset_registry import DatasetRegistry
from ingestion.input_detector import detect

MODES = ("auto", "csv", "sqlite", "filesystem")


def human_size(n: int | None) -> str:
    if n is None:
        return "unknown"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:,.2f} {unit}" if unit != "B" else f"{int(size):,} B"
        size /= 1024
    return f"{size:,.2f} TB"


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="run_pipeline.py",
        description="Inspect and register a CSV, SQLite or folder input.")
    ap.add_argument("--input", required=True, metavar="PATH",
                    help="File or folder to inspect")
    ap.add_argument("--mode", default="auto", choices=MODES,
                    help="Force the source type (default: auto-detect)")
    ap.add_argument("--table", default=None,
                    help="Table name for SQLite inputs")
    ap.add_argument("--confirm-drive", action="store_true",
                    help="Confirm scanning an entire drive")
    ap.add_argument("--chunk-size", type=int, default=100_000,
                    help="Rows per chunk when reading (default 100000)")
    ap.add_argument("--no-register", action="store_true",
                    help="Inspect only; do not write to the dataset registry")
    return ap


def _build_loader(detection, args, context):
    loader_cls = detection.suggested_loader()
    if loader_cls is None:
        raise PlatformError(
            "The input type could not be determined; try --mode.",
            detail=f"status={detection.status} reason={detection.reason}")
    kwargs = {"chunk_size": args.chunk_size}
    if detection.source_type.value == "sqlite":
        kwargs["table"] = args.table
    if detection.source_type.value == "filesystem":
        kwargs["context"] = context
        kwargs["confirm_drive"] = args.confirm_drive
    return loader_cls(args.input, **kwargs)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    context = ProcessingContext()
    try:
        mode = None if args.mode == "auto" else args.mode
        detection = detect(args.input, source_type=mode)
        if not detection.detected:
            print(f"Input rejected: {detection.reason}")
            if detection.status == "not_found":
                return 2
            print("Tip: use --mode to force csv, sqlite or filesystem.")
            return 2

        loader = _build_loader(detection, args, context)
        dataset: Dataset = loader.to_dataset()

        meta_path = context.write_metadata(
            "inspect",
            {"detection": {"status": detection.status,
                           "reason": detection.reason,
                           "source_type": detection.source_type.value},
             "dataset": dataset.to_dict(),
             "progress": context.progress})

        registry_id = None
        if not args.no_register:
            registry = DatasetRegistry()
            registry_id = registry.register(dataset, meta_path)

        md = dataset.metadata
        print("=" * 68)
        print("DATASET INSPECTION")
        print("=" * 68)
        print(f"Name                 : {dataset.name}")
        print(f"Type                 : {dataset.source_type.value}")
        print(f"Detection            : {detection.reason}")
        print(f"Rows                 : {md.row_count:,}"
              if md.row_count is not None else "Rows                 : unknown")
        print(f"Columns              : {md.column_count or 0}")
        print(f"Size                 : {human_size(md.file_size_bytes)}")
        print(f"Validation           : {dataset.validation_status.value}")
        if md.source_info:
            extra = {k: v for k, v in md.source_info.items()
                     if k in ("encoding", "delimiter", "table",
                              "folders_scanned", "errors")}
            for k, v in extra.items():
                print(f"{k.capitalize():<20}: {v}")
        for w in md.warnings:
            print(f"Warning              : {w}")
        print(f"Dataset ID           : {dataset.dataset_id}")
        print(f"Metadata             : output/metadata/"
              f"{meta_path.name}")
        if registry_id:
            print("Registry             : output/metadata/"
                  "dataset_registry.db")
        print("=" * 68)
        context.set_status("completed")
        return 0

    except PlatformError as exc:
        context.set_status("failed")
        print(f"Error: {exc.user_message}")
        return 2
    except KeyboardInterrupt:
        context.cancel()
        print("Cancelled.")
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI last-resort guard
        context.set_status("failed")
        print(f"Unexpected failure: {type(exc).__name__}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
