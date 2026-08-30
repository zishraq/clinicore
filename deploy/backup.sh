#!/usr/bin/env bash
#
# Nightly backup: the database and the uploaded photographs, encrypted before
# either leaves this box, rotated, and copied off-site.
#
# Both halves, every night. Since photographs landed, a database dump on its own
# restores a clinic whose visits are all intact and whose every photograph is
# missing — and nothing errors, because the rows are fine. See
# docs/adr/0014-encounter-photos-served-through-a-view.md.
#
# Encryption is `age` with a *recipient public key*, not a passphrase. Only the
# public key is on this server, so an attacker who takes the box still cannot
# read a single backup it produced. The private key lives off-box and is the one
# thing whose loss is unrecoverable — docs/RUNBOOK.md says so at the top of the
# restore section.
#
# Run by clinicore-backup.timer. Safe to run by hand at any time.

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

STARTED="$(now_iso)"
STAMP="$(date '+%Y-%m-%d_%H%M%S')"
DAY_OF_MONTH="$(date '+%d')"

disk_free_bytes() {
    # Free space where the backups are written. Reported here rather than read
    # by the app: the app is in a container and can only see the filesystem the
    # status directory happens to be mounted from, which is not necessarily this
    # one. 0 when it cannot be measured, which the app renders as "not reported"
    # rather than as an empty disk.
    # Always a number: an empty substitution here would write
    # `"disk_free_bytes": ,` and turn the whole status file into something the
    # app reads as "no information", i.e. an alarm about the wrong thing.
    local free
    free="$(df -PB1 "${BACKUP_DIR:-/}" 2>/dev/null | awk 'NR==2 {print $4}')" || true
    printf '%s' "${free:-0}"
}

