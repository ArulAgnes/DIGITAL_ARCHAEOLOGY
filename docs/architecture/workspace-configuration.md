# Diagram D — Workspace and configuration flow

```mermaid
flowchart TB
    ENV["Environment variable<br/>DIGITAL_ARCHAEOLOGY_HOME"]
    Q{"Set and non-empty?<br/>(surrounding quotes tolerated)"}
    PROJ["PROJECT_ROOT<br/>folder containing config.py / app.py<br/>(the code location)"]
    WS["WORKSPACE_ROOT<br/>resolved + expanduser"]
    ENVACTIVE["WORKSPACE_ENV_ACTIVE<br/>true only when override differs"]

    DDIRS["data/ (raw, processed, snapshots, database)<br/>reports/, logs/"]
    PDIRS["input_data/, output/<br/>(datasets, metadata, analysis,<br/>comparisons, visualizations, reports)"]
    TDIRS["tests/ - always stays with the code"]
    NOTICE["workspace_notice()<br/>warning shown in the sidebar when an override<br/>would hide existing research data"]

    SCANENV["Environment variable<br/>DA_SCAN_ROOT or flag --scan-root PATH"]
    SCAN["SCAN_ROOT - default: drive D root<br/>(research scan target ONLY;<br/>used by explicit scans, never automatically)"]
    EXCL["WORKSPACE_EXCLUSION<br/>the active workspace is never scanned"]

    subgraph SAFETY["Import-time safety (config.py)"]
        MK["mkdir(parents=True, exist_ok=True)<br/>creates only what is missing"]
        ERR["OSError with actionable message<br/>names the path, workspace and remedy"]
        MK --> ERR
        NODB["Database file is NEVER created,<br/>rewritten or deleted at import"]
    end

    ENV --> Q
    Q -->|no| PROJ
    Q -->|yes| WS
    PROJ --> WS
    WS --> ENVACTIVE
    WS --> DDIRS
    WS --> PDIRS
    PROJ --> TDIRS
    WS --> NOTICE
    PROJ -. "research data present here?" .-> NOTICE
    SCANENV --> SCAN
    WS --> EXCL
    WS --> MK
    WS --> NODB
```

Rules (verified in `config.py`, 2026-11-23):

- **Default (variable unset):** `WORKSPACE_ROOT == PROJECT_ROOT` — behavior
  is byte-identical to Phases 0–7.
- The override **never** moves, copies or deletes anything; it only
  re-resolves where the application looks.
- If the override points at an empty workspace while research data exists
  under the project root, the sidebar shows an explicit warning — the
  application never silently switches away from existing data.
- `SCAN_ROOT` is a *scan target*, not the workspace: it only matters when
  you run an explicit scan, and drive roots additionally require `--yes`.

See also: [../WINDOWS_INSTALL.md](../WINDOWS_INSTALL.md) §4 (how to set the
variables) and [../PRIVACY_AND_DATA_PROTECTION.md](../PRIVACY_AND_DATA_PROTECTION.md).
