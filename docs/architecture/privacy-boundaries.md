# Diagram E — Data privacy and local-processing boundaries

```mermaid
flowchart LR
    subgraph OUTSIDE["Outside this machine"]
        CLOUD["Cloud services, APIs, telemetry<br/>NOT USED - application code<br/>makes no network calls"]
    end

    subgraph MACHINE["Your Windows machine (everything happens here)"]
        subgraph SOURCES["Sources - read-only"]
            FOLDERS["Folder trees<br/>(metadata only: name, size,<br/>timestamps, path)"]
            CSVS["CSV files<br/>(bounded nrows/usecols reads)"]
            SITES["SQLite databases<br/>opened file:...?mode=ro -<br/>writes refused by SQLite itself"]
        end

        subgraph PROTECTED["PROTECTED research area - never written by the platform"]
            RDATA["data/ raw, processed, database,<br/>snapshots + evidence folders"]
            RREP["reports/ research outputs<br/>+ historical phase reports"]
            RBACK["backups/, logs/"]
            GUARD["ProcessingContext guard +<br/>22-file SHA-256 integrity gate"]
            GUARD --- RDATA
            GUARD --- RREP
            GUARD --- RBACK
        end

        subgraph APPRUN["Application (local process)"]
            STREAM["Streamlit dashboard<br/>localhost:8501 - local browser only"]
            CLIS["pipeline.py / run_pipeline.py<br/>explicit flags only"]
            ENGINES["analysis + comparison + visualization<br/>bounded, seeded, disclosed"]
        end

        subgraph OWNED["App-owned outputs - yours to delete"]
            OUTPUT["output/ datasets, metadata,<br/>analysis, comparisons, visualizations"]
            INPUTZ["input_data/ drop zones"]
            TEMP["OS temp files (tests, previews)<br/>cleaned up by the tests"]
        end
    end

    FOLDERS --> ENGINES
    CSVS --> ENGINES
    SITES --> ENGINES
    ENGINES --> OUTPUT
    STREAM --> ENGINES
    CLIS --> ENGINES
    STREAM -. "reads research data READ-ONLY for the<br/>8 research pages" .-> RDATA
    OUTPUT -.-> OWNED

    CLOUD -. "no connection exists" .- ENGINES
```

Boundary rules (from code inspection, 2026-11-23):

1. **Local processing only** — no network, telemetry, cloud upload or
   model/API calls with user data anywhere in the application code.
   (The one network operation in the project is `pip install` during
   setup, which downloads packages — never data.)
2. **No automatic full-drive scan** — `pipeline.py` without flags prints
   usage; drive roots need `--yes`; the dashboard never scans.
3. **Metadata only for folders** — file contents are never opened by the
   scanner; input files are never executed or imported.
4. **Read-only inputs** — CSVs read with bounds; SQLite opened with
   `mode=ro` so writes are refused at the engine level.
5. **Research preservation** — protected trees are guarded by
   `ProcessingContext` and re-verified with the 22-file integrity gate.
6. **Registry stores descriptors, not contents** — row data never enters
   `dataset_registry.db`.
7. **Error redaction** — user-facing errors print file names only;
   absolute paths stay in local logs.

Full details: [../PRIVACY_AND_DATA_PROTECTION.md](../PRIVACY_AND_DATA_PROTECTION.md).
