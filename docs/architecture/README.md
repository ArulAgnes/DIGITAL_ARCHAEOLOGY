# Architecture documentation and diagrams

Reusable, GitHub-rendered Mermaid diagrams for the Digital Archaeology
platform. Every diagram was drawn from the actual implementation
(`app.py`, `config.py`, `pipeline.py`, `run_pipeline.py`, package
imports) and verified against the code on 2026-11-23.

| # | Document | Diagram |
|---|---|---|
| A | [high-level-architecture.md](high-level-architecture.md) | System layers, packages and data stores |
| B | [data-ingestion-workflow.md](data-ingestion-workflow.md) | Dataset ingestion → analysis → outputs (plus the research scan branch) |
| C | [operating-modes.md](operating-modes.md) | The four operating modes and their relationships (includes the full mode reference) |
| D | [workspace-configuration.md](workspace-configuration.md) | `PROJECT_ROOT` vs `WORKSPACE_ROOT`, environment variables, derived paths |
| E | [privacy-boundaries.md](privacy-boundaries.md) | Local-processing boundaries: what is read, written, and never done |

Viewing: GitHub renders Mermaid blocks natively. Locally, any Mermaid
viewer (e.g. VS Code Markdown preview with a Mermaid extension) works.

Related guides: [../ARCHITECTURE.md](../ARCHITECTURE.md) (Phase 3-era
package reference, kept for history), [../PROJECT_STRUCTURE.md](../PROJECT_STRUCTURE.md)
(folder map), [../PRIVACY_AND_DATA_PROTECTION.md](../PRIVACY_AND_DATA_PROTECTION.md).
