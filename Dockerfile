# One image for both stacks. It is built production-shaped — collected static,
# a non-root user, gunicorn as the default command — and docker-compose.yml
# overrides the command for development. The alternative, a separate dev image,
# means the thing you test is not the thing you ship.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# libpq is what psycopg[binary] links against; build-essential is not needed
# because every dependency here ships a wheel.
RUN apt-get update \
    && apt-get install --no-install-recommends -y libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# WhiteNoise's manifest storage resolves {% static %} through staticfiles.json,
# which only exists once this has run — a missing entry raises at render time
# rather than 404ing quietly, so this step is not optional.
#
# The key here is a throwaway: settings refuses to import without one when
# DEBUG is off, collectstatic signs nothing, and it never reaches a layer
# because it is scoped to this RUN. The real key arrives from the environment
# at runtime.
RUN DJANGO_SECRET_KEY=build-time-only-not-a-secret \
    python manage.py collectstatic --noinput --clear

# Created before the chown, and that order is load-bearing. docker-compose.prod
# mounts a named volume here, and Docker seeds a fresh volume from the image's
# directory *including its ownership*. If this path does not exist in the
# image, Docker creates the mount point as root and the non-root user below
# cannot write a single upload — silent until the first photo.
RUN mkdir -p /app/media

# Runs as a normal user: an application that never writes to its own code has
# no reason to be able to. uid 1000 matches the usual host user, so the
# development bind mount stays writable.
RUN useradd --uid 1000 --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Production default. docker-compose.yml replaces this with runserver.
# Migrations are deliberately not run here — see docker-compose.prod.yml.
#
# Shell form so GUNICORN_WORKERS is read at run time. `.env.example` has
# documented that variable since the production slice landed and the CMD
# hardcoded 3, so setting it did nothing — a configuration knob that silently
# does nothing is worse than no knob.
#
# `exec` matters: without it gunicorn runs as a child of /bin/sh, sh becomes
# PID 1, and PID 1 does not forward SIGTERM. `docker compose stop` would then
# wait the full timeout and kill the container instead of letting gunicorn
# finish in-flight requests — a restart during surgery hours dropping whatever
# was mid-save.
#
# --timeout is the piece that makes a hung worker self-healing: gunicorn's
# master kills and replaces any worker silent for this long, which covers the
# common wedge without anything watching from outside (see deploy/heal.sh for
# the container-level case it does not cover).
CMD ["sh", "-c", "exec gunicorn config.wsgi:application \
     --bind 0.0.0.0:8000 \
     --workers ${GUNICORN_WORKERS:-3} \
     --timeout ${GUNICORN_TIMEOUT:-30} \
     --graceful-timeout 30 \
     --access-logfile - \
     --error-logfile -"]
