#!/usr/bin/env bash
#
# TIQCollect container entrypoint.
#
# Waits for Postgres, seeds ONCE if the database is empty, then execs whatever
# command the service was given (the API, a Celery worker, or Celery beat).
#
# Seeding has to be gated. scripts/seed_data.py is destructive by design — it
# drops every table, drops every enum type in the public schema with CASCADE,
# then recreates and repopulates. Running it on each boot would wipe the demo
# after any restart. The other apps in this platform self-seed idempotently from
# parquet at import; this is the same idea, done explicitly because the seed
# itself cannot be made idempotent.
#
# ─── CHANGELOG ───
# 2026-09-22 — an empty database is now filled from a committed FIXTURE when
#   one is present, and seeded only when it is not. Why: the seed produces a
#   day-zero book, but the book people actually demo is three weeks of nightly
#   runs, a daily feed, hand-recorded visits, leave requests and 142 allocation
#   plans on top of it — none of which the seed can regenerate. The first merge
#   into the Collections monorepo (2026-09-21) shipped the code and NOT that
#   data; a fresh clone came up with 743 cases and a 106-stop plan against the
#   1,698 / 225-stop book the demo shows, and the gap was closed by hand with a
#   pg_dump/pg_restore that lived in one person's temp directory. That is the
#   fix, made repeatable: the same dump is committed as
#   backend/fixtures/fieldops-demo.dump (see fixtures/README.md for provenance
#   and how to refresh it) and restored here.
#
#   Three properties worth keeping:
#     * The fixture is restored and then `alembic upgrade head` runs, because
#       the dump carries the alembic_version it was taken at. A migration added
#       after the fixture applies on top instead of the fixture going stale.
#     * The seed path is UNCHANGED and is still the fallback — delete or rename
#       the fixture, or set SEED_FROM_FIXTURE=false, and you get the day-zero
#       book exactly as before.
#     * The gate is the same `public.agents` check. A restore that fails halfway
#       leaves no `agents` table (pg_restore is not transactional, but the table
#       is created late in the TOC), so the next boot tries again rather than
#       treating a half-restored database as seeded.
#
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL is required}"

# postgresql+psycopg2://user:pass@host:port/dbname  ->  the parts pg_isready needs
_url="${DATABASE_URL#*://}"
_creds="${_url%%@*}"; _hostpart="${_url#*@}"
DB_USER="${_creds%%:*}"
DB_HOST="${_hostpart%%:*}"
_rest="${_hostpart#*:}"
DB_PORT="${_rest%%/*}"
DB_NAME="${_hostpart##*/}"; DB_NAME="${DB_NAME%%\?*}"

echo "[entrypoint] waiting for postgres at ${DB_HOST}:${DB_PORT} ..."
for i in $(seq 1 60); do
  if pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -q; then break; fi
  [ "$i" = 60 ] && { echo "[entrypoint] postgres never became ready"; exit 1; }
  sleep 2
done
echo "[entrypoint] postgres is up"

# Only the API seeds. Worker and beat share this image and would otherwise race
# each other and the API to run the same destructive script on a cold start.
if [ "${RUN_SEED:-false}" = "true" ]; then
  export PGPASSWORD="${_creds#*:}"
  # "agents" is created by seed_data and by nothing else, so its presence is a
  # reliable "this database has already been seeded".
  seeded=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -tAc \
    "SELECT to_regclass('public.agents') IS NOT NULL" 2>/dev/null || echo "f")
  if [ "$seeded" = "t" ]; then
    echo "[entrypoint] database already seeded — skipping (reseed deliberately with:"
    echo "[entrypoint]   docker compose exec field-ops python -m scripts.seed_data )"
  else
    # A committed fixture wins over the seed. SEED_FROM_FIXTURE=false forces the
    # seed; DEMO_FIXTURE points at a different dump. Both default to the demo.
    fixture="${DEMO_FIXTURE:-/app/fixtures/fieldops-demo.dump}"
    if [ "${SEED_FROM_FIXTURE:-true}" = "true" ] && [ -s "$fixture" ]; then
      echo "[entrypoint] empty database — restoring fixture $fixture"
      # NOT `pg_restore -d ... --exit-on-error`. The image ships Postgres 17
      # client tools and the server is 16: pg_restore 17 prefixes its output
      # with `SET transaction_timeout = 0`, a 17-only GUC that a 16 server
      # rejects, and --exit-on-error then aborts before the first table.
      # Found on the first real run (2026-09-22). Emitting SQL and feeding it
      # to psql keeps the fail-fast behaviour (ON_ERROR_STOP + pipefail) and
      # drops the one line the server cannot understand — with sed, not
      # grep -v, because grep exits 1 on empty input and pipefail would make
      # that fatal. Harmless on a 17 server too, so it is not conditional
      # on the version.
      pg_restore --no-owner --no-acl -f - "$fixture" \
        | sed '/^SET transaction_timeout/d' \
        | psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
            -X -q -v ON_ERROR_STOP=1 -o /dev/null
      echo "[entrypoint] fixture restored — applying migrations newer than it"
      alembic upgrade head
      echo "[entrypoint] restore complete"
    else
      echo "[entrypoint] empty database — seeding (this is destructive, and runs once)"
      python -m scripts.seed_data
      echo "[entrypoint] seed complete"
    fi
    # Snapshot the showcase case while it is provably clean — straight off the
    # seed is the only moment that is guaranteed. DEMO_REHEARSAL_MODE rewinds to
    # this on every check-in. Never fatal: a box that is not running the demo has
    # no baseline to take and should still start.
    if python -m scripts.demo_reset --save; then
      echo "[entrypoint] demo baseline captured"
    else
      echo "[entrypoint] demo baseline not captured (fine unless you are running the demo)"
    fi
  fi
  unset PGPASSWORD
fi

# 2026-09-24 (hotfix DEMO-LOGIN) — on EVERY boot of the API (and of the seed
# container, after it seeds): the three DEMO_MASTER_ACCOUNTS log in with
# DEMO_MASTER_PASSWORD and every other account's password becomes unusable,
# which retires the published seed passwords. OUTSIDE the RUN_SEED gate on
# purpose: the dev compose runs the api with RUN_SEED=false (only the one-shot
# seed container has it true), so inside that gate this ran once at first
# bring-up and never on the restart that deploys it — the published passwords
# would have kept working (found in review before merge). Skipped for the
# Celery worker and beat, which share this image. The password reaches the
# script through the environment only, never this command line. Never fatal: a
# refusal is logged and the container still starts.
if [ -n "${DEMO_MASTER_PASSWORD:-}" ] && [ "${1:-}" != "celery" ]; then
  rc=0
  python -m scripts.apply_demo_logins || rc=$?
  case "$rc" in
    0) echo "[entrypoint] demo master login applied" ;;
    3) echo "[entrypoint] demo master login not configured — nothing changed" ;;
    *) echo "[entrypoint] demo master login NOT applied — see the error above; nothing was changed" ;;
  esac
fi

exec "$@"
