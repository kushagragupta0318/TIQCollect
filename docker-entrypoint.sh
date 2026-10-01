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
# after any restart, so it runs only on an empty database.
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
#   and how to refresh it) and restored here. Since the v2 data model the
#   restored fixture is fieldops-demo-v2.dump; the v1 dump is refused (below).
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
#       *(2026-09-24: no longer true, and no longer relied on. The gate is now
#       the v2 GENERATION probe, workforce.agents, which the v2 TOC creates
#       BEFORE any data — so a half-finished restore read as initialised. The
#       restore is now one transaction instead; see below.)*
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
#     v2     workforce.agents exists       -> leave the data alone
#     v1     public.agents exists          -> REFUSE to start, with the reason.
#            v2 code cannot run on v1, and the v1 chain is no longer on this
#            image's upgrade path (it is alembic/versions_v1; operate a v1
#            database with `alembic -c alembic_v1.ini`). v1 -> v2 is the
#            transform, task B15, never an in-place upgrade.
#     empty  neither                       -> build v2, then fill it
#   A v1 fixture is refused the same way: it would create v1 tables in public.
#
# 2026-09-24 (coordinator re-audit, MED 4-6 and a LOW):
#   * EVERY container reads the generation now, not only the seeding one. The
#     worker, beat and a reloading dev API used to start blind on a v1
#     database. Only the seeding container (RUN_SEED=true) ever changes
#     anything; the others refuse on v1 / unreadable and otherwise start.
#   * The restore is ONE TRANSACTION. It was not, and the header above said a
#     half-failed restore "leaves no agents table ... so the next boot tries
#     again" — true of v1's TOC order, false of v2's: workforce.agents is
#     created before any data is copied, so a restore that died halfway read
#     as a finished v2 database for ever after. The SQL now goes to psql inside
#     BEGIN, and COMMIT is sent only if pg_restore itself finished cleanly;
#     psql reaching end of input with the transaction open rolls it back.
#   * The fixture's TOC is captured, then searched: `pg_restore -l | grep -q`
#     under pipefail refuses a valid fixture whenever grep exits at the first
#     match and pg_restore takes SIGPIPE writing the rest of a large listing.
#   * search_path and timezone are DATABASE settings (v2_0001 sets them). A
#     restore without -C drops them, and the restored alembic_version is past
#     v2_0001, so nothing set them again. scripts.ensure_db_settings --apply
#     re-applies both (idempotent) after a restore and on every start of the
#     seeding container, and fails the start if a new connection still does
#     not see them; the other containers --check and warn.
export PGPASSWORD="${_creds#*:}"
# The one generation probe (here, and in the worker/beat wait below).
probe_generation() {
  psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -tAc \
    "SELECT CASE WHEN to_regclass('workforce.agents') IS NOT NULL THEN 'v2'
                 WHEN to_regclass('public.agents') IS NOT NULL THEN 'v1'
                 ELSE 'empty' END" 2>/dev/null || echo "unknown"
}
generation=$(probe_generation)
if [ "$generation" = "v1" ]; then
  echo "[entrypoint] REFUSING TO START: database ${DB_NAME} holds the v1 schema (public.agents)"
  echo "[entrypoint] and this image runs the v2 data model. Nothing was changed."
  echo "[entrypoint]   - v1 migrations: alembic -c alembic_v1.ini <command>"
  echo "[entrypoint]   - moving v1 data to v2: scripts/migrate_v1_to_v2 (task B15), into a NEW database"
  exit 1
elif [ "$generation" != "v2" ] && [ "$generation" != "empty" ]; then
  echo "[entrypoint] could not read the database's schema generation — refusing to guess"
  exit 1
fi

