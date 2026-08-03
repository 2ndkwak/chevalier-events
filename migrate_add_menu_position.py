"""
One-off migration: adds a `position` column to menu_items, and changes the
unique constraint so course 0 (Cocktails) is allowed to hold more than one
row -- e.g. several separate hors d'oeuvres items -- while every other
course still gets exactly one dish, same as always.

The old constraint was a plain UNIQUE(event_id, course), which rejects a
second row for ANY course, including course 0. The new one is a partial
index -- UNIQUE(event_id, course) WHERE course != 0 -- so the "only one
dish per course" rule still holds everywhere except course 0, enforced by
the database itself, not just application code.

SQLite can't alter a constraint in place, so this rebuilds the table:
rename old -> create new (matching the current model) -> copy rows ->
drop old. Safe to run more than once -- it checks the existing schema
before doing anything.

Run this once on the server, from the project root, with the venv active:
    python3 migrate_add_menu_position.py

Existing rows are unaffected -- every current menu_items row gets
position=1, which is exactly right for courses that only ever had one row
anyway. Nothing needs to be re-uploaded.
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

    cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='menu_items'")
    row = cur.fetchone()
    if not row:
        print("  - menu_items table doesn't exist yet, nothing to migrate")
        conn.close()
        return

    current_sql = row[0]
    cur.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name='uq_menu_item_per_course'")
    index_row = cur.fetchone()
    already_migrated = (
        "position" in current_sql
        and index_row is not None
        and "WHERE course != 0" in (index_row[0] or "")
    )
    if already_migrated:
        print("✓ Already migrated -- position column and partial index both present.")
        conn.close()
        return

    print("Rebuilding menu_items with the position column and corrected constraint...")

    cur.executescript("""
        BEGIN TRANSACTION;

        ALTER TABLE menu_items RENAME TO menu_items_old;

        CREATE TABLE menu_items (
            id           INTEGER NOT NULL PRIMARY KEY,
            event_id     INTEGER NOT NULL,
            course       INTEGER NOT NULL DEFAULT 1,
            dish_french  TEXT,
            dish_english TEXT,
            position     INTEGER DEFAULT 1,
            created_at   DATETIME,
            updated_at   DATETIME,
            FOREIGN KEY(event_id) REFERENCES events (id)
        );

        CREATE UNIQUE INDEX uq_menu_item_per_course
            ON menu_items (event_id, course)
            WHERE course != 0;

        INSERT INTO menu_items (id, event_id, course, dish_french, dish_english,
                                 position, created_at, updated_at)
        SELECT id, event_id, course, dish_french, dish_english,
               1, created_at, updated_at
        FROM menu_items_old;

        DROP TABLE menu_items_old;

        COMMIT;
    """)

    conn.commit()

    cur.execute("SELECT COUNT(*) FROM menu_items")
    count = cur.fetchone()[0]
    conn.close()

    print(f"✓ Migration complete -- {count} existing menu row(s) carried over, all at position=1.")


if __name__ == "__main__":
    main()
