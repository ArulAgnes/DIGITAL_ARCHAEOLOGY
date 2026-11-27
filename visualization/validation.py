"""Validation for visualization requests (Phase 7).

Two layers:

* ``validate_chart_request`` — non-raising pre-flight used by the
  dashboard page before the *Generate visualization* button is enabled.
* ``require_*`` helpers — raising wrappers used inside chart builders.

Every message is user-facing, actionable, and free of absolute paths.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from core.exceptions import PlatformError

NUMERIC_KINDS = ("int", "float")
TEMPORAL_KINDS = ("datetime", "date")
CATEGORICAL_KINDS = ("object", "category", "bool", "string")


def column_kind(dtype: Any) -> str:
    """Map a pandas dtype to a coarse kind used by the chart registry."""
    if pd.api.types.is_bool_dtype(dtype):
        return "bool"
    if pd.api.types.is_integer_dtype(dtype):
        return "int"
    if pd.api.types.is_float_dtype(dtype):
        return "float"
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "datetime"
    return "object"


def kind_from_dtype_text(text: str) -> str:
    """Coarse kind from a registered dtype *string* (no pandas needed).

    Registration metadata records dtypes like ``int64``, ``float64``,
    ``str``, ``datetime64[us]`` or ``bool``; this maps them to the same
    kind vocabulary as :func:`column_kind` so column pickers can offer
    valid choices without loading a single row.
    """
    t = (text or "").strip().lower()
    if not t:
        return "object"
    if t.startswith("bool"):
        return "bool"
    if "datetime" in t or t.startswith("date"):
        return "datetime"
    if "int" in t:
        return "int"
    if "float" in t or "double" in t or "decimal" in t:
        return "float"
    if t in ("str", "string", "object", "category", "categorical"):
        return "object"
    return "object"


def kind_label(kind: str) -> str:
    if kind in NUMERIC_KINDS:
        return "numeric"
    if kind in TEMPORAL_KINDS:
        return "temporal"
    if kind in ("object", "category", "string"):
        return "categorical"
    if kind == "bool":
        return "boolean"
    return kind


@dataclass
class ValidationReport:
    """Result of a non-raising chart request validation."""

    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def fail(self, message: str) -> None:
        self.ok = False
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "details": dict(self.details),
        }


def validate_chart_request(
    chart_id: str | None,
    column_kinds: dict[str, str],
    roles: dict[str, str | list[str] | None],
    *,
    run_available: bool = True,
    needs: str = "preview",
    frame_empty: bool = False,
    role_kinds: dict[str, tuple[str, ...]] | None = None,
    role_required: dict[str, bool] | None = None,
) -> ValidationReport:
    """Validate a chart request without raising.

    Parameters mirror the dashboard form state. ``needs`` is ``"preview"``,
    ``"run"`` or ``"none"`` (aggregation charts only need the frame).
    """
    report = ValidationReport()
    if not chart_id:
        report.fail("Select a chart type first.")
        return report

    if needs == "run" and not run_available:
        report.fail(
            "This chart visualises analysis-run output, but no run exists "
            "for this dataset. Run the analysis on the Dataset Analysis "
            "page first, then come back."
        )

    if frame_empty and needs != "run":
        report.fail(
            "No rows are available after filtering. "
            "Adjust or reset the filters and try again."
        )

    role_kinds = role_kinds or {}
    role_required = role_required or {}
    for role, value in roles.items():
        required = role_required.get(role, True)
        chosen = value if isinstance(value, list) else [value]
        chosen = [c for c in chosen if c]
        if not chosen:
            if required:
                report.fail(f"Select a column for '{role}'.")
            continue
        allowed = role_kinds.get(role)
        if not allowed:
            continue
        for col in chosen:
            kind = column_kinds.get(col)
            if kind is None:
                report.fail(f"Column '{col}' is not available in this data.")
                continue
            if not kind_matches(kind, allowed):
                report.fail(
                    f"Column '{col}' is {kind_label(kind)}, but "
                    f"'{role}' expects "
                    f"{', '.join(kind_label(k) for k in allowed)}."
                )
    return report


def kind_matches(kind: str, allowed: tuple[str, ...]) -> bool:
    """Whether a column kind satisfies a role's allowed kinds.

    Tolerates int/float interchange and the object/string/category/bool
    family so picker lists and validation agree.
    """
    if "any" in allowed:
        return True
    if kind in allowed:
        return True
    if kind in NUMERIC_KINDS and any(a in NUMERIC_KINDS for a in allowed):
        return True
    if kind in ("object", "string", "category") and \
            any(a in ("object", "category", "string", "bool") for a in allowed):
        return True
    if kind == "bool" and any(a in ("bool", "object") for a in allowed):
        return True
    return False


#: Backwards-compatible private alias.
_kind_matches = kind_matches


# ---------------------------------------------------------------- raising


def require_non_empty(frame: pd.DataFrame, *, what: str = "data") -> None:
    if frame is None or len(frame) == 0:
        raise PlatformError(
            "No rows are available for this visualization.",
            detail=f"empty {what}",
        )


def require_columns(
    columns: list[str], available: list[str], *, what: str = "chart"
) -> None:
    missing = [c for c in columns if c is None or c not in available]
    if missing:
        if any(c is None for c in missing):
            raise PlatformError(
                f"This {what} needs a column, but none was selected. "
                "Pick a column and generate again.",
                detail=f"{what} missing role",
            )
        raise PlatformError(
            "One or more selected columns are not present in the data: "
            + ", ".join(str(c) for c in missing),
            detail=f"{what} missing columns",
        )


def require_kinds(
    frame: pd.DataFrame, roles: dict[str, str | list[str]], role_kinds: dict
) -> None:
    for role, value in roles.items():
        cols = value if isinstance(value, list) else [value]
        cols = [c for c in cols if c]
        allowed = role_kinds.get(role)
        if not allowed:
            continue
        for col in cols:
            kind = column_kind(frame[col].dtype)
            if not kind_matches(kind, allowed):
                raise PlatformError(
                    f"Column '{col}' ({kind_label(kind)}) cannot be used as "
                    f"'{role}' for this chart; expected "
                    f"{', '.join(kind_label(k) for k in allowed)}.",
                    detail=f"role {role} kind mismatch",
                )


def require_numeric(frame: pd.DataFrame, cols: list[str], *, what: str) -> None:
    bad = [c for c in cols if not pd.api.types.is_numeric_dtype(frame[c])]
    if bad:
        raise PlatformError(
            f"'{what}' needs numeric columns; not numeric: "
            + ", ".join(bad),
            detail=f"{what} requires numeric input",
        )
