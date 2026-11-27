"""Input ingestion: loaders, detection, validation, and the dataset registry."""
from ingestion.base_loader import BaseLoader
from ingestion.csv_loader import CsvLoader
from ingestion.dataset_registry import DatasetRegistry
from ingestion.filesystem_loader import FilesystemLoader
from ingestion.input_detector import DetectionResult, detect
from ingestion.sqlite_loader import SqliteLoader
from ingestion.validators import (ValidationResult, combine, is_drive_root,
                                  require)

__all__ = [
    "BaseLoader", "CsvLoader", "DatasetRegistry", "DetectionResult",
    "FilesystemLoader", "SqliteLoader", "ValidationResult", "combine",
    "detect", "is_drive_root", "require",
]
