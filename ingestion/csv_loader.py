"""CSV input loader: streaming, encoding-sniffing, fail-fast validation."""
from __future__ import annotations

import csv
import io
import logging
from collections.abc import Iterator
from pathlib import Path

import pandas as pd

from core.dataset import (Dataset, DatasetMetadata, SourceType,
                          ValidationStatus)
from core.dataset_metadata import build_metadata, columns_from_dataframe, now_iso
from core.exceptions import (MalformedDataError, SourceNotReadableError,
                             redact_path)
from ingestion.base_loader import BaseLoader
from ingestion.validators import (ValidationResult, combine,
                                  require, validate_csv_headers,
                                  validate_exists, validate_is_file,
                                  validate_nonempty_file, validate_readable)

log = logging.getLogger("da.ingestion.csv")

DEFAULT_CHUNK_SIZE = 100_000
SNIFF_BYTES = 65_536
SAMPLE_ROWS = 1_000
CSV_EXTENSIONS = {".csv"}
TEXT_FALLBACK_EXTENSIONS = {".tsv", ".txt"}


class CsvLoader(BaseLoader):
    def __init__(self, path, encoding: str | None = None,
                 delimiter: str | None = None,
                 chunk_size: int = DEFAULT_CHUNK_SIZE):
        self.path = Path(path)
        self.chunk_size = int(chunk_size)
        self._forced_encoding = encoding
        self._forced_delimiter = delimiter
        self.encoding: str | None = encoding
        self.delimiter: str | None = delimiter
        self._validated = False

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #
    def validate(self) -> list[ValidationResult]:
        ext = self.path.suffix.lower()
        if ext in CSV_EXTENSIONS:
            ext_level = "ok"
        elif ext in TEXT_FALLBACK_EXTENSIONS:
            ext_level = "warning"
        else:
            ext_level = "error"

        results: list[ValidationResult] = [
            validate_exists(self.path),
        ]
        if results[-1].level == "error":
            self._results = results
            return results
        results.extend([
            validate_is_file(self.path),
            validate_readable(self.path),
            validate_nonempty_file(self.path),
        ])
        if ext_level != "ok":
            results.append(ValidationResult(
                ext_level != "error",
                ext_level,
                "unexpected_extension" if ext_level != "error"
                else "unsupported_source",
                (f"'{redact_path(self.path)}' has extension "
                 f"'{ext or '(none)'}'; proceeding as delimited text."
                 if ext_level != "error" else
                 f"'{redact_path(self.path)}' has unsupported extension "
                 f"'{ext or '(none)'}'."),
                detail=str(self.path)))
        if not any(r.level == "error" for r in results):
            header_res = self._validate_header()
            if header_res:
                results.append(header_res)
        self._results = results
        return results

    def _validate_header(self) -> ValidationResult | None:
        try:
            with open(self.path, "r", encoding=self._detect_encoding(),
                      newline="") as fh:
                reader = csv.reader(fh, delimiter=self._detect_delimiter())
                header = next(reader, None)
        except (UnicodeDecodeError, UnicodeError, csv.Error) as exc:
            return ValidationResult.error(
                "malformed_data",
                f"'{redact_path(self.path)}' could not be parsed as CSV.",
                detail=repr(exc))
        except OSError as exc:
            return ValidationResult.error(
                "source_not_readable",
                f"'{redact_path(self.path)}' cannot be read.",
                detail=repr(exc))
        if header is None:
            return ValidationResult.error(
                "dataset_empty", "The file has no header row.",
                detail="no rows")
        return validate_csv_headers(header)

    def _require_valid(self) -> None:
        if not self._validated:
            require(combine(self.validate()))
            self._validated = True

    # ------------------------------------------------------------------ #
    # Format sniffing
    # ------------------------------------------------------------------ #
    def _sample_bytes(self) -> bytes:
        with open(self.path, "rb") as fh:
            return fh.read(SNIFF_BYTES)

    def _detect_encoding(self) -> str:
        if self.encoding:
            return self.encoding
        if self._forced_encoding:
            self.encoding = self._forced_encoding
            return self.encoding
        head = self._sample_bytes()
        if head.startswith(b"\xef\xbb\xbf"):
            self.encoding = "utf-8-sig"
        elif head.startswith(b"\xff\xfe") or head.startswith(b"\xfe\xff"):
            self.encoding = "utf-16"
        else:
            for enc in ("utf-8", "cp1252", "latin-1"):
                try:
                    head.decode(enc, errors="strict")
                    self.encoding = enc
                    break
                except UnicodeDecodeError:
                    continue
            else:
                self.encoding = "latin-1"
        return self.encoding

    def _detect_delimiter(self) -> str:
        if self.delimiter:
            return self.delimiter
        if self._forced_delimiter:
            self.delimiter = self._forced_delimiter
            return self.delimiter
        text = self._sample_bytes().decode(self._detect_encoding(),
                                           errors="replace")
        sample = text[:8192]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            self.delimiter = dialect.delimiter
        except csv.Error:
            first_line = sample.splitlines()[0] if sample.splitlines() else ""
            candidates = {c: first_line.count(c) for c in ",;\t|"}
            best = max(candidates, key=candidates.get)
            self.delimiter = best if candidates[best] > 0 else ","
        return self.delimiter

    # ------------------------------------------------------------------ #
    # Metadata (bounded memory)
    # ------------------------------------------------------------------ #
    def load_metadata(self, count_rows: bool = True) -> DatasetMetadata:
        """Inspect schema; ``count_rows=False`` skips the full-file count
        (used for previews/registration of large files)."""
        self._require_valid()
        encoding = self._detect_encoding()
        delimiter = self._detect_delimiter()
        size = self.path.stat().st_size

        row_count: int | None = 0
        try:
            sample = pd.read_csv(self.path, encoding=encoding, sep=delimiter,
                                 nrows=SAMPLE_ROWS)
            if count_rows:
                row_count = 0
                for chunk in pd.read_csv(self.path, encoding=encoding,
                                         sep=delimiter,
                                         chunksize=self.chunk_size,
                                         usecols=[sample.columns[0]]):
                    row_count += len(chunk)
            else:
                row_count = None
        except (pd.errors.ParserError, UnicodeDecodeError, UnicodeError,
                csv.Error) as exc:
            raise MalformedDataError(
                f"'{redact_path(self.path)}' contains malformed data.",
                detail=repr(exc)) from exc
        except OSError as exc:
            raise SourceNotReadableError(
                f"'{redact_path(self.path)}' cannot be read.",
                detail=repr(exc)) from exc

        warnings: list[str] = []
        if row_count is None:
            warnings.append(
                "row count skipped for this large file "
                "(bounded preview/registration mode)")
        elif row_count > len(sample):
            warnings.append(
                f"column statistics computed from the first {len(sample)} "
                f"of {row_count} rows")
        if row_count == 0:
            warnings.append("header only - dataset has 0 data rows")
        if self.path.suffix.lower() != ".csv":
            warnings.append("non-.csv extension treated as delimited text")

        columns = columns_from_dataframe(sample)
        return build_metadata(
            row_count=row_count,
            columns=columns,
            file_size_bytes=size,
            source_info={
                "format": "csv",
                "encoding": encoding,
                "delimiter": delimiter,
                "sample_rows": len(sample),
                "header": [str(c) for c in sample.columns],
                "path_name": redact_path(self.path),
            },
            warnings=warnings,
        )

    # ------------------------------------------------------------------ #
    # Reading
    # ------------------------------------------------------------------ #
    def preview(self, nrows: int = 20) -> pd.DataFrame:
        """Bounded head of the file - never loads the whole file."""
        self._require_valid()
        try:
            return pd.read_csv(self.path, encoding=self._detect_encoding(),
                               sep=self._detect_delimiter(),
                               nrows=int(nrows))
        except (pd.errors.ParserError, UnicodeDecodeError, UnicodeError,
                csv.Error) as exc:
            raise MalformedDataError(
                f"'{redact_path(self.path)}' contains malformed data.",
                detail=repr(exc)) from exc
        except OSError as exc:
            raise SourceNotReadableError(
                f"'{redact_path(self.path)}' cannot be read.",
                detail=repr(exc)) from exc

    def iter_chunks(self, chunk_size: int | None = None
                    ) -> Iterator[pd.DataFrame]:
        self._require_valid()
        size = chunk_size or self.chunk_size
        encoding = self._detect_encoding()
        delimiter = self._detect_delimiter()
        reader = pd.read_csv(self.path, encoding=encoding, sep=delimiter,
                             chunksize=size)
        try:
            for chunk in reader:
                yield chunk
        except (pd.errors.ParserError, UnicodeDecodeError, UnicodeError,
                csv.Error) as exc:
            raise MalformedDataError(
                f"'{redact_path(self.path)}' contains malformed data.",
                detail=repr(exc)) from exc
        except OSError as exc:
            raise SourceNotReadableError(
                f"'{redact_path(self.path)}' cannot be read.",
                detail=repr(exc)) from exc

    # ------------------------------------------------------------------ #
    # Dataset
    # ------------------------------------------------------------------ #
    def to_dataset(self) -> Dataset:
        results = self.validate()
        combined = combine(results)
        if combined.level == "error":
            require(combined)
        ds = Dataset.from_source(SourceType.CSV, self.path)
        ds.validation_status = ValidationStatus(
            "ok" if combined.level == "ok"
            else "warning" if combined.level == "warning" else "failed")
        ds.metadata = self.load_metadata()
        ds.notes.append(f"validated at {now_iso()}")
        log.info("CSV dataset ready: %s rows=%s", redact_path(self.path),
                 ds.metadata.row_count)
        return ds
