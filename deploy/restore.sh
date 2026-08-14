#!/usr/bin/env bash
#
# Put a backup back. This is the script somebody runs at night with the clinic
# waiting, so it asks before it destroys anything and it says what it is doing.
#
#   ./restore.sh /path/to/db-2026-08-15_020011.dump.age \
#                /path/to/media-2026-08-15_020011.tar.gz.age \
#                /path/to/clinicore-backup.key
#
# The third argument is the age PRIVATE key, which is not on this server and
# must be fetched from the password manager or the printed copy in the clinic
# safe. Without it no backup can be read by anyone, ever. docs/RUNBOOK.md.

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

DB_ARCHIVE="${1:-}"
MEDIA_ARCHIVE="${2:-}"
KEY_FILE="${3:-}"

usage() {
    cat >&2 <<'EOF'
Usage: restore.sh <db-*.dump.age> <media-*.tar.gz.age> <private-key-file>

  All three are required. The private key is NOT on this server: get it from
  the password manager entry "Clinicore backup key", or the printed copy in the
  clinic safe. See docs/RUNBOOK.md, "Restoring from a backup".
EOF
    exit 64
}

[ -n "$DB_ARCHIVE" ] && [ -n "$MEDIA_ARCHIVE" ] && [ -n "$KEY_FILE" ] || usage
[ -r "$DB_ARCHIVE" ] || die "cannot read $DB_ARCHIVE"
[ -r "$MEDIA_ARCHIVE" ] || die "cannot read $MEDIA_ARCHIVE"
[ -r "$KEY_FILE" ] || die "cannot read the private key at $KEY_FILE"

load_config
require age "Install it with: sudo apt install age"

cat <<EOF

  This will REPLACE the live clinic data on this box.

    database : $POSTGRES_DB  (every current row is dropped)
    photos   : ${COMPOSE_PROJECT}_media_data  (replaced wholesale)
    from     : $(basename "$DB_ARCHIVE")
               $(basename "$MEDIA_ARCHIVE")

  Anything entered since that backup was taken will be gone.

EOF
read -r -p "  Type RESTORE to continue: " confirm
[ "$confirm" = RESTORE ] || die "cancelled — nothing was changed"

# Stop the app but leave the database running: web holding connections makes
# `pg_restore --clean` fail on objects still in use, and a half-restored
# database is worse than a stopped one.
log "stopping the web container (the database stays up)"
compose stop web

log "restoring the database"
age -d -i "$KEY_FILE" < "$DB_ARCHIVE" \
    | compose exec -T db pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
        --clean --if-exists --no-owner --no-privileges
log "database restored"

log "restoring the photographs"
age -d -i "$KEY_FILE" < "$MEDIA_ARCHIVE" \
    | docker run --rm -i -v "${COMPOSE_PROJECT}_media_data:/data" alpine:3.20 \
        sh -c 'rm -rf /data/* && tar xzf - -C /data'
log "photographs restored"

# The backup may predate a migration that has since been deployed, which is the
# normal case when restoring an old copy onto current code.
log "applying any migrations the backup predates"
compose run --rm web python manage.py migrate

log "starting the app"
compose up -d web

cat <<EOF

  Done. Check it:

    ${SCRIPT_DIR}/status.sh

  Then open the site and confirm a patient you expect to see is there.

EOF
