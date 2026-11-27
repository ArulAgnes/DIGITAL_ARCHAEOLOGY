"""Comparison visualizations (Mode C, section 3).

Charts built strictly from Phase 6 comparison outputs
(``load_comparison`` payloads). Honesty rules enforced here:

* every figure carries the run's ``strategy`` and ``exactness`` as an
  on-canvas badge; ``exactness != "exact"`` forces the approximate
  (red) badge — approximate runs are never presented as exact,
* deltas are (B − A) and labelled as such,
* missing/disabled statistics produce a clear empty-state chart with an
  explanation, never a fabricated chart.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from core.exceptions import PlatformError
from visualization import plotly_helpers as ph
from visualization.chart_factory import (ChartArtifact, ChartRequest,
                                         _display, human_bytes,
                                         human_count)

# counts shown on the figures per comparison kind (full dict is exported)
_CSV_COUNT_ORDER = ("a_rows", "b_rows", "matched", "modified", "added",
                    "removed")
_FS_COUNT_ORDER = ("files_a", "files_b", "unchanged", "modified", "added",
                   "removed", "moved", "renamed")

_COUNT_LABELS = {
    "a_rows": "rows in A", "b_rows": "rows in B",
    "files_a": "files in A", "files_b": "files in B",
    "matched": "matched", "unchanged": "unchanged",
    "modified": "modified", "added": "added", "removed": "removed",
    "moved": "moved", "renamed": "renamed",
}


def _comparison(req: ChartRequest) -> dict:
    comp = req.comparison
    if not isinstance(comp, dict) or not comp.get("comparison_summary"):
        raise PlatformError(
            "No comparison run is selected. Run a comparison on the "
            "Dataset Comparison page, then pick it here.",
            detail=f"{req.chart_id} without comparison",
        )
    return comp


def _summary(req: ChartRequest) -> tuple[dict, dict]:
    comp = _comparison(req)
    summary = comp.get("comparison_summary") or {}
    status = str(summary.get("status") or "")
    if status != "completed":
        raise PlatformError(
            f"This comparison finished with status '{status or 'unknown'}', "
            "so it has no results to chart. Re-run it from the Dataset "
            "Comparison page.",
            detail=f"{req.chart_id} status {status}",
        )
    return summary, comp


def _exactness(summary: dict) -> tuple[str, bool]:
    """(badge text, approximate?) for a comparison summary."""
    exactness = str(summary.get("exactness") or "unknown")
    strategy = str(summary.get("strategy") or "")
    keys = list(summary.get("keys") or [])
    strategy_txt = strategy
    if keys and "key" in strategy:
        shown = ", ".join(str(k) for k in keys[:3])
        if len(keys) > 3:
            shown += "…"
        strategy_txt = f"{strategy} ({shown})"
    badge = f"exactness: {exactness} · strategy: {strategy_txt or '—'}"
    return badge, exactness != "exact"


def _empty(message: str, title: str, summary: dict | None = None
           ) -> ChartArtifact:
    fig = go.Figure()
    fig.add_annotation(text=message, xref="paper", yref="paper",
                       x=0.5, y=0.5, showarrow=False, width=640,
                       font=dict(size=14, color="#666666"))
    notes = [message]
    badge = None
    approx = False
    if summary:
        badge, approx = _exactness(summary)
    fig.update_layout(template="plotly_white", height=400,
                      title=dict(text=title, x=0.02, xanchor="left"))
    if badge:
        fig.add_annotation(
            text=badge, xref="paper", yref="paper", x=0.0, y=-0.16,
            showarrow=False, xanchor="left",
            font=dict(size=11,
                      color=ph.APPROX_COLOR if approx else "#666666"))
    return ChartArtifact(
         figure=fig, chart_id="", title=title,
        data=pd.DataFrame(), notes=notes,
        display=_display(0, basis="comparison outputs", approximate=approx))


def _stats(req: ChartRequest, summary: dict, comp: dict) -> dict:
    stats = comp.get("statistics_differences")
    if not isinstance(stats, dict) or stats.get("status") in (
            "not-run", "disabled") or not stats.get("columns") and \
            not stats.get("storage") and not stats.get("extensions"):
        raise PlatformError(
            "This comparison ran without statistical differences "
            "(statistics were disabled or not available for its kind). "
            "Re-run the comparison with statistics enabled to use this "
            "chart.",
            detail=f"{req.chart_id} statistics unavailable",
        )
    return stats


def comparison_counts(req: ChartRequest) -> ChartArtifact:
    summary, comp = _summary(req)
    counts = dict(summary.get("counts") or {})
    kind = str(summary.get("kind") or "")
    order = _FS_COUNT_ORDER if kind == "filesystem" else _CSV_COUNT_ORDER
    items = [(k, counts[k]) for k in order if k in counts]
    other = {k: v for k, v in counts.items() if k not in order}
    if not items:
        items = sorted(counts.items())
    if not items:
        raise PlatformError(
            "This comparison stored no record counts.",
            detail=f"{req.chart_id} empty counts",
        )
    table = pd.DataFrame(
        [{"count": _COUNT_LABELS.get(k, k.replace("_", " ")),
          "value": int(v)} for k, v in items])
    badge, approx = _exactness(summary)
    fig = go.Figure(go.Bar(
        x=table["value"], y=table["count"], orientation="h",
        text=table["value"].map(lambda v: f"{v:,}"),
        textposition="outside",
        marker_color=ph.palette_color(0),
        hovertemplate="%{y}: %{x:,}<extra></extra>",
    ))
    title = "Record counts: A vs B"
    notes = [f"kind: {kind}; counts are exact totals from the "
             "comparison run" + (
                 " (every count in the run's summary is exported)."
                 if other else ".")]
    truncated = dict(summary.get("truncated") or {})
    if truncated:
        notes.append("some record exports were truncated — counts remain "
                     "exact (listed in warnings).")
    names = summary.get("datasets") or {}
    a_name = str((names.get("a") or {}).get("name") or "A")
    b_name = str((names.get("b") or {}).get("name") or "B")
    notes.append(f"A = {a_name}, B = {b_name}; deltas elsewhere are "
                 "B − A.")
    data = table.copy()
    if other:
        data = pd.concat([data, pd.DataFrame(
            [{"count": k.replace("_", " "), "value": int(v)}
             for k, v in other.items()])], ignore_index=True)
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title="records",
                           y_title="", notes=notes,
                           exactness_note=badge, approximate=approx,
                           height=440),
        chart_id=req.chart_id, title=title, data=data, notes=notes,
        display=_display(len(table),
                         basis="comparison summary counts", approximate=approx),
        options={"exactness": summary.get("exactness")},
    )


def comparison_matched(req: ChartRequest) -> ChartArtifact:
    summary, comp = _summary(req)
    counts = dict(summary.get("counts") or {})
    kind = str(summary.get("kind") or "")
    if kind == "filesystem":
        parts = [("unchanged", counts.get("unchanged", 0)),
                 ("modified", counts.get("modified", 0)),
                 ("added", counts.get("added", 0)),
                 ("removed", counts.get("removed", 0))]
    else:
        parts = [("matched", counts.get("matched", 0)),
                 ("modified", counts.get("modified", 0)),
                 ("added", counts.get("added", 0)),
                 ("removed", counts.get("removed", 0))]
    parts = [(k, int(v)) for k, v in parts]
    if sum(v for _, v in parts) == 0:
        raise PlatformError(
            "This comparison has zero records to show in the match "
            "composition.",
            detail=f"{req.chart_id} zero counts",
        )
    table = pd.DataFrame(parts, columns=["outcome", "records"])
    badge, approx = _exactness(summary)
    fig = go.Figure(go.Pie(
        labels=table["outcome"], values=table["records"],
        hole=0.45, textinfo="label+percent+value",
        marker=dict(colors=[ph.palette_color(i)
                            for i in range(len(table))]),
        hovertemplate="%{label}: %{value:,} (%{percent})<extra></extra>",
    ))
    title = "Match composition (donut)"
    notes = ["slice values are exact record counts from the run.",
             "modified = matched key, changed values."]
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, notes=notes,
                           exactness_note=badge, approximate=approx,
                           height=470),
        chart_id=req.chart_id, title=title, data=table, notes=notes,
        display=_display(len(table),
                         basis="comparison summary counts", approximate=approx),
        options={"exactness": summary.get("exactness")},
    )


def _numeric_columns(stats: dict) -> list[str]:
    return [c for c, info in (stats.get("columns") or {}).items()
            if isinstance(info, dict)
            and info.get("basis") == "numeric"
            and isinstance(info.get("delta"), dict)]


def comparison_numeric_delta(req: ChartRequest) -> ChartArtifact:
    summary, comp = _summary(req)
    stats = _stats(req, summary, comp)
    badge, approx = _exactness(summary)
    columns = _numeric_columns(stats)
    chosen = req.roles.get("column")
    if chosen:
        if chosen not in columns:
            raise PlatformError(
                f"'{chosen}' has no numeric statistics in this "
                "comparison (pick a numeric column analysed on both "
                "sides).",
                detail=f"{req.chart_id} column not numeric {chosen}",
            )
        columns = [chosen]
    if not columns:
        return _empty(
            "No numeric columns were analysed on both sides in this "
            "comparison.", "Numerical statistics changes", summary)
    if chosen:
        info = stats["columns"][chosen]
        delta = info.get("delta") or {}
        rows = [{"field": k, "delta_b_minus_a": v}
                for k, v in delta.items()
                if v is not None and not isinstance(v, bool)]
        table = pd.DataFrame(rows)
        if not len(table):
            return _empty(
                f"No comparable numeric fields for '{chosen}'.",
                "Numerical statistics changes", summary)
        fig = go.Figure(go.Bar(
            x=table["field"], y=table["delta_b_minus_a"],
            text=table["delta_b_minus_a"].map(lambda v: f"{v:+,.4g}"),
            textposition="outside",
            marker_color=[ph.palette_color(0) if v >= 0
                          else ph.palette_color(2)
                          for v in table["delta_b_minus_a"]],
            hovertemplate="%{x}: %{y:+,.6g} (B − A)<extra></extra>",
        ))
        title = f"Δ statistics for {chosen} (B − A)"
        notes = ["positive = higher in dataset B; exact per-field "
                 "deltas from the run."]
    else:
        rows = []
        for c in columns:
            delta = stats["columns"][c].get("delta") or {}
            value = delta.get("mean")
            if value is None:
                value = delta.get("count")
            if value is None or isinstance(value, bool):
                continue
            rows.append({"column": c, "delta_b_minus_a": float(value)})
        table = pd.DataFrame(rows)
        if not len(table):
            return _empty(
                "Stored numeric deltas are unavailable for this "
                "comparison.", "Numerical statistics changes", summary)
        table["abs"] = table["delta_b_minus_a"].abs()
        table = table.sort_values(["abs", "column"],
                                  ascending=[False, True],
                                  kind="mergesort") \
            .reset_index(drop=True)
        truncated = False
        if len(table) > req.cfg.top_n:
            table = table.head(req.cfg.top_n).reset_index(drop=True)
            truncated = True
        fig = go.Figure(go.Bar(
            x=table["delta_b_minus_a"], y=table["column"], orientation="h",
            marker_color=[ph.palette_color(0) if v >= 0
                          else ph.palette_color(2)
                          for v in table["delta_b_minus_a"]],
            hovertemplate="%{y}: %{x:+,.6g} (B − A)<extra></extra>",
        ))
        title = "Δ mean per numeric column (B − A, top changes)"
        notes = ["positive = higher in dataset B; columns sorted by "
                 "absolute change."]
        if truncated:
            notes.append(f"showing the top {req.cfg.top_n} of "
                         f"{len(columns):,} numeric columns.")
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title="delta (B − A)"
                           if not chosen else "field",
                           y_title="" if not chosen else "delta (B − A)",
                           notes=notes, exactness_note=badge,
                           approximate=approx, height=460),
        chart_id=req.chart_id, title=title,
        data=table.drop(columns=["abs"], errors="ignore"),
        notes=notes,
        display=_display(len(table),
                         basis="stored statistics differences", approximate=approx),
        roles=dict(req.roles),
        options={"exactness": summary.get("exactness")},
    )


def comparison_category_change(req: ChartRequest) -> ChartArtifact:
    summary, comp = _summary(req)
    stats = _stats(req, summary, comp)
    badge, approx = _exactness(summary)
    cat_cols = [c for c, info in (stats.get("columns") or {}).items()
                if isinstance(info, dict)
                and info.get("basis") == "categorical"]
    chosen = req.roles.get("column")
    if chosen and chosen not in cat_cols:
        raise PlatformError(
            f"'{chosen}' was not compared as a categorical column in "
            "this run (pick one of the analysed categorical columns).",
            detail=f"{req.chart_id} column not categorical {chosen}",
        )
    column = chosen or (cat_cols[0] if cat_cols else None)
    if column is None:
        return _empty(
            "No categorical columns were compared in this run.",
            "Categorical value changes", summary)
    info = stats["columns"][column]
    rows = list(info.get("top_values") or [])
    if not rows:
        return _empty(
            f"No stored value counts for '{column}'.",
            "Categorical value changes", summary)
    table = pd.DataFrame(rows)
    table = table.rename(columns={"value": column, "a": "A",
                                  "b": "B"})
    for col in ("A", "B"):
        table[col] = pd.to_numeric(table[col], errors="coerce") \
            .fillna(0).astype(int)
    table["delta_b_minus_a"] = table["B"] - table["A"]
    truncated = bool(info.get("values_truncated"))
    fig = go.Figure()
    fig.add_trace(go.Bar(x=table[column].astype(str), y=table["A"],
                         name="dataset A",
                         marker_color=ph.palette_color(0),
                         hovertemplate="%{x}: A %{y:,}<extra></extra>"))
    fig.add_trace(go.Bar(x=table[column].astype(str), y=table["B"],
                         name="dataset B",
                         marker_color=ph.palette_color(1),
                         hovertemplate="%{x}: B %{y:,}<extra></extra>"))
    title = f"Value counts A vs B: {column}"
    notes = ["bars are exact stored counts per value; "
             "deltas are B − A."]
    if truncated:
        notes.append(f"value list capped at {info.get('top_k', '?')} "
                     "distinct values by the comparison — counts shown "
                     "are exact for those values; others are not "
                     "enumerated.")
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title=str(column),
                           y_title="records", notes=notes,
                           barmode="group", exactness_note=badge,
                           approximate=approx, legend_title="dataset",
                           height=470),
        chart_id=req.chart_id, title=title, data=table, notes=notes,
        display=_display(len(table),
                         basis="stored statistics differences", approximate=approx),
        roles=dict(req.roles),
        options={"exactness": summary.get("exactness")},
    )


def comparison_missing_delta(req: ChartRequest) -> ChartArtifact:
    summary, comp = _summary(req)
    stats = _stats(req, summary, comp)
    badge, approx = _exactness(summary)
    rows = []
    for c, info in (stats.get("columns") or {}).items():
        if not isinstance(info, dict):
            continue
        delta = info.get("delta") or {}
        value = delta.get("missing")
        if value is None or isinstance(value, bool):
            continue
        rows.append({"column": c, "delta_b_minus_a": float(value)})
    if not rows:
        return _empty(
            "No comparable missing-value statistics were stored for "
            "this comparison.", "Missing-value changes", summary)
    table = pd.DataFrame(rows)
    table["abs"] = table["delta_b_minus_a"].abs()
    table = table.sort_values(["abs", "column"],
                              ascending=[False, True],
                              kind="mergesort").reset_index(drop=True)
    shown_all = len(table) <= req.cfg.top_n
    if not shown_all:
        table = table.head(req.cfg.top_n).reset_index(drop=True)
    table = table[table["delta_b_minus_a"] != 0].reset_index(drop=True)
    if not len(table):
        fig = go.Figure()
        fig.add_annotation(
            text="Missing-value counts are identical in A and B for "
                 "every analysed column.",
            xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False,
            font=dict(size=14, color="#666666"))
        fig.update_layout(template="plotly_white", height=380,
                          title=dict(text="Missing-value changes (B − A)",
                                     x=0.02, xanchor="left"))
        notes = ["no missing-count changes across analysed columns."]
        return ChartArtifact(
            figure=ph.finalize(fig, title="Missing-value changes (B − A)",
                               notes=notes, exactness_note=badge,
                               approximate=approx),
            chart_id=req.chart_id,
            title="Missing-value changes (B − A)",
            data=pd.DataFrame(columns=["column", "delta_b_minus_a"]),
            notes=notes,
            display=_display(0,
                             basis="stored statistics differences", approximate=approx))
    fig = go.Figure(go.Bar(
        x=table["delta_b_minus_a"], y=table["column"], orientation="h",
        marker_color=[ph.palette_color(0) if v >= 0
                      else ph.palette_color(2)
                      for v in table["delta_b_minus_a"]],
        hovertemplate="%{y}: %{x:+g} missing cells (B − A)"
                      "<extra></extra>",
    ))
    title = "Missing-value changes per column (B − A)"
    notes = ["positive = more missing cells in dataset B; zero-change "
             "columns are omitted from the chart."]
    if not shown_all:
        notes.append(f"largest {req.cfg.top_n} absolute changes shown "
                     f"of {len(rows):,} analysed columns.")
    return ChartArtifact(
        figure=ph.finalize(fig, title=title,
                           x_title="delta missing cells (B − A)",
                           y_title="", notes=notes,
                           exactness_note=badge, approximate=approx,
                           height=max(380, 34 * len(table) + 160)),
        chart_id=req.chart_id, title=title,
        data=table.drop(columns=["abs"], errors="ignore"),
        notes=notes,
        display=_display(len(table),
                         basis="stored statistics differences", approximate=approx),
        options={"exactness": summary.get("exactness")},
    )


def comparison_schema(req: ChartRequest) -> ChartArtifact:
    summary, comp = _summary(req)
    badge, approx = _exactness(summary)
    schema = comp.get("schema_differences") or {}
    if not schema or schema.get("status") in ("not-run",):
        return _empty(
            "Schema differences were not computed for this comparison.",
            "Schema differences", summary)
    kind = str(schema.get("kind") or summary.get("kind") or "")
    rows: list[dict] = []
    if kind == "filesystem":
        files = schema.get("files") or {}
        rows.append({"aspect": "fields only in A",
                     "count": len(files.get("a_only_columns") or [])})
        rows.append({"aspect": "fields only in B",
                     "count": len(files.get("b_only_columns") or [])})
        folders = schema.get("folders") or {}
        if folders.get("status") == "ok":
            rows.append({"aspect": "folders only in A",
                         "count": int(folders.get("a_only_count") or 0)})
            rows.append({"aspect": "folders only in B",
                         "count": int(folders.get("b_only_count") or 0)})
    else:
        columns = schema.get("columns") or {}
        rows.append({"aspect": "columns added in B",
                     "count": len(columns.get("added") or [])})
        rows.append({"aspect": "columns removed in B",
                     "count": len(columns.get("removed") or [])})
        rows.append({"aspect": "dtype changes",
                     "count": len(columns.get("dtype_changes") or [])})
    table = pd.DataFrame(rows)
    nonzero = table[table["count"] > 0]
    if not len(nonzero):
        fig = go.Figure()
        fig.add_annotation(text="No schema differences: both sides have "
                                "the same columns/fields and folder set.",
                           xref="paper", yref="paper", x=0.5, y=0.5,
                           showarrow=False,
                           font=dict(size=14, color="#666666"))
        fig.update_layout(template="plotly_white", height=380,
                          title=dict(text="Schema differences",
                                     x=0.02, xanchor="left"))
        notes = ["schema comparison found no differences."]
        if kind == "filesystem":
            notes.append(str(schema.get("note") or ""))
        return ChartArtifact(
            figure=ph.finalize(fig, title="Schema differences",
                               notes=[n for n in notes if n],
                               exactness_note=badge, approximate=approx),
            chart_id=req.chart_id, title="Schema differences",
            data=table, notes=[n for n in notes if n],
            display=_display(len(table),
                             basis="schema differences output", approximate=approx))
    fig = go.Figure(go.Bar(
        x=nonzero["aspect"], y=nonzero["count"],
        text=nonzero["count"].map(lambda v: f"{v:,}"),
        textposition="outside",
        marker_color=ph.palette_color(0),
        hovertemplate="%{x}: %{y:,}<extra></extra>",
    ))
    title = "Schema differences"
    notes = []
    if kind == "filesystem":
        notes.append(str(schema.get("note") or
                         "from registered scan outputs only — folders "
                         "are never re-walked."))
        if schema.get("folders", {}).get("status") == "skipped":
            notes.append(str(schema.get("folders", {}).get("note") or ""))
    else:
        row_counts = schema.get("row_counts") or {}
        notes.append(f"rows: A={human_count(row_counts.get('a'))}, "
                     f"B={human_count(row_counts.get('b'))}.")
        notes.append(str(schema.get("note") or
                         "column types observed on the first chunk of "
                         "each file."))
    for dtype_change in ((schema.get("columns") or {})
                         .get("dtype_changes") or [])[:4]:
        notes.append(f"dtype: {dtype_change.get('column')} "
                     f"{dtype_change.get('a_dtype')} → "
                     f"{dtype_change.get('b_dtype')}.")
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title="",
                           y_title="count", notes=notes,
                           exactness_note=badge, approximate=approx,
                           height=440),
        chart_id=req.chart_id, title=title, data=table, notes=notes,
        display=_display(len(table),
                         basis="schema differences output", approximate=approx),
        options={"exactness": summary.get("exactness")},
    )


def comparison_storage(req: ChartRequest) -> ChartArtifact:
    summary, comp = _summary(req)
    stats = _stats(req, summary, comp)
    badge, approx = _exactness(summary)
    storage = stats.get("storage")
    if not isinstance(storage, dict):
        return _empty(
            "Storage statistics are only available for filesystem "
            "comparisons.", "Storage A vs B", summary)
    a = int(storage.get("a_total_bytes") or 0)
    b = int(storage.get("b_total_bytes") or 0)
    delta = int(storage.get("delta_bytes") or (b - a))
    table = pd.DataFrame({
        "side": ["dataset A", "dataset B"],
        "bytes": [a, b],
        "display": [human_bytes(a), human_bytes(b)],
    })
    fig = go.Figure(go.Bar(
        x=table["side"], y=table["bytes"], text=table["display"],
        textposition="outside",
        marker_color=[ph.palette_color(0), ph.palette_color(1)],
        hovertemplate="%{x}: %{y:,.0f} B<extra></extra>",
    ))
    title = "Storage A vs B"
    notes = [f"delta: {human_bytes(delta)} ({delta:+,} bytes, B − A) — "
             "exact sums over both scan outputs.",
             f"files: {human_count(storage.get('a_files'))} → "
             f"{human_count(storage.get('b_files'))}."]
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title="",
                           y_title="bytes", notes=notes,
                           exactness_note=badge, approximate=approx,
                           height=440),
        chart_id=req.chart_id, title=title, data=table, notes=notes,
        display=_display(2, basis="stored storage statistics", approximate=approx),
        options={"exactness": summary.get("exactness")},
    )


def comparison_extension(req: ChartRequest) -> ChartArtifact:
    summary, comp = _summary(req)
    stats = _stats(req, summary, comp)
    badge, approx = _exactness(summary)
    extensions = stats.get("extensions") or {}
    rows = list(extensions.get("top") or [])
    if not rows:
        return _empty(
            "No extension statistics were stored for this comparison.",
            "Extension changes A vs B", summary)
    table = pd.DataFrame(rows)
    table = table.rename(columns={"extension": "extension"})
    for col in ("a", "b", "delta"):
        table[col] = pd.to_numeric(table[col], errors="coerce") \
            .fillna(0).astype(int)
    table = table.head(25).reset_index(drop=True)
    fig = go.Figure()
    fig.add_trace(go.Bar(x=table["extension"], y=table["a"],
                         name="dataset A",
                         marker_color=ph.palette_color(0),
                         hovertemplate="%{x}: A %{y:,}<extra></extra>"))
    fig.add_trace(go.Bar(x=table["extension"], y=table["b"],
                         name="dataset B",
                         marker_color=ph.palette_color(1),
                         hovertemplate="%{x}: B %{y:,}<extra></extra>"))
    title = "Extension counts A vs B"
    notes = ["extensions sorted by absolute change (exact sums); "
             f"showing {len(table)} of {extensions.get('shown', len(table))}"
             " stored."]
    if extensions.get("truncated"):
        notes.append("extension tracking was capped by the comparison — "
                     "counts may be incomplete (recorded in warnings).")
    for note in (stats.get("notes") or [])[:2]:
        notes.append(str(note))
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title="extension",
                           y_title="files", notes=notes,
                           barmode="group", exactness_note=badge,
                           approximate=approx, legend_title="dataset",
                           height=470),
        chart_id=req.chart_id, title=title, data=table, notes=notes,
        display=_display(len(table),
                         basis="stored extension statistics", approximate=approx),
        options={"exactness": summary.get("exactness")},
    )


def comparison_naming(req: ChartRequest) -> ChartArtifact:
    summary, comp = _summary(req)
    stats = _stats(req, summary, comp)
    badge, approx = _exactness(summary)
    naming = stats.get("naming_patterns") or {}
    case = stats.get("path_case_changes") or {}
    counts = dict(summary.get("counts") or {})
    rows = [
        {"change": "naming-pattern changes",
         "count": int(naming.get("changed") or 0)},
        {"change": "path-case changes",
         "count": int(case.get("count") or 0)},
        {"change": "moved files",
         "count": int(counts.get("moved") or 0)},
        {"change": "renamed files",
         "count": int(counts.get("renamed") or 0)},
    ]
    table = pd.DataFrame(rows)
    fig = go.Figure(go.Bar(
        x=table["count"], y=table["change"], orientation="h",
        text=table["count"].map(lambda v: f"{v:,}"),
        textposition="outside",
        marker_color=ph.palette_color(3),
        hovertemplate="%{y}: %{x:,}<extra></extra>",
    ))
    compared = int(naming.get("compared_pairs") or 0)
    title = "Naming & path changes"
    notes = [f"compared pairs: {human_count(compared)}; counts are exact "
             "from the comparison run.",
             "samples of changed names are stored in "
             "statistics_differences.json (not plotted to avoid "
             "overcrowding)."]
    for note in (stats.get("notes") or [])[:2]:
        notes.append(str(note))
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title="changes",
                           y_title="", notes=notes,
                           exactness_note=badge, approximate=approx,
                           height=430),
        chart_id=req.chart_id, title=title, data=table, notes=notes,
        display=_display(len(table),
                         basis="stored naming statistics", approximate=approx),
        options={"exactness": summary.get("exactness")},
    )
