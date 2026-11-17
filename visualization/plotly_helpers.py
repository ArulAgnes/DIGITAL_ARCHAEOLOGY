"""Shared Plotly styling and export helpers (Phase 7).

Responsibilities:

* one consistent layout for every chart (title, axis titles, units,
  hover format, height),
* deterministic colour assignment (no random palettes),
* mandatory disclosures drawn **on the figure** — sampling/approximate
  badges and comparison exactness flags, so exported HTML files remain
  honest outside the application,
* self-contained HTML export with Plotly.js inlined.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from visualization.visualization_config import APPROX_COLOR, OTHER_COLOR, PALETTE


def palette_color(index: int) -> str:
    """Deterministic colour from the shared palette."""
    return PALETTE[int(index) % len(PALETTE)]


def category_colors(
    labels, explicit_order: list[str] | None = None
) -> dict[str, str]:
    """Stable label -> colour mapping (first-appearance order)."""
    order: list[str] = []
    seen: set[str] = set()
    for lab in labels:
        lab = str(lab)
        if lab not in seen:
            seen.add(lab)
            order.append(lab)
    if explicit_order:
        order = list(dict.fromkeys([str(x) for x in explicit_order] + order))
    colors = {}
    for i, lab in enumerate(order):
        colors[lab] = OTHER_COLOR if lab.startswith("Other (rest)") \
            else palette_color(i)
    return colors


def finalize(
    fig: go.Figure,
    *,
    title: str,
    x_title: str | None = None,
    y_title: str | None = None,
    height: int | None = None,
    notes: list[str] | None = None,
    disclosure: dict | None = None,
    exactness_note: str | None = None,
    approximate: bool = False,
    barmode: str | None = None,
    hover: str | None = None,
    legend_title: str | None = None,
) -> go.Figure:
    """Apply the standard layout and mandatory disclosure badges.

    ``disclosure`` is a sampling dict (see data_adapter.sample_frame);
    ``exactness_note`` annotates comparison charts (e.g.
    "exact match · strategy=key"); ``approximate`` forces the badge into
    the approximate colour (used when a comparison ran approximately).
    Both badges are drawn on the canvas so exported HTML stays honest.
    """
    notes = list(notes or [])
    badges: list[str] = []
    approx = bool(disclosure and disclosure.get("approximate")) or approximate
    if disclosure and disclosure.get("applied"):
        method = disclosure.get("method", "sampling")
        params = disclosure.get("params") or {}
        detail = ", ".join(f"{k}={v}" for k, v in sorted(params.items())
                           if k in ("seed", "limit", "max_rows",
                                    "top_n", "bins"))
        badges.append(f"Sampling: {method}" + (f" ({detail})" if detail else "")
                      + " — values are approximate")
    if exactness_note:
        badges.append(exactness_note)

    fig.update_layout(
        template="plotly_white",
        title={
            "text": title,
            "x": 0.02,
            "xanchor": "left",
            "font": {"size": 17},
        },
        height=height or 460,
        margin=dict(l=60, r=30, t=70, b=90),
        hovermode="closest",
        legend_title_text=legend_title or "",
    )
    if x_title:
        fig.update_xaxes(title_text=x_title, showgrid=True)
    if y_title:
        fig.update_yaxes(title_text=y_title, showgrid=True)
    if barmode:
        fig.update_layout(barmode=barmode)

    annotations = []
    if badges:
        annotations.append(dict(
            text="  •  ".join(badges),
            xref="paper", yref="paper",
            x=0.0, y=-0.20,
            showarrow=False,
            xanchor="left",
            font=dict(size=11,
                      color=APPROX_COLOR if approx else "#666666"),
            align="left",
        ))
    if notes:
        annotations.append(dict(
            text="  •  ".join(notes),
            xref="paper", yref="paper",
            x=0.0, y=-0.27,
            showarrow=False,
            xanchor="left",
            font=dict(size=10, color="#888888"),
            align="left",
        ))
    if annotations:
        fig.update_layout(annotations=annotations)
    else:
        fig.update_layout(margin=dict(l=60, r=30, t=70, b=60))
    return fig


def figure_to_html(fig: go.Figure) -> str:
    """Self-contained HTML (Plotly.js inlined) for offline export."""
    return fig.to_html(
        include_plotlyjs="inline",
        full_html=True,
        config={"displaylogo": False, "responsive": True},
    )


def numeric_hover(series: pd.Series, unit: str = "") -> str:
    """Hovertemplate fragment for a numeric axis."""
    suffix = f" {unit}" if unit else ""
    return f"%{{y}}{suffix}<extra></extra>"


def label_series(values) -> str:
    """Short axis label derived from a column or constant."""
    if isinstance(values, pd.Series):
        return str(values.name or "")
    return str(values)
