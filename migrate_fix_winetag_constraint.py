"""
One-off migration: fixes the wine_tags unique constraint.

Position now means "this wine's order within its own course" and resets
to 1 for each new course, instead of counting up once across the whole
event. The old constraint enforced uniqueness on (event_id, position)
alone, which rejects perfectly valid data under the new scheme (e.g. the
1st wine of course 1 and the 1st wine of course 2 both being position 1).
The correct constraint is (event_id, course, position).

SQLite can't alter a constraint in place, so this rebuilds the table:
rename old -> create new (matching the current model) -> copy rows ->
drop old. Safe to run more than once -- it checks the existing
constraint before doing anything.

Run this once on the server, from the project root, with the venv active:
    python3 migrate_fix_winetag_constraint.py

IMPORTANT: after this runs, any event that already had a wine list
uploaded under the old scheme will show incorrect "position/course"
labels on the printed tags (the numbers will look off) until that
event's CSV is re-uploaded with position correctly reset per course.
The migration does not attempt to guess the right renumbering for you --
re-upload is the safe way to fix existing data.
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

    cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='wine_tags'")
    row = cur.fetchone()
    if not row:
        print("  - wine_tags table doesn't exist yet, nothing to migrate")
        conn.close()
        return

    current_sql = row[0]
    if "uq_wine_position_per_course" in current_sql:
        print("✓ Already migrated -- constraint is already (event_id, course, position).")
        conn.close()
        return

    print("Rebuilding wine_tags with the corrected constraint...")

    cur.executescript("""
        BEGIN TRANSACTION;

        ALTER TABLE wine_tags RENAME TO wine_tags_old;

        CREATE TABLE wine_tags (
            id          INTEGER NOT NULL PRIMARY KEY,
            event_id    INTEGER NOT NULL,
            position    INTEGER NOT NULL,
            course      INTEGER NOT NULL DEFAULT 1,
            vintage     VARCHAR(20),
            domain      VARCHAR(200) NOT NULL,
            appellation VARCHAR(300) NOT NULL,
            created_at  DATETIME,
            updated_at  DATETIME,
            FOREIGN KEY(event_id) REFERENCES events (id),
            CONSTRAINT uq_wine_position_per_course UNIQUE (event_id, course, position)
        );

        INSERT INTO wine_tags (id, event_id, position, course, vintage, domain,
                                appellation, created_at, updated_at)
        SELECT id, event_id, position, course, vintage, domain,
               appellation, created_at, updated_at
        FROM wine_tags_old;

        DROP TABLE wine_tags_old;

        COMMIT;
    """)

    conn.commit()

    cur.execute("SELECT COUNT(*) FROM wine_tags")
    count = cur.fetchone()[0]
    conn.close()

    print(f"✓ Migration complete -- {count} existing wine row(s) carried over.")
    print("  Reminder: re-upload the wine list CSV for any event that had one")
    print("  before this migration, so position numbering resets per course.")


if __name__ == "__main__":
    main()
