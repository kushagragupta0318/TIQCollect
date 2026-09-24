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

# 2026-09-24 (B11, coordinator audit gates 1 and 2) — SCHEMA GENERATION.
#   This image runs the v2 data model (ten schemas; alembic chain v2_0001..).
#   The probe used to be `public.agents`, which does not exist on v2 (it is
#   workforce.agents), so every restart of a POPULATED v2 database read as
#   empty: with the fixture that crash-looped on the restore, and with
#   SEED_FROM_FIXTURE=false it re-ran the destructive seed on every boot —
#   data loss. The probe now names the generation:
#     v2     workforce.agents exists       -> leave it alone
#     v1     public.agents exists          -> REFUSE to start, with the reason.
#            v2 code cannot run on v1, and the v1 chain is no longer on this
#            image's upgrade path (it is alembic/versions_v1; operate a v1
#            database with `alembic -c alembic_v1.ini`). v1 -> v2 is the
#            transform, task B15, never an in-place upgrade.
#     empty  neither                       -> build v2, then fill it
#   A v1 fixture is refused the same way: it would create v1 tables in public.
#
# Only the API seeds. Worker and beat share this image and would otherwise race
# each other and the API to run the same destructive script on a cold start.
if [ "${RUN_SEED:-false}" = "true" ]; then
  export PGPASSWORD="${_creds#*:}"
  generation=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -tAc \
    "SELECT CASE WHEN to_regclass('workforce.agents') IS NOT NULL THEN 'v2'
                 WHEN to_regclass('public.agents') IS NOT NULL THEN 'v1'
                 ELSE 'empty' END" 2>/dev/null || echo "unknown")
  if [ "$generation" = "v1" ]; then
    echo "[entrypoint] REFUSING TO START: database ${DB_NAME} holds the v1 schema (public.agents)"
    echo "[entrypoint] and this image runs the v2 data model. Nothing was changed."
    echo "[entrypoint]   - v1 migrations: alembic -c alembic_v1.ini <command>"
    echo "[entrypoint]   - moving v1 data to v2: scripts/migrate_v1_to_v2 (task B15), into a NEW database"
    exit 1
  elif [ "$generation" = "unknown" ]; then
    echo "[entrypoint] could not read the database's schema generation — refusing to guess"
    exit 1
  elif [ "$generation" = "v2" ]; then
    echo "[entrypoint] database already initialised (v2) — skipping"
  else
    # A committed fixture wins over the seed. SEED_FROM_FIXTURE=false forces the
    # seed; DEMO_FIXTURE points at a different dump. The default is the v2
    # demo fixture (task B18); the v1 dump is never the default any more.
    fixture="${DEMO_FIXTURE:-/app/fixtures/fieldops-demo-v2.dump}"
    if [ "${SEED_FROM_FIXTURE:-true}" = "true" ] && [ -s "$fixture" ]; then
      if ! pg_restore -l "$fixture" | grep -q "SCHEMA - workforce"; then
        echo "[entrypoint] REFUSING: $fixture is not a v2 dump (no workforce schema). Nothing was changed."
        exit 1
      fi
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
      # No fixture: build the v2 schema, then seed. (Until B16 replaces it,
      # scripts.seed_data is the v1 seed and refuses to run without
      # ALLOW_V1_SEED=true — the database is left with the v2 schema and no
      # data, never dropped.)
      echo "[entrypoint] empty database — creating the v2 schema"
      alembic upgrade head
      echo "[entrypoint] seeding (runs once, on an empty database only)"
      python -m scripts.seed_data
      echo "[entrypoint] seed step complete"
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

exec "$@"
