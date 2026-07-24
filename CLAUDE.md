# Clinicore

Generic clinic / practice management and digital prescription system.
Django + HTMX. Public repo, MIT licensed, also serves as a portfolio piece.

## Read this first

Read `docs/SPEC.md` in full before doing anything in this repo. It is the
single source of truth for stack, architecture, domain model, scope, and
delivery phases. Follow the working agreement in §0.

## Current status

**Phase: 0** — schema design, repo skeleton, Docker, CI, layout shell.
Do not begin the next phase without my explicit confirmation.

## Standing rules

- Propose before you build: approach, files you'll touch, then wait.
- Small increments. Never generate the whole app in one pass.
- I am an experienced Python/Django/Postgres engineer — skip the basics.
  Frontend (HTMX/Alpine/Tailwind) and deployment are my weak spots; explain
  those briefly as you go.
- Nothing specialty-specific (homeopathy, dental, etc.) in code. Configuration
  and seed data only.
- No new dependency without asking. No scope beyond the current phase.
- Never commit real patient data, real clinic branding, or a `.env` file.
- Tests and CI stay green. Run `ruff` and `pytest` before declaring work done.

## Commands

```bash
docker compose up          # local stack
docker compose exec web pytest
docker compose exec web python manage.py migrate
ruff check . && ruff format --check .
make seed                  # synthetic demo data
```

(Update this section as the commands actually come into existence.)
