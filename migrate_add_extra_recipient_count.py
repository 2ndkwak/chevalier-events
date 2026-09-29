"""
One-off migration: adds the column needed to fix the Email History
"targeted but not yet sent" false alarm for sends that include
manually-entered addresses (Sep 2026). Safe to run more than once --
checks whether the column already exists before adding it.

Run this once on the server, from the project root, with the venv active:
    python3 migrate_add_extra_recipient_count.py
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "instance", "chevalier.db")

def main():
    if not os.path.exists(DB_PATH):
        print(f"\u2717 Database not found at {DB_PATH}")
        print("  Run this from the project root (same folder as run.py).")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(adhoc_emails)")
    existing = {row[1] for row in cur.fetchall()}

    if "extra_recipient_count" in existing:
        print("  - extra_recipient_count already exists, skipping")
    else:
        cur.execute("ALTER TABLE adhoc_emails ADD COLUMN extra_recipient_count "
                     "INTEGER NOT NULL DEFAULT 0")
        print("  + added extra_recipient_count (INTEGER, default 0)")

    conn.commit()
    conn.close()
    print("\n\u2713 Migration complete.")

if __name__ == "__main__":
    main()
