"""Charts built from Phase 5 analysis-run outputs (Mode B, section 2).

These builders **never recompute statistics**: they visualize exactly
what ``output/datasets/<dataset_id>/runs/<run_id>/`` contains
(distributions.json, statistics.json, quality_report.json,
correlation.csv), preserving each run's stated basis, exclusions and
limitations as chart notes/disclosures.

Request inputs used: ``req.run`` (the dict returned by
``analysis.analysis_runner.load_run``).
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from core.exceptions import PlatformError
from visualization import plotly_helpers as ph
from visualization.chart_factory import (ChartArtifact, ChartRequest,
                                         _display, _empty_display,
                                         human_count)
from visualization.validation import require_non_empty


def _run(req: ChartRequest) -> dict:
    run = req.run
    if not isinstance(run, dict) or not run.get("files"):
        raise PlatformError(
            "No analysis run is selected. Run the analysis on the "
            "Dataset Analysis page, then pick a run here.",
            detail=f"{req.chart_id} without run",
        )
    return run


def _basis_notes(run: dict) -> list[str]:
    """Basis/notes as recorded by the run (load_run file shape)."""
    notes: list[str] = []
    statistics = run.get("statistics") or {}
    cols = statistics.get("columns")
    if not isinstance(cols, dict):
        cols = statistics
    for note in (cols.get("_notes") or [])[:2]:
        notes.append(str(note))
    quality = run.get("quality") or {}
    basis = statistics.get("basis") or quality.get("basis")
    if basis:
        notes.append(f"run basis: {basis} (as recorded by the run).")
    return notes


def _profile(run: dict) -> dict:
    """Run profile from the load_run shape (summary.profile)."""
    profile = run.get("profile")
    if not isinstance(profile, dict):
        profile = ((run.get("summary") or {}).get("profile") or {})
    return profile if isinstance(profile, dict) else {}


def _empty_component(message: str, title: str) -> ChartArtifact:
    fig = go.Figure()
    fig.add_annotation(text=message, xref="paper", yref="paper",
                       x=0.5, y=0.5, showarrow=False,
                       font=dict(size=14, color="#666666"))
    fig.update_layout(template="plotly_white", height=380,
                      title=dict(text=title, x=0.02, xanchor="left"))
    return ChartArtifact(
        figure=fig, chart_id="", title=title, data=pd.DataFrame(),
        notes=[message], display=_empty_display(),
    )


def run_histogram(req: ChartRequest) -> ChartArtifact:
    run = _run(req)
    column = req.roles.get("column")
    distributions = run.get("distributions") or {}
    histograms = distributions.get("histograms") or {}
    if not column:
        raise PlatformError(
            "Select one of the numeric columns analysed by the run.",
            detail=f"{req.chart_id} missing column",
        )
    payload = histograms.get(column)
    if payload is None:
        available = ", ".join(sorted(histograms)[:8]) or "none"
        raise PlatformError(
            f"'{column}' has no histogram in this run "
            f"(numeric columns in the run: {available}"
            + ("…" if len(histograms) > 8 else "") + ").",
            detail=f"{req.chart_id} column not in run",
        )
    edges = [float(e) for e in (payload.get("edges") or [])]
    counts = [int(c) for c in (payload.get("counts") or [])]
    if len(edges) != len(counts) + 1 or not counts:
        raise PlatformError(
            "The stored histogram for this column is incomplete, so it "
            "cannot be drawn faithfully.",
            detail=f"{req.chart_id} malformed histogram {column}",
        )
    centers = [(edges[i] + edges[i + 1]) / 2 for i in range(len(counts))]
    width = edges[1] - edges[0]
    fig = go.Figure(go.Bar(
        x=centers, y=counts, width=width * 0.94,
        marker_color=ph.palette_color(0),
        hovertemplate="bin centre %{x:,.4g}<br>count %{y:,}<extra></extra>",
    ))
    notes = [
        f"bins and counts are exactly those stored by the run "
        f"({payload.get('bins', len(counts))} bins).",
        f"excluded from this histogram: {payload.get('excluded_missing', 0):,} "
        f"missing, {payload.get('excluded_nonfinite', 0):,} non-finite; "
        f"used {payload.get('used', sum(counts)):,} values.",
    ] + _basis_notes(run)
    title = f"Run distribution of {column}"
    data = pd.DataFrame({"bin_start": edges[:-1], "bin_end": edges[1:],
                         "count": counts})
    display = _display(sum(counts),
                       basis=f"stored histogram · basis "
                             f"{payload.get('basis', 'exact')}")
    display["approximate"] = payload.get("basis") == "sample"
    if display["approximate"]:
        display["sampling"] = {"applied": True, "method": "run_sample",
                               "params": {}, "approximate": True}
    return ChartArtifact(
        figure=ph.finalize(
            fig, title=title, x_title=str(column), y_title="count",
            notes=notes,
            disclosure=display["sampling"] if display["approximate"] else None),
        chart_id=req.chart_id, title=title, data=data, notes=notes,
        display=display, roles=dict(req.roles),
    )


def run_categorical(req: ChartRequest) -> ChartArtifact:
    run = _run(req)
    column = req.roles.get("column")
    distributions = run.get("distributions") or {}
    categorical = distributions.get("categorical") or {}
    if not column:
        raise PlatformError(
            "Select one of the categorical columns analysed by the run.",
            detail=f"{req.chart_id} missing column",
        )
    payload = categorical.get(column)
    if payload is None:
        available = ", ".join(sorted(categorical)[:8]) or "none"
        raise PlatformError(
            f"'{column}' has no categorical summary in this run "
            f"(categorical columns: {available}"
            + ("…" if len(categorical) > 8 else "") + ").",
            detail=f"{req.chart_id} column not in run",
        )
    rows = list(payload.get("top_categories") or [])
    if not rows:
        return _empty_component(
            f"No non-missing values were recorded for '{column}'.",
            f"Run distribution of {column}")
    table = pd.DataFrame(rows)
    table = table.rename(columns={"value": column, "count": "records"})
    if "pct" in table.columns:
        table["pct"] = pd.to_numeric(table["pct"], errors="coerce")
    truncated = bool(payload.get("high_cardinality"))
    fig = go.Figure(go.Bar(
        x=table[column].astype(str), y=table["records"],
        marker_color=ph.palette_color(0),
        hovertemplate="%{x}<br>records %{y:,}<extra></extra>",
    ))
    notes = [
        f"missing: {payload.get('missing', 0):,} "
        f"({payload.get('missing_pct', 0):.1f}%), "
        f"unique: {payload.get('unique', '—')}"
        + (" (approximate — value counts capped)" if truncated else "") + ".",
    ] + _basis_notes(run)
    if truncated:
        notes.append("high-cardinality column: only the top values are "
                     "stored and shown — no records were dropped, the "
                     "rest are simply not enumerated.")
    if payload.get("constant"):
        notes.append("this column is constant (one repeated value).")
    title = f"Run distribution of {column}"
    display = _display(int(table['records'].sum()),
                       basis=f"stored top categories · basis "
                             f"{payload.get('basis', 'exact')}")
    if payload.get("basis") == "sample":
        display["approximate"] = True
        display["sampling"] = {"applied": True, "method": "run_sample",
                               "params": {}, "approximate": True}
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title=str(column),
                           y_title="records", notes=notes,
                           disclosure=display["sampling"]
                           if display["approximate"] else None),
        chart_id=req.chart_id, title=title, data=table, notes=notes,
        display=display, roles=dict(req.roles),
    )


def run_correlation(req: ChartRequest) -> ChartArtifact:
    run = _run(req)
    correlation = run.get("correlation") or {}
    methods = correlation.get("methods") or {}
    method = req.roles.get("method")
    df = run.get("correlation_df")
    if method is None:
        method = next(iter(methods), None)
        if method is None and df is not None and len(df):
            method = str(df["method"].iloc[0]) if "method" in df.columns \
                else None
    if not methods and (df is None or not len(df)):
        return _empty_component(
            "This run did not compute a correlation matrix (disabled, "
            "or fewer than two usable numeric columns).",
            "Run correlation heatmap")
    notes = [f"method: {method}" if method else "method: —"]
    profile = _profile(run)
    matrix_df: pd.DataFrame | None = None
    if df is not None and len(df) and method is not None and "method" in df.columns:
        subset = df[df["method"].astype(str) == str(method)]
        if len(subset):
            matrix_df = subset.pivot(index="column_a", columns="column_b",
                                     values="value")
            matrix_df = matrix_df.apply(pd.to_numeric,
                                        errors="coerce").fillna(0.0)
    if matrix_df is None:
        payload = methods.get(str(method)) or {}
        cols = list(payload.get("columns") or [])
        matrix = payload.get("matrix") or []
        if not cols or len(matrix) != len(cols):
            return _empty_component(
                "The stored correlation matrix for this run is "
                "incomplete.", "Run correlation heatmap")
        matrix_df = pd.DataFrame(matrix, index=cols, columns=cols) \
            .apply(pd.to_numeric, errors="coerce").fillna(0.0)
    fig = go.Figure(go.Heatmap(
        z=matrix_df.to_numpy(),
        x=[str(c) for c in matrix_df.columns],
        y=[str(c) for c in matrix_df.index],
        colorscale="RdBu_r", zmin=-1, zmax=1, text=matrix_df.to_numpy(),
        texttemplate="%{text:.2f}",
        colorbar=dict(title="r"),
        hovertemplate="%{y} × %{x}<br>r = %{z:.3f}<extra></extra>",
    ))
    if correlation:
        notes.append(f"basis {correlation.get('basis', 'exact')}; "
                     f"{correlation.get('rows_total', 0):,} rows total, "
                     f"{correlation.get('rows_complete', 0):,} complete.")
    else:
        notes.append("matrix read from the run's correlation.csv; "
                     f"run basis {profile.get('basis', 'exact')} over "
                     f"{profile.get('rows', 0):,} profile row(s).")
    excluded = correlation.get("excluded_columns") or {}
    if excluded:
        notes.append(f"excluded columns: {len(excluded)} ("
                     + ", ".join(sorted(excluded)[:4])
                     + ("…" if len(excluded) > 4 else "") + ").")
    for note in (correlation.get("notes") or [])[:2]:
        notes.append(str(note))
    notes.extend(_basis_notes(run))
    title = f"Run correlation heatmap ({method})"
    data = matrix_df.reset_index().rename(columns={"index": "column"})
    display = _display(int(matrix_df.shape[0]),
                       basis=f"run matrix · {method}")
    if correlation.get("basis") not in (None, "exact"):
        display["approximate"] = True
        display["sampling"] = {"applied": True, "method": "run_sample",
                               "params": {}, "approximate": True}
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, notes=notes, height=560,
                           disclosure=display["sampling"]
                           if display["approximate"] else None),
        chart_id=req.chart_id, title=title, data=data, notes=notes,
        display=display, roles=dict(req.roles),
    )


def run_missing_values(req: ChartRequest) -> ChartArtifact:
    run = _run(req)
    rows = []
    profile = _profile(run)
    detail = profile.get("column_detail") or {}
    for col, info in detail.items():
        rows.append({
            "column": str(col),
            "missing": int(info.get("missing") or 0),
            "missing_pct": float(info.get("missing_pct") or 0.0),
            "kind": str(info.get("kind") or ""),
        })
    if not rows:
        statistics = run.get("statistics") or {}
        stored = statistics.get("columns")
        if not isinstance(stored, dict):
            stored = statistics
        for col, info in stored.items():
            if col.startswith("_") or not isinstance(info, dict):
                continue
            rows.append({"column": str(col),
                         "missing": int(info.get("missing") or 0),
                         "missing_pct": 0.0, "kind": "numeric"})
        categorical = (run.get("distributions") or {}).get("categorical") or {}
        for col, info in categorical.items():
            rows.append({"column": str(col),
                         "missing": int(info.get("missing") or 0),
                         "missing_pct": float(info.get("missing_pct") or 0.0),
                         "kind": "categorical"})
    if not rows:
        return _empty_component(
            "This run stored no per-column missing-value counts.",
            "Run missing values")
    table = pd.DataFrame(rows).sort_values(
        ["missing", "column"], ascending=[False, True],
        kind="mergesort").reset_index(drop=True)
    fig = go.Figure(go.Bar(
        x=table["column"], y=table["missing"],
        marker_color=ph.APPROX_COLOR,
        customdata=table[["missing_pct", "kind"]].to_numpy(),
        hovertemplate="%{x}<br>missing %{y:,} (%{customdata[0]:.1f}%)"
                      "<br>kind: %{customdata[1]}<extra></extra>",
    ))
    notes = [f"computed by the run over {human_count(profile.get('rows', 0))} "
             "row(s); missing cells are excluded from statistics."] + \
        _basis_notes(run)
    title = "Run: missing values per column"
    display = _display(int(len(table)),
                       basis=f"run profile · basis "
                             f"{profile.get('basis', 'exact')}")
    if profile.get("basis") == "sample":
        display["approximate"] = True
        display["sampling"] = {"applied": True, "method": "run_sample",
                               "params": {}, "approximate": True}
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title="column",
                           y_title="missing rows", notes=notes,
                           disclosure=display["sampling"]
                           if display["approximate"] else None),
        chart_id=req.chart_id, title=title, data=table, notes=notes,
        display=display,
    )


def run_grouped_stats(req: ChartRequest) -> ChartArtifact:
    run = _run(req)
    column = req.roles.get("column")
    grouped = ((run.get("distributions") or {}).get("grouped") or {})
    groups = grouped.get("groups") or {}
    if not groups or not any(isinstance(v, dict) for v in groups.values()):
        raise PlatformError(
            "This run has no grouped statistics (group-by was not "
            "configured, or the group column was missing). Re-run the "
            "analysis with a group-by column to enable this chart.",
            detail=f"{req.chart_id} no grouped stats",
        )
    if not column:
        sample = next((next(iter(v)) for v in groups.values()
                       if isinstance(v, dict) and v), None)
        raise PlatformError(
            "Select a numeric column analysed in this run"
            + (f" (e.g. '{sample}')" if sample else "") + ".",
            detail=f"{req.chart_id} missing column",
        )
    rows = []
    for group_name, payload in groups.items():
        if not isinstance(payload, dict):
            continue
        stats = payload.get(column)
        if not isinstance(stats, dict):
            continue
        mean = stats.get("mean")
        if mean is None:
            continue
        rows.append({
            "group": str(group_name),
            "mean": float(mean),
            "min": float(stats.get("min")) if stats.get("min") is not None
            else float(mean),
            "max": float(stats.get("max")) if stats.get("max") is not None
            else float(mean),
            "count": int(stats.get("count") or 0),
            "median": float(stats["median"])
            if stats.get("median") is not None else float(mean),
        })
    if not rows:
        raise PlatformError(
            f"No stored group statistics for column '{column}'.",
            detail=f"{req.chart_id} column not in grouped stats",
        )
    table = pd.DataFrame(rows).sort_values("mean", ascending=False,
                                           kind="mergesort")
    table = table.reset_index(drop=True)
    if len(table) > 60:
        table = table.head(60)
        note_cap = "only the 60 groups with the highest means are shown."
    else:
        note_cap = ""
    err_plus = (table["max"] - table["mean"]).clip(lower=0).tolist()
    err_minus = (table["mean"] - table["min"]).clip(lower=0).tolist()
    fig = go.Figure(go.Bar(
        x=table["group"], y=table["mean"],
        marker_color=ph.palette_color(0),
        customdata=table[["count", "median"]].to_numpy(),
        error_y=dict(type="data", array=err_plus, arrayminus=err_minus,
                     visible=True, color="#888888"),
        hovertemplate="%{x}<br>mean %{y:,.4g}<br>min/max whiskers show "
                      "the group range<br>rows %{customdata[0]:,}"
                      "<br>median %{customdata[1]:,.4g}<extra></extra>",
    ))
    group_by = grouped.get("group_by", "group")
    notes = [f"groups by '{group_by}'; bar = mean, whiskers = min/max "
             "range; exact values stored by the run.",
             f"basis: {grouped.get('basis', 'exact')}"]
    if note_cap:
        notes.append(note_cap)
    notes.extend(_basis_notes(run))
    title = f"Run: mean {column} by {group_by}"
    display = _display(int(len(table)),
                       basis=f"stored grouped statistics · "
                             f"{grouped.get('basis', 'exact')}")
    if grouped.get("basis") == "sample":
        display["approximate"] = True
        display["sampling"] = {"applied": True, "method": "run_sample",
                               "params": {}, "approximate": True}
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title=str(group_by),
                           y_title=f"mean of {column}", notes=notes,
                           disclosure=display["sampling"]
                           if display["approximate"] else None),
        chart_id=req.chart_id, title=title, data=table, notes=notes,
        display=display, roles=dict(req.roles),
    )


def run_quality(req: ChartRequest) -> ChartArtifact:
    run = _run(req)
    quality = run.get("quality")
    if not isinstance(quality, dict) or "score" not in quality:
        return _empty_component(
            "This run stored no quality report.", "Run quality score")
    score = float(quality.get("score") or 0.0)
    outliers = (quality.get("potential_outliers") or {}).get("columns") or {}
    issues = {
        "duplicate rows": int(quality.get("duplicate_rows") or 0),
        "high-missing columns": len(quality.get("columns_high_missing") or []),
        "constant columns": len(quality.get("constant_columns") or []),
        "duplicate column names": len(quality.get("duplicate_columns") or []),
        "inconsistent types": len(quality.get("inconsistent_types") or []),
        "outlier columns": len(outliers),
        "high-cardinality columns": len(
            quality.get("high_cardinality_columns") or []),
    }
    fig = make_subplots(rows=1, cols=2, column_widths=[0.42, 0.58],
                        specs=[[{"type": "indicator"},
                                {"type": "bar"}]])
    fig.add_trace(go.Indicator(
        mode="gauge+number",
        value=score,
        title={"text": "quality score",
               "font": {"size": 13}},
        number={"suffix": "/100", "font": {"size": 30}},
        gauge={
            "axis": {"range": [0, 100]},
            "bar": {"color": ph.palette_color(0)},
            "steps": [
                {"range": [0, 60], "color": "#f3d0d0"},
                {"range": [60, 85], "color": "#f7e8c8"},
                {"range": [85, 100], "color": "#d7ecd9"},
            ],
        },
    ), row=1, col=1)
    labels = list(issues)
    values = [issues[k] for k in labels]
    fig.add_trace(go.Bar(
        x=values, y=labels, orientation="h",
        marker_color=ph.APPROX_COLOR,
        hovertemplate="%{y}: %{x:,}<extra></extra>",
    ), row=1, col=2)
    notes = ["score formula: " + str(quality.get("score_formula", "")),
             "score is a platform heuristic, not a standard — read the "
             "per-issue counts and warnings."]
    warnings = quality.get("warnings") or []
    for w in warnings[:3]:
        notes.append(f"warning: {w}")
    for lim in (quality.get("score_limitations") or [])[:2]:
        notes.append(str(lim))
    notes.extend(_basis_notes(run))
    title = "Run quality: score and issues"
    fig.update_layout(template="plotly_white", height=460,
                      title=dict(text=title, x=0.02, xanchor="left"),
                      margin=dict(l=60, r=30, t=70, b=70),
                      showlegend=False)
    table = pd.DataFrame({"issue": labels, "count": values})
    display = _display(1, basis=f"run quality report · basis "
                                f"{quality.get('basis', 'exact')}")
    return ChartArtifact(
        figure=fig, chart_id=req.chart_id, title=title, data=table,
        notes=notes, display=display,
    )


def run_outliers(req: ChartRequest) -> ChartArtifact:
    run = _run(req)
    quality = run.get("quality") or {}
    payload = quality.get("potential_outliers") or {}
    columns = payload.get("columns") or {}
    if not columns:
        return _empty_component(
            "No potential outliers were flagged in this run "
            "(detection may also have been disabled).",
            "Run potential outliers")
    rows = [{"column": str(c), "count": int(info.get("count") or 0),
             "pct": float(info.get("pct") or 0.0)}
            for c, info in columns.items() if isinstance(info, dict)]
    table = pd.DataFrame(rows).sort_values("count", ascending=False,
                                           kind="mergesort") \
        .reset_index(drop=True)
    fig = go.Figure(go.Bar(
        x=table["column"], y=table["count"],
        marker_color=ph.palette_color(3),
        customdata=table["pct"].to_numpy(),
        hovertemplate="%{x}<br>%{y:,} flagged (%{customdata:.1f}%)"
                      "<extra></extra>",
    ))
    k = payload.get("k", 1.5)
    notes = [f"flagged with the IQR rule (k={k}) over "
             f"{human_count(quality.get('rows', 0))} rows — for review "
             "only; nothing was removed from the data.",
             f"basis: {payload.get('basis', quality.get('basis', 'exact'))}"]
    notes.extend(_basis_notes(run))
    title = "Run: potential outliers per column"
    display = _display(int(len(table)),
                       basis=f"run quality report · "
                             f"{payload.get('basis', 'exact')}")
    if payload.get("basis") == "sample":
        display["approximate"] = True
        display["sampling"] = {"applied": True, "method": "run_sample",
                               "params": {}, "approximate": True}
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title="column",
                           y_title="flagged rows", notes=notes,
                           disclosure=display["sampling"]
                           if display["approximate"] else None),
        chart_id=req.chart_id, title=title, data=table, notes=notes,
        display=display,
    )
