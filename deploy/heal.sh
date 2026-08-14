#!/usr/bin/env bash
#
# Restart any container Docker has marked unhealthy.
#
# Docker records health and does nothing with it: `restart: unless-stopped`
# reacts to a container *exiting*, not to it failing its healthcheck. A wedged
# connection pool or a dead gunicorn master with PID 1 still alive is a
# container that is running, unhealthy, and serving nobody, forever.
#
# Worth knowing before adding more machinery here: gunicorn already handles the
# common case. Its --timeout kills and replaces a worker that stops responding,
# so a single hung request does not need this script. What this covers is the
# whole container being unwell.
#
# Chosen over the usual willfarrell/autoheal container, which does the same job
# but wants the Docker socket mounted into a long-running third-party image —
# root-equivalent access, and another thing to keep patched. This is fifteen
# lines using the same systemd timers the backups already use, so there is one
# mechanism on this box rather than two.
#
# Run by clinicore-heal.timer every two minutes.

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

load_config

healed=0
for container in $(compose ps -q); do
    name="$(docker inspect --format '{{.Name}}' "$container" | sed 's|^/||')"
    # Containers without a healthcheck report no Health object at all.
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container")"
    if [ "$health" = unhealthy ]; then
        log "$name is unhealthy — restarting it"
        docker restart "$container" >/dev/null
        healed=$((healed + 1))
    fi
done

# Only says anything when it acted. A timer that logs "nothing to do" every two
# minutes buries the one line that matters under seven hundred that do not.
if [ "$healed" -gt 0 ]; then
    log "restarted $healed unhealthy container(s)"
    log "NOTE: repeated healing is a symptom. If this recurs, read the logs rather than relying on it: docker compose -f $CLINICORE_DIR/docker-compose.prod.yml logs --tail=200"
fi
