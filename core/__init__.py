"""Core domain model for the reusable data-analysis platform."""
from core.dataset import (ColumnMetadata, Dataset, DatasetMetadata,
                          ProcessingStatus, SourceType, ValidationStatus,
                          make_dataset_id, source_fingerprint)
from core.dataset_metadata import build_metadata, columns_from_dataframe
from core.exceptions import (ChunkReadError, ConfirmationRequiredError,
                             DatasetEmptyError, InputValidationError,
                             MalformedDataError, PlatformError,
                             RegistryError, SourceNotReadableError,
                             SourceNotFoundError, UnsupportedSourceError,
                             redact_path)
from core.processing_context import (ProcessingContext,
                                     assert_writable_target,
                                     is_protected_research_path)

__all__ = [
    "ChunkReadError", "ColumnMetadata", "ConfirmationRequiredError",
    "Dataset", "DatasetEmptyError", "DatasetMetadata",
    "InputValidationError", "MalformedDataError", "PlatformError",
    "ProcessingContext", "ProcessingStatus", "RegistryError", "SourceType",
    "SourceNotReadableError", "SourceNotFoundError",
    "UnsupportedSourceError", "ValidationStatus", "assert_writable_target",
    "build_metadata", "columns_from_dataframe", "is_protected_research_path",
    "make_dataset_id", "redact_path", "source_fingerprint",
]
