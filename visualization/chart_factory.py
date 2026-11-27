"""Generic chart builders and shared request/artifact types (Phase 7).

Every builder is a **pure function** of a ``ChartRequest``: it never
reads files itself (callers load bounded frames via ``data_adapter``),
never mutates its input, and returns a ``ChartArtifact`` carrying:

* the finished Plotly figure (with disclosure badges drawn on it),
* the plotted data as a tidy DataFrame for CSV export,
* explicit display metadata (total/displayed rows, sampling, basis).

Builders raise ``PlatformError`` with actionable messages on bad input —
never raw tracebacks for user mistakes.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from core.exceptions import PlatformError
from visualization import filters as flt
from visualization import plotly_helpers as ph
from visualization.validation import column_kind, require_columns, require_kinds, require_non_empty, require_numeric
from visualization.visualization_config import VisualizationConfig


@dataclass
class ChartRequest:
    """Everything a builder may touch. Pure data — no I/O."""

    chart_id: str
    cfg: VisualizationConfig
    roles: dict = field(default_factory=dict)
    options: dict = field(default_factory=dict)
    # mode A inputs
    files: pd.DataFrame | None = None
    folders: pd.DataFrame | None = None
    snapshots: list[dict] | None = None
    snapshot_diff: dict | None = None
    # mode B inputs
    frame: pd.DataFrame | None = None
    run: dict | None = None
    run_meta: dict | None = None
    # mode C inputs
    comparison: dict | None = None
    # provenance (labels only — never absolute paths)
    source_label: str = ""
    meta: dict = field(default_factory=dict)


@dataclass
class ChartArtifact:
    """A finished chart plus everything needed to display and export it."""

    figure: go.Figure
    chart_id: str
    title: str
    data: pd.DataFrame = field(default_factory=pd.DataFrame)
    notes: list[str] = field(default_factory=list)
    display: dict = field(default_factory=dict)
    roles: dict = field(default_factory=dict)
    options: dict = field(default_factory=dict)
    extra_exports: dict = field(default_factory=dict)

    @property
    def approximate(self) -> bool:
        return bool(self.display.get("approximate"))


# ----------------------------------------------------------------- utils

def human_bytes(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "—"
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    i = 0
    while n >= 1024.0 and i < len(units) - 1:
        n /= 1024.0
        i += 1
    return f"{n:,.1f} {units[i]}" if i else f"{int(n):,} B"


def human_count(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return "—"


def _frame(req: ChartRequest, name: str, label: str) -> pd.DataFrame:
    frame = getattr(req, name, None)
    if frame is None or not isinstance(frame, pd.DataFrame):
        raise PlatformError(
            f"This chart needs {label}, which is not available for the "
            "current selection.",
            detail=f"{req.chart_id} missing {name}",
        )
    return frame


def _as_datetime(series: pd.Series) -> tuple[pd.Series, int]:
    parsed = flt.parse_dates(series)
    dropped = int(parsed.isna().sum() - series.isna().sum())
    return parsed, max(dropped, 0)


def _top_frame(
    frame: pd.DataFrame,
    column: str,
    cfg: VisualizationConfig,
    cap: int | None = None,
    value_col: str | None = None,
    agg: str = "count",
) -> tuple[pd.DataFrame, str | None]:
    """Bar-ready top-N table with explicit regrouping disclosure."""
    cap = cap or cfg.max_bar_categories
    if value_col is None:
        out, info = flt.top_categories(frame, column, cap)
        note = None
        if info["regrouped"]:
            note = (
                f"{column}: {info['total_categories']:,} distinct values — "
                f"showing top {info['shown']} plus an aggregated "
                "'Other (rest)' slice. No records were discarded."
            )
        return out, note
    require_numeric(frame, [value_col], what="value column")
    if agg not in ("sum", "mean", "count", "median"):
        raise PlatformError(
            f"Aggregation '{agg}' is not supported; use sum, mean, median "
            "or count.",
            detail=f"agg {agg}",
        )
    work = frame[[column, value_col]].copy()
    work[column] = work[column].astype(str)
    grouped = work.groupby(column, dropna=False)[value_col]
    series = {"sum": grouped.sum(), "mean": grouped.mean(),
              "median": grouped.median(),
              "count": grouped.count()}[agg]
    series = series.reset_index(name="value")
    series = series.sort_values("value", ascending=False,
                                kind="mergesort").reset_index(drop=True)
    note = None
    if len(series) > cap:
        head = series.head(cap)
        rest = series.iloc[cap:]
        other_val = float(rest["value"].sum())
        series = pd.concat(
            [head, pd.DataFrame([{column: "Other (rest)",
                                  "value": other_val}])],
            ignore_index=True)
        note = (
            f"{column}: {len(rest) + cap:,} distinct values grouped — "
            f"showing top {cap} plus 'Other (rest)' "
            f"(aggregated {agg}). No records were discarded."
        )
    return series, note


def _display(
    rows_used: int,
    sampling: dict | None = None,
    *,
    total_rows: int | None = None,
    basis: str = "",
    approximate: bool = False,
) -> dict:
    sampling = sampling or {"applied": False, "method": "none",
                            "params": {}, "approximate": False}
    total = total_rows if total_rows is not None else rows_used
    return {
        "total_rows": int(total),
        "displayed_rows": int(rows_used),
        "sampling": dict(sampling),
        "approximate": bool(approximate)
        or bool(sampling.get("approximate")),
        "basis": basis,
    }


def _empty_display() -> dict:
    return _display(0)


# ------------------------------------------------------------------ bars

def build_bar(req: ChartRequest) -> ChartArtifact:
    frame = _frame(req, "frame", "the dataset preview")
    x = req.roles.get("x")
    y = req.roles.get("y")
    agg = str(req.options.get("agg") or "count")
    require_non_empty(frame)
    require_columns([x], list(frame.columns), what="bar chart")
    notes: list[str] = []
    if y:
        require_columns([y], list(frame.columns), what="bar chart")
        require_kinds(frame, {"y": y}, {"y": ("int", "float")})
        table, note = _top_frame(frame, x, req.cfg, value_col=y, agg=agg)
        if note:
            notes.append(note)
        title = f"{agg.capitalize()} of {y} by {x}"
        y_title = f"{agg.capitalize()} of {y}"
        fig = px.bar(table, x=x, y="value", color_discrete_sequence=ph.PALETTE)
        fig.update_traces(hovertemplate=f"{x}=%{{x}}<br>{y_title}=%{{y:,.4g}}"
                                       "<extra></extra>")
        data = table.rename(columns={"value": f"{agg}_{y}"})
    else:
        table, note = _top_frame(frame, x, req.cfg)
        if note:
            notes.append(note)
        title = f"Records by {x}"
        y_title = "Count"
        fig = px.bar(table, x=x, y="count", color_discrete_sequence=ph.PALETTE)
        fig.update_traces(hovertemplate=f"{x}=%{{x}}<br>count=%{{y:,}}"
                                       "<extra></extra>")
        data = table
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title=x, y_title=y_title,
                           notes=notes, height=440),
        chart_id=req.chart_id, title=title, data=data, notes=notes,
        display=_display(len(frame),
                         basis=f"{len(frame):,} rows scanned"),
        roles=dict(req.roles), options=dict(req.options),
    )


def build_hbar(req: ChartRequest) -> ChartArtifact:
    art = build_bar(req)
    art.figure.update_layout(yaxis=dict(autorange="reversed"),
                             xaxis_title=art.figure.layout.yaxis.title.text,
                             yaxis_title=art.figure.layout.xaxis.title.text)
    art.title = art.title + " (horizontal)"
    art.figure.layout.title.text = art.title
    return art


def _group_table(
    frame: pd.DataFrame, x: str, value: str, group: str, agg: str
) -> pd.DataFrame:
    require_numeric(frame, [value], what="value column")
    work = frame[[x, value, group]].copy()
    work[x] = work[x].astype(str)
    work[group] = work[group].astype(str)
    grouped = work.groupby([x, group], dropna=False)[value]
    series = {"sum": grouped.sum(), "mean": grouped.mean(),
              "median": grouped.median(), "count": grouped.count()}[agg]
    out = series.reset_index(name="value")
    return out.sort_values([group, x], kind="mergesort").reset_index(drop=True)


def build_grouped_bar(req: ChartRequest) -> ChartArtifact:
    frame = _frame(req, "frame", "the dataset preview")
    x = req.roles.get("x")
    value = req.roles.get("value") or req.roles.get("y")
    group = req.roles.get("group")
    agg = str(req.options.get("agg") or "mean")
    require_non_empty(frame)
    require_columns([c for c in (x, value, group) if c],
                    list(frame.columns), what="grouped bar chart")
    table = _group_table(frame, x, value, group, agg)
    notes: list[str] = []
    if len(table) * 1.0 > req.cfg.max_bar_categories * 12:
        notes.append(
            f"{x} × {group}: {len(table):,} bars shown — consider a "
            "top-N filter on either column for readability.")
    title = f"{agg.capitalize()} of {value} by {x} and {group}"
    fig = px.bar(table, x=x, y="value", color=group,
                 barmode="group", color_discrete_sequence=ph.PALETTE)
    fig.update_traces(hovertemplate=f"{x}=%{{x}}<br>{group}=%{{color}}"
                                    f"<br>{value}=%{{y:,.4g}}<extra></extra>")
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title=x,
                           y_title=f"{agg.capitalize()} of {value}",
                           notes=notes, legend_title=group, height=460),
        chart_id=req.chart_id, title=title, data=table, notes=notes,
        display=_display(len(frame), basis=f"{len(frame):,} rows scanned"),
        roles=dict(req.roles), options=dict(req.options),
    )


def build_stacked_bar(req: ChartRequest) -> ChartArtifact:
    frame = _frame(req, "frame", "the dataset preview")
    x = req.roles.get("x")
    group = req.roles.get("group")
    value = req.roles.get("value")
    agg = str(req.options.get("agg") or "count")
    require_non_empty(frame)
    require_columns([c for c in (x, group) if c],
                    list(frame.columns), what="stacked bar chart")
    if value:
        table = _group_table(frame, x, value, group, agg)
        y_col, y_title = "value", f"{agg.capitalize()} of {value}"
        title = f"{agg.capitalize()} of {value} by {x} and {group}"
    else:
        table, note = _top_frame(frame, x, req.cfg)
        notes_pre = [note] if note else []
        work = frame[[x, group]].astype({x: str, group: str})
        counts = work.groupby([x, group]).size().reset_index(name="count")
        # keep bar categories consistent with top-N cap
        keep = set(table[x].tolist())
        counts = counts[counts[x].isin(keep)]
        table = counts
        y_col, y_title = "count", "Count"
        title = f"Records by {x} and {group}"
        notes_pre = notes_pre
    notes: list[str] = []
    if value is None and notes_pre:
        notes.extend(notes_pre)
    fig = px.bar(table, x=x, y=y_col, color=group, barmode="stack",
                 color_discrete_sequence=ph.PALETTE)
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title=x, y_title=y_title,
                           notes=notes, barmode="stack", legend_title=group),
        chart_id=req.chart_id, title=title, data=table, notes=notes,
        display=_display(len(frame), basis=f"{len(frame):,} rows scanned"),
        roles=dict(req.roles), options=dict(req.options),
    )


def _time_table(
    frame: pd.DataFrame, x: str, y: str | None, group: str | None, cfg
) -> tuple[pd.DataFrame, str]:
    series, dropped = _as_datetime(frame[x])
    work = pd.DataFrame({x: series})
    if y:
        work[y] = pd.to_numeric(frame[y], errors="coerce")
    if group:
        work[group] = frame[group].astype(str)
    work = work.dropna(subset=[x])
    note = f"{dropped:,} unparseable date value(s) excluded" if dropped else ""
    return work, note


def build_line(req: ChartRequest) -> ChartArtifact:
    frame = _frame(req, "frame", "the dataset preview")
    x = req.roles.get("x")
    y = req.roles.get("y")
    group = req.roles.get("group")
    require_non_empty(frame)
    cols = [c for c in (x, y, group) if c]
    require_columns(cols, list(frame.columns), what="line chart")
    work, note = _time_table(frame, x, y, group, req.cfg)
    require_non_empty(work, what="parsed dates")
    notes = [note] if note else []
    if y:
        work = work.dropna(subset=[y])
        title = f"{y} over {x}"
        fig = px.line(work, x=x, y=y, color=group if group else None,
                      markers=True, color_discrete_sequence=ph.PALETTE)
        y_title = y
    else:
        counts = work.groupby(x).size().reset_index(name="count")
        title = f"Records over {x}"
        fig = px.line(counts, x=x, y="count", markers=True,
                      color_discrete_sequence=ph.PALETTE)
        y_title = "Count"
        work = counts
    if len(work) > req.cfg.max_points:
        # line charts aggregate by x — only point sampling would apply
        pass
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title=str(x),
                           y_title=y_title, notes=notes,
                           legend_title=group or ""),
        chart_id=req.chart_id, title=title, data=work, notes=notes,
        display=_display(len(work), basis=f"{len(work):,} plotted rows"),
        roles=dict(req.roles), options=dict(req.options),
    )


def build_area(req: ChartRequest) -> ChartArtifact:
    art = build_line(req)
    art.figure.update_traces(fill="tozeroy")
    art.title = art.title + " (area)"
    art.figure.layout.title.text = art.title
    return art


def build_pie(req: ChartRequest) -> ChartArtifact:
    frame = _frame(req, "frame", "the dataset preview")
    names = req.roles.get("names")
    values = req.roles.get("values")
    donut = bool(req.options.get("donut"))
    require_non_empty(frame)
    require_columns([names], list(frame.columns), what="pie chart")
    notes: list[str] = []
    cap = req.cfg.pie_max_categories
    if values:
        require_columns([values], list(frame.columns), what="pie chart")
        require_kinds(frame, {"values": values}, {"values": ("int", "float")})
        work = frame[[names, values]].copy()
        work[names] = work[names].astype(str)
        sums = work.groupby(names)[values].sum().reset_index(name="value")
        sums = sums.sort_values("value", ascending=False,
                                kind="mergesort").reset_index(drop=True)
        if len(sums) > cap:
            head = sums.head(cap)
            rest = sums.iloc[cap:]
            sums = pd.concat([head, pd.DataFrame(
                [{names: "Other (rest)", "value": float(rest["value"].sum())}])],
                ignore_index=True)
            notes.append(
                f"{names}: {len(rest) + cap:,} distinct values — top "
                f"{cap} shown plus 'Other (rest)'; totals are exact.")
        fig = px.pie(sums, names=names, values="value",
                     color_discrete_sequence=ph.PALETTE)
        title = f"{values} share by {names}"
        table = sums
    else:
        table, info = flt.top_categories(frame, names, cap)
        if info.get("regrouped"):
            notes.append(
                f"{names}: {info['total_categories']:,} distinct values — "
                f"top {info['shown']} shown plus an aggregated "
                "'Other (rest)' slice. No records were discarded.")
        fig = px.pie(table, names=names, values="count",
                     color_discrete_sequence=ph.PALETTE)
        title = f"Share of records by {names}"
        notes.append(
            "pie charts are hard to compare — read exact values in the "
            f"hover labels; bar charts are used beyond {cap} categories.")
    if donut:
        fig.update_traces(hole=0.45)
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, notes=notes, height=470),
        chart_id=req.chart_id, title=title, data=table, notes=notes,
        display=_display(len(frame), basis=f"{len(frame):,} rows scanned"),
        roles=dict(req.roles), options=dict(req.options),
    )


def build_donut(req: ChartRequest) -> ChartArtifact:
    req.options = dict(req.options, donut=True)
    art = build_pie(req)
    art.title = art.title.replace("share", "donut share") + " (donut)" \
        if "share" in art.title else art.title + " (donut)"
    art.figure.layout.title.text = art.title
    return art


# ----------------------------------------------------------- statistics

def build_histogram(req: ChartRequest) -> ChartArtifact:
    frame = _frame(req, "frame", "the dataset preview")
    col = req.roles.get("values")
    bins = int(req.options.get("bins") or req.cfg.histogram_bins)
    log_x = bool(req.options.get("log_x"))
    require_non_empty(frame)
    require_columns([col], list(frame.columns), what="histogram")
    require_kinds(frame, {"values": col}, {"values": ("int", "float")})
    values = pd.to_numeric(frame[col], errors="coerce")
    finite = values[np.isfinite(values.to_numpy(dtype="float64",
                                                 na_value=np.nan))]
    excluded = int(len(values) - len(finite))
    require_non_empty(finite.rename(col).to_frame(), what="finite values")
    hist = np.histogram(finite.to_numpy(dtype="float64"), bins=bins)
    counts, edges = hist
    centers = [(edges[i] + edges[i + 1]) / 2 for i in range(len(counts))]
    width = edges[1] - edges[0]
    notes: list[str] = []
    if excluded:
        notes.append(f"{excluded:,} missing/non-finite value(s) excluded "
                     "from the histogram.")
    if log_x:
        notes.append("x-axis shown in log10 scale for readability.")
    fig = go.Figure(go.Bar(
        x=centers, y=counts,
        marker_color=ph.palette_color(0),
        width=width * 0.94,
        hovertemplate="bin centre %{x:,.4g}<br>count %{y:,}<extra></extra>",
    ))
    title = f"Distribution of {col}"
    if log_x:
        fig.update_xaxes(type="log", title_text=f"{col} (log10)")
    data = pd.DataFrame({"bin_start": edges[:-1], "bin_end": edges[1:],
                         "count": counts})
    return ChartArtifact(
        figure=ph.finalize(fig, title=title,
                           x_title=None if log_x else str(col),
                           y_title="Count", notes=notes),
        chart_id=req.chart_id, title=title, data=data, notes=notes,
        display=_display(len(finite),
                         basis=f"{bins} bins over {len(finite):,} values"),
        roles=dict(req.roles), options=dict(req.options),
    )


def _box_like(req: ChartRequest, kind: str) -> ChartArtifact:
    frame = _frame(req, "frame", "the dataset preview")
    col = req.roles.get("values")
    group = req.roles.get("group")
    require_non_empty(frame)
    require_columns([c for c in (col, group) if c],
                    list(frame.columns), what=f"{kind} plot")
    require_kinds(frame, {"values": col}, {"values": ("int", "float")})
    cols = [col] + ([group] if group else [])
    work = frame[cols].copy()
    work[col] = pd.to_numeric(work[col], errors="coerce")
    work = work.dropna(subset=[col])
    require_non_empty(work, what="finite values")
    sampled, sampling = flt.sample_frame(work, req.cfg.max_points,
                                         req.cfg.seed)
    if kind == "box":
        fig = px.box(sampled, x=group if group else None, y=col,
                     color_discrete_sequence=ph.PALETTE)
        title = f"Box plot of {col}" + (f" by {group}" if group else "")
    else:
        fig = px.violin(sampled, x=group if group else None, y=col,
                        box=True, color_discrete_sequence=ph.PALETTE)
        title = f"Violin plot of {col}" + (f" by {group}" if group else "")
    notes: list[str] = []
    if sampling.get("applied"):
        notes.append(f"plotted from a random sample of "
                     f"{sampling['params']['limit']:,} of "
                     f"{sampling['params']['requested_rows']:,} rows "
                     "(seed shown on badge) — quartile positions are "
                     "approximate.")
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title=group or "",
                           y_title=col, notes=notes,
                           disclosure=sampling),
        chart_id=req.chart_id, title=title, data=sampled, notes=notes,
        display=_display(len(sampled), sampling,
                         total_rows=len(work),
                         basis="quartiles from displayed rows"),
        roles=dict(req.roles), options=dict(req.options),
    )


def build_box(req: ChartRequest) -> ChartArtifact:
    return _box_like(req, "box")


def build_violin(req: ChartRequest) -> ChartArtifact:
    return _box_like(req, "violin")


def build_density(req: ChartRequest) -> ChartArtifact:
    frame = _frame(req, "frame", "the dataset preview")
    col = req.roles.get("values")
    require_non_empty(frame)
    require_columns([col], list(frame.columns), what="density plot")
    require_kinds(frame, {"values": col}, {"values": ("int", "float")})
    values = pd.to_numeric(frame[col], errors="coerce")
    values = values[np.isfinite(values.to_numpy(dtype="float64",
                                                na_value=np.nan))].dropna()
    require_non_empty(values.rename(col).to_frame(), what="finite values")
    arr = values.to_numpy(dtype="float64")
    if len(arr) > req.cfg.max_points:
        arr, sampling = flt.sample_array(arr, req.cfg.max_points,
                                         req.cfg.seed)
    else:
        sampling = {"applied": False, "method": "none", "params": {},
                    "approximate": False}
    # Gaussian KDE (Silverman bandwidth) — numpy only, no scipy dependency
    std = float(np.std(arr)) or 1.0
    n = len(arr)
    bw = 1.06 * std * n ** (-1 / 5)
    grid = np.linspace(float(arr.min()), float(arr.max()), 200)
    z = (grid[:, None] - arr[None, :]) / max(bw, 1e-12)
    dens = np.exp(-0.5 * z * z).mean(axis=1) / (max(bw, 1e-12) *
                                                np.sqrt(2 * np.pi))
    fig = go.Figure(go.Scatter(
        x=grid, y=dens, fill="tozeroy",
        line=dict(color=ph.palette_color(0)),
        hovertemplate="x %{x:,.4g}<br>density %{y:,.4g}<extra></extra>"))
    title = f"Density of {col}"
    notes = ["Gaussian kernel density estimate (Silverman bandwidth); "
             "a smoothed view, not exact bin counts."]
    if sampling.get("applied"):
        notes.append(f"estimated from a random sample of {len(arr):,} of "
                     f"{int(sampling['params']['requested_rows']):,} rows "
                     "(seed shown on badge).")
    data = pd.DataFrame({"x": grid, "density": dens})
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title=str(col),
                           y_title="Density", notes=notes,
                           disclosure=sampling),
        chart_id=req.chart_id, title=title, data=data, notes=notes,
        display=_display(len(arr), sampling, total_rows=int(len(values)),
                         basis="KDE over displayed rows"),
        roles=dict(req.roles), options=dict(req.options),
    )


def build_ecdf(req: ChartRequest) -> ChartArtifact:
    frame = _frame(req, "frame", "the dataset preview")
    col = req.roles.get("values")
    require_non_empty(frame)
    require_columns([col], list(frame.columns), what="ECDF")
    require_kinds(frame, {"values": col}, {"values": ("int", "float")})
    values = pd.to_numeric(frame[col], errors="coerce")
    values = values.dropna()
    arr = np.sort(values.to_numpy(dtype="float64"))
    arr = arr[np.isfinite(arr)]
    require_non_empty(pd.DataFrame({col: arr}), what="finite values")
    total = len(arr)
    if total > req.cfg.max_points:
        keep = np.linspace(0, total - 1, req.cfg.max_points).astype(int)
        arr = arr[keep]
        sampling = {"applied": True, "method": "uniform_points",
                    "params": {"limit": req.cfg.max_points,
                               "requested_rows": total},
                    "approximate": True}
        notes = ["curve rendered from evenly spaced order statistics "
                 "(display cap) — approximated visually, not in value."]
    else:
        sampling = {"applied": False, "method": "none", "params": {},
                    "approximate": False}
        notes = []
    y = np.arange(1, len(arr) + 1) / total
    fig = go.Figure(go.Scatter(x=arr, y=y, mode="lines",
                               line=dict(color=ph.palette_color(0)),
                               hovertemplate="x %{x:,.4g}"
                                             "<br>cumulative share "
                                             "%{y:.1%}<extra></extra>"))
    title = f"ECDF of {col}"
    data = pd.DataFrame({col: arr, "cumulative_share": y})
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title=str(col),
                           y_title="Cumulative share", notes=notes,
                           disclosure=sampling),
        chart_id=req.chart_id, title=title, data=data, notes=notes,
        display=_display(len(arr), sampling, total_rows=total,
                         basis="order statistics over all rows"),
        roles=dict(req.roles), options=dict(req.options),
    )


def build_scatter(req: ChartRequest) -> ChartArtifact:
    frame = _frame(req, "frame", "the dataset preview")
    x, y = req.roles.get("x"), req.roles.get("y")
    color = req.roles.get("color")
    trend = bool(req.options.get("trendline"))
    require_non_empty(frame)
    cols = [c for c in (x, y, color) if c]
    require_columns(cols, list(frame.columns), what="scatter chart")
    require_kinds(frame, {"x": x, "y": y},
                  {"x": ("int", "float"), "y": ("int", "float")})
    work = frame[cols].copy()
    work[x] = pd.to_numeric(work[x], errors="coerce")
    work[y] = pd.to_numeric(work[y], errors="coerce")
    work = work.dropna(subset=[x, y])
    require_non_empty(work, what="paired numeric rows")
    sampled, sampling = flt.sample_frame(work, req.cfg.max_points,
                                         req.cfg.seed)
    color_kw = {"color": color} if color else {}
    fig = px.scatter(sampled, x=x, y=y, **color_kw,
                     color_discrete_sequence=ph.PALETTE)
    notes: list[str] = []
    if sampling.get("applied"):
        notes.append(f"plotted from a random sample of "
                     f"{sampling['params']['limit']:,} of "
                     f"{sampling['params']['requested_rows']:,} rows "
                     "(seed shown on badge).")
    if trend:
        coef = np.polyfit(sampled[x].to_numpy(dtype="float64"),
                          sampled[y].to_numpy(dtype="float64"), 1)
        xs = np.array([float(sampled[x].min()), float(sampled[x].max())])
        fig.add_trace(go.Scatter(x=xs, y=np.polyval(coef, xs),
                                 mode="lines", name="linear fit",
                                 line=dict(dash="dash",
                                           color=ph.palette_color(1))))
        notes.append("dashed line = least-squares fit over plotted rows.")
    title = f"{y} vs {x}"
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title=str(x),
                           y_title=str(y), notes=notes,
                           disclosure=sampling, legend_title=color or ""),
        chart_id=req.chart_id, title=title, data=sampled, notes=notes,
        display=_display(len(sampled), sampling, total_rows=len(work),
                         basis="paired complete rows"),
        roles=dict(req.roles), options=dict(req.options),
    )


def build_bubble(req: ChartRequest) -> ChartArtifact:
    frame = _frame(req, "frame", "the dataset preview")
    x, y, size = req.roles.get("x"), req.roles.get("y"), req.roles.get("size")
    color = req.roles.get("color")
    require_non_empty(frame)
    cols = [c for c in (x, y, size, color) if c]
    require_columns(cols, list(frame.columns), what="bubble chart")
    require_kinds(frame, {"x": x, "y": y, "size": size},
                  {"x": ("int", "float"), "y": ("int", "float"),
                   "size": ("int", "float")})
    work = frame[cols].copy()
    for c in (x, y, size):
        work[c] = pd.to_numeric(work[c], errors="coerce")
    work = work.dropna(subset=[x, y, size])
    require_non_empty(work, what="rows with x, y and size")
    sampled, sampling = flt.sample_frame(work, req.cfg.max_points,
                                         req.cfg.seed)
    color_kw = {"color": color} if color else {}
    fig = px.scatter(sampled, x=x, y=y, size=size, **color_kw,
                     color_discrete_sequence=ph.PALETTE)
    title = f"{y} vs {x} (size: {size})"
    notes: list[str] = []
    if sampling.get("applied"):
        notes.append(f"plotted from a random sample of "
                     f"{sampling['params']['limit']:,} of "
                     f"{sampling['params']['requested_rows']:,} rows "
                     "(seed shown on badge).")
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title=str(x),
                           y_title=str(y), notes=notes,
                           disclosure=sampling, legend_title=color or ""),
        chart_id=req.chart_id, title=title, data=sampled, notes=notes,
        display=_display(len(sampled), sampling, total_rows=len(work),
                         basis="rows with all three measures"),
        roles=dict(req.roles), options=dict(req.options),
    )


def _multi_columns(req: ChartRequest, role: str, max_cols: int,
                   min_cols: int = 2) -> list[str]:
    cols = req.roles.get(role) or []
    if isinstance(cols, str):
        cols = [cols]
    cols = [c for c in cols if c]
    if len(cols) < min_cols:
        raise PlatformError(
            f"Select at least {min_cols} numeric columns for this chart "
            f"(you selected {len(cols)}).",
            detail=f"{req.chart_id} {role} too few",
        )
    if len(cols) > max_cols:
        raise PlatformError(
            f"This chart supports at most {max_cols} columns; "
            f"{len(cols)} selected. Reduce the selection.",
            detail=f"{req.chart_id} {role} too many",
        )
    return cols


def build_pair(req: ChartRequest) -> ChartArtifact:
    frame = _frame(req, "frame", "the dataset preview")
    cols = _multi_columns(req, "columns", req.cfg.pair_max_columns)
    require_non_empty(frame)
    require_columns(cols, list(frame.columns), what="pair plot")
    require_numeric(frame, cols, what="pair plot")
    work = frame[cols].apply(pd.to_numeric, errors="coerce").dropna()
    require_non_empty(work, what="complete numeric rows across "
                                 "the selected columns")
    sampled, sampling = flt.sample_frame(work, req.cfg.max_points,
                                         req.cfg.seed)
    fig = px.scatter_matrix(sampled, dimensions=cols,
                            color_discrete_sequence=ph.PALETTE)
    notes: list[str] = []
    if sampling.get("applied"):
        notes.append(f"matrix drawn from {len(sampled):,} of "
                     f"{sampling['params']['requested_rows']:,} complete "
                     "rows (seed shown on badge).")
    title = "Pair plot of " + ", ".join(cols[:4]) + \
        ("…" if len(cols) > 4 else "")
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, notes=notes,
                           disclosure=sampling, height=620),
        chart_id=req.chart_id, title=title, data=sampled, notes=notes,
        display=_display(len(sampled), sampling, total_rows=len(work),
                         basis="complete rows across selected columns"),
        roles=dict(req.roles), options=dict(req.options),
    )


def build_corr_heatmap(req: ChartRequest) -> ChartArtifact:
    frame = _frame(req, "frame", "the dataset preview")
    cols = req.roles.get("columns") or []
    if isinstance(cols, str):
        cols = [cols]
    cols = [c for c in cols if c]
    require_non_empty(frame)
    if len(cols) < 2:
        numeric = [c for c in frame.columns
                   if pd.api.types.is_numeric_dtype(frame[c])]
        cols = numeric[:12]
    if len(cols) < 2:
        raise PlatformError(
            "Correlation needs at least two numeric columns; this data "
            "does not have them.",
            detail=f"{req.chart_id} corr needs 2 numeric",
        )
    if len(cols) > req.cfg.matrix_max_columns + 6:
        cols = cols[:req.cfg.matrix_max_columns + 6]
    require_columns(cols, list(frame.columns), what="correlation heatmap")
    require_numeric(frame, cols, what="correlation heatmap")
    work = frame[cols].apply(pd.to_numeric, errors="coerce")
    corr = work.corr(method="pearson").fillna(0.0)
    fig = px.imshow(corr, text_auto=".2f", color_continuous_scale="RdBu_r",
                    zmin=-1, zmax=1)
    title = "Pearson correlation heatmap"
    notes = ["pairwise-complete rows; |r| near 1 = strong linear "
             "relationship (not causation).",
             f"based on {len(work):,} rows scanned."]
    data = corr.reset_index().rename(columns={"index": "column"})
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, notes=notes, height=560),
        chart_id=req.chart_id, title=title, data=data, notes=notes,
        display=_display(len(work), basis="pairwise-complete rows"),
        roles=dict(req.roles), options=dict(req.options),
    )


def build_missing_values(req: ChartRequest) -> ChartArtifact:
    frame = _frame(req, "frame", "the dataset preview")
    require_non_empty(frame)
    missing = frame.isna().sum()
    total = int(len(frame))
    table = (missing.rename("missing").reset_index()
             .rename(columns={"index": "column"}))
    table["missing_pct"] = (table["missing"] / total * 100).round(2)
    table = table.sort_values("missing", ascending=False,
                              kind="mergesort").reset_index(drop=True)
    title = "Missing values per column"
    fig = px.bar(table, x="column", y="missing",
                 color_discrete_sequence=[ph.APPROX_COLOR],
                 hover_data={"missing_pct": True})
    notes = [f"computed over {total:,} displayed row(s)."]
    if bool(table["missing"].sum()):
        notes.append("missing values are excluded from statistics — "
                     "counts here show the impact per column.")
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title="column",
                           y_title="missing rows", notes=notes),
        chart_id=req.chart_id, title=title, data=table, notes=notes,
        display=_display(total, basis=f"{total:,} rows scanned"),
        roles=dict(req.roles), options=dict(req.options),
    )


# -------------------------------------------------------- multivariate

def _numeric_matrix(
    req: ChartRequest, frame: pd.DataFrame, cols: list[str],
    *, max_rows: int, what: str,
    extra_cols: tuple[str, ...] = (),
) -> tuple[pd.DataFrame, pd.DataFrame, list[str], dict, list[str]]:
    """Standardised numeric matrix on complete rows, bounded + disclosed.

    ``cols`` are standardized; ``extra_cols`` (e.g. a colour column) are
    carried through raw. Returns (rows_used, scaled_df, notes, sampling,
    exclusion_info).
    """
    all_cols = list(cols) + [c for c in extra_cols if c not in cols]
    require_columns(all_cols, list(frame.columns), what=what)
    require_numeric(frame, cols, what=what)
    work = frame[cols].apply(pd.to_numeric, errors="coerce")
    for c in extra_cols:
        work[c] = frame[c]
    before = len(work)
    complete = work.dropna(subset=cols)
    excluded = before - len(complete)
    notes: list[str] = []
    if excluded:
        notes.append(f"{excluded:,} row(s) with missing values in the "
                     f"selected columns excluded ({len(complete):,} used).")
    require_non_empty(complete, what="complete numeric rows")
    sampled, sampling = flt.sample_frame(complete, max_rows, req.cfg.seed)
    if sampling.get("applied"):
        notes.append(f"fitted on a random sample of {len(sampled):,} of "
                     f"{sampling['params']['requested_rows']:,} complete "
                     "rows (seed shown on badge).")
    values = sampled[cols].to_numpy(dtype="float64")
    mean = values.mean(axis=0)
    std = values.std(axis=0)
    std[std == 0] = 1.0
    scaled = (values - mean) / std
    notes.append("columns standardized (zero mean, unit variance) so "
                 "high-magnitude columns do not dominate.")
    scaled_df = pd.DataFrame(scaled, columns=cols)
    return sampled, scaled_df, notes, sampling, {"excluded_missing": excluded}


def build_parallel_coordinates(req: ChartRequest) -> ChartArtifact:
    frame = _frame(req, "frame", "the dataset preview")
    cols = _multi_columns(req, "columns", req.cfg.parallel_max_columns)
    color = req.roles.get("color")
    require_non_empty(frame)
    if color:
        require_columns([color], list(frame.columns),
                        what="parallel coordinates")
    require_columns(cols, list(frame.columns), what="parallel coordinates")
    require_numeric(frame, cols, what="parallel coordinates")
    numeric = frame[cols].apply(pd.to_numeric, errors="coerce")
    comb = numeric.copy()
    if color:
        comb[color] = frame[color]
    before = len(comb)
    comb = comb.dropna(subset=cols)
    notes: list[str] = []
    excluded = before - len(comb)
    if excluded:
        notes.append(f"{excluded:,} row(s) with missing values in the "
                     f"selected columns excluded ({len(comb):,} used).")
    require_non_empty(comb, what="complete numeric rows")
    sampled, sampling = flt.sample_frame(comb, req.cfg.max_points,
                                         req.cfg.seed)
    if sampling.get("applied"):
        notes.append(f"drawn from a random sample of {len(sampled):,} of "
                     f"{sampling['params']['requested_rows']:,} rows "
                     "(seed shown on badge).")
    values = sampled[cols].to_numpy(dtype="float64")
    mean = values.mean(axis=0)
    std = values.std(axis=0)
    std[std == 0] = 1.0
    scaled = (values - mean) / std
    dims = [dict(label=c, values=scaled[:, i])
            for i, c in enumerate(cols)]
    line: dict
    if color:
        if pd.api.types.is_numeric_dtype(sampled[color]):
            line = dict(colorscale="Bluered", showscale=True,
                        color=sampled[color].to_numpy(dtype="float64"),
                        cmin=float(pd.to_numeric(sampled[color],
                                                 errors="coerce").min()),
                        cmax=float(pd.to_numeric(sampled[color],
                                                 errors="coerce").max()))
            notes.append(f"line colour encodes the numeric values of "
                         f"{color} (colour bar at right).")
        else:
            codes = sampled[color].astype(str).factorize()[0]
            line = dict(colorscale="Bluered", showscale=False,
                        color=np.asarray(codes, dtype="float64"))
            notes.append(f"line colour encodes order-of-appearance codes "
                         f"of {color} (NOT a quantity) — use hover or a "
                         "bar chart for its exact values.")
    else:
        line = dict(color=ph.palette_color(0))
    fig = go.Figure(go.Parcoords(line=line, dimensions=dims))
    title = "Parallel coordinates: " + ", ".join(cols[:4]) + \
        ("…" if len(cols) > 4 else "")
    data = sampled.copy()
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, notes=notes,
                           disclosure=sampling, height=520),
        chart_id=req.chart_id, title=title, data=data, notes=notes,
        display=_display(len(sampled), sampling, total_rows=len(frame),
                         basis="complete rows over selected columns"),
        roles=dict(req.roles), options=dict(req.options),
    )


def build_pca(req: ChartRequest) -> ChartArtifact:
    frame = _frame(req, "frame", "the dataset preview")
    cols = req.roles.get("columns") or []
    if isinstance(cols, str):
        cols = [cols]
    cols = [c for c in cols if c]
    if not cols:
        cols = [c for c in frame.columns
                if pd.api.types.is_numeric_dtype(frame[c])]
    if len(cols) < 2:
        raise PlatformError(
            "PCA needs at least two numeric columns; select columns "
            "explicitly if this data has several.",
            detail=f"{req.chart_id} pca needs 2 numeric columns",
        )
    if len(cols) > req.cfg.pca_max_columns:
        raise PlatformError(
            f"PCA supports at most {req.cfg.pca_max_columns} columns; "
            f"{len(cols)} selected.",
            detail=f"{req.chart_id} pca too many columns",
        )
    color = req.roles.get("color")
    require_non_empty(frame)
    if color:
        require_columns([color], list(frame.columns), what="PCA scatter")
    extra = (color,) if color and color not in cols else ()
    sampled, scaled, notes, sampling, _ = _numeric_matrix(
        req, frame, cols, max_rows=req.cfg.pca_max_rows, what="PCA",
        extra_cols=extra)
    load = scaled[cols].to_numpy(dtype="float64")
    _, singular, vt = np.linalg.svd(load, full_matrices=False)
    # deterministic sign convention: largest-|loading| entry positive
    for i in range(vt.shape[0]):
        anchor = int(np.argmax(np.abs(vt[i])))
        if vt[i, anchor] < 0:
            vt[i] = -vt[i]
    scores = load @ vt.T
    var = singular ** 2
    total_var = float(var.sum()) or 1.0
    evr = var / total_var
    k = min(2, scores.shape[1])
    pc_cols = [f"PC{i + 1} ({evr[i] * 100:.1f}%)" for i in range(k)]
    plot_df = pd.DataFrame({pc_cols[i]: scores[:, i] for i in range(k)})
    legend = None
    if color and color in sampled.columns:
        plot_df[color] = sampled[color].astype(str).values
        legend = color
    fig = px.scatter(plot_df, x=pc_cols[0],
                     y=pc_cols[1] if k > 1 else pc_cols[0],
                     color=legend, color_discrete_sequence=ph.PALETTE)
    notes.append(
        f"first two principal components explain "
        f"{(evr[:k].sum() * 100):.1f}% of total variance; "
        "close points are similar across the selected columns.")
    title = "PCA scatter plot (first 2 principal components)"
    coords = plot_df.copy()
    for i in range(min(scores.shape[1], len(cols))):
        coords[f"PC{i + 1}"] = scores[:, i]
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title=pc_cols[0],
                           y_title=pc_cols[1] if k > 1 else "",
                           notes=notes, disclosure=sampling,
                           legend_title=legend or "", height=540),
        chart_id=req.chart_id, title=title, data=coords, notes=notes,
        display=_display(len(sampled), sampling, total_rows=len(frame),
                         basis="complete rows; PCA is exact for the "
                               "rows used"),
        roles=dict(req.roles), options=dict(req.options),
        extra_exports={"coordinates.csv": coords},
    )


def build_tsne(req: ChartRequest) -> ChartArtifact:
    try:
        from sklearn.manifold import TSNE
    except ImportError as exc:  # pragma: no cover - environment guard
        raise PlatformError(
            "t-SNE requires scikit-learn, which is not installed in this "
            "environment. Use the PCA chart (numpy-based) instead, or "
            "install scikit-learn to enable t-SNE.",
            detail=f"sklearn missing: {type(exc).__name__}",
        ) from exc
    frame = _frame(req, "frame", "the dataset preview")
    cols = req.roles.get("columns") or []
    if isinstance(cols, str):
        cols = [cols]
    cols = [c for c in cols if c]
    if not cols:
        cols = [c for c in frame.columns
                if pd.api.types.is_numeric_dtype(frame[c])]
    if len(cols) < 2:
        raise PlatformError(
            "t-SNE needs at least two numeric columns.",
            detail=f"{req.chart_id} tsne needs 2 numeric columns",
        )
    if len(cols) > req.cfg.pca_max_columns:
        raise PlatformError(
            f"t-SNE supports at most {req.cfg.pca_max_columns} columns.",
            detail=f"{req.chart_id} tsne too many columns",
        )
    color = req.roles.get("color")
    require_non_empty(frame)
    if color:
        require_columns([color], list(frame.columns), what="t-SNE scatter")
    extra = (color,) if color and color not in cols else ()
    sampled, scaled, notes, sampling, _ = _numeric_matrix(
        req, frame, cols, max_rows=req.cfg.tsne_max_rows, what="t-SNE",
        extra_cols=extra)
    load = scaled[cols].to_numpy(dtype="float64")
    n = len(load)
    perplexity = float(req.cfg.tsne_perplexity)
    clamped = False
    if perplexity >= n:
        perplexity = max(1.0, (n - 1) / 3.0)
        clamped = True
    emb = TSNE(
        n_components=2, random_state=int(req.cfg.seed),
        perplexity=float(perplexity), init="pca", n_jobs=1,
    ).fit_transform(load)
    plot_df = pd.DataFrame({"t-SNE 1": emb[:, 0], "t-SNE 2": emb[:, 1]})
    legend = None
    if color and color in sampled.columns:
        plot_df[color] = sampled[color].astype(str).values
        legend = color
    fig = px.scatter(plot_df, x="t-SNE 1", y="t-SNE 2", color=legend,
                     color_discrete_sequence=ph.PALETTE)
    notes.append(
        "t-SNE preserves LOCAL neighbourhoods only: distances between "
        "clusters and their global arrangement are not meaningful and "
        "differ from PCA. A different seed produces a different layout "
        f"(seed={req.cfg.seed} recorded in the export).")
    notes.append(f"perplexity={perplexity:g}"
                 + (" (clamped to fit the row count)" if clamped else "")
                 + ".")
    title = "t-SNE scatter plot (local structure, not comparable to PCA)"
    coords = plot_df.copy()
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title="t-SNE 1",
                           y_title="t-SNE 2", notes=notes,
                           disclosure=sampling, legend_title=legend or "",
                           height=540),
        chart_id=req.chart_id, title=title, data=coords, notes=notes,
        display=_display(len(sampled), sampling, total_rows=len(frame),
                         basis="complete rows; t-SNE embeds the rows "
                               "shown"),
        roles=dict(req.roles), options=dict(req.options),
        extra_exports={"coordinates.csv": coords},
    )


def build_scatter3d(req: ChartRequest) -> ChartArtifact:
    frame = _frame(req, "frame", "the dataset preview")
    x, y, z = req.roles.get("x"), req.roles.get("y"), req.roles.get("z")
    color = req.roles.get("color")
    require_non_empty(frame)
    cols = [c for c in (x, y, z, color) if c]
    require_columns(cols, list(frame.columns), what="3-D scatter")
    require_kinds(frame, {"x": x, "y": y, "z": z},
                  {"x": ("int", "float"), "y": ("int", "float"),
                   "z": ("int", "float")})
    work = frame[cols].copy()
    for c in (x, y, z):
        work[c] = pd.to_numeric(work[c], errors="coerce")
    work = work.dropna(subset=[x, y, z])
    require_non_empty(work, what="rows with x, y and z")
    sampled, sampling = flt.sample_frame(work, req.cfg.max_points,
                                         req.cfg.seed)
    color_kw = {"color": color} if color else {}
    fig = px.scatter_3d(sampled, x=x, y=y, z=z, **color_kw,
                        color_discrete_sequence=ph.PALETTE)
    notes: list[str] = []
    if sampling.get("applied"):
        notes.append(f"plotted from a random sample of "
                     f"{sampling['params']['limit']:,} of "
                     f"{sampling['params']['requested_rows']:,} rows "
                     "(seed shown on badge).")
    title = f"3-D scatter: {z} vs {y} vs {x}"
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title=str(x),
                           y_title=str(y), notes=notes,
                           disclosure=sampling, legend_title=color or "",
                           height=560),
        chart_id=req.chart_id, title=title, data=sampled, notes=notes,
        display=_display(len(sampled), sampling, total_rows=len(work),
                         basis="rows with all three axes"),
        roles=dict(req.roles), options=dict(req.options),
    )


def build_scatter_matrix(req: ChartRequest) -> ChartArtifact:
    frame = _frame(req, "frame", "the dataset preview")
    cols = _multi_columns(req, "columns", req.cfg.matrix_max_columns,
                          min_cols=3)
    color = req.roles.get("color")
    require_non_empty(frame)
    require_columns(cols, list(frame.columns), what="scatter matrix")
    require_numeric(frame, cols, what="scatter matrix")
    work = frame[cols].apply(pd.to_numeric, errors="coerce")
    if color:
        require_columns([color], list(frame.columns), what="scatter matrix")
        work[color] = frame[color]
    work = work.dropna(subset=cols)
    require_non_empty(work, what="complete numeric rows")
    sampled, sampling = flt.sample_frame(work, req.cfg.max_points,
                                         req.cfg.seed)
    fig = px.scatter_matrix(sampled, dimensions=cols, color=color,
                            color_discrete_sequence=ph.PALETTE)
    notes: list[str] = []
    if sampling.get("applied"):
        notes.append(f"matrix drawn from {len(sampled):,} of "
                     f"{sampling['params']['requested_rows']:,} complete "
                     "rows (seed shown on badge).")
    title = "Scatter matrix of " + ", ".join(cols[:4]) + \
        ("…" if len(cols) > 4 else "")
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, notes=notes,
                           disclosure=sampling, height=640),
        chart_id=req.chart_id, title=title, data=sampled, notes=notes,
        display=_display(len(sampled), sampling, total_rows=len(work),
                         basis="complete rows across selected columns"),
        roles=dict(req.roles), options=dict(req.options),
    )


# ------------------------------------------------------------ hierarchy

def _hierarchy_table(req: ChartRequest, frame: pd.DataFrame) -> tuple[
        pd.DataFrame, str | None]:
    cat = req.roles.get("cat")
    sub = req.roles.get("sub")
    values = req.roles.get("values")
    require_columns([c for c in (cat, sub, values) if c],
                    list(frame.columns), what="hierarchy chart")
    cap = req.cfg.max_bar_categories * 2
    notes: list[str] = []
    if values:
        require_numeric(frame, [values], what="hierarchy value column")
        cols = [c for c in (cat, sub) if c]
        work = frame[cols + [values]].copy()
        work[cols] = work[cols].astype(str)
        grouped = work.groupby(cols, dropna=False)[values].sum() \
            .reset_index(name="value")
        grouped = grouped.sort_values("value", ascending=False,
                                      kind="mergesort") \
            .reset_index(drop=True)
        if len(grouped) > cap:
            raise PlatformError(
                f"Too many hierarchy nodes ({len(grouped):,} > {cap}) for "
                "a single chart. Filter the data or choose a column with "
                "fewer distinct values.",
                detail=f"{req.chart_id} hierarchy too large",
            )
        return grouped, None
    cols = [c for c in (cat, sub) if c]
    work = frame[cols].astype({c: str for c in cols})
    counts = work.groupby(cols, dropna=False).size().reset_index(name="count")
    counts = counts.sort_values("count", ascending=False, kind="mergesort") \
        .reset_index(drop=True)
    if len(counts) > cap:
        counts = counts.head(cap)
        notes.append(f"showing the {cap} largest nodes only — use filters "
                     "to explore the rest (no records were changed).")
    return counts, (notes[0] if notes else None)


def _build_hierarchy(req: ChartRequest, kind: str) -> ChartArtifact:
    frame = _frame(req, "frame", "the dataset preview")
    require_non_empty(frame)
    table, note = _hierarchy_table(req, frame)
    value_col = "value" if "value" in table.columns else "count"
    cat, sub = req.roles.get("cat"), req.roles.get("sub")
    path = [c for c in (cat, sub) if c]
    if kind == "treemap":
        fig = px.treemap(table, path=path, values=value_col,
                         color_discrete_sequence=ph.PALETTE)
    elif kind == "sunburst":
        fig = px.sunburst(table, path=path, values=value_col,
                          color_discrete_sequence=ph.PALETTE)
    else:
        fig = px.icicle(table, path=path, values=value_col,
                        color_discrete_sequence=ph.PALETTE)
    title = f"{kind.replace('_', ' ').capitalize()} of " + " / ".join(path)
    notes = [note] if note else []
    notes.append(f"node sizes are proportional to "
                 f"{'summed ' + (req.roles.get('values') or '') if value_col == 'value' else 'record counts'}; "
                 "hover for exact values.")
    if value_col == "count":
        notes.append("nested shares roll up from child to parent — "
                     "labels show absolute counts.")
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, notes=notes, height=520),
        chart_id=req.chart_id, title=title, data=table, notes=notes,
        display=_display(len(frame),
                         basis=f"{len(frame):,} rows aggregated"),
        roles=dict(req.roles), options=dict(req.options),
    )


def build_treemap(req: ChartRequest) -> ChartArtifact:
    return _build_hierarchy(req, "treemap")


def build_sunburst(req: ChartRequest) -> ChartArtifact:
    return _build_hierarchy(req, "sunburst")


def build_icicle(req: ChartRequest) -> ChartArtifact:
    return _build_hierarchy(req, "icicle")

