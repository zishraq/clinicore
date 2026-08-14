#!/usr/bin/env bash
#
# "Is it running?" — one command, plain answers. The first thing docs/RUNBOOK.md
# tells anyone to type.

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

load_config

echo
echo "  CONTAINERS"
# Indented with sed rather than inside the template: compose drops leading
# whitespace from --format, so the padding has to be added afterwards.
compose ps --format '{{.Service}}: {{.Status}}' 2>/dev/null | sed 's/^/    /' \
    || echo "    (compose could not read the stack)"

echo
echo "  THE APP ANSWERS?"
port="$(env_value WEB_PORT)"; port="${port:-8000}"
if curl -fsS --max-time 5 "http://127.0.0.1:${port}/healthz" >/dev/null 2>&1; then
    echo "    yes — http://127.0.0.1:${port}/healthz returned OK"
else
    echo "    NO — the app is not answering on port ${port}"
    echo "    Next step: docs/RUNBOOK.md, \"The site will not load\""
fi

echo
echo "  LAST BACKUP"
if [ -r "$STATUS_DIR/backup-status.json" ]; then
    sed -n 's/.*"last_success": *"\([^"]*\)".*/    succeeded: \1/p' "$STATUS_DIR/backup-status.json"
    sed -n 's/.*"last_attempt": *"\([^"]*\)".*/    last tried: \1/p' "$STATUS_DIR/backup-status.json"
    sed -n 's/.*"message": *"\([^"]*\)".*/    \1/p' "$STATUS_DIR/backup-status.json"
else
    echo "    NEVER RUN — no status file at $STATUS_DIR/backup-status.json"
fi

echo
echo "  LAST VERIFIED RESTORE"
if [ -r "$STATUS_DIR/restore-check.json" ]; then
    sed -n 's/.*"last_success": *"\([^"]*\)".*/    succeeded: \1/p' "$STATUS_DIR/restore-check.json"
    sed -n 's/.*"message": *"\([^"]*\)".*/    \1/p' "$STATUS_DIR/restore-check.json"
else
    echo "    NEVER RUN — the backups have not been proven to restore"
fi

echo
echo "  DISK"
df -h "$BACKUP_DIR" | tail -1 | awk '{print "    " $4 " free (" $5 " used) on " $6}'
echo
