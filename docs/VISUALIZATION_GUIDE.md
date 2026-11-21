# Visualization Guide

How the Phase 7 visualization engine works, what every chart discloses,
and what it deliberately never claims. Run everything from
`<PROJECT_ROOT>` with the project virtualenv Python.

## 1. Overview

The engine lives in `visualization/` and is independent of Streamlit:
it turns registered datasets, analysis runs, comparisons and research
scan frames into Plotly figures with explicit provenance. The dashboard
entry point is page 12, **Advanced Visualization**
(`dashboard/advanced_visualization.py`).

```text
visualization/
  chart_registry.py        57 chart specs (id, modes, needs, roles)
  chart_factory.py         dataset builders (basic/statistical/multi)
  archaeology_visualizations.py   scan + snapshot builders
  dataset_visualizations.py       analysis-run builders
  comparison_visualizations.py    A-vs-B comparison builders
  validation.py            role/kind validation (no data read)
  filters.py               row filters + top-N helpers
  data_adapter.py          bounded read-only loaders
  plotly_helpers.py        shared layout, palettes, badges
  export.py                HTML + CSV + metadata bundles
  visualization_config.py  limits (all clamped, all disclosed)
```

## 2. Safety and honesty rules

* **Nothing is computed until Generate is pressed.** Page load only
  reads small JSON indexes (dataset registry, run lists, comparison
  lists); every preview, filter and chart build happens on the button.
* **Sources are read-only.** CSVs are read with `nrows`/`usecols`;
  SQLite opens `file:...?mode=ro`; the research database and the 22
  protected research files are never opened for write.
* **Bounds are disclosed, never silent.** Row caps, point sampling and
  top-N regrouping always attach a note (and an on-figure badge where
  the chart is approximate).
* **No absolute paths** reach user-facing messages, figure annotations
  or export metadata (`export.py` re-checks every string it writes).
* **Comparison honesty:** charts carry `exactness: … · strategy: …`;
  anything other than `exact` marks the artifact `approximate` and
  colours the badge red.
* Sampling is **seeded (42)** so the same request reproduces the same
  figure.

## 3. Chart registry

`CHARTS` holds **57 specs**. Every spec declares `modes` (which page
section offers it), `needs` (which data it requires), `roles` (column
inputs with kinds and required/multiple flags) and a human description.

| Mode | Charts | Section |
|---|---|---|
| `archaeology` | 15 | scan frames + stored snapshots |
| `dataset` | 33 | 26 preview charts + 7 analysis-run charts |
| `comparison` | 9 | completed A-vs-B comparison runs |

Dataset preview charts by category: Basic (bar, hbar, grouped/stacked
bar, line, area, pie, donut), Statistical (histogram, box, violin,
density, ecdf, scatter, bubble, pair, corr_heatmap, missing_values),
Multivariate (parallel_coordinates, pca_scatter, tsne_scatter,
scatter3d, scatter_matrix), Hierarchical (treemap, sunburst, icicle).
Run charts: run_histogram, run_categorical, run_correlation,
run_missing_values, run_grouped_stats, run_quality, run_outliers.

List charts for a mode with `charts_for(mode)` (optionally filtered by
`needs=` or column `kinds=`); look one up with `get_chart(chart_id)`.

## 4. Chart modes and data needs

`needs` decides which loader runs at Generate time:

* `files` / `folders` — the research scan frames already filtered by
  the research sidebar (archaeology section).
* `snapshots` / `snapshot_diff` — the stored snapshot index only
  (`scanner.snapshot_manager`); diff charts need two selected
  snapshots and fail with a clear message when fewer exist.
* `preview` — a **bounded, read-only** preview of a registered CSV or
  SQLite dataset (`data_adapter.load_preview`). Filesystem
  registrations are refused here; use the archaeology section.
* `run` — a completed analysis run (`analysis_runner.load_run`):
  statistics, distributions and correlation come from the run's own
  JSON/CSV outputs, never recomputed.
* `comparison` — a completed comparison payload
  (`analysis.comparison.load_comparison`).

Role options come from metadata when it exists (dataset column specs,
run profile kinds, comparison statistics columns) and fall back to
free-text entry that is strictly verified against the real frame at
Generate time.

## 5. Configuration and limits

All limits live in `VisualizationConfig` and are **clamped** in
`__post_init__` (a chart can never be asked for an unsafe value):

| Field | Default | Clamp |
|---|---|---|
| `max_points` | 5 000 | ≥ 50 |
| `preview_rows` | 100 000 | ≥ 100 |
| `aggregation_row_limit` | 2 000 000 | ≥ 1 000 |
| `top_n` | 20 | ≥ 1 |
| `pie_max_categories` | 8 | ≥ 2 |
| `histogram_bins` | 30 | ≥ 2 |
| `max_bar_categories` | 60 | ≥ 2 |
| `pca_max_rows` / `tsne_max_rows` | 50 000 / 10 000 | ≥ 10 |
| `seed` | 42 | fixed |
| `export_max_rows` | 50 000 | ≥ 100 |

The page exposes the main ones under **Chart limits & sampling**;
`to_dict()` round-trips the full config into the export bundle.

## 6. Row filters

Dataset preview charts get four filter groups (expander **Row
filters**, applied only at Generate time):

* **Date range** — one temporal column, optional from/to bounds.
* **Numeric range** — one numeric column, optional min/max (inclusive;
  notes read `score: >= 2`).
* **Category keep-list** — values are read from the first preview rows
  (bounded, disclosed when truncated); empty selection keeps all.
* **Missing-value policy** — optionally drop rows where a chosen
  column is missing.

Every applied filter and its removed-row count is listed with the
result (`section filters removed N row(s)…`). Sidebar research filters
are summarised next to archaeology file charts the same way.

