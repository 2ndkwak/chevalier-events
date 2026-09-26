#!/bin/bash
# Safe export of the Chevalier Events codebase for sharing/backup/review.
# Always excludes secrets and local cruft -- this exists specifically so
# that instance/config.py (real production secrets) can never accidentally
# end up in an export again, the way it did on Sep 24, 2026.
#
# Usage (from /var/www/chevalier):
#   ./make_export_tar.sh
#
# Produces: /tmp/chevalier-export-YYYYMMDD_HHMMSS.tar.gz

set -euo pipefail

OUT="/tmp/chevalier-export-$(date +%Y%m%d_%H%M%S).tar.gz"

tar \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.git' \
  --exclude='venv' \
  --exclude='node_modules' \
  --exclude='backups' \
  --exclude='instance/*.db' \
  --exclude='instance/config.py' \
  --exclude='instance/uploads' \
  -czf "$OUT" .

echo "Wrote $OUT"
echo ""
echo "Contents check -- confirming nothing sensitive is in there:"
if tar -tzf "$OUT" | grep -q "instance/config.py"; then
  echo "  !! WARNING: instance/config.py is present in this archive !!"
  echo "  Do not share this file -- something is wrong with the excludes above."
  exit 1
else
  echo "  OK -- instance/config.py is not present."
fi
