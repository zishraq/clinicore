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

# Runs as a normal user: an application that never writes to its own code has
# no reason to be able to. uid 1000 matches the usual host user, so the
# development bind mount stays writable.
RUN useradd --uid 1000 --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Production default. docker-compose.yml replaces this with runserver.
# Migrations are deliberately not run here — see docker-compose.prod.yml.
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