## 7. Sampling and disclosure

* **Point sampling** (`filters.sample_frame`): deterministic random
  sample with the config seed when a frame exceeds `max_points`;
  applies to scatter-like charts. The figure carries a
  `Sampling: … (seed=42)` badge; `display.sampling` records method,
  params and `approximate: true`.
* **Preview truncation**: `load_preview` reads at most `preview_rows`
  (`first_n_rows`); the note reads `Preview shows the first 1,000 of
  1,200 rows (row cap: 1,000)`.
* **Top-N regrouping**: category axes keep the top `top_n`/cap and an
  explicit `Other (rest)` slice — no records are discarded, totals
  stay exact (pie totals are exact even when regrouped).
* **Run charts** read exactly what the run stored; their notes say so
  (e.g. histogram counts are the stored ones).
* The page merges preview sampling into `artifact.display` via
  `data_adapter.merge_sampling`, so exports inherit the disclosure.

## 8. Programmatic API

```python
from visualization import build_chart, VisualizationConfig, charts_for
from visualization.chart_registry import MODE_DATASET

spec = get_chart("scatter")                  # metadata only
art = build_chart(
    "scatter", VisualizationConfig(max_points=2000),
    roles={"x": "mass_g", "y": "length_mm", "color": "site"},
    frame=preview_frame,                     # your bounded DataFrame
    source_label="site_register.csv",
)
art.figure            # plotly Figure (badges + notes on canvas)
art.display           # rows, basis, sampling, approximate
art.notes             # human-readable honesty notes
art.approximate       # property: display["approximate"]
```

`build_chart` raises `PlatformError` (user-facing message, no paths)
for unknown ids, missing columns, kind mismatches or empty data.
`validate_chart_request(...)` performs the same checks without touching
data — the page uses it to keep Generate disabled until the form is
valid.

## 9. Dashboard page (page 12)

Three sections behind one radio (`ds7_section`): **Archaeology**,
**Dataset**, **Comparison**. The flow in each section:

1. Light selectors (dataset / analysis run / comparison run /
   snapshot pair) — JSON or directory reads only.
2. Chart type picker (filtered to the section) with its description.
3. Column-role pickers (selectbox / multiselect / free-text fallback),
   chart options (aggregation, bins, trendline, log axis) and limit
   widgets.
4. Row filters (preview charts only).
5. Pre-flight validation — **Generate visualization** stays disabled
   until the form validates; **Reset page** clears every `ds7_*` key.
6. Result: row metrics, the figure, all notes, then **Export bundle**
   and **Download chart data (CSV)**.

Changing any setting after a build captions *Settings changed since
this chart was generated* until Generate is pressed again. Session
keys are prefixed `ds7_`; nothing on the page writes outside
`output/visualizations/`.

## 10. Export bundles

**Export bundle** writes to `output/visualizations/viz_YYYYmmdd_HHMMSS_<hex6>/`:

| File | Contents |
|---|---|
| `chart.html` | standalone figure (badges/notes on canvas) |
| `chart_data.csv` | chart data, capped at `export_max_rows` |
| `config.json` | full `VisualizationConfig` + roles/options |
| `metadata.json` | id, notes, display, sampling, export accounting, `contains_absolute_paths: false` |

The success message lists the files and whether the CSV was truncated.
The in-page **Download chart data** button honours the same cap.
Export runs are asserted to stay outside protected research data by
the test suite.

## 11. Benchmarks

Measured on this machine (Python 3.11.9, pandas 3.0.5, plotly 7.1.0;
median of 3 builds per cell, synthetic frames in OS temp, default
config):

| Chart | Rows | Build (s) | Peak MB | Points | Sampled |
|---|---:|---:|---:|---:|---|
| scatter | 10 000 | 0.42 | 20.5* | 1 291 | yes |
| scatter | 100 000 | 0.33 | 3.1 | 1 285 | yes |
| scatter | 200 000 | 0.32 | 6.1 | 1 271 | yes |
| bar (count) | 10 000 | 0.27 | 0.6 | 61 | no |
| bar (count) | 100 000 | 0.28 | 0.6 | 61 | no |
| bar (count) | 200 000 | 0.30 | 0.5 | 61 | no |
| histogram | 10 000 | 0.10 | 0.4 | 40 | no |
| histogram | 100 000 | 0.11 | 2.9 | 40 | no |
| histogram | 200 000 | 0.13 | 3.7 | 40 | no |

\* first measurement includes one-time Plotly warmup; steady-state
scatter peaks are 3–6 MB.

Reading: scatter time is flat in row count because sampling caps the
rendered points (`max_points=5000`, split across colour traces —
~1.3 k points per trace); aggregations scan every row but stay exact
and under 0.5 s at 200 k; output size is always bounded (≤ 61 bars,
40 bins).

## 12. Extending the engine

To add a chart:

1. Write a builder `build_x(req: ChartRequest) -> ChartArtifact` in the
   right module; validate with `require_columns` / `require_kinds`,
   disclose every bound via notes and `ph.finalize(..., disclosure=)`.
2. Register a `ChartSpec` in `chart_registry.CHARTS` (id, label,
   category, modes, needs, roles with kinds/required flags,
   description).
3. Add coverage in `tests/test_visualization_engine.py` (builder) and,
   if the page offers new widgets, in
   `tests/test_advanced_visualization_page.py`.

**Optional dependency note:** `tsne_scatter` is the only chart that
needs scikit-learn; it is imported lazily and raises a clear
*install scikit-learn* message when missing. scikit-learn is **not**
declared in `requirements.txt` (decision deferred to the Phase 7
review). PCA is plain numpy SVD and has no such dependency.
