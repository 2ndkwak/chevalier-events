"""
One-off migration: adds the second-address block and per-address home phone
columns to the persons table. Safe to run more than once -- it checks which
columns already exist before adding anything.

Run this once on the server, from the project root, with the venv active:
    python3 migrate_add_address2.py
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "instance", "chevalier.db")

NEW_COLUMNS = [
    ("home_phone_1",    "VARCHAR(50)"),
    ("address2_label",  "VARCHAR(100)"),
    ("home_phone_2",    "VARCHAR(50)"),
    ("address2_line1",  "VARCHAR(200)"),
    ("address2_line2",  "VARCHAR(200)"),
    ("city2",           "VARCHAR(100)"),
    ("province_state2", "VARCHAR(100)"),
    ("postal_code2",    "VARCHAR(20)"),
    ("country2",        "VARCHAR(100)"),
]

def main():
    if not os.path.exists(DB_PATH):
        print(f"✗ Database not found at {DB_PATH}")
        print("  Run this from the project root (same folder as run.py).")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(persons)")
    existing = {row[1] for row in cur.fetchall()}

    added = 0
    for col_name, col_type in NEW_COLUMNS:
        if col_name in existing:
            print(f"  - {col_name} already exists, skipping")
            continue
        cur.execute(f"ALTER TABLE persons ADD COLUMN {col_name} {col_type}")
        print(f"  + added {col_name} ({col_type})")
        added += 1

    conn.commit()
    conn.close()

    if added:
        print(f"\n✓ Migration complete -- {added} column(s) added.")
    else:
        print("\n✓ Already up to date -- nothing to do.")

if __name__ == "__main__":
    main()
