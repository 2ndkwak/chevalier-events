#!/bin/bash
# Daily backup of the Chevalier Events database.
#
# Copies the live SQLite database to /var/backups/chevalier/ with a
# timestamped filename, then removes backups older than 30 days so this
# doesn't grow disk usage forever. Intended to run once a day via the
# chevalier-backup.timer systemd timer -- see chevalier-backup.service
# and chevalier-backup.timer alongside this script.
#
# Safe to also run by hand any time:
#     sudo bash /var/www/chevalier/backup_database.sh

set -e

SRC="/var/www/chevalier/instance/chevalier.db"
DEST_DIR="/var/backups/chevalier"
RETENTION_DAYS=30
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DEST="$DEST_DIR/chevalier_$TIMESTAMP.db"

mkdir -p "$DEST_DIR"

if [ ! -f "$SRC" ]; then
    echo "ERROR: database not found at $SRC" >&2
    exit 1
fi

cp "$SRC" "$DEST"
echo "Backed up $SRC -> $DEST"

# Prune anything older than the retention window
find "$DEST_DIR" -name "chevalier_*.db" -type f -mtime "+$RETENTION_DAYS" -print -delete

echo "Current backups in $DEST_DIR:"
ls -la "$DEST_DIR"