# Only the API seeds. Worker and beat share this image and would otherwise race
# each other and the API to run the same destructive script on a cold start.
#
# An initialised v2 database is never upgraded at boot (coordinator decision,
# 2026-09-24) — but it is CHECKED: scripts.check_migrations compares its
# alembic revision with this code's head and every container refuses to start
# on a mismatch, printing both revisions and the command to run.
if [ "${RUN_SEED:-false}" != "true" ]; then
  if [ "$generation" = "empty" ]; then
    # Worker and beat can start before the API container has built the schema.
    # (Since the 2026-09-28 merge of v1 main: in dev compose the API itself
    # also runs with RUN_SEED=false, and a one-shot seed container builds the
    # database; the API then waits here like the others.)
    # 2026-09-28 (audit LOW): they used to warn and start blind, never checking
    # again. Now they WAIT for the API to build it (DB_WAIT_SECONDS, default
    # 300), then take the same checks as a v2 start below; still empty after
    # the wait, they refuse and their restart policy tries again.
    # Validated (audit LOW, 2026-09-28): a non-number — a literal "${VAR}", a
    # typo — broke `[ -lt ]` and refused at once with "after 0s". Now: not a
    # plain non-negative integer -> 300 with a warning; capped at 1800.
    db_wait="${DB_WAIT_SECONDS:-300}"
    case "$db_wait" in
      ''|*[!0-9]*)
        echo "[entrypoint] WARNING: DB_WAIT_SECONDS='${db_wait}' is not a number of seconds — using 300"
        db_wait=300 ;;
    esac
    if [ "$db_wait" -gt 1800 ]; then
      echo "[entrypoint] WARNING: DB_WAIT_SECONDS=${db_wait} capped at 1800"
      db_wait=1800
    fi
    echo "[entrypoint] database ${DB_NAME} is empty; only the API container (RUN_SEED=true) builds it — waiting up to ${db_wait}s"
    waited=0
    while [ "$generation" = "empty" ] && [ "$waited" -lt "$db_wait" ]; do
      sleep 5; waited=$((waited + 5))
      generation=$(probe_generation)
    done
    if [ "$generation" != "v2" ]; then
      echo "[entrypoint] REFUSING TO START: database ${DB_NAME} is still '${generation}' after ${waited}s, not an initialised v2 database"
      exit 1
    fi
  fi
  if [ "$generation" = "v2" ]; then
    if ! python -m scripts.ensure_db_settings --check; then
      echo "[entrypoint] WARNING: database-level search_path/timezone are not set; the API container re-applies them on start"
    fi
    if ! python -m scripts.check_migrations; then
      echo "[entrypoint] REFUSING TO START: the database is not at this code's migration head (see above)"
      exit 1
    fi
  fi
elif [ "$generation" = "v2" ]; then
  echo "[entrypoint] database already initialised (v2) — skipping"
  python -m scripts.ensure_db_settings --apply
  if ! python -m scripts.check_migrations; then
    echo "[entrypoint] REFUSING TO START: the database is not at this code's migration head (see above)"
    exit 1
  fi
else
  # A committed fixture wins over the seed. SEED_FROM_FIXTURE=false forces the
  # seed; DEMO_FIXTURE points at a different dump. The default is the v2
  # demo fixture (task B18); the v1 dump is never the default any more.
  fixture="${DEMO_FIXTURE:-/app/fixtures/fieldops-demo-v2.dump}"
  if [ "${SEED_FROM_FIXTURE:-true}" = "true" ] && [ -s "$fixture" ]; then
    if ! toc=$(pg_restore -l "$fixture"); then
      echo "[entrypoint] REFUSING: could not read the table of contents of $fixture. Nothing was changed."
      exit 1
    fi
    if ! grep -q "SCHEMA - workforce" <<<"$toc"; then
      echo "[entrypoint] REFUSING: $fixture is not a v2 dump (no workforce schema). Nothing was changed."
      exit 1
    fi
    echo "[entrypoint] empty database — restoring fixture $fixture (one transaction)"
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
    {
      echo "BEGIN;"
      if pg_restore --no-owner --no-acl -f - "$fixture" | sed '/^SET transaction_timeout/d'; then
        echo "COMMIT;"
      else
        echo "[entrypoint] pg_restore failed — the restore is rolled back, nothing was kept" >&2
        exit 1
      fi
    } | psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
          -X -q -v ON_ERROR_STOP=1 -o /dev/null
    python -m scripts.ensure_db_settings --apply
    echo "[entrypoint] fixture restored — applying migrations newer than it"
    alembic upgrade head
    # A restored database carries no planner statistics: pg_restore loads the
    # rows, not pg_statistic. Planned on guesses, the Overview's
    # v_visit_to_pay join takes 300s+ where it takes 1s analysed (measured
    # 2026-10-01). autovacuum gets there on its own; the first request must not
    # pay for it.
    echo "[entrypoint] collecting planner statistics"
    psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -X -q -c "ANALYZE"
    echo "[entrypoint] restore complete"
    # 2026-09-28 (B16, d4) — the agencies' specimen documents live in MinIO,
    # which a pg_dump does not carry. Re-created from the roster (the bytes
    # are deterministic, so each sha256 matches its tenancy.agency_documents
    # row) and uploaded where missing. Never fatal: MinIO being late costs a
    # document preview, not the boot.
    if python -m scripts.ensure_demo_documents; then
      echo "[entrypoint] demo agency documents present in object storage"
    else
      echo "[entrypoint] demo agency documents not uploaded (fine unless you open one; rerun scripts.ensure_demo_documents)"
    fi
  else
    # No fixture: build the v2 schema, then seed. (Until B16 replaces it,
    # scripts.seed_data is the v1 seed and refuses to run without
    # ALLOW_V1_SEED=true — the database is left with the v2 schema and no
    # data, never dropped.) v2_0001 sets search_path/timezone itself here.
    echo "[entrypoint] empty database — creating the v2 schema"
    alembic upgrade head
    python -m scripts.ensure_db_settings --check
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
# 2026-09-24 (hotfix DEMO-LOGIN) — on EVERY boot of the API (and of the seed
# container, after it seeds): the DEMO_MASTER_ACCOUNTS log in with
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
