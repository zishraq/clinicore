#!/usr/bin/env bash
#
# Monthly: prove the newest backup actually loads.
#
# A backup nobody has restored is a guess. This restores the latest database
# dump into a scratch database beside the live one, checks it contains what a
# clinic's data looks like, drops it, and records the result where the
# dashboard can see it. It never touches the live database.
#
# It needs the age PRIVATE key, which is deliberately not kept on this box — so
# this runs unattended only if you place a copy at VERIFY_KEY_FILE. That is a
# real trade and the runbook states it: a key readable by root on this server
# weakens the "stolen box cannot read its backups" property. The alternative is
# running this by hand once a month with the key on a USB stick. Choose one and
# write down which; an unverified backup is the larger risk of the two.
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

on_error() {
    local line="$1"
    cleanup
    write_status error "verification failed at line $line — see: journalctl -u clinicore-verify-restore"
    log "restore verification FAILED (line $line)"
}
trap 'on_error $LINENO' ERR

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
TABLES="$(compose exec -T db psql -U "$POSTGRES_USER" -d "$SCRATCH_DB" -tAc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" | tr -d '[:space:]')"
PATIENTS="$(compose exec -T db psql -U "$POSTGRES_USER" -d "$SCRATCH_DB" -tAc \
    "SELECT count(*) FROM patients_patient;" | tr -d '[:space:]')"
USERS="$(compose exec -T db psql -U "$POSTGRES_USER" -d "$SCRATCH_DB" -tAc \
    "SELECT count(*) FROM accounts_user;" | tr -d '[:space:]')"

[ "$TABLES" -ge 20 ] || die "restored database has only $TABLES tables"
[ "$USERS" -ge 1 ] || die "restored database has no user accounts — nobody could sign in to it"
log "restored: $TABLES tables, $USERS users, $PATIENTS patients"

cleanup
trap - ERR
write_status ok "$(basename "$LATEST") restored: $TABLES tables, $USERS users, $PATIENTS patients"
log "restore verification OK"
