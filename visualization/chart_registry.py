"""Chart catalogue and dispatch (Phase 7).

``ChartSpec`` entries declare, for every supported chart:

* which dashboard section(s) it belongs to (``modes``),
* what it needs to run (``needs``: preview frame, analysis run,
  filesystem frames, snapshots, a comparison),
* its column roles with accepted kinds — everything the dashboard needs
  to build pickers and pre-flight validation **without loading data**,
* a pure builder function resolved lazily (no import cycles).

``build_chart`` is the single public entry point used by the dashboard
and the tests.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field

from core.exceptions import PlatformError
from visualization.chart_factory import (ChartArtifact, ChartRequest,
                                         VisualizationConfig)

# mode identifiers (dashboard sections)
MODE_ARCHAEOLOGY = "archaeology"
MODE_DATASET = "dataset"
MODE_COMPARISON = "comparison"

# needs identifiers
NEEDS_PREVIEW = "preview"
NEEDS_RUN = "run"
NEEDS_FILES = "files"
NEEDS_FOLDERS = "folders"
NEEDS_SNAPSHOTS = "snapshots"
NEEDS_SNAPSHOT_DIFF = "snapshot_diff"
NEEDS_COMPARISON = "comparison"

ALL_MODES = (MODE_ARCHAEOLOGY, MODE_DATASET, MODE_COMPARISON)

# kind vocabularies (mirrors visualization.validation)
NUM = ("int", "float")
CAT = ("object", "category", "bool", "string", "int")
ANY = ("any",)


@dataclass(frozen=True)
class RoleSpec:
    """One column role of a chart."""

    kinds: tuple[str, ...]
    required: bool = True
    label: str = ""
    multiple: bool = False


@dataclass(frozen=True)
class ChartSpec:
    """Static description of one chart type."""

    chart_id: str
    label: str
    category: str
    modes: tuple[str, ...]
    needs: str
    roles: dict[str, RoleSpec] = field(default_factory=dict)
    description: str = ""
    builder: str = ""
    kinds: tuple[str, ...] = ()        # restrict (comparison kinds, e.g. fs)
    default_height: int = 460

    @property
    def role_kinds(self) -> dict[str, tuple[str, ...]]:
        return {name: role.kinds for name, role in self.roles.items()}

    @property
    def role_required(self) -> dict[str, bool]:
        return {name: role.required for name, role in self.roles.items()}


def _spec(chart_id: str, label: str, category: str, modes: tuple[str, ...],
          needs: str, builder: str, *, roles: dict | None = None,
          description: str = "", kinds: tuple[str, ...] = ()) -> ChartSpec:
    return ChartSpec(
        chart_id=chart_id, label=label, category=category, modes=modes,
        needs=needs, roles=roles or {}, description=description,
        builder=builder, kinds=kinds,
    )


_DS = (MODE_DATASET,)
_AR = (MODE_ARCHAEOLOGY,)
_CP = (MODE_COMPARISON,)

_CHART_LIST: tuple[ChartSpec, ...] = (
    # ------------------------------------------------------ Basic
    _spec("bar", "Bar chart", "Basic", _DS, NEEDS_PREVIEW,
          "visualization.chart_factory:build_bar",
          roles={"x": RoleSpec(ANY, label="category / axis"),
                 "y": RoleSpec(NUM, required=False, label="value")},
          description="Counts per category, or sum/mean/median of a "
                      "numeric value per category (choose the "
                      "aggregation). Top-N grouping is disclosed."),
    _spec("hbar", "Horizontal bar chart", "Basic", _DS, NEEDS_PREVIEW,
          "visualization.chart_factory:build_hbar",
          roles={"x": RoleSpec(ANY, label="category / axis"),
                 "y": RoleSpec(NUM, required=False, label="value")},
          description="Bar chart with horizontal bars — better for long "
                      "category labels."),
    _spec("grouped_bar", "Grouped bar chart", "Basic", _DS, NEEDS_PREVIEW,
          "visualization.chart_factory:build_grouped_bar",
          roles={"x": RoleSpec(CAT, label="axis category"),
                 "value": RoleSpec(NUM, label="value"),
                 "group": RoleSpec(CAT, label="group series")},
          description="Side-by-side bars comparing a numeric value "
                      "across two categories."),
    _spec("stacked_bar", "Stacked bar chart", "Basic", _DS, NEEDS_PREVIEW,
          "visualization.chart_factory:build_stacked_bar",
          roles={"x": RoleSpec(CAT, label="axis category"),
                 "group": RoleSpec(CAT, label="stack series"),
                 "value": RoleSpec(NUM, required=False, label="value")},
          description="Stacked composition of counts (or a value) per "
                      "category and group."),
    _spec("line", "Line chart", "Basic", _DS, NEEDS_PREVIEW,
          "visualization.chart_factory:build_line",
          roles={"x": RoleSpec(ANY, label="time / x axis"),
                 "y": RoleSpec(NUM, required=False, label="value"),
                 "group": RoleSpec(CAT, required=False, label="series")},
          description="Trend over a date/time column (or record counts "
                      "over time when no value is chosen)."),
    _spec("area", "Area chart", "Basic", _DS, NEEDS_PREVIEW,
          "visualization.chart_factory:build_area",
          roles={"x": RoleSpec(ANY, label="time / x axis"),
                 "y": RoleSpec(NUM, required=False, label="value"),
                 "group": RoleSpec(CAT, required=False, label="series")},
          description="Filled line chart showing volume over time."),
    _spec("pie", "Pie chart", "Basic", _DS, NEEDS_PREVIEW,
          "visualization.chart_factory:build_pie",
          roles={"names": RoleSpec(CAT, label="slices"),
                 "values": RoleSpec(NUM, required=False, label="value")},
          description="Share of records (or summed value) per category. "
                      "Beyond 8 categories the remainder is grouped as "
                      "'Other (rest)' — never truncated silently."),
    _spec("donut", "Donut chart", "Basic", _DS, NEEDS_PREVIEW,
          "visualization.chart_factory:build_donut",
          roles={"names": RoleSpec(CAT, label="slices"),
                 "values": RoleSpec(NUM, required=False, label="value")},
          description="Pie chart with a hollow centre for the label."),
    # ------------------------------------------------ Statistical
    _spec("histogram", "Histogram", "Statistical", _DS, NEEDS_PREVIEW,
          "visualization.chart_factory:build_histogram",
          roles={"values": RoleSpec(NUM, label="numeric column")},
          description="Distribution of one numeric column in equal-width "
                      "bins (bin count configurable; non-finite values "
                      "excluded and reported)."),
    _spec("box", "Box plot", "Statistical", _DS, NEEDS_PREVIEW,
          "visualization.chart_factory:build_box",
          roles={"values": RoleSpec(NUM, label="numeric column"),
                 "group": RoleSpec(CAT, required=False, label="group")},
          description="Median, quartiles and IQR whiskers; optional "
                      "grouping. Large frames are sampled with a "
                      "disclosed seed."),
    _spec("violin", "Violin plot", "Statistical", _DS, NEEDS_PREVIEW,
          "visualization.chart_factory:build_violin",
          roles={"values": RoleSpec(NUM, label="numeric column"),
                 "group": RoleSpec(CAT, required=False, label="group")},
          description="Kernel shape of the distribution with an embedded "
                      "box plot."),
    _spec("density", "Density plot (KDE)", "Statistical", _DS,
          NEEDS_PREVIEW,
          "visualization.chart_factory:build_density",
          roles={"values": RoleSpec(NUM, label="numeric column")},
          description="Gaussian kernel density estimate (numpy only, "
                      "Silverman bandwidth) — a smoothed view, labelled "
                      "as such."),
    _spec("ecdf", "Empirical CDF", "Statistical", _DS, NEEDS_PREVIEW,
          "visualization.chart_factory:build_ecdf",
          roles={"values": RoleSpec(NUM, label="numeric column")},
          description="Cumulative share of rows at or below each value — "
                      "no binning bias."),
    _spec("scatter", "Scatter plot", "Statistical", _DS, NEEDS_PREVIEW,
          "visualization.chart_factory:build_scatter",
          roles={"x": RoleSpec(NUM, label="x column"),
                 "y": RoleSpec(NUM, label="y column"),
                 "color": RoleSpec(ANY, required=False, label="colour by")},
          description="Relationship between two numeric columns "
                      "(optional linear fit; point cap with disclosed "
                      "random sampling)."),
    _spec("bubble", "Bubble chart", "Statistical", _DS, NEEDS_PREVIEW,
          "visualization.chart_factory:build_bubble",
          roles={"x": RoleSpec(NUM, label="x column"),
                 "y": RoleSpec(NUM, label="y column"),
                 "size": RoleSpec(NUM, label="bubble size"),
                 "color": RoleSpec(ANY, required=False, label="colour by")},
          description="Scatter plot with a third numeric measure as "
                      "marker size."),
    _spec("pair", "Pair plot", "Statistical", _DS, NEEDS_PREVIEW,
          "visualization.chart_factory:build_pair",
          roles={"columns": RoleSpec(NUM, multiple=True,
                                     label="numeric columns (2-5)")},
          description="Scatter matrix for a small set of numeric "
                      "columns (complete rows only)."),
    _spec("corr_heatmap", "Correlation heatmap", "Statistical", _DS,
          NEEDS_PREVIEW,
          "visualization.chart_factory:build_corr_heatmap",
          roles={"columns": RoleSpec(NUM, multiple=True,
                                     label="numeric columns")},
          description="Pearson correlation between numeric columns "
                      "(pairwise-complete rows). Defaults to all numeric "
                      "columns when none are chosen."),
    _spec("missing_values", "Missing values per column", "Statistical",
          _DS, NEEDS_PREVIEW,
          "visualization.chart_factory:build_missing_values",
          description="Count and percentage of missing cells per column "
                      "over the displayed rows."),
    # --------------------------------------------- Multivariate
    _spec("parallel_coordinates", "Parallel coordinates", "Multivariate",
          _DS, NEEDS_PREVIEW,
          "visualization.chart_factory:build_parallel_coordinates",
          roles={"columns": RoleSpec(NUM, multiple=True,
                                     label="numeric columns (2-8)"),
                 "color": RoleSpec(ANY, required=False, label="colour by")},
          description="One axis per numeric column; rows drawn as lines. "
                      "Colour on a categorical column shows "
                      "order-of-appearance codes, stated on the chart."),
    _spec("pca_scatter", "PCA scatter (projection)", "Multivariate", _DS,
          NEEDS_PREVIEW,
          "visualization.chart_factory:build_pca",
          roles={"columns": RoleSpec(NUM, multiple=True, required=False,
                                     label="numeric columns"),
                 "color": RoleSpec(CAT, required=False, label="colour by")},
          description="Deterministic principal-component projection "
                      "(numpy SVD, standardized columns, fixed sign "
                      "convention) with explained-variance labels."),
    _spec("tsne_scatter", "t-SNE scatter (projection)", "Multivariate",
          _DS, NEEDS_PREVIEW,
          "visualization.chart_factory:build_tsne",
          roles={"columns": RoleSpec(NUM, multiple=True, required=False,
                                     label="numeric columns"),
                 "color": RoleSpec(CAT, required=False, label="colour by")},
          description="t-SNE embedding of local neighbourhoods "
                      "(scikit-learn, fixed seed). NOT comparable to "
                      "PCA: global distances are not meaningful; the "
                      "chart says so."),
    _spec("scatter3d", "3-D scatter plot", "Multivariate", _DS,
          NEEDS_PREVIEW,
          "visualization.chart_factory:build_scatter3d",
          roles={"x": RoleSpec(NUM, label="x column"),
                 "y": RoleSpec(NUM, label="y column"),
                 "z": RoleSpec(NUM, label="z column"),
                 "color": RoleSpec(ANY, required=False, label="colour by")},
          description="Three numeric axes with the standard point cap "
                      "and sampling disclosure."),
    _spec("scatter_matrix", "Scatter matrix", "Multivariate", _DS,
          NEEDS_PREVIEW,
          "visualization.chart_factory:build_scatter_matrix",
          roles={"columns": RoleSpec(NUM, multiple=True,
                                     label="numeric columns (3-6)"),
                 "color": RoleSpec(CAT, required=False, label="colour by")},
          description="Pairwise scatter of up to six numeric columns "
                      "with optional colouring."),
    # ---------------------------------------------- Hierarchical
    _spec("treemap", "Treemap", "Hierarchical", _DS, NEEDS_PREVIEW,
          "visualization.chart_factory:build_treemap",
          roles={"cat": RoleSpec(CAT, label="level 1 category"),
                 "sub": RoleSpec(CAT, required=False,
                                 label="level 2 category"),
                 "values": RoleSpec(NUM, required=False,
                                    label="size value")},
          description="Nested rectangles sized by record counts (or a "
                      "summed value); optional second level."),
    _spec("sunburst", "Sunburst", "Hierarchical", _DS, NEEDS_PREVIEW,
          "visualization.chart_factory:build_sunburst",
          roles={"cat": RoleSpec(CAT, label="level 1 category"),
                 "sub": RoleSpec(CAT, required=False,
                                 label="level 2 category"),
                 "values": RoleSpec(NUM, required=False,
                                    label="size value")},
          description="Ring chart of nested shares (counts or summed "
                      "value) with exact hover values."),
    _spec("icicle", "Icicle chart", "Hierarchical", _DS, NEEDS_PREVIEW,
          "visualization.chart_factory:build_icicle",
          roles={"cat": RoleSpec(CAT, label="level 1 category"),
                 "sub": RoleSpec(CAT, required=False,
                                 label="level 2 category"),
                 "values": RoleSpec(NUM, required=False,
                                    label="size value")},
          description="Vertical hierarchy of nested bands (counts or "
                      "summed value)."),
    # --------------------------------------- Analysis-run outputs
    _spec("run_histogram", "Run: numeric distribution", "Analysis run",
          _DS, NEEDS_RUN,
          "visualization.dataset_visualizations:run_histogram",
          roles={"column": RoleSpec(NUM, label="numeric column")},
          description="Histogram exactly as computed by the analysis run "
                      "(bin edges/counts from distributions.json — no "
                      "recomputation, basis and exclusions preserved)."),
    _spec("run_categorical", "Run: categorical distribution",
          "Analysis run", _DS, NEEDS_RUN,
          "visualization.dataset_visualizations:run_categorical",
          roles={"column": RoleSpec(ANY, label="categorical column")},
          description="Top categories, missing and unique counts as "
                      "computed by the analysis run."),
    _spec("run_correlation", "Run: correlation heatmap", "Analysis run",
          _DS, NEEDS_RUN,
          "visualization.dataset_visualizations:run_correlation",
          roles={"method": RoleSpec(ANY, required=False,
                                    label="method")},
          description="Correlation matrix exactly as stored by the run "
                      "(method selectable; pairwise-complete notes "
                      "preserved)."),
    _spec("run_missing_values", "Run: missing values", "Analysis run",
          _DS, NEEDS_RUN,
          "visualization.dataset_visualizations:run_missing_values",
          description="Missing-cell counts per column from the run "
                      "profile (all column kinds, run basis shown)."),
    _spec("run_grouped_stats", "Run: grouped statistics", "Analysis run",
          _DS, NEEDS_RUN,
          "visualization.dataset_visualizations:run_grouped_stats",
          roles={"column": RoleSpec(NUM, label="numeric column")},
          description="Per-group mean with min/max range bars from the "
                      "run's grouped statistics."),
    _spec("run_quality", "Run: quality score & issues", "Analysis run",
          _DS, NEEDS_RUN,
          "visualization.dataset_visualizations:run_quality",
          description="Quality gauge plus counts of warnings/issues "
                      "(duplicate rows, high-missing columns, constants, "
                      "outliers) with the run's stated limitations."),
    _spec("run_outliers", "Run: potential outliers", "Analysis run", _DS,
          NEEDS_RUN,
          "visualization.dataset_visualizations:run_outliers",
          description="Potential-outlier counts per column exactly as "
                      "flagged by the run (review only — nothing was "
                      "removed)."),
    # ------------------------------------------------ Archaeology
    _spec("creation_timeline", "File creation timeline", "Archaeology",
          _AR, NEEDS_FILES,
          "visualization.archaeology_visualizations:creation_timeline",
          roles={"group": RoleSpec(CAT, required=False,
                                   label="split by")},
          description="Files created per time bucket, optionally split "
                      "by a category column (e.g. file_category)."),
    _spec("modification_timeline", "File modification timeline",
          "Archaeology", _AR, NEEDS_FILES,
          "visualization.archaeology_visualizations:modification_timeline",
          roles={"group": RoleSpec(CAT, required=False,
                                   label="split by")},
          description="Files last modified per time bucket, optionally "
                      "split by category."),
    _spec("type_distribution", "File-type distribution", "Archaeology",
          _AR, NEEDS_FILES,
          "visualization.archaeology_visualizations:type_distribution",
          description="Top file extensions by record count with "
                      "top-N disclosure."),
    _spec("storage_by_category", "Storage by category", "Archaeology",
          _AR, NEEDS_FILES,
          "visualization.archaeology_visualizations:storage_by_category",
          roles={"split": RoleSpec(CAT, required=False,
                                   label="group bars by")},
          description="Total storage per file category (or other "
                      "categorical column) with human-readable sizes."),
    _spec("size_distribution", "File-size distribution", "Archaeology",
          _AR, NEEDS_FILES,
          "visualization.archaeology_visualizations:size_distribution",
          description="Histogram of file sizes with an optional log "
                      "scale (recommended for long-tailed sizes)."),
    _spec("growth_timeline", "Project growth over time", "Archaeology",
          _AR, NEEDS_FILES,
          "visualization.archaeology_visualizations:growth_timeline",
          description="Cumulative file count and cumulative storage per "
                      "creation year."),
    _spec("folder_hierarchy", "Folder hierarchy", "Archaeology", _AR,
          NEEDS_FOLDERS,
          "visualization.archaeology_visualizations:folder_hierarchy",
          description="Icicle of the folder tree sized by total storage "
                      "(ids/parents built from the registered scan "
                      "output — folders are never re-walked)."),
    _spec("depth_distribution", "Folder depth distribution",
          "Archaeology", _AR, NEEDS_FOLDERS,
          "visualization.archaeology_visualizations:depth_distribution",
          description="Folders and files per depth level from the scan "
                      "output."),
    _spec("naming_patterns", "Naming convention analysis", "Archaeology",
          _AR, NEEDS_FILES,
          "visualization.archaeology_visualizations:naming_patterns",
          description="Counts of naming patterns (version/final/date/"
                      "copy indicators, separators) with top-N "
                      "disclosure."),
    _spec("capitalization_distribution", "Capitalization styles",
          "Archaeology", _AR, NEEDS_FILES,
          "visualization.archaeology_visualizations:"
          "capitalization_distribution",
          description="Distribution of filename capitalization styles."),
    _spec("duplicate_candidates", "Duplicate candidates", "Archaeology",
          _AR, NEEDS_FILES,
          "visualization.archaeology_visualizations:duplicate_candidates",
          description="Groups of files sharing name and size (exact "
                      "match on those two fields — candidates only, "
                      "nothing is deleted)."),
    _spec("snapshot_timeline", "Snapshot timeline", "Archaeology", _AR,
          NEEDS_SNAPSHOTS,
          "visualization.archaeology_visualizations:snapshot_timeline",
          description="File count and total storage across snapshots "
                      "(from the snapshot index)."),
    _spec("snapshot_diff", "Added / removed / modified between "
                           "snapshots", "Archaeology", _AR,
          NEEDS_SNAPSHOT_DIFF,
          "visualization.archaeology_visualizations:snapshot_diff",
          description="Record counts of new, removed, modified files "
                      "and new folders between the two selected "
                      "snapshots."),
    _spec("snapshot_storage_growth", "Snapshot storage growth",
          "Archaeology", _AR, NEEDS_SNAPSHOT_DIFF,
          "visualization.archaeology_visualizations:"
          "snapshot_storage_growth",
          description="Storage before/after between two snapshots with "
                      "the exact delta."),
    _spec("snapshot_extension_changes", "Extension changes between "
                                        "snapshots", "Archaeology", _AR,
          NEEDS_SNAPSHOT_DIFF,
          "visualization.archaeology_visualizations:"
          "snapshot_extension_changes",
          description="Per-extension counts before vs after between two "
                      "snapshots (top changes shown)."),
    # ------------------------------------------------ Comparison
    _spec("comparison_counts", "Record counts A vs B", "Comparison",
          _CP, NEEDS_COMPARISON,
          "visualization.comparison_visualizations:comparison_counts",
          description="Rows/files in A vs B with matched, added, removed "
                      "and modified counts (exact totals)."),
    _spec("comparison_matched", "Match composition", "Comparison", _CP,
          NEEDS_COMPARISON,
          "visualization.comparison_visualizations:comparison_matched",
          description="Donut of matched vs modified vs added vs removed "
                      "records; strategy and exactness stated on the "
                      "chart."),
    _spec("comparison_numeric_delta", "Numerical statistics changes",
          "Comparison", _CP, NEEDS_COMPARISON,
          "visualization.comparison_visualizations:"
          "comparison_numeric_delta",
          roles={"column": RoleSpec(ANY, required=False,
                                    label="column (all if empty)")},
          description="Before/after deltas of count, mean, std, min, "
                      "max and unique per numeric column (exact pass — "
                      "or clearly flagged approximate runs)."),
    _spec("comparison_category_change", "Categorical value changes",
          "Comparison", _CP, NEEDS_COMPARISON,
          "visualization.comparison_visualizations:"
          "comparison_category_change",
          roles={"column": RoleSpec(ANY, required=False,
                                    label="categorical column")},
          description="Top value counts in A vs B for one categorical "
                      "column, side by side."),
    _spec("comparison_missing_delta", "Missing-value changes",
          "Comparison", _CP, NEEDS_COMPARISON,
          "visualization.comparison_visualizations:"
          "comparison_missing_delta",
          description="Delta of missing-cell counts per column between "
                      "A and B."),
    _spec("comparison_schema", "Schema differences", "Comparison", _CP,
          NEEDS_COMPARISON,
          "visualization.comparison_visualizations:comparison_schema",
          description="Added/removed columns and dtype changes (CSV/"
                      "SQLite) or file/folder field and folder "
                      "differences (filesystem)."),
    _spec("comparison_storage", "Storage A vs B", "Comparison", _CP,
          NEEDS_COMPARISON,
          "visualization.comparison_visualizations:comparison_storage",
          kinds=("filesystem",),
          description="Total storage and file counts of both scan "
                      "outputs with the exact delta (filesystem "
                      "comparisons)."),
    _spec("comparison_extension", "Extension changes A vs B",
          "Comparison", _CP, NEEDS_COMPARISON,
          "visualization.comparison_visualizations:"
          "comparison_extension",
          kinds=("filesystem",),
          description="Top extensions by absolute change, counts before "
                      "vs after (exact sums)."),
    _spec("comparison_naming", "Naming & path changes", "Comparison",
          _CP, NEEDS_COMPARISON,
          "visualization.comparison_visualizations:comparison_naming",
          kinds=("filesystem",),
          description="Naming-pattern changes, path-case changes, moves "
                      "and renames between the two scans."),
)


CHARTS: dict[str, ChartSpec] = {c.chart_id: c for c in _CHART_LIST}


def get_chart(chart_id: str) -> ChartSpec:
    spec = CHARTS.get(str(chart_id))
    if spec is None:
        raise PlatformError(
            f"Unknown chart type '{chart_id}'.",
            detail=f"chart {chart_id} not in registry",
        )
    return spec


def charts_for(
    mode: str | None = None,
    *,
    needs: str | None = None,
    kinds: str | None = None,
) -> list[ChartSpec]:
    """Registry entries filtered for a dashboard section."""
    out = []
    for spec in _CHART_LIST:
        if mode is not None and mode not in spec.modes:
            continue
        if needs is not None and spec.needs != needs:
            continue
        if kinds is not None and spec.kinds and kinds not in spec.kinds:
            continue
        out.append(spec)
    return out


def _resolve(builder_path: str):
    module_name, _, attr = str(builder_path).partition(":")
    if not module_name or not attr:
        raise PlatformError(
            "Internal chart registry entry is malformed.",
            detail=f"builder {builder_path!r}",
        )
    try:
        module = importlib.import_module(module_name)
        return getattr(module, attr)
    except (ImportError, AttributeError) as exc:
        raise PlatformError(
            "This chart type could not be loaded in the current "
            "environment.",
            detail=f"builder {builder_path}: {type(exc).__name__}",
        ) from exc


def build_chart(
    chart_id: str,
    cfg: VisualizationConfig,
    *,
    roles: dict | None = None,
    options: dict | None = None,
    **inputs,
) -> ChartArtifact:
    """Validate the registry entry and run its builder.

    ``inputs`` are passed to the ``ChartRequest`` (frame, files, folders,
    snapshots, snapshot_diff, run, run_meta, comparison, source_label,
    meta). Unknown keys raise — typos never fail silently.
    """
    spec = get_chart(chart_id)
    request = ChartRequest(
        chart_id=spec.chart_id, cfg=cfg,
        roles=dict(roles or {}), options=dict(options or {}),
    )
    allowed = set(request.__dataclass_fields__)
    for key, value in inputs.items():
        if key not in allowed:
            raise PlatformError(
                f"Internal error: unknown input '{key}' for chart "
                f"'{chart_id}'.",
                detail=f"unknown request field {key}",
            )
        setattr(request, key, value)
    fn = _resolve(spec.builder)
    artifact = fn(request)
    if not isinstance(artifact, ChartArtifact):
        raise PlatformError(
            "This chart type did not produce a valid figure.",
            detail=f"builder {spec.builder} returned "
                   f"{type(artifact).__name__}",
        )
    return artifact
