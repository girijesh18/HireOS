#!/bin/sh
# Hourly online backup of the local HireOS DB (safe while the app writes).
# Keeps 48 hourly + 14 daily copies in ~/backups/hireos. Added after the 2026-10-24 wipe.
# Install:  crontab -e  →  7 * * * * "/home/gs/Documents/Deployed Apps/HireOS/scripts/backup_db.sh"
# Reports to HireOS's scheduler (backend/scheduler.py), which posts problems to Telegram:
#   backend/output/ops/backup.ok     touched on success (no touch for 2h → "backups not running")
#   backend/output/ops/backup.alert  one line appended per failure
set -eu
SRC="/home/gs/Documents/Deployed Apps/HireOS/backend/swarm.db"
DST="$HOME/backups/hireos"
OPS="$(dirname "$SRC")/output/ops"
mkdir -p "$DST/hourly" "$DST/daily" "$OPS"
fail() { echo "$(date '+%F %T') HireOS backup FAILED: $1" | tee -a "$OPS/backup.alert" "$DST/ALERT.txt" >&2; exit 1; }
trap 'fail "script error (line $LINENO)"' EXIT

NOW=$(date +%Y%m%d-%H%M)
sqlite3 "$SRC" ".backup '$DST/hourly/swarm-$NOW.db'" || fail "sqlite .backup of $SRC failed"
# A copy that lost most of its jobs means the source was damaged: keep everything, say so loudly.
JOBS=$(sqlite3 "$DST/hourly/swarm-$NOW.db" "select count(*) from jobs") || fail "backup copy unreadable"
PREV=$(ls -1 "$DST/daily" | tail -1)
if [ -n "$PREV" ]; then
  PJ=$(sqlite3 "$DST/daily/$PREV" "select count(*) from jobs")
  [ "$JOBS" -lt $((PJ / 2)) ] && fail "jobs dropped $PJ -> $JOBS since $PREV; not rotating old backups"
fi
if [ -z "$PREV" ] || [ "$(date +%H)" = "03" ]; then
  cp "$DST/hourly/swarm-$NOW.db" "$DST/daily/swarm-$(date +%Y%m%d).db"
fi
ls -1t "$DST/hourly"/swarm-*.db | tail -n +49 | xargs -r rm --
ls -1t "$DST/daily"/swarm-*.db | tail -n +15 | xargs -r rm --
trap - EXIT
touch "$OPS/backup.ok"
