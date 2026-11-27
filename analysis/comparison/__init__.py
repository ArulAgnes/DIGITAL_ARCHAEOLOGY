"""Dataset comparison engine (Phase 6).

Compares two registered datasets of the same source type (CSV, SQLite or
filesystem snapshot) and writes a self-contained, reproducible report
under ``output/comparisons/<comparison_id>/runs/<run_id>/``.

Public API::

    from analysis.comparison import (
        ComparisonConfig, run_comparison, list_comparisons,
        load_comparison, validate_selection)

The engine is Streamlit-free: the dashboard page only calls these
functions. Sources are opened read-only and a changed or missing
registered source refuses the run with guidance.
"""
from analysis.comparison.base import (ComparisonConfig, ComparisonResult,
                                      comparison_id_for, list_comparisons,
                                      load_comparison, registration_column_names,
                                      run_comparison, validate_selection)

__all__ = ["ComparisonConfig", "ComparisonResult", "comparison_id_for",
           "list_comparisons", "load_comparison", "run_comparison",
           "validate_selection", "registration_column_names"]
