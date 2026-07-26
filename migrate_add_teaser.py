"""
One-off migration: adds the short "teaser" column to the events table
(used for the portal home page event list, separate from the full
description shown on the event detail page). Safe to run more than
once -- it checks whether the column already exists before adding it.

Run this once on the server, from the project root, with the venv active:
    python3 migrate_add_teaser.py
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "instance", "chevalier.db")

NEW_COLUMNS = [
    ("teaser", "VARCHAR(300)"),
]

def main():
    if not os.path.exists(DB_PATH):
        print(f"✗ Database not found at {DB_PATH}")
        print("  Run this from the project root (same folder as run.py).")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(events)")
    existing = {row[1] for row in cur.fetchall()}

    added = 0
    for col_name, col_type in NEW_COLUMNS:
        if col_name in existing:
            print(f"  - {col_name} already exists, skipping")
            continue
        cur.execute(f"ALTER TABLE events ADD COLUMN {col_name} {col_type}")
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
