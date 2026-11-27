# Diagram A — High-level architecture

```mermaid
flowchart TB
    subgraph UI["Dashboard (Streamlit)"]
        APP["app.py - 12 pages in one radio navigation"]
        SUB["Sidebar: workspace status, filters, page radio,<br/>mode help, snapshot panel, privacy caption"]
        APP --- SUB
    end

    subgraph CLI["Command line"]
        PIPE["pipeline.py<br/>research scan / analysis (explicit --scan)"]
        RPIPE["run_pipeline.py<br/>detect -> inspect -> register"]
    end

    subgraph PKG["Platform packages"]
        DASH["dashboard/<br/>12 page modules + common.py"]
        ING["ingestion/<br/>detector, validators, loaders, registry"]
        ANA["analysis/<br/>research modules, dataset engine,<br/>comparison/"]
        VIS["visualization/<br/>57 chart specs, builders, export"]
        SCAN["scanner/<br/>metadata-only scanner, snapshots,<br/>duplicate detector"]
        CORE["core/<br/>Dataset model, exceptions,<br/>processing context (write guard)"]
    end

    subgraph STORES["Local storage (workspace)"]
        RESEARCH["Research data - PROTECTED<br/>data/raw, data/processed, data/database,<br/>data/snapshots, reports/"]
        PLATFORM["Platform output<br/>output/datasets, output/metadata,<br/>output/analysis, output/comparisons,<br/>output/visualizations"]
        INPUTD["input_data/ drop zones"]
        LOGS["logs/"]
    end

    CFG["config.py<br/>PROJECT_ROOT, WORKSPACE_ROOT (DIGITAL_ARCHAEOLOGY_HOME),<br/>SCAN_ROOT (DA_SCAN_ROOT), protected paths"]

    APP --> DASH
    DASH --> ANA
    DASH --> VIS
    DASH --> ING
    DASH --> SCAN
    PIPE --> SCAN
    PIPE --> ANA
    RPIPE --> ING
    ING --> CORE
    ANA --> CORE
    VIS --> CORE
    SCAN --> CORE
    CORE --> RESEARCH
    ING --> PLATFORM
    ANA --> PLATFORM
    VIS --> PLATFORM
    ING --> INPUTD
    CORE --> LOGS
    CFG -. "resolves every path" .-> STORES
```

Notes (verified 2026-11-23):

- Single-process, fully local: no service, queue, API server or cloud
  component exists in the codebase.
- `core.processing_context.ProcessingContext` enforces the protected-path
  write guard used by every platform writer.
- The research SQLite archive under `data/database/` is protected and is
  never written by application or CLI code; rebuilding it from CSVs was a
  one-off manual research operation whose utility is deliberately
  excluded from the public repository (dangerous, machine-specific).
