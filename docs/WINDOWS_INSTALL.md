# Windows Installation and Configuration Guide

Step-by-step guide to install, configure, launch and troubleshoot the
Digital Archaeology platform on a Windows computer.

**Requirements**

- Windows 10/11
- Python **3.11 or newer** (tested with 3.11.9) —
  <https://www.python.org/downloads/> (tick *Add python.exe to PATH*)
- ~1 GB free disk space (dependencies + optional local data)
- Internet access for the one-time dependency download

---

## 1. Install (scripted — recommended)

Open a Command Prompt **in the project folder** and run:

```bat
scripts\install_windows.bat
```

What it does, in order:

1. Checks that Python exists and is 3.11+ (fails with instructions if not).
2. Creates `.venv` **only if it does not exist** — an existing virtual
   environment is never deleted or replaced.
3. Installs exactly the four declared dependencies from `requirements.txt`
   (`pandas`, `numpy`, `streamlit`, `plotly`). No other packages, no
   automatic upgrades of unrelated packages.
4. Never touches `data\`, `reports\`, `logs\` or `backups\`.

Then verify everything:

```bat
scripts\check_environment.bat
```

Expected final line: `All required checks passed.` (exit code 0).

## 2. Install (manual equivalent)

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe scripts\check_environment.py
```

## 3. Launch

```bat
scripts\run_windows.bat
```

- Opens Streamlit at **http://localhost:8501** (the URL is printed).
- The terminal keeps the application output visible — leave the window open;
  close it or press `Ctrl+C` to stop.
- The launch script only starts the dashboard. It never scans folders or
  drives.
- First launch with no scan data shows a **First-time setup** panel with the
  exact commands to run.

### First scan (optional, research workflow)

```bat
.venv\Scripts\python.exe pipeline.py --scan --scan-root "C:\path\you\want\to\scan"
```

- Metadata only; file contents are never read.
- A whole-drive root additionally requires `--yes`.
- The default scan target when neither flag nor `DA_SCAN_ROOT` is set is
  `D:\` (the original research machine's drive) — on other computers always
  pass `--scan-root`.

### Analyze your own CSV / SQLite / folder (platform workflow)

```bat
.venv\Scripts\python.exe run_pipeline.py --input "C:\data\myfile.csv"
.venv\Scripts\python.exe run_pipeline.py --input "C:\data\myfolder"
```

Inputs can also be dropped into `input_data\csv`, `input_data\databases` or
`input_data\metadata` and picked up on the **Data Sources** page.

## 4. Workspace configuration

| Variable | Purpose | Default |
|---|---|---|
| `DIGITAL_ARCHAEOLOGY_HOME` | Workspace root: where `data\`, `reports\`, `logs\`, `output\`, `input_data\` are resolved | project folder (the folder containing `app.py`) |
| `DA_SCAN_ROOT` | Default target of the research scan CLI | `D:\` |

Set per session:

```bat
set DIGITAL_ARCHAEOLOGY_HOME=D:\MyArchaeologyWorkspace
scripts\run_windows.bat
```

or permanently (System Properties → Environment Variables).

Rules:

- The override **never** moves, copies or deletes existing research data.
- If an override points at an empty location while research data exists in
  the original project folder, the sidebar shows a warning and the original
  data stays untouched.
- The active workspace is always displayed in the application sidebar.
- Missing workspace directories are created automatically and safely
  (nothing is ever overwritten; the research database is never recreated by
  initialization).

## 5. Directory map

| Location | Owner | Notes |
|---|---|---|
| `data\` (raw, processed, snapshots, database) | research | Protected. Never written by tests or platform init |
| `reports\`, `logs\` | research/runtime | Protected research outputs; `logs\run.log` is the app log |
| `output\`, `input_data\` | platform | Created on demand; safe to delete (except registered dataset outputs you need) |
| `backups\`, evidence folders | research | Never modified |
| OS temp (`%TEMP%`) | tests/preview scans | Temporary files are created here and cleaned up by the tests |

## 6. Tests (optional, for developers)

`pytest` is a development-only dependency and is deliberately **not** in
`requirements.txt`. To run the test suite:

```bat
.venv\Scripts\python.exe -m pip install pytest
.venv\Scripts\python.exe -m pytest tests -q
```

All test data is synthetic and written to OS temporary directories; the
suite runs without any research data present (2 tests skip when
scikit-learn is absent, 1 skips where the OS forbids symlink creation).

## 7. Optional: scikit-learn (t-SNE)

scikit-learn is **optional**. Without it:

- PCA charts keep working (pure NumPy).
- t-SNE shows: *"t-SNE requires scikit-learn, which is not installed in
  this environment. Use the PCA chart (numpy-based) instead, or install
  scikit-learn to enable t-SNE."*

To enable t-SNE:

```bat
.venv\Scripts\python.exe -m pip install scikit-learn
```

## 8. Troubleshooting

### Common installation errors

| When | Message you will see | Fix |
|---|---|---|
| Running the installer | `[ERROR] Python not found on PATH.` | Install Python 3.11+ from python.org and tick *Add python.exe to PATH*; open a **new** terminal and re-run `scripts\install_windows.bat` |
| Running the installer | `[ERROR] Python 3.11 or newer is required - tested with 3.11.9.` | Upgrade Python; re-run the script (nothing was changed by the failed attempt) |
| Running the installer | `[ERROR] could not create the virtual environment.` | Check free disk space and folder permissions on the project folder, then re-run |
| Running the installer | `[ERROR] dependency installation failed.` | Check internet access and re-run; the existing `.venv` and all data files are left untouched |
| Re-running the installer | `[OK] existing virtual environment found - keeping it unchanged.` | Normal — the script never deletes an existing `.venv`; it only (re)installs declared packages |
| Verifying | `[WARN] virtual environment not found` | Run the check from the project folder after `scripts\install_windows.bat`, or activate `.venv` first |

### Runtime and data issues

| Symptom | Fix |
|---|---|
| `'python' is not recognized` | Install Python 3.11+ and tick *Add python.exe to PATH*, then open a new terminal |
| `[ERROR] Python 3.11 or newer is required` | Upgrade Python from python.org |
| `virtual environment missing` | Run `scripts\install_windows.bat` |
| `[MISSING] required package ...` | Run `scripts\install_windows.bat` (checks network if pip fails) |
| `No scan data found` | Expected on a fresh install — run a scan (§3) or use the Data Sources page |
| Dashboard does not open | Confirm the URL printed by the script; check the terminal for Streamlit errors; port 8501 busy → close the other instance |
| `Cannot create required directory` | The workspace path is not writable — pick another `DIGITAL_ARCHAEOLOGY_HOME` |
| Workspace shows a warning about data not loaded | An override is active but data lives in the original folder — unset `DIGITAL_ARCHAEOLOGY_HOME` |
| Scan says root not accessible | Pass `--scan-root PATH` for a folder that exists on this machine |
| Whole-drive scan refused | Add `--yes` — deliberate safety gate |
| Very long paths (>260 chars) during scans | Those entries are recorded as per-path errors; shorten the tree depth or move the data |

## 9. Uninstall / move

- To **move** the project: copy the whole folder (including `.venv` if you
  want to skip reinstall) to the new location and re-run
  `scripts\check_environment.bat`. Paths are resolved from the code location
  — no configuration change is needed.
- To **remove** the application: delete the project folder. Research data
  lives inside it (`data\`, `reports\`, `backups\`) — copy those first if you
  want to keep them. Nothing is stored elsewhere on your system except
  ordinary OS temp files, which tests clean up.
