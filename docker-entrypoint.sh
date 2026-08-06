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
    echo "[entrypoint] empty database — seeding (this is destructive, and runs once)"
    python -m scripts.seed_data
    echo "[entrypoint] seed complete"
  fi
  unset PGPASSWORD
fi

exec "$@"
