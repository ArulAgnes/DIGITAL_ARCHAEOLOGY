"""Visualization configuration (Phase 7).

All limits that bound chart preparation live here so every chart type
uses the same, explicit safeguards. Values are JSON-serialisable and
recorded with each exported visualization.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Deterministic seed for every random operation (sampling, t-SNE).
SEED = 42

#: Fixed, deterministic colour cycle (matches the research pages' style).
PALETTE: tuple[str, ...] = (
    "#4c78a8", "#f58518", "#e45756", "#72b7b2", "#54a24b",
    "#eeca3b", "#b279a2", "#ff9da6", "#9d755d", "#bab0ac",
)

#: Colour for "Other (rest)" grouped categories.
OTHER_COLOR = "#9c9c9c"

#: Colour used when a chart is approximate (sampling / approximate runs).
APPROX_COLOR = "#e45756"


@dataclass
class VisualizationConfig:
    """Bounds and options shared by all chart builders."""

    # point / row safeguards
    max_points: int = 5_000            # points rendered per scatter-like chart
    preview_rows: int = 100_000        # raw dataset rows loaded for charts
    aggregation_row_limit: int = 2_000_000  # rows scanned for aggregations

    # aggregation
    top_n: int = 20                    # top-N categories for bar charts
    pie_max_categories: int = 8        # beyond this pies regroup into "Other"
    histogram_bins: int = 30
    max_bar_categories: int = 60       # hard cap for category axes

    # multivariate charts
    pair_max_columns: int = 5
    matrix_max_columns: int = 6
    parallel_max_columns: int = 8
    pca_max_columns: int = 15
    pca_max_rows: int = 50_000
    tsne_max_rows: int = 10_000
    tsne_perplexity: float = 30.0
    seed: int = SEED

    # exports
    export_max_rows: int = 50_000      # rows written to chart_data.csv

    def __post_init__(self) -> None:
        self.max_points = max(50, int(self.max_points))
        self.preview_rows = max(100, int(self.preview_rows))
        self.aggregation_row_limit = max(1_000,
                                         int(self.aggregation_row_limit))
        self.top_n = max(1, int(self.top_n))
        self.pie_max_categories = max(2, int(self.pie_max_categories))
        self.histogram_bins = max(2, int(self.histogram_bins))
        self.max_bar_categories = max(2, int(self.max_bar_categories))
        self.pair_max_columns = max(2, int(self.pair_max_columns))
        self.matrix_max_columns = max(2, int(self.matrix_max_columns))
        self.parallel_max_columns = max(2, int(self.parallel_max_columns))
        self.pca_max_columns = max(2, int(self.pca_max_columns))
        self.pca_max_rows = max(10, int(self.pca_max_rows))
        self.tsne_max_rows = max(10, int(self.tsne_max_rows))
        self.tsne_perplexity = float(self.tsne_perplexity)
        self.seed = int(self.seed)
        self.export_max_rows = max(100, int(self.export_max_rows))

    def to_dict(self) -> dict:
        return {
            "max_points": self.max_points,
            "preview_rows": self.preview_rows,
            "aggregation_row_limit": self.aggregation_row_limit,
            "top_n": self.top_n,
            "pie_max_categories": self.pie_max_categories,
            "histogram_bins": self.histogram_bins,
            "max_bar_categories": self.max_bar_categories,
            "pair_max_columns": self.pair_max_columns,
            "matrix_max_columns": self.matrix_max_columns,
            "parallel_max_columns": self.parallel_max_columns,
            "pca_max_columns": self.pca_max_columns,
            "pca_max_rows": self.pca_max_rows,
            "tsne_max_rows": self.tsne_max_rows,
            "tsne_perplexity": self.tsne_perplexity,
            "seed": self.seed,
            "export_max_rows": self.export_max_rows,
        }
