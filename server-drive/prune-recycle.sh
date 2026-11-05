#!/usr/bin/env bash
#
# Server Drive — prune the SMB recycle bin.
#
#   sudo ./prune-recycle.sh [days] [--dry-run]
#
# Files deleted through the Finder drive land in /.recycle/<user>/ instead of
# being unlinked. This removes the ones that have sat there longer than <days>
# (default 30). Run daily by server-drive-recycle-prune.timer.
#
# Age is time-since-deletion because smb.conf sets recycle:touch_mtime = yes.
#
set -euo pipefail

RECYCLE_ROOT="/.recycle"
DAYS="${1:-30}"
DRY_RUN=0
[ "${2:-}" = "--dry-run" ] && DRY_RUN=1
[ "${1:-}" = "--dry-run" ] && { DRY_RUN=1; DAYS=30; }

info() { printf '[recycle-prune] %s\n' "$1"; }

case "$DAYS" in
    ''|*[!0-9]*) info "ERROR: days must be a whole number, got '${DAYS}'"; exit 1 ;;
esac

if [ ! -d "$RECYCLE_ROOT" ]; then
    info "No recycle bin at ${RECYCLE_ROOT} — nothing to do."
    exit 0
fi

BEFORE_SIZE="$(du -sh "$RECYCLE_ROOT" 2>/dev/null | cut -f1)"
COUNT="$(find "$RECYCLE_ROOT" -type f -mtime "+${DAYS}" 2>/dev/null | wc -l)"

if [ "$DRY_RUN" -eq 1 ]; then
    info "DRY RUN — would delete ${COUNT} file(s) older than ${DAYS} days from ${RECYCLE_ROOT} (currently ${BEFORE_SIZE})"
    find "$RECYCLE_ROOT" -type f -mtime "+${DAYS}" 2>/dev/null | head -20
    exit 0
fi

find "$RECYCLE_ROOT" -type f -mtime "+${DAYS}" -delete 2>/dev/null || true
# Clear out directories left empty by the pass above.
find "$RECYCLE_ROOT" -mindepth 1 -type d -empty -delete 2>/dev/null || true

AFTER_SIZE="$(du -sh "$RECYCLE_ROOT" 2>/dev/null | cut -f1)"
info "Pruned ${COUNT} file(s) older than ${DAYS} days — ${BEFORE_SIZE} → ${AFTER_SIZE}"
