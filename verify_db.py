"""Read-only verification of the SQLite database (original scan archive)."""
import sqlite3
import config

c = sqlite3.connect(str(config.DATABASE_PATH))
print("=== SQLite tables (built from ORIGINAL 617k scan at 00:32) ===")
for (t,) in c.execute("select name from sqlite_master where type='table' order by name"):
    n = c.execute("select count(*) from [" + t + "]").fetchone()[0]
    print("  %-25s %8d" % (t, n))

print()
print("files sum bytes  :", c.execute("select sum(file_size_bytes) from files").fetchone()[0])
print("files distinct p :", c.execute("select count(distinct full_file_path) from files").fetchone()[0])
print("files cols       :", len(c.execute("select * from files limit 1").description))
print("sample rows      :", c.execute(
    "select file_id, file_name, root_folder, folder_depth, file_size_bytes "
    "from files limit 3").fetchall())
print("folders sum bytes:", c.execute("select sum(total_size_bytes) from folders").fetchone()[0])
print("folder cols      :", [d[0] for d in c.execute("select * from folders limit 1").description])
print("errors cols      :", [d[0] for d in c.execute("select * from scan_errors limit 1").description])
print("dup cols         :", [d[0] for d in c.execute("select * from duplicate_candidates limit 1").description])
print("naming cols      :", [d[0] for d in c.execute("select * from naming_analysis limit 1").description])
# any non-integer file_size_bytes?
bad = c.execute("select count(*) from files where file_size_bytes not like '%.%' "
                "and file_size_bytes GLOB '*[^0-9]*'").fetchone()[0]
print("non-numeric size values in DB:", bad)
c.close()
