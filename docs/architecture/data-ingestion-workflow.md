# Diagram B — Data ingestion and analysis workflow

Two branches share the same packages: the **research branch** (Mode A,
metadata scan of a folder tree) and the **platform branch** (Mode B,
user-supplied datasets). Both are local and read-only towards their
sources.

```mermaid
flowchart TB
    subgraph RESEARCH["Research branch (Mode A)"]
        RROOT["Folder / drive root<br/>(explicit --scan-root or DA_SCAN_ROOT)"]
        RSCAN["scanner.filesystem_scanner<br/>metadata-only, single pass,<br/>resume + exclusions"]
        RRAW["data/raw/<br/>files_metadata.csv,<br/>folders_metadata.csv, scan_errors.csv"]
        RANA["analysis/ research modules<br/>cleaning, temporal, storage, filetype,<br/>naming, structural, duplicates"]
        RPROC["data/processed/<br/>files_processed.csv, naming_analysis.csv,<br/>duplicate_candidates.csv, ..."]
        RFIND["reports/findings.json<br/>+ research report"]
        RDB["data/database/digital_archaeology.db<br/>(manual rebuild utility only)"]
        RSNAP["data/snapshots/<br/>snapshot index (JSON)"]

        RROOT --> RSCAN --> RRAW --> RANA --> RPROC --> RFIND
        RRAW -. "manual utility" .-> RDB
        RSCAN --> RSNAP
    end

    subgraph PLATFORM["Platform branch (Mode B)"]
        SRC["User source: CSV file,<br/>SQLite database, or folder"]
        DET["ingestion.input_detector<br/>auto / forced source type"]
        VAL["ingestion.validators<br/>ok | warning | error"]
        LOAD["ingestion loaders (read-only)<br/>chunked bounded preview"]
        DST["core.Dataset<br/>uuid5 id + fingerprint"]
        REG["output/metadata/dataset_registry.db<br/>(descriptors only, never row contents)"]

        SRC --> DET --> VAL --> LOAD --> DST --> REG
    end

    subgraph ANALYSIS["Analysis engine"]
        RUN["analysis.analysis_runner<br/>mode: full | sample | streaming"]
        OUTA["output/analysis/<br/>statistics, distributions, correlation,<br/>quality, outliers, run summary"]
        RUN --> OUTA
    end

    subgraph CMP["Comparison engine"]
        CMPIN["Two registered datasets<br/>or two stored snapshots"]
        CMPOUT["output/comparisons/<br/>summary, schema, statistics,<br/>records files"]
        CMPIN --> CMPOUT
    end

    subgraph VIZ["Visualization engine"]
        GEN["visualization.build_chart<br/>(Generate button - nothing earlier)"]
        FIG["Plotly figure + provenance,<br/>bounded by disclosed limits"]
        EXP["output/visualizations/<br/>HTML / CSV export bundles"]
        GEN --> FIG --> EXP
    end

    REG --> RUN
    OUTA --> GEN
    REG --> CMPIN
    RSNAP --> CMPIN
    RRAW --> GEN
    CMPOUT --> GEN
    REG --> GEN
```

Safety facts reflected in the diagram (verified in code):

- Every source opens **read-only** (CSV with bounded `nrows`/`usecols`;
  SQLite via `file:...?mode=ro`).
- Nothing runs until an explicit button/CLI flag; no implicit scans.
- Analysis and comparison outputs go exclusively under `output/`;
  research outputs stay under the protected `data/` + `reports/` trees.
- Chart generation is bounded (`VisualizationConfig` clamps) and
  discloses sampling/caps on the figure and in exports.
