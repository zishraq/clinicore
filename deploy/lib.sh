#!/usr/bin/env bash
# Shared by every script in deploy/. Sourced, never executed.
#
# Kept deliberately small: four operators' worth of machinery on one box, so
# these are plain functions over `docker compose`, not a framework.

# shellcheck disable=SC2034

CONFIG_FILE="${CLINICORE_CONFIG:-/etc/clinicore/clinicore.env}"

log() {
    # Timestamped, to stdout. systemd captures it into the journal, which is
    # where the runbook sends the son to read what happened.
    printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "$*"
}

die() {
    log "ERROR: $*" >&2
    exit 1
}

load_config() {
    [ -r "$CONFIG_FILE" ] || die "no config at $CONFIG_FILE (see deploy/clinicore.env.example)"
    # shellcheck source=/dev/null
    . "$CONFIG_FILE"

    : "${CLINICORE_DIR:?CLINICORE_DIR not set in $CONFIG_FILE}"
    : "${BACKUP_DIR:?BACKUP_DIR not set in $CONFIG_FILE}"
    : "${STATUS_DIR:?STATUS_DIR not set in $CONFIG_FILE}"
    COMPOSE_PROJECT="${COMPOSE_PROJECT:-clinicore-prod}"
    KEEP_DAILY="${KEEP_DAILY:-30}"
    KEEP_MONTHLY="${KEEP_MONTHLY:-12}"

    ENV_FILE="$CLINICORE_DIR/.env"
    [ -r "$ENV_FILE" ] || die "no stack environment at $ENV_FILE"

    # Read individually rather than sourcing the file. `.env` holds
    # DJANGO_SECRET_KEY, which routinely contains characters bash would treat
    # as syntax — sourcing it has been observed to fail in confusing ways.
    POSTGRES_DB="$(env_value POSTGRES_DB)"
    POSTGRES_USER="$(env_value POSTGRES_USER)"
    : "${POSTGRES_DB:?POSTGRES_DB missing from $ENV_FILE}"
    : "${POSTGRES_USER:?POSTGRES_USER missing from $ENV_FILE}"
}

env_value() {
    # First match wins, everything after the first `=` is the value.
    sed -n "s/^$1=//p" "$ENV_FILE" | head -n 1
}

compose() {
    docker compose --project-directory "$CLINICORE_DIR" \
        -f "$CLINICORE_DIR/docker-compose.prod.yml" "$@"
}

require() {
    command -v "$1" >/dev/null 2>&1 || die "$1 is not installed. $2"
}

json_escape() {
    # Enough for the messages these scripts produce: quotes, backslashes and
    # newlines. Not a general JSON encoder and does not need to be.
    printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' | tr '\n' ' '
}

now_iso() {
    date --iso-8601=seconds
}
