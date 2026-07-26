"""
One-off migration: adds the linked_rsvp_id column, which lets two
separate RSVP rows (e.g. a couple added via the admin "Add RSVP" tool,
where each partner has their own row) be tracked and resolved together
when promoted off the waitlist. Safe to run more than once.

Run this once on the server, from the project root, with the venv active:
    python3 migrate_add_linked_rsvp.py
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "instance", "chevalier.db")

def main():
    if not os.path.exists(DB_PATH):
        print(f"✗ Database not found at {DB_PATH}")
        print("  Run this from the project root (same folder as run.py).")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(rsvps)")
    existing = {row[1] for row in cur.fetchall()}

    if "linked_rsvp_id" in existing:
        print("  - linked_rsvp_id already exists, skipping")
    else:
        cur.execute("ALTER TABLE rsvps ADD COLUMN linked_rsvp_id INTEGER")
        print("  + added linked_rsvp_id (INTEGER)")

    conn.commit()
    conn.close()
    print("\n✓ Migration complete.")

if __name__ == "__main__":
    main()
