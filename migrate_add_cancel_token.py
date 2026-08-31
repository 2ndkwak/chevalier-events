"""
One-off migration: adds the columns needed for the "cancel my partner's
reservation too" self-service email link, sent when a member cancels and
their partner has a separate (unlinked) RSVP for the same event. Safe to
run more than once -- it checks whether each column already exists
before adding it.

Run this once on the server, from the project root, with the venv active:
    python3 migrate_add_cancel_token.py
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "instance", "chevalier.db")

NEW_COLUMNS = [
    ("cancel_token",            "VARCHAR(64)"),
    ("cancel_token_expires_at", "DATETIME"),
]

def main():
    if not os.path.exists(DB_PATH):
        print(f"✗ Database not found at {DB_PATH}")
        print("  Run this from the project root (same folder as run.py).")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(rsvps)")
    existing = {row[1] for row in cur.fetchall()}

    added = 0
    for col_name, col_type in NEW_COLUMNS:
        if col_name in existing:
            print(f"  - {col_name} already exists, skipping")
            continue
        cur.execute(f"ALTER TABLE rsvps ADD COLUMN {col_name} {col_type}")
        print(f"  + added {col_name} ({col_type})")
        added += 1

    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_rsvps_cancel_token
        ON rsvps (cancel_token)
    """)
    print("  + ensured unique index on cancel_token")

    conn.commit()
    conn.close()

    if added:
        print(f"\n✓ Migration complete -- {added} column(s) added.")
    else:
        print("\n✓ Already up to date -- nothing to do.")

if __name__ == "__main__":
    main()
