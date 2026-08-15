#!/usr/bin/env bash
#
# Monthly: prove the newest backup actually loads.
#
# A backup nobody has restored is a guess. This restores the latest database
# dump into a scratch database beside the live one, checks it contains what a
# clinic's data looks like, drops it, and records the result where the
# dashboard can see it. It never touches the live database.
#
# It needs the age PRIVATE key, and a copy therefore lives on this box at
# /etc/clinicore/backup-identity.key (root-only, 0600).
#
# DECIDED 2026-08-15, and a real trade rather than an oversight. Keeping the
# private key here weakens the property that makes keypair encryption worth
# using: a stolen server can now decrypt the backups it made. That is accepted,
# because the alternative — a human running this by hand once a month with the
# key on a USB stick — stops happening by the third month, and an unverified
# backup is a guess. A weakened stolen-server property is a smaller risk than a
# year of backups nobody has ever proven can be read.
#
# What this does NOT change: the off-site copies on Google Drive are still only
# readable with the key, and the key is still not in the repository, not in the
# image, and not in any backup. Losing this box loses one copy of the key, not
# the key — the password manager and the printed copy in the clinic safe are
# the two that matter.
#
# Run by clinicore-verify-restore.timer.

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

STARTED="$(now_iso)"
SCRATCH_DB="clinicore_restore_check"

write_status() {
    local state="$1" message="$2" success_line
    # Before load_config there is nowhere to write. Nothing is lost: a run that
    # dies that early has not started, and systemd still records the failure.
    [ -n "${STATUS_DIR:-}" ] || return 0
    mkdir -p "$STATUS_DIR"
    if [ "$state" = ok ]; then
        success_line="$(now_iso)"
    else
        success_line="$(sed -n 's/.*"last_success": *"\([^"]*\)".*/\1/p' \
            "$STATUS_DIR/restore-check.json" 2>/dev/null || true)"
    fi
    cat > "$STATUS_DIR/restore-check.json.tmp" <<EOF
{
  "ok": $([ "$state" = ok ] && echo true || echo false),
  "last_attempt": "$STARTED",
  "last_success": "$success_line",
  "message": "$(json_escape "$message")"
}
EOF
    mv "$STATUS_DIR/restore-check.json.tmp" "$STATUS_DIR/restore-check.json"
}

cleanup() {
    compose exec -T db psql -U "$POSTGRES_USER" -d postgres \
        -c "DROP DATABASE IF EXISTS $SCRATCH_DB;" >/dev/null 2>&1 || true
}

# Two traps, and both are needed. ERR fires when a command fails and is the only
# thing that knows the line number; it does NOT fire on an explicit `exit`, which
# is how `die` reports a missing key or an absent backup. Trapping ERR alone
# therefore left the commonest failures writing no status at all — the dashboard
# went on showing the *previous* run's success, which is precisely the silence
# this script exists to break. Found by deleting the key and watching the status
# file stay green.
COMPLETED=0
FAILURE_LINE=''
trap 'FAILURE_LINE=$LINENO' ERR

finish() {
    local code=$?
    [ "$COMPLETED" = 1 ] && return 0
    cleanup
    write_status error \
        "verification failed${FAILURE_LINE:+ at line $FAILURE_LINE} (exit $code) — see: journalctl -u clinicore-verify-restore"
    log "restore verification FAILED${FAILURE_LINE:+ (line $FAILURE_LINE)}"
}
trap finish EXIT

load_config
require age "Install it with: sudo apt install age"

VERIFY_KEY_FILE="${VERIFY_KEY_FILE:-/etc/clinicore/backup-identity.key}"
[ -r "$VERIFY_KEY_FILE" ] \
    || die "no private key at $VERIFY_KEY_FILE — see the comment at the top of this script"

LATEST="$(find "$BACKUP_DIR/daily" -name 'db-*.dump.age' -type f -printf '%T@ %p\n' \
    | sort -rn | head -n 1 | cut -d' ' -f2-)"
[ -n "$LATEST" ] || die "no database backups found in $BACKUP_DIR/daily"
log "verifying $(basename "$LATEST")"

cleanup
compose exec -T db psql -U "$POSTGRES_USER" -d postgres \
    -c "CREATE DATABASE $SCRATCH_DB;" >/dev/null

age -d -i "$VERIFY_KEY_FILE" < "$LATEST" \
    | compose exec -T db pg_restore -U "$POSTGRES_USER" -d "$SCRATCH_DB" \
        --no-owner --no-privileges >/dev/null

# "It loaded" is not the same as "it has the clinic in it". An empty dump
# restores perfectly and tells you nothing, so check for the tables and for
# rows in the two that a real clinic always has.
# Table count first, and asserted before anything queries an application table.
# The other way round, a dump of a database that was never migrated fails on
# `relation "patients_patient" does not exist` at whatever line happens to ask
# first — a psql error where the useful sentence is "this backup contains no
# clinic". Order matters here only for the message, but the message is the whole
# product of this script.
TABLES="$(compose exec -T db psql -U "$POSTGRES_USER" -d "$SCRATCH_DB" -tAc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" | tr -d '[:space:]')"
[ "${TABLES:-0}" -ge 20 ] \
    || die "restored database has only ${TABLES:-0} tables — that backup holds no clinic"

PATIENTS="$(compose exec -T db psql -U "$POSTGRES_USER" -d "$SCRATCH_DB" -tAc \
    "SELECT count(*) FROM patients_patient;" | tr -d '[:space:]')"
USERS="$(compose exec -T db psql -U "$POSTGRES_USER" -d "$SCRATCH_DB" -tAc \
    "SELECT count(*) FROM accounts_user;" | tr -d '[:space:]')"

[ "${USERS:-0}" -ge 1 ] || die "restored database has no user accounts — nobody could sign in to it"
log "restored: $TABLES tables, $USERS users, $PATIENTS patients"

cleanup
COMPLETED=1
write_status ok "$(basename "$LATEST") restored: $TABLES tables, $USERS users, $PATIENTS patients"
log "restore verification OK"
