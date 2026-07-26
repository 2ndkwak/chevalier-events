"""
One-off migration: adds the columns needed for self-service password
reset (a "Forgot password?" link on the sign-in page). Safe to run
more than once -- it checks whether each column already exists before
adding it.

Run this once on the server, from the project root, with the venv active:
    python3 migrate_add_reset_token.py
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "instance", "chevalier.db")

NEW_COLUMNS = [
    ("reset_token",            "VARCHAR(64)"),
    ("reset_token_expires_at", "DATETIME"),
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

    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_persons_reset_token
        ON persons (reset_token)
    """)
    print("  + ensured unique index on reset_token")

    conn.commit()
    conn.close()

    if added:
        print(f"\n✓ Migration complete -- {added} column(s) added.")
    else:
        print("\n✓ Already up to date -- nothing to do.")

if __name__ == "__main__":
    main()
