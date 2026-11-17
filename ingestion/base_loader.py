"""Abstract interface shared by all input loaders."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

import pandas as pd

from core.dataset import Dataset, DatasetMetadata
from ingestion.validators import ValidationResult


class BaseLoader(ABC):
    """validate() -> load_metadata() -> iter_chunks()/read_all() -> to_dataset()."""

    @abstractmethod
    def validate(self) -> list[ValidationResult]:
        """Run all checks; returns results (callers use validators.combine)."""

    @abstractmethod
    def load_metadata(self) -> DatasetMetadata:
        """Inspect the source without loading it fully into memory."""

    @abstractmethod
    def iter_chunks(self, chunk_size: int | None = None
                    ) -> Iterator[pd.DataFrame]:
        """Yield the data in bounded-size pieces (memory-safe)."""

    @abstractmethod
    def to_dataset(self) -> Dataset:
        """Validate and return a Dataset carrying metadata."""

    def read_all(self, chunk_size: int | None = None) -> pd.DataFrame:
        """Concatenate all chunks. Loads everything into memory - callers must
        judge whether the source size makes that acceptable."""
        chunks = list(self.iter_chunks(chunk_size))
        if not chunks:
            return pd.DataFrame()
        return pd.concat(chunks, ignore_index=True)
