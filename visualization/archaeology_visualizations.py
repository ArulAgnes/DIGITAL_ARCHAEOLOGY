"""Archaeology visualizations (Mode A, section 1).

Charts built from scan/snapshot outputs — files, folders, snapshot
index entries and snapshot-diff results. Request inputs:

* ``req.files`` / ``req.folders`` — DataFrames from the snapshot scan
  output (already sidebar-filtered by the dashboard),
* ``req.snapshots`` — index entries from
  ``scanner.snapshot_manager.list_snapshots``,
* ``req.snapshot_diff`` — the dict returned by
  ``scanner.snapshot_manager.compare``.

Path handling: hierarchy labels are always rendered **relative to the
common root of the scan** so exports never carry personal absolute
paths; exact totals stay exact (no sampling on scan aggregations,
guarded by ``cfg.aggregation_row_limit``).
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from core.exceptions import PlatformError
from visualization import filters as flt
from visualization import plotly_helpers as ph
from visualization.chart_factory import (ChartArtifact, ChartRequest,
                                         _display, human_bytes,
                                         human_count)
from visualization.validation import require_non_empty

_DRIVE_RE = re.compile(r"^[A-Za-z]:")

_TRUTHY = {True, 1, "1", "true", "True", "TRUE", "yes", "Yes", "YES",
           "y", "Y", "t", "T"}


def _files(req: ChartRequest) -> pd.DataFrame:
    frame = req.files
    if frame is None or not isinstance(frame, pd.DataFrame):
        raise PlatformError(
            "File records are not available for the current selection. "
            "Refresh the scan or pick another snapshot.",
            detail=f"{req.chart_id} missing files frame",
        )
    if not len(frame):
        raise PlatformError(
            "No file records match the current filters.",
            detail=f"{req.chart_id} empty files frame",
        )
    if len(frame) > req.cfg.aggregation_row_limit:
        raise PlatformError(
            f"Too many rows to aggregate safely ({len(frame):,} > "
            f"{req.cfg.aggregation_row_limit:,}). Narrow the filters.",
            detail=f"{req.chart_id} rows over aggregation limit",
        )
    return frame


def _folders(req: ChartRequest) -> pd.DataFrame:
    frame = req.folders
    if frame is None or not isinstance(frame, pd.DataFrame):
        raise PlatformError(
            "Folder records are not available for the current "
            "selection.",
            detail=f"{req.chart_id} missing folders frame",
        )
    if not len(frame):
        raise PlatformError(
            "No folder records match the current filters.",
            detail=f"{req.chart_id} empty folders frame",
        )
    return frame


def _first_col(frame: pd.DataFrame, candidates: list[str],
               what: str) -> str:
    for name in candidates:
        if name in frame.columns:
            return name
    raise PlatformError(
        f"This chart needs a {what} column, which the scan output does "
        "not contain.",
        detail=f"missing column for {what}",
    )


def _timeline(
    req: ChartRequest, column_candidates: list[str], what: str
) -> tuple[pd.DataFrame, str, list[str]]:
    frame = _files(req)
    column = _first_col(frame, column_candidates, what)
    if pd.api.types.is_datetime64_any_dtype(frame[column]):
        parsed = frame[column]
        dropped = int(parsed.isna().sum())
    else:
        parsed = flt.parse_dates(frame[column])
        dropped = int(parsed.isna().sum() - frame[column].isna().sum())
    valid = parsed.notna()
    span = (parsed.max() - parsed.min()) if valid.any() else None
    if span is None or span.total_seconds() < 0:
        freq, freq_label = "D", "day"
    elif span.days > 3 * 365:
        freq, freq_label = "Y", "year"
    elif span.days > 120:
        freq, freq_label = "M", "month"
    else:
        freq, freq_label = "D", "day"
    series = parsed[valid]
    buckets = series.dt.to_period(freq).dt.start_time
    notes: list[str] = []
    if dropped > 0:
        notes.append(f"{dropped:,} row(s) with missing/unparseable "
                     f"{column} excluded.")
    return buckets.to_frame(name="bucket"), freq_label, notes


def _timeline_chart(req: ChartRequest, *, column_candidates: list[str],
                    what: str) -> ChartArtifact:
    frame = _files(req)
    buckets, freq_label, notes = _timeline(req, column_candidates, what)
    group = req.roles.get("group")
    work = buckets.copy()
    if group and group in frame.columns:
        work[group] = frame.loc[buckets.index, group].astype(str)
        table = work.groupby(["bucket", group], dropna=False).size() \
            .reset_index(name="records")
        fig = px.bar(table, x="bucket", y="records", color=group,
                     color_discrete_sequence=ph.PALETTE)
        fig.update_layout(barmode="stack")
        title = f"Files per {freq_label} by {group}"
    else:
        table = work.groupby("bucket", dropna=False).size() \
            .reset_index(name="records")
        fig = px.bar(table, x="bucket", y="records",
                     color_discrete_sequence=ph.PALETTE)
        title = f"Files per {freq_label}"
    notes.append(f"bucketed by {freq_label}; exact counts over the "
                 f"{human_count(len(buckets))} displayed record(s).")
    table = table.sort_values("bucket").reset_index(drop=True)
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title=what,
                           y_title="records", notes=notes, height=440),
        chart_id=req.chart_id, title=title, data=table, notes=notes,
        display=_display(len(buckets),
                         basis=f"{len(buckets):,} scan rows aggregated"),
        roles=dict(req.roles),
    )


def creation_timeline(req: ChartRequest) -> ChartArtifact:
    return _timeline_chart(
        req, column_candidates=["creation_timestamp", "creation_date"],
        what="creation date")


def modification_timeline(req: ChartRequest) -> ChartArtifact:
    return _timeline_chart(
        req, column_candidates=["modified_timestamp", "modified_date",
                                "accessed_date"],
        what="modification date")


def type_distribution(req: ChartRequest) -> ChartArtifact:
    frame = _files(req)
    column = _first_col(frame, ["file_extension", "file_type"],
                        "file extension")
    counts = (frame[column].astype(str).str.lower()
              .replace({"nan": "(no extension)", "": "(no extension)"})
              .value_counts())
    table = counts.rename_axis(column).reset_index(name="files")
    note = None
    if len(table) > req.cfg.max_bar_categories:
        head = table.head(req.cfg.max_bar_categories)
        rest = table.iloc[req.cfg.max_bar_categories:]
        table = pd.concat([head, pd.DataFrame(
            [{column: "Other (rest)", "files": int(rest["files"].sum())}])],
            ignore_index=True)
        note = (f"{len(rest) + req.cfg.max_bar_categories:,} distinct "
                "extensions grouped into 'Other (rest)' — counts are "
                "exact, no files discarded.")
    fig = px.bar(table, x=column, y="files",
                 color_discrete_sequence=ph.PALETTE)
    notes = [note] if note else []
    notes.append(f"exact counts over {human_count(len(frame))} "
                 "file record(s).")
    title = "File-type distribution"
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title=column,
                           y_title="files", notes=notes, height=440),
        chart_id=req.chart_id, title=title, data=table, notes=notes,
        display=_display(len(frame),
                         basis=f"{len(frame):,} scan rows aggregated"),
        roles=dict(req.roles),
    )


def storage_by_category(req: ChartRequest) -> ChartArtifact:
    frame = _files(req)
    column = _first_col(frame, ["file_category", "file_extension",
                                "root_folder"], "category")
    split = req.roles.get("split")
    size_col = _first_col(frame, ["file_size_bytes"], "size")
    work = frame[[column, size_col]].copy()
    work[column] = work[column].astype(str)
    work[size_col] = pd.to_numeric(work[size_col], errors="coerce") \
        .fillna(0.0)
    if split and split in frame.columns and split != column:
        work[split] = frame[split].astype(str)
        table = work.groupby([column, split], dropna=False)[size_col] \
            .sum().reset_index(name="bytes")
        table = table.sort_values("bytes", ascending=False,
                                  kind="mergesort") \
            .reset_index(drop=True)
        if len(table) > req.cfg.max_bar_categories * 6:
            table = table.head(req.cfg.max_bar_categories * 6)
            cap_note = ("top category/split combinations by storage only — "
                        "narrow the filters for the rest.")
        else:
            cap_note = ""
        fig = px.bar(table, x=column, y="bytes", color=split,
                     barmode="stack", color_discrete_sequence=ph.PALETTE)
        title = f"Storage by {column} and {split}"
    else:
        table = work.groupby(column, dropna=False)[size_col].sum() \
            .reset_index(name="bytes") \
            .sort_values("bytes", ascending=False, kind="mergesort") \
            .reset_index(drop=True)
        cap_note = ""
        if len(table) > req.cfg.max_bar_categories:
            head = table.head(req.cfg.max_bar_categories)
            rest = table.iloc[req.cfg.max_bar_categories:]
            table = pd.concat([head, pd.DataFrame(
                [{column: "Other (rest)",
                  "bytes": float(rest["bytes"].sum())}])],
                ignore_index=True)
            cap_note = (f"{len(rest) + req.cfg.max_bar_categories:,} "
                        "categories grouped into 'Other (rest)' — the "
                        "total stays exact.")
        fig = px.bar(table, x=column, y="bytes",
                     color_discrete_sequence=ph.PALETTE)
        title = f"Storage by {column}"
    table["display_size"] = table["bytes"].map(human_bytes)
    notes = [f"exact total: {human_bytes(table['bytes'].sum())} over "
             f"{human_count(len(frame))} file record(s)."]
    if cap_note:
        notes.append(cap_note)
    fig.update_traces(hovertemplate=f"{column}=%{{x}}<br>"
                                    f"size %{{y:,.0f}} B<extra></extra>")
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title=column,
                           y_title="bytes", notes=notes,
                           legend_title=split or "", height=460),
        chart_id=req.chart_id, title=title, data=table, notes=notes,
        display=_display(len(frame),
                         basis=f"{len(frame):,} scan rows aggregated"),
        roles=dict(req.roles),
    )


def size_distribution(req: ChartRequest) -> ChartArtifact:
    frame = _files(req)
    size_col = _first_col(frame, ["file_size_bytes"], "size")
    log_x = bool(req.options.get("log_x", True))
    sizes = pd.to_numeric(frame[size_col], errors="coerce").dropna()
    require_non_empty(sizes.rename(size_col).to_frame(),
                      what="file sizes")
    notes: list[str] = []
    if log_x:
        positive = sizes[sizes > 0]
        dropped = int(len(sizes) - len(positive))
        if not len(positive):
            raise PlatformError(
                "All file sizes are zero — a log scale cannot be used. "
                "Turn the log option off.",
                detail=f"{req.chart_id} all sizes zero",
            )
        if dropped:
            notes.append(f"{dropped:,} zero-size file(s) excluded from "
                         "the log-scale histogram.")
        log_vals = np.log10(positive.to_numpy(dtype="float64"))
        counts, edges = np.histogram(log_vals,
                                     bins=req.cfg.histogram_bins)
        edges = [10.0 ** e for e in edges]
        centers = [(edges[i] + edges[i + 1]) / 2 for i in range(len(counts))]
        widths = [max(edges[i + 1] - edges[i], 0.0) for i in
                  range(len(counts))]
        x_type = "log"
        x_title = f"{size_col} (log scale, equal log-width bins)"
        used = len(positive)
        notes.append("bins are equal WIDTHS IN LOG SPACE (each bar covers "
                     "a fixed ratio of sizes); counts are exact.")
    else:
        counts, edges = np.histogram(sizes.to_numpy(dtype="float64"),
                                     bins=req.cfg.histogram_bins)
        centers = [(edges[i] + edges[i + 1]) / 2 for i in range(len(counts))]
        widths = [edges[i + 1] - edges[i] for i in range(len(counts))]
        x_type = "linear"
        x_title = size_col
        used = len(sizes)
        notes.append("equal-width byte bins; counts are exact.")
    fig = go.Figure(go.Bar(
        x=centers, y=counts, width=widths,
        marker_color=ph.palette_color(0),
        hovertemplate="size bin %{x:,.4g} B<br>%{y:,} files"
                      "<extra></extra>",
    ))
    fig.update_xaxes(type=x_type, title_text=x_title)
    notes.append(f"exact counts over {human_count(used)} of "
                 f"{human_count(len(frame))} displayed file record(s).")
    title = "File-size distribution"
    data = pd.DataFrame({"bin_start": edges[:-1], "bin_end": edges[1:],
                         "count": counts})
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, y_title="files",
                           notes=notes, height=440),
        chart_id=req.chart_id, title=title, data=data, notes=notes,
        display=_display(used, basis=f"{len(frame):,} scan rows"),
        roles=dict(req.roles), options=dict(req.options),
    )


def growth_timeline(req: ChartRequest) -> ChartArtifact:
    frame = _files(req)
    column = _first_col(frame, ["creation_timestamp", "creation_date"],
                        "creation date")
    size_col = "file_size_bytes" if "file_size_bytes" in frame.columns \
        else None
    parsed = flt.parse_dates(frame[column])
    valid = parsed.notna()
    work = pd.DataFrame({"when": parsed[valid]})
    if size_col:
        work["bytes"] = pd.to_numeric(frame.loc[valid, size_col],
                                      errors="coerce").fillna(0.0)
    if not len(work):
        raise PlatformError(
            "No rows have a usable creation date for the growth chart.",
            detail=f"{req.chart_id} no parseable dates",
        )
    work["year"] = work["when"].dt.to_period("Y").dt.start_time
    table = work.groupby("year", dropna=False).size() \
        .reset_index(name="files_per_year")
    table["cumulative_files"] = table["files_per_year"].cumsum()
    if size_col:
        per_year = work.groupby("year", dropna=False)["bytes"].sum() \
            .reset_index(name="bytes_per_year")
        table = table.merge(per_year, on="year", how="left")
        table["cumulative_bytes"] = table["bytes_per_year"].cumsum()
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    if size_col:
        fig.add_trace(go.Bar(
            x=table["year"], y=table["bytes_per_year"],
            name="storage added", marker_color=ph.palette_color(0),
            hovertemplate="%{x}<br>added %{y:,.0f} B<extra></extra>",
        ), secondary_y=False)
        fig.add_trace(go.Scatter(
            x=table["year"], y=table["cumulative_bytes"],
            name="cumulative storage", mode="lines+markers",
            line=dict(color=ph.palette_color(1)),
            hovertemplate="%{x}<br>total %{y:,.0f} B<extra></extra>",
        ), secondary_y=False)
    fig.add_trace(go.Scatter(
        x=table["year"], y=table["cumulative_files"],
        name="cumulative files", mode="lines+markers",
        line=dict(color=ph.palette_color(2), dash="dash"),
        hovertemplate="%{x}<br>%{y:,} files<extra></extra>",
    ), secondary_y=not size_col)
    title = "Project growth over time (by creation year)"
    notes = ["bars = storage added per year (left axis); dashed line = "
             "cumulative file count"
             + ("; solid line = cumulative storage (left axis)."
                if size_col else "."),
             "years without files are omitted from the axis; totals are "
             "exact."]
    table = table.rename(columns={"year": "creation_year"})
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title="creation year",
                           y_title="bytes" if size_col else "files",
                           notes=notes, height=460, legend_title=""),
        chart_id=req.chart_id, title=title, data=table, notes=notes,
        display=_display(len(work),
                         basis=f"{len(work):,} dated file record(s)"),
        roles=dict(req.roles),
    )


# ------------------------------------------------------------- folders

def folder_hierarchy(req: ChartRequest) -> ChartArtifact:
    frame = _folders(req)
    path_col = _first_col(frame, ["folder_path"], "folder path")
    parent_col = "parent_folder" if "parent_folder" in frame.columns else None
    size_col = "total_size_bytes" if "total_size_bytes" in frame.columns \
        else None
    paths = frame[path_col].astype(str)
    ids = paths.tolist()
    unique_ids = list(dict.fromkeys(ids))
    parent_map: dict[str, str] = {}
    if parent_col:
        for p, parent in zip(ids, frame[parent_col].astype(str)):
            parent_map[p] = parent
    else:
        for p in ids:
            text = str(p)
            cut = max(text.rfind("/"), text.rfind("\\"))
            parent_map[p] = text[:cut] if cut > 0 else ""
    roots = [p for p in unique_ids
             if parent_map.get(p, "") not in unique_ids]
    labels = _relative_labels(unique_ids)
    parents = ["" if p in roots else parent_map.get(p, "")
               for p in unique_ids]
    parents = [pa if pa in unique_ids else "" for pa in parents]
    if size_col:
        values = (pd.to_numeric(frame[size_col], errors="coerce")
                  .fillna(0.0).groupby(frame[path_col].astype(str)).sum())
        vals = [float(values.get(p, 0.0)) for p in unique_ids]
        size_note = "sized by total storage (exact sums)."
        if float(sum(vals)) == 0.0:
            vals = [1.0] * len(unique_ids)
            size_note = ("all folder sizes are zero — nodes sized by "
                         "folder count instead.")
    else:
        vals = [1.0] * len(unique_ids)
        size_note = "sized by folder count."
    fig = go.Figure(go.Icicle(
        ids=unique_ids, parents=parents, labels=labels, values=vals,
        branchvalues="total",
        hovertemplate="%{label}<br>%{value:,.0f}<extra></extra>",
    ))
    title = "Folder hierarchy"
    notes = [size_note,
             "paths are shown relative to the common scan root so "
             "exports never contain absolute locations; folders are "
             "never re-walked."]
    table = pd.DataFrame({"folder": labels, "parent": [
        labels[unique_ids.index(pa)] if pa else "" for pa in parents],
        "size_or_count": vals})
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, notes=notes, height=560),
        chart_id=req.chart_id, title=title, data=table, notes=notes,
        display=_display(len(unique_ids),
                         basis=f"{len(unique_ids):,} folders from the "
                               "scan output"),
    )


def _relative_labels(paths: list[str]) -> list[str]:
    """Strip the longest common ancestor prefix (display safety)."""
    if not paths:
        return []
    norm = [_DRIVE_RE.sub("", str(p)).replace("/", "\\") for p in paths]
    split = [p.split("\\") for p in norm]
    first = split[0]
    common = []
    for parts in zip(*split):
        if len(set(parts)) == 1:
            common.append(parts[0])
        else:
            break
    depth = len(common)
    if depth == 0:
        return [p.lstrip("\\") for p in norm]
    out = []
    for parts in split:
        rest = parts[depth:]
        out.append("\\".join(rest) if rest else parts[-1])
    return out


def depth_distribution(req: ChartRequest) -> ChartArtifact:
    files = _files(req)
    depth_col = _first_col(files, ["folder_depth"], "folder depth")
    depths = pd.to_numeric(files[depth_col], errors="coerce")
    file_counts = depths.dropna().astype(int).value_counts().sort_index()
    table = file_counts.rename_axis("depth").reset_index(name="files")
    folders_table = None
    if req.folders is not None and len(req.folders) and \
            "folder_depth" in req.folders.columns:
        fdepth = pd.to_numeric(req.folders["folder_depth"], errors="coerce")
        folder_counts = fdepth.dropna().astype(int).value_counts() \
            .sort_index()
        folders_table = folder_counts.rename_axis("depth") \
            .reset_index(name="folders")
        table = table.merge(folders_table, on="depth", how="outer") \
            .fillna(0).sort_values("depth").reset_index(drop=True)
        for c in ("files", "folders"):
            table[c] = table[c].astype(int)
    fig = go.Figure()
    fig.add_trace(go.Bar(x=table["depth"], y=table["files"],
                         name="files", marker_color=ph.palette_color(0),
                         hovertemplate="depth %{x}<br>%{y:,} files"
                                       "<extra></extra>"))
    if "folders" in table.columns:
        fig.add_trace(go.Bar(x=table["depth"], y=table["folders"],
                             name="folders",
                             marker_color=ph.palette_color(1),
                             hovertemplate="depth %{x}<br>%{y:,} folders"
                                           "<extra></extra>"))
        fig.update_layout(barmode="group")
        title = "Files and folders by depth"
    else:
        title = "Files by folder depth"
    notes = [f"exact counts over {human_count(len(files))} file "
             "record(s); depth 0 = scan root."]
    if table["depth"].max() > 40:
        notes.append("deep hierarchy present — consider filtering to a "
                     "depth range for readability.")
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title="folder depth",
                           y_title="count", notes=notes,
                           legend_title="", height=440),
        chart_id=req.chart_id, title=title, data=table, notes=notes,
        display=_display(len(files),
                         basis=f"{len(files):,} scan rows aggregated"),
    )


def naming_patterns(req: ChartRequest) -> ChartArtifact:
    frame = _files(req)
    trait_cols = [c for c in [
        "contains_version_indicator", "contains_final_keyword",
        "contains_date_indicator", "contains_copy_indicator",
        "has_underscore", "has_space", "has_hyphen",
    ] if c in frame.columns]
    pattern_col = "naming_pattern" if "naming_pattern" in frame.columns \
        else None
    if not trait_cols and not pattern_col:
        raise PlatformError(
            "The scan output contains no naming-convention columns, so "
            "this chart cannot be built.",
            detail=f"{req.chart_id} no naming columns",
        )
    rows = []
    for col in trait_cols:
        series = frame[col]
        if pd.api.types.is_bool_dtype(series):
            n = int(series.fillna(False).sum())
        else:
            n = int(series.astype(str).isin(_TRUTHY).sum())
        rows.append({"trait": col, "files": n})
    traits_df = pd.DataFrame(rows)
    pattern_df = None
    if pattern_col:
        counts = frame[pattern_col].astype(str).value_counts() \
            .rename_axis("pattern").reset_index(name="files")
        pattern_df = counts
    has_both = pattern_df is not None and len(traits_df)
    if has_both:
        fig = make_subplots(
            rows=2, cols=1, shared_xaxes=False,
            subplot_titles=("naming traits (files containing each)",
                            f"naming style ({pattern_col})"),
            row_heights=[0.5, 0.5], vertical_spacing=0.16)
        fig.add_trace(go.Bar(x=traits_df["files"], y=traits_df["trait"],
                             orientation="h",
                             marker_color=ph.palette_color(0),
                             hovertemplate="%{y}: %{x:,} files"
                                           "<extra></extra>"), row=1, col=1)
        show = pattern_df.head(req.cfg.max_bar_categories)
        fig.add_trace(go.Bar(x=show["pattern"], y=show["files"],
                             marker_color=ph.palette_color(1),
                             hovertemplate="%{x}: %{y:,} files"
                                           "<extra></extra>"), row=2, col=1)
        data = pd.concat([
            traits_df.assign(section="trait"),
            pattern_df.assign(section="style"),
        ], ignore_index=True)
        title = "Naming convention analysis"
    elif pattern_df is not None:
        fig = go.Figure(go.Bar(x=pattern_df["pattern"],
                               y=pattern_df["files"],
                               color_discrete_sequence=ph.PALETTE))
        data = pattern_df
        title = "Naming style distribution"
    else:
        fig = go.Figure(go.Bar(x=traits_df["files"], y=traits_df["trait"],
                               orientation="h",
                               color_discrete_sequence=ph.PALETTE))
        data = traits_df
        title = "Naming traits"
    notes = [f"exact counts over {human_count(len(frame))} file "
             "record(s)."]
    if pattern_df is not None and len(pattern_df) > \
            req.cfg.max_bar_categories:
        notes.append(f"naming styles: showing the top "
                     f"{req.cfg.max_bar_categories} of "
                     f"{len(pattern_df):,} (top-N grouping, no files "
                     "discarded).")
    if has_both:
        fig.update_layout(barmode="group")
    else:
        fig.update_layout(barmode="h")
    return ChartArtifact(
        figure=ph.finalize(fig, title=title,
                           y_title="count" if not has_both else "",
                           notes=notes,
                           height=520 if has_both else 440),
        chart_id=req.chart_id, title=title, data=data, notes=notes,
        display=_display(len(frame),
                         basis=f"{len(frame):,} scan rows aggregated"),
    )


def capitalization_distribution(req: ChartRequest) -> ChartArtifact:
    frame = _files(req)
    column = _first_col(frame, ["capitalization_style"],
                        "capitalization style")
    counts = frame[column].astype(str).value_counts()
    table = counts.rename_axis(column).reset_index(name="files")
    note = None
    if len(table) > req.cfg.max_bar_categories:
        head = table.head(req.cfg.max_bar_categories)
        rest = table.iloc[req.cfg.max_bar_categories:]
        table = pd.concat([head, pd.DataFrame(
            [{column: "Other (rest)",
              "files": int(rest["files"].sum())}])], ignore_index=True)
        note = f"{len(rest):,} additional styles grouped as 'Other (rest)'."
    fig = px.bar(table, x=column, y="files",
                 color_discrete_sequence=ph.PALETTE)
    notes = [note] if note else []
    notes.append(f"exact counts over {human_count(len(frame))} file "
                 "record(s).")
    title = "Capitalization styles"
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title=column,
                           y_title="files", notes=notes, height=440),
        chart_id=req.chart_id, title=title, data=table, notes=notes,
        display=_display(len(frame),
                         basis=f"{len(frame):,} scan rows aggregated"),
    )


def duplicate_candidates(req: ChartRequest) -> ChartArtifact:
    frame = _files(req)
    name_col = _first_col(frame, ["file_name"], "file name")
    size_col = "file_size_bytes" if "file_size_bytes" in frame.columns \
        else None
    if not size_col:
        raise PlatformError(
            "Duplicate detection needs the file size column, which this "
            "scan output does not contain.",
            detail=f"{req.chart_id} no size column",
        )
    work = frame[[name_col, size_col]].copy()
    work[size_col] = pd.to_numeric(work[size_col], errors="coerce")
    grouped = work.groupby([name_col, size_col], dropna=False).size() \
        .reset_index(name="occurrences")
    dups = grouped[grouped["occurrences"] > 1] \
        .sort_values("occurrences", ascending=False, kind="mergesort")
    total_groups = int(len(dups))
    dups = dups.head(req.cfg.top_n).reset_index(drop=True)
    if not len(dups):
        fig = go.Figure()
        fig.add_annotation(text="No files share both the same name and "
                                "size in the displayed records.",
                           xref="paper", yref="paper", x=0.5, y=0.5,
                           showarrow=False,
                           font=dict(size=14, color="#666666"))
        fig.update_layout(template="plotly_white", height=380,
                          title=dict(text="Duplicate candidates",
                                     x=0.02, xanchor="left"))
        notes = [f"scanned {human_count(len(frame))} file records — no "
                 "exact name+size duplicates found."]
        return ChartArtifact(
            figure=fig, chart_id=req.chart_id,
            title="Duplicate candidates",
            data=pd.DataFrame(columns=[name_col, size_col, "occurrences"]),
            notes=notes, display=_display(len(frame)),
        )
    dups["approx_total_bytes"] = dups[size_col] * (dups["occurrences"] - 1)
    fig = go.Figure(go.Bar(
        x=dups[name_col].astype(str), y=dups["occurrences"],
        marker_color=ph.palette_color(3),
        customdata=dups[[size_col, "approx_total_bytes"]].to_numpy(),
        hovertemplate=f"{name_col}=%{{x}}<br>occurrences %{{y}}"
                      "<br>size %{{customdata[0]:,.0f}} B"
                      "<br>potential redundant bytes "
                      "%{{customdata[1]:,.0f}}<extra></extra>",
    ))
    shown_groups = int(len(dups))
    notes = [
        f"{human_count(total_groups)} name+size groups occur more than "
        f"once; showing the top {shown_groups} by occurrences "
        "(candidates only — review manually, nothing is deleted).",
        "'potential redundant bytes' counts every occurrence beyond the "
        "first at the group's exact file size.",
    ]
    if total_groups > shown_groups:
        notes.append(f"{total_groups - shown_groups:,} more duplicate "
                     "groups exist than are shown (top-N grouping).")
    title = "Duplicate candidates (same name and size)"
    data = dups.rename(columns={name_col: "file_name",
                                size_col: "file_size_bytes"})
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title="file name",
                           y_title="occurrences", notes=notes, height=450),
        chart_id=req.chart_id, title=title, data=data, notes=notes,
        display=_display(len(frame),
                         basis=f"{len(frame):,} scan rows aggregated"),
    )


# ------------------------------------------------------------ snapshots

def _snapshots(req: ChartRequest) -> list[dict]:
    snaps = req.snapshots
    if not snaps:
        raise PlatformError(
            "No snapshots are available yet. Run a scan first, then "
            "create a snapshot on the Overview page.",
            detail=f"{req.chart_id} no snapshots",
        )
    return list(snaps)


def snapshot_timeline(req: ChartRequest) -> ChartArtifact:
    snaps = _snapshots(req)
    rows = []
    for s in snaps:
        rows.append({
            "created_at": str(s.get("created_at") or s.get("snapshot_id")
                              or "unknown"),
            "files": int(s.get("total_files") or 0),
            "storage_bytes": int(s.get("total_storage_bytes") or 0),
            "snapshot_id": str(s.get("snapshot_id") or ""),
        })
    table = pd.DataFrame(rows).sort_values("created_at") \
        .reset_index(drop=True)
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=table["created_at"], y=table["files"],
                         name="files", marker_color=ph.palette_color(0),
                         hovertemplate="%{x}<br>%{y:,} files"
                                       "<extra></extra>"),
                  secondary_y=False)
    fig.add_trace(go.Scatter(x=table["created_at"],
                             y=table["storage_bytes"],
                             name="total storage", mode="lines+markers",
                             line=dict(color=ph.palette_color(1)),
                             hovertemplate="%{x}<br>%{y:,.0f} B"
                                           "<extra></extra>"),
                  secondary_y=True)
    title = "Snapshot timeline"
    notes = [f"{len(table)} snapshot(s); exact totals as recorded when "
             "each snapshot was taken."]
    if len(table) == 1:
        notes.append("only one snapshot exists — create a later one to "
                     "compare growth.")
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title="snapshot created",
                           y_title="files", notes=notes,
                           legend_title="", height=450),
        chart_id=req.chart_id, title=title, data=table, notes=notes,
        display=_display(len(table), basis="snapshot index entries"),
    )


def _diff(req: ChartRequest) -> dict:
    diff = req.snapshot_diff
    if not isinstance(diff, dict) or not diff:
        raise PlatformError(
            "Select two valid snapshots to compare (the older one as "
            "baseline).",
            detail=f"{req.chart_id} no snapshot diff",
        )
    return diff


def snapshot_diff(req: ChartRequest) -> ChartArtifact:
    diff = _diff(req)
    items = [
        ("new files", int(diff.get("new_files_count") or 0)),
        ("removed files", int(diff.get("removed_files_count") or 0)),
        ("modified files", int(diff.get("modified_files_count") or 0)),
        ("new folders", int(diff.get("new_folders_count") or 0)),
    ]
    table = pd.DataFrame(items, columns=["change", "count"])
    fig = go.Figure(go.Bar(
        x=table["count"], y=table["change"], orientation="h",
        text=table["count"].map(lambda v: f"{v:,}"),
        textposition="outside",
        marker_color=[ph.palette_color(0), ph.palette_color(2),
                      ph.palette_color(1), ph.palette_color(3)][:len(table)],
        hovertemplate="%{y}: %{x:,}<extra></extra>",
    ))
    title = "Snapshot diff: added / removed / modified"
    notes = [
        f"files: {human_count(diff.get('file_count_before'))} before → "
        f"{human_count(diff.get('file_count_after'))} after "
        f"({int(diff.get('file_count_delta') or 0):+d}).",
        "counts are exact set operations over the two snapshot indexes "
        "(samples of paths are stored in the snapshot, not shown here).",
    ]
    base = str(diff.get("baseline_created") or "")[:19]
    comp = str(diff.get("comparison_created") or "")[:19]
    if base or comp:
        notes.append(f"baseline {base} → comparison {comp}.")
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title="files / folders",
                           y_title="", notes=notes, height=430),
        chart_id=req.chart_id, title=title, data=table, notes=notes,
        display=_display(len(table),
                         basis="exact snapshot set difference"),
    )


def snapshot_storage_growth(req: ChartRequest) -> ChartArtifact:
    diff = _diff(req)
    before = int(diff.get("storage_before_bytes") or 0)
    after = int(diff.get("storage_after_bytes") or 0)
    delta = int(diff.get("storage_delta_bytes") or (after - before))
    table = pd.DataFrame({
        "snapshot": ["baseline", "comparison"],
        "bytes": [before, after],
        "display_size": [human_bytes(before), human_bytes(after)],
    })
    fig = go.Figure(go.Bar(
        x=table["snapshot"], y=table["bytes"],
        text=table["display_size"], textposition="outside",
        marker_color=[ph.palette_color(0), ph.palette_color(1)],
        hovertemplate="%{x}: %{y:,.0f} B<extra></extra>",
    ))
    title = "Snapshot storage growth"
    notes = [f"delta: {human_bytes(delta)} "
             f"({delta:+,} bytes) — exact difference of the two "
             "snapshot totals.",
             f"files {human_count(diff.get('file_count_before'))} → "
             f"{human_count(diff.get('file_count_after'))}."]
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title="",
                           y_title="bytes", notes=notes, height=430),
        chart_id=req.chart_id, title=title, data=table, notes=notes,
        display=_display(2, basis="exact snapshot totals"),
    )


def snapshot_extension_changes(req: ChartRequest) -> ChartArtifact:
    diff = _diff(req)
    changes = diff.get("extension_changes") or {}
    if not changes:
        return _empty_artifact(
            "No extension-level changes between these two snapshots.",
            "Snapshot extension changes")
    rows = [{"extension": str(ext),
             "before": int(v.get("before") or 0),
             "after": int(v.get("after") or 0),
             "delta": int(v.get("delta") or 0)}
            for ext, v in changes.items() if isinstance(v, dict)]
    table = pd.DataFrame(rows)
    table["abs_delta"] = table["delta"].abs()
    table = table.sort_values(["abs_delta", "extension"],
                              ascending=[False, True],
                              kind="mergesort") \
        .reset_index(drop=True)
    truncated = False
    if len(table) > 25:
        table = table.head(25)
        truncated = True
    fig = go.Figure()
    fig.add_trace(go.Bar(x=table["extension"], y=table["before"],
                         name="before",
                         marker_color=ph.palette_color(0),
                         hovertemplate="%{x}: before %{y:,}<extra></extra>"))
    fig.add_trace(go.Bar(x=table["extension"], y=table["after"],
                         name="after",
                         marker_color=ph.palette_color(1),
                         hovertemplate="%{x}: after %{y:,}<extra></extra>"))
    title = "Extension changes between snapshots"
    notes = ["exact counts from both snapshot indexes; extensions sorted "
             "by absolute change."]
    if truncated:
        notes.append(f"showing the 25 largest changes of "
                     f"{len(changes):,} changed extensions.")
    if not len(table):
        return _empty_artifact(
            "No extension-level changes between these two snapshots.",
            "Snapshot extension changes")
    return ChartArtifact(
        figure=ph.finalize(fig, title=title, x_title="extension",
                           y_title="files", notes=notes,
                           barmode="group", legend_title="", height=450),
        chart_id=req.chart_id, title=title, data=table, notes=notes,
        display=_display(len(table),
                         basis="exact snapshot extension counts"),
    )


def _empty_artifact(message: str, title: str) -> ChartArtifact:
    fig = go.Figure()
    fig.add_annotation(text=message, xref="paper", yref="paper",
                       x=0.5, y=0.5, showarrow=False,
                       font=dict(size=14, color="#666666"))
    fig.update_layout(template="plotly_white", height=380,
                      title=dict(text=title, x=0.02, xanchor="left"))
    return ChartArtifact(figure=fig, chart_id="", title=title,
                         data=pd.DataFrame(), notes=[message],
                         display=_display(0))
