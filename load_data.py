"""
Load Ookla CSV into data/data.duckdb.

Usage:
    python load_data.py path/to/ookla_data.csv
"""
import sys
import duckdb
from pathlib import Path

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "data.duckdb"
TABLE = "ookla_aggregated_data"

def main():
    if len(sys.argv) < 2:
        print("Usage: python load_data.py <path_to_csv>")
        sys.exit(1)

    csv_path = Path(sys.argv[1]).resolve()
    if not csv_path.exists():
        print(f"ERROR: File not found: {csv_path}")
        sys.exit(1)

    DB_PATH.parent.mkdir(exist_ok=True)

    if DB_PATH.exists():
        ans = input(f"{DB_PATH} already exists. Overwrite? [y/N] ")
        if ans.strip().lower() != "y":
            print("Aborted.")
            sys.exit(0)
        DB_PATH.unlink()

    print(f"Loading {csv_path} → {DB_PATH} as table '{TABLE}' ...")
    conn = duckdb.connect(str(DB_PATH))
    conn.execute(f"""
        CREATE TABLE "{TABLE}" AS
        SELECT * FROM read_csv('{str(csv_path).replace("'", "''")}',
                               header=true, auto_detect=true, ignore_errors=true)
    """)
    count = conn.execute(f'SELECT COUNT(*) FROM "{TABLE}"').fetchone()[0]
    cols  = conn.execute(f'DESCRIBE "{TABLE}"').fetchall()
    conn.close()

    print(f"Done. {count:,} rows, {len(cols)} columns loaded into '{TABLE}'.")

if __name__ == "__main__":
    main()
