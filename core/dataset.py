"""Dataset model: one object describing any supported input source."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from core.exceptions import redact_path

_ID_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL, "digital-archaeology/dataset-identity/v1")


class SourceType(str, Enum):
    CSV = "csv"
    SQLITE = "sqlite"
    FILESYSTEM = "filesystem"


class ValidationStatus(str, Enum):
    PENDING = "pending"
    OK = "ok"
    WARNING = "warning"
    FAILED = "failed"


class ProcessingStatus(str, Enum):
    REGISTERED = "registered"
    INSPECTED = "inspected"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def make_dataset_id(source_type: SourceType, source_path) -> str:
    """Stable identity derived from source type + resolved path (never name-only)."""
    resolved = Path(source_path).resolve().as_posix().casefold()
    return str(uuid.uuid5(_ID_NAMESPACE, f"{source_type.value}:{resolved}"))


def source_fingerprint(source_path) -> str:
    """Cheap change detector: size + mtime (no content is read or copied)."""
    p = Path(source_path)
    st = p.stat()
    return f"{st.st_size}:{st.st_mtime_ns}"


@dataclass
class ColumnMetadata:
    name: str
    dtype: str = "unknown"
    nullable: bool = True
    missing_count: int | None = None
    unique_count: int | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "dtype": self.dtype,
            "nullable": self.nullable,
            "missing_count": self.missing_count,
            "unique_count": self.unique_count,
        }


@dataclass
class DatasetMetadata:
    row_count: int | None = None
    column_count: int | None = None
    columns: list[ColumnMetadata] = field(default_factory=list)
    file_size_bytes: int | None = None
    source_info: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    collected_at: str = ""

    def to_dict(self) -> dict:
        return {
            "row_count": self.row_count,
            "column_count": self.column_count,
            "columns": [c.to_dict() for c in self.columns],
            "file_size_bytes": self.file_size_bytes,
            "source_info": self.source_info,
            "warnings": self.warnings,
            "collected_at": self.collected_at,
        }


@dataclass
class Dataset:
    dataset_id: str
    name: str
    source_type: SourceType
    source_path: Path
    created_at: str
    metadata: DatasetMetadata
    processing_status: ProcessingStatus = ProcessingStatus.REGISTERED
    validation_status: ValidationStatus = ValidationStatus.PENDING
    source_fingerprint: str = ""
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_source(cls, source_type: SourceType, source_path,
                    name: str | None = None) -> "Dataset":
        path = Path(source_path)
        return cls(
            dataset_id=make_dataset_id(source_type, path),
            name=name or redact_path(path),
            source_type=source_type,
            source_path=path,
            created_at=datetime.now().isoformat(timespec="seconds"),
            metadata=DatasetMetadata(),
            source_fingerprint=source_fingerprint(path),
        )

    def to_dict(self) -> dict:
        return {
            "dataset_id": self.dataset_id,
            "name": self.name,
            "source_type": self.source_type.value,
            "source_path": str(self.source_path),
            "created_at": self.created_at,
            "metadata": self.metadata.to_dict(),
            "processing_status": self.processing_status.value,
            "validation_status": self.validation_status.value,
            "source_fingerprint": self.source_fingerprint,
            "notes": self.notes,
        }
