"""Visualization package (Phase 7).

Streamlit-free chart engine: configuration, validation, explicit
filters, bounded data adaptation, a declarative chart registry, pure
Plotly builders for every section of the Advanced Visualization page,
and self-contained export bundles.

Public surface (also used by the tests):

    from visualization import (
        VisualizationConfig, build_chart, charts_for, get_chart,
        validate_chart_request, export_visualization, load_preview,
        ChartArtifact, ChartRequest,
    )
"""
from __future__ import annotations

from visualization.chart_factory import ChartArtifact, ChartRequest
from visualization.chart_registry import (CHARTS, ChartSpec,
                                          build_chart, charts_for,
                                          get_chart)
from visualization.data_adapter import (PreparedData, dataset_options,
                                        load_preview,
                                        metadata_column_specs,
                                        registration_info, sample_frame)
from visualization.export import export_visualization
from visualization.validation import (ValidationReport, column_kind,
                                      kind_from_dtype_text,
                                      validate_chart_request)
from visualization.visualization_config import PALETTE, VisualizationConfig

__all__ = [
    "CHARTS",
    "ChartArtifact",
    "ChartRequest",
    "ChartSpec",
    "PALETTE",
    "PreparedData",
    "ValidationReport",
    "VisualizationConfig",
    "build_chart",
    "charts_for",
    "column_kind",
    "dataset_options",
    "export_visualization",
    "get_chart",
    "kind_from_dtype_text",
    "load_preview",
    "metadata_column_specs",
    "registration_info",
    "sample_frame",
    "validate_chart_request",
]
