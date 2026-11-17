# input_data/csv/

Drop zone for user-supplied **CSV files** to inspect with the platform.

- Files placed here are read on demand (`run_pipeline.py --input ...`); nothing
  is executed and nothing is copied out of this folder.
- A header row is required (duplicate column names are rejected).
- All files in this directory are ignored by git - only this README is tracked.
- Do not place research data from `data/` here.

Example:

```text
python run_pipeline.py --input input_data/csv/my_table.csv
```
