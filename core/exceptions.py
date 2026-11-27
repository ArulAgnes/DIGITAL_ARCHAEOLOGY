"""Domain exceptions for the reusable data-analysis platform.

Every user-facing message is safe to display: absolute paths are redacted to
file names. Full detail stays available for logs via ``detail``.
"""
from __future__ import annotations

from pathlib import Path


def redact_path(path) -> str:
    """Return a display-safe form of a path (name only)."""
    try:
        return Path(path).name or "<path>"
    except (TypeError, ValueError):
        return "<path>"


class PlatformError(Exception):
    """Base class: carries a safe user message plus optional log detail."""

    code = "platform_error"

    def __init__(self, user_message: str, detail: str = ""):
        super().__init__(user_message)
        self.user_message = user_message
        self.detail = detail

    def __str__(self) -> str:
        return self.user_message


class InputValidationError(PlatformError):
    code = "input_validation"


class SourceNotFoundError(PlatformError):
    code = "source_not_found"


class SourceNotReadableError(PlatformError):
    code = "source_not_readable"


class UnsupportedSourceError(PlatformError):
    code = "unsupported_source"


class DatasetEmptyError(PlatformError):
    code = "dataset_empty"


class MalformedDataError(PlatformError):
    code = "malformed_data"


class ConfirmationRequiredError(PlatformError):
    code = "confirmation_required"


class RegistryError(PlatformError):
    code = "registry_error"


class ChunkReadError(PlatformError):
    code = "chunk_read_error"
