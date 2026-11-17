# input_data/databases/

Drop zone for user-supplied **SQLite databases** (`.db`, `.sqlite`,
`.sqlite3`).

- Databases are opened strictly **read-only** (`file:...?mode=ro`); the platform
  can never modify them.
- Detection uses the SQLite content header, not only the file extension.
- Databases containing several tables need `--table NAME`.
- All files here are ignored by git - only this README is tracked.

Example:

```text
python run_pipeline.py --input input_data/databases/my_db.db --table my_table
```