write_status() {
    # $1 = ok|error, $2 = message
    local state="$1" message="$2" success_line
    # Before load_config there is nowhere to write; systemd still records it.
    [ -n "${STATUS_DIR:-}" ] || return 0
    mkdir -p "$STATUS_DIR"
    # A failed run must not advance last_success. The app reads that field and
    # nothing else to decide whether backups are stale, so overwriting it here
    # would turn every failure into a green dashboard — the exact silence this
    # whole file exists to break.
    if [ "$state" = ok ]; then
        success_line="$(now_iso)"
    else
        success_line="$(sed -n 's/.*"last_success": *"\([^"]*\)".*/\1/p' \
            "$STATUS_DIR/backup-status.json" 2>/dev/null || true)"
    fi
    cat > "$STATUS_DIR/backup-status.json.tmp" <<EOF
{
  "ok": $([ "$state" = ok ] && echo true || echo false),
  "last_attempt": "$STARTED",
  "last_success": "$success_line",
  "message": "$(json_escape "$message")",
  "offsite": $([ -n "${RCLONE_REMOTE:-}" ] && echo true || echo false),
  "disk_free_bytes": $(disk_free_bytes),
  "db_bytes": ${DB_BYTES:-0},
  "media_bytes": ${MEDIA_BYTES:-0}
}
EOF
    # Renamed into place so the app never reads a half-written file.
    mv "$STATUS_DIR/backup-status.json.tmp" "$STATUS_DIR/backup-status.json"
}

# Two traps, and both are needed. ERR fires when a command fails and is the only
# thing that knows the line number; it does NOT fire on an explicit `exit`, which
# is how `die` reports an unset AGE_RECIPIENT or a suspiciously small dump.
# Trapping ERR alone left those failures writing no status at all, so the
# dashboard kept showing the previous night's success — the exact silence this
# file exists to break.
COMPLETED=0
FAILURE_LINE=''
trap 'FAILURE_LINE=$LINENO' ERR

finish() {
    local code=$?
    [ "$COMPLETED" = 1 ] && return 0
    write_status error \
        "backup failed${FAILURE_LINE:+ at line $FAILURE_LINE} (exit $code) — see: journalctl -u clinicore-backup"
    log "backup FAILED${FAILURE_LINE:+ (line $FAILURE_LINE)}"
}
trap finish EXIT

load_config
require age "Install it with: sudo apt install age"
: "${AGE_RECIPIENT:?AGE_RECIPIENT not set in $CONFIG_FILE}"

mkdir -p "$BACKUP_DIR/daily" "$BACKUP_DIR/monthly" "$STATUS_DIR"

DB_FILE="$BACKUP_DIR/daily/db-$STAMP.dump.age"
MEDIA_FILE="$BACKUP_DIR/daily/media-$STAMP.tar.gz.age"

# --- Database --------------------------------------------------------------
#
# -Fc (custom format) rather than plain SQL: pg_restore can then --clean the
# target first, which is what a restore onto a running clinic actually needs.
log "dumping database $POSTGRES_DB"
compose exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc \
    | age -r "$AGE_RECIPIENT" > "$DB_FILE"
DB_BYTES="$(stat -c %s "$DB_FILE")"
[ "$DB_BYTES" -gt 1024 ] || die "database dump is only $DB_BYTES bytes — refusing to call that a backup"
log "database: $DB_BYTES bytes encrypted"

# --- Uploaded photographs --------------------------------------------------
#
# Read straight off the volume with a throwaway container rather than through
# the web service, so this still works on a night when web is the thing that is
# broken.
log "archiving media volume ${COMPOSE_PROJECT}_media_data"
docker run --rm -v "${COMPOSE_PROJECT}_media_data:/data:ro" alpine:3.20 \
    tar czf - -C /data . \
    | age -r "$AGE_RECIPIENT" > "$MEDIA_FILE"
MEDIA_BYTES="$(stat -c %s "$MEDIA_FILE")"
log "media: $MEDIA_BYTES bytes encrypted"

# --- Monthly copy ----------------------------------------------------------
if [ "$DAY_OF_MONTH" = 01 ]; then
    cp "$DB_FILE" "$MEDIA_FILE" "$BACKUP_DIR/monthly/"
    log "kept a monthly copy"
fi

# --- Rotation --------------------------------------------------------------
find "$BACKUP_DIR/daily" -type f -name '*.age' -mtime "+$KEEP_DAILY" -delete
# 12 months, in days, rounded up. Coarse on purpose: an extra week of an old
# monthly costs a few hundred megabytes and nothing else.
find "$BACKUP_DIR/monthly" -type f -name '*.age' -mtime "+$((KEEP_MONTHLY * 31))" -delete
log "rotated: keeping ${KEEP_DAILY} daily, ${KEEP_MONTHLY} monthly"

# --- Off-box ---------------------------------------------------------------
#
# A backup on the same disk as the database protects against almost nothing:
# the disk, the box being stolen, and the flood are all single events that take
# both. This copy is the one that counts.
if [ -n "${RCLONE_REMOTE:-}" ]; then
    require rclone "Install it with: curl https://rclone.org/install.sh | sudo bash"
    log "copying to $RCLONE_REMOTE"
    rclone copy "$BACKUP_DIR/daily/db-$STAMP.dump.age" "$RCLONE_REMOTE/daily/" --no-traverse
    rclone copy "$BACKUP_DIR/daily/media-$STAMP.tar.gz.age" "$RCLONE_REMOTE/daily/" --no-traverse
    # Mirror the rotation off-box too, or Drive fills up quietly for a year.
    rclone delete "$RCLONE_REMOTE/daily" --min-age "${KEEP_DAILY}d"
    if [ "$DAY_OF_MONTH" = 01 ]; then
        rclone copy "$BACKUP_DIR/monthly/db-$STAMP.dump.age" "$RCLONE_REMOTE/monthly/" --no-traverse
        rclone copy "$BACKUP_DIR/monthly/media-$STAMP.tar.gz.age" "$RCLONE_REMOTE/monthly/" --no-traverse
        rclone delete "$RCLONE_REMOTE/monthly" --min-age "$((KEEP_MONTHLY * 31))d"
    fi
    log "off-box copy done"
else
    log "WARNING: RCLONE_REMOTE is empty, so this backup exists only on this box"
fi

COMPLETED=1
write_status ok "backed up $((DB_BYTES / 1024)) KB database and $((MEDIA_BYTES / 1024)) KB media"
log "backup OK"
