# Deploying TIQCollect

How to run TIQCollect on its own host: first install, releases, backups and rollback. This
repo is the whole lifecycle (ADR 0009). The Collections-platform path in
[MERGING-INTO-PLATFORM.md](MERGING-INTO-PLATFORM.md) is historical.

Files: [`docker-compose.prod.yml`](../docker-compose.prod.yml),
[`deploy/.env.prod.example`](../deploy/.env.prod.example) and
[`deploy/Caddyfile`](../deploy/Caddyfile). The image is the root [`Dockerfile`](../Dockerfile).

---

> ## State today (2026-09-28): not ready for real borrowers
>
> The stack below runs, and it is what a pilot will use. **Do not load real borrower data or
> send messages to real borrowers until every line here is closed.** Each item links to the
> plan that closes it.
>
> | Gap | What it means | Closed by |
> |---|---|---|
> | **Row-level security is not enforced** (RLS step 2) | The policies exist (`v2_0012`) but the API connects as the tables' owner, which bypasses them. Tenant isolation rests on the application's own scoping alone | DATA-MODEL-V2 §8.6, step 2: move the API onto `tiq_app` and the workers onto `tiq_jobs` |
> | **No load test** (B21) | Worker counts and pool sizes are estimates, not measurements | STANDALONE-TASKS B21, RESTRUCTURE-PLAN step 1.7 |
> | **No offline field app** | An agent cannot record a visit without signal | PILOT-PLAN (offline outbox) |
> | **No SMS provider with DLT registration** | Borrower OTPs, receipts and the post-visit message need an Indian DLT-registered sender. Twilio stays unset | PILOT-PLAN N06 / ECONOMICS §0 |
> | **The MinIO image cannot be pulled** | `minio/minio` is refused by Docker Hub and quay.io (checked 2026-09-28). A host with no cached copy cannot start the stack as written | an owner decision, proposed in [ADR 0010](adr/0010-object-storage-image.md): the `pgsty/minio` community fork, pinned and mirrored. `MINIO_IMAGE` names whichever image is used |
> | **Road routing is straight-line** unless you self-host OSRM | Travel-time estimates are straight-line estimates | [Routing](#routing) below |
>
> A demo host with the invented demo book is fine today: see [Demo host](#demo-host).

---

## 1. What runs

```
 internet ──443──> caddy (172.30.0.10) ──> api:8300      SPA + /api/v1, uvicorn × WEB_CONCURRENCY
                      │                  └> minio:9000    visit media (pre-signed URLs)
                      │
   private network:  postgres 16 · redis 7 · minio · worker (Celery) · beat (Celery, exactly one)
```

- **One image, three roles.** The API, the Celery worker and beat all run the same image. The
  command decides the role. The API container is the only one that builds or restores the
  database (`RUN_SEED=true`); worker and beat wait for it.
- **Caddy is the only published service** (ports 80 and 443). It has a fixed address, and
  `FORWARDED_ALLOW_IPS` names exactly that address. The API therefore believes
  `X-Forwarded-For` from Caddy alone, and the login rate limit is per real client.
- **Headers.** Caddy adds HSTS, `X-Frame-Options: DENY`, `nosniff` and a report-only
  Content-Security-Policy. Switch the CSP to enforcing once the app has run clean under it.
- **Two public names.** `APP_DOMAIN` serves the app. `FILES_DOMAIN` serves visit photos and
  recordings through pre-signed MinIO URLs: the browser talks to MinIO directly, and the
  signature binds the host name.

## 2. The host

Figures marked A are assumptions from the capacity model (RESTRUCTURE-PLAN §4); the rest are
measured.

| Pilot size | Host | Why |
|---|---|---|
| up to ~150 agents | 4 vCPU, 16 GB RAM, 200 GB SSD (A) | API + worker + Postgres + Redis + MinIO on one box. Postgres grows about **20.5 MB per agent-month** and media about **0.22 GB per agent-month** (M, demo book) |
| self-hosted OSRM | + a maps VM, 32 GB RAM for a north-India extract (A) | see [Routing](#routing) |

- **OS and tools.** Ubuntu 24.04 LTS (or any Linux with Docker Engine 27+ and the compose
  plugin). Open only 22 (SSH, keys only), 80 and 443.
- **DNS.** `APP_DOMAIN` and `FILES_DOMAIN` must resolve to the host before the first start,
  so Caddy can get the certificates.
- **Clock.** Keep the host on NTP. Tokens, OTP expiry and the nightly schedule depend on
  it. Celery schedules in IST (`Asia/Kolkata`) whatever the host's zone.

## 3. First install

```bash
git clone <this repo> tiqcollect && cd tiqcollect
git checkout v<version>                          # a release tag, never a branch
cp deploy/.env.prod.example deploy/.env.prod     # gitignored
chmod 600 deploy/.env.prod
```

Fill in `deploy/.env.prod`:
- every value under **REQUIRED**;
- the choices you need: LLM keys, `UPI_VPA`, `OSRM_BASE_URL`, `MINIO_IMAGE` (see State
  today), and `VITE_MAPBOX_TOKEN`;
- leave every **Demo only** line commented.

The file itself says how to generate each secret.

```bash
C="docker compose -f docker-compose.prod.yml --env-file deploy/.env.prod"
$C build                     # tags tiqcollect:$TIQ_VERSION
$C up -d
$C logs -f api               # wait for "[entrypoint] empty database — creating the v2 schema"
```

- **The map tile source is baked into the image at build time.** `VITE_MAPBOX_TOKEN` and
  `VITE_ALLOWED_MAP_HOSTS` are Vite build args, inlined into the SPA bundle; changing either
  needs `$C build` again, `up -d` alone will not pick it up.

- **First start on an empty database.** The API container creates the v2 schema (Alembic
  `upgrade head`) and sets the database's `search_path` and timezone. It also creates the
  `tiq_app` and `tiq_jobs` roles, because the Postgres user from the image may create roles.
  It loads **no data**. `SEED_FROM_FIXTURE` is `false` in the production compose file.
- **Then** the worker and beat start, and Caddy gets certificates. A one-shot `minio-init`
  container creates the media bucket and makes sure nothing in it is public.

**Create the first bank and its admin.** There are no users yet. This prints a single-use link,
once, valid for 72 hours, with which the admin chooses their own password. No password is
chosen, printed or logged. The command refuses if the bank already has an admin.

```bash
$C exec api python -m scripts.create_first_admin --bank-code <CODE> \
    --bank-name "<legal name>" --bank-display "<short name>" \
    --email <admin email> --name "<full name>" --phone <10-digit mobile>
```

Hand the link to the admin over a channel you trust. From there the admin invites everyone
else from the app.

Verify, from outside the host:

- [ ] The build log shows no `WARNING: VITE_MAPBOX_TOKEN is not set` (or you meant OSM tiles).
- [ ] `https://$APP_DOMAIN/api/v1/health` returns 200.
- [ ] `/api/v1/ready` returns 200 with `database: ok`, `redis: ok` and
      `rate_limit_storage: redis`.
- [ ] `https://$APP_DOMAIN/` serves the app.
- [ ] 11 bad logins in a minute give **429**, while a second client can still try: the rate
      limit is per client, not per proxy.
- [ ] An OTP send response carries **no `demo_otp`** field.
- [ ] `$C logs worker beat` shows no `REFUSING` and no `PermissionError`.
- [ ] `$C exec api python -m scripts.check_migrations` exits 0.
- [ ] `curl -s -o /dev/null -w '%{http_code}' https://$FILES_DOMAIN/tiq-documents/` returns
      **403**: the bucket does not list to anonymous callers.
- [ ] The first admin's link opens the set-password page, and the admin can sign in.

## 4. The database

- **One owner login for now.** `POSTGRES_USER` owns the schema and runs migrations, and today
  the API and workers connect with it too.
- **The RLS roles.** `tiq_app` (NOBYPASSRLS) and `tiq_jobs` (BYPASSRLS) exist as group roles
  from `v2_0012`, with their grants.
- **Moving onto them is RLS step 2** (see State today). When it lands, the API and workers
  switch to their own logins with these roles, and the owner login is kept for migrations only.
- **If the migrating user may not create roles** (a managed Postgres, for example), create
  them first, then re-apply the grants:
  ```sql
  CREATE ROLE tiq_app NOLOGIN NOBYPASSRLS;
  CREATE ROLE tiq_jobs NOLOGIN BYPASSRLS;
  ```
  ```bash
  $C run --rm --entrypoint alembic api downgrade v2_0011
  $C run --rm --entrypoint alembic api upgrade head
  ```
- **Connection budget.** Each process holds up to `DB_POOL_SIZE + DB_MAX_OVERFLOW` (5 + 5)
  connections, plus 2 + 3 for the analytics read pool, which uses the primary unless
  `ANALYTICS_DATABASE_URL` names a replica. That is 15 per process. Keep (API workers + Celery
  worker processes + 1 beat) × 15 under Postgres' `max_connections` (100 by default). Past about 6 processes, put PgBouncer in front,
  in transaction mode. Statement timeouts are `SET LOCAL` per transaction, which PgBouncer
  tolerates.
- **Migrations never run at boot on a populated database.** Every container checks the
  database against the code's head and **refuses to start** on a mismatch, printing both
  revisions. Upgrading is a release step (§5).

## 5. Releasing a new version

```bash
git fetch --tags && git checkout v<new>
sed -i 's/^TIQ_VERSION=.*/TIQ_VERSION=<new>/' deploy/.env.prod
$C build api                                     # the new image; the running stack is untouched
/srv/tiqcollect/deploy-backup.sh                 # §6: a dump taken just before the migration
$C stop worker beat                              # no job mid-way through a migration
$C run --rm --entrypoint alembic api upgrade head
$C up -d                                         # recreates api, worker, beat on the new image
```

Then re-run the checks in §3. Record in the release notes:
- the tag;
- the image id (`docker image inspect tiqcollect:<new> --format '{{.Id}}'`);
- the Alembic head before and after.

### One-time steps after a specific migration

Some migrations fix past data, not just the schema, and need a script run once afterwards.
Only on an instance that was already running (a fresh install starts clean). Run each once,
in the release that carries the migration it names, before re-opening the site.

- **`v2_0016`** (placement reconciliation): a bug before this migration could leave a
  placement ACTIVE with no live case backing it. Preview first, then apply, one bank at a
  time so the output stays readable:
  ```bash
  $C exec api python -m scripts.reconcile_placements --bank <CODE> --dry-run
  $C exec api python -m scripts.reconcile_placements --bank <CODE>
  ```
  Idempotent: running it again on a bank with nothing left to fix does nothing.

## 6. Backups

**Postgres.** A nightly custom-format dump, kept 14 days, plus one a month for 12 months.
Save as `deploy-backup.sh` on the host (not in the repo), run it from cron at 01:30 IST, which
is clear of the 19:15–20:00 and 02:00–03:00 jobs:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd /srv/tiqcollect
C="docker compose -f docker-compose.prod.yml --env-file deploy/.env.prod"
f="tiq-$(date +%F-%H%M).dump"
$C exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /backups/'"$f"
mkdir -p backups/monthly
[ "$(date +%d)" = 01 ] && cp "backups/$f" backups/monthly/
find backups -maxdepth 1 -name 'tiq-*.dump' -mtime +14 -delete
find backups/monthly -name 'tiq-*.dump' -mtime +370 -delete
```

**Copy `backups/` off the host** (another region or provider) every night. A backup on the
same disk is not a backup.

**MinIO.** Mirror the bucket off the host nightly, for example with `mc mirror --overwrite`.
Media is kept forever today; set a lifecycle rule once the owner sets a retention period.

**Redis** holds nothing that cannot be lost. OTPs expire in 5 minutes; the queue is rebuilt by
the next schedule. It is not backed up.

**Test a restore every month,** into a throwaway container. A dump nobody has restored is a
hope, not a backup:

```bash
docker run -d --name tiq-restore-test -e POSTGRES_PASSWORD=x postgres:16
docker cp backups/<file>.dump tiq-restore-test:/tmp/
docker exec tiq-restore-test sh -c 'createdb -U postgres t && pg_restore -U postgres -d t --no-owner /tmp/<file>.dump'
docker exec tiq-restore-test psql -U postgres -d t -c 'SELECT count(*) FROM workforce.agents'
docker rm -f tiq-restore-test
```

## 7. Rollback

- **Code only** (the release added no migration): set `TIQ_VERSION` back to the previous tag,
  then `$C up -d`. The previous image is still on the host.
- **The release migrated the database:**
  1. Stop the stack: `$C stop api worker beat`.
  2. If the migration's downgrade is safe for the data written since, run
     `$C run --rm --entrypoint alembic api downgrade <previous head>`. Otherwise restore the
     dump taken in §5 into a new database and point `POSTGRES_DB` at it.
  3. Set `TIQ_VERSION` back, then `$C up -d`.
- **Rollback does not touch MinIO.** Objects uploaded under the newer release stay in the
  bucket.

## 8. Operations

- **Health.**
  - `/api/v1/health`: the process is alive.
  - `/api/v1/ready`: dependencies. The container healthcheck uses it. It returns 503 when Postgres or Redis is down.
    `rate_limit_storage` reads `memory-fallback` while Redis is unreachable; limits still
    apply, per process.
- **Logs.** `$C logs <service>`. The app logs JSON through structlog. Two things are worth an
  alert:
  - `ratelimit.storage_unreachable` (ERROR);
  - any `REFUSING TO START` from the entrypoint.
- **Scaling.**
  - `WEB_CONCURRENCY` is the number of API workers. Size it at 2–4 per 2 vCPU until the load
    test says otherwise; rate limits are shared through Redis.
  - `WORKER_CONCURRENCY` is the number of Celery worker processes.
  - Keep exactly one beat.
- **The nightly engine** (IST):
  - 19:15 outcome labelling;
  - 19:45 repayment scoring;
  - 20:00 allocation and routing;
  - 02:00 beat reconciliation;
  - 03:00 location retention (`LOCATION_RETENTION_DAYS`, 90).
  - The bank-file ingest (`scripts/ingest_daily.py`) is not scheduled. Run it by hand:
    `$C exec api python -m scripts.ingest_daily --file <path>`.
- **Retention.** GPS points are deleted after `LOCATION_RETENTION_DAYS`. Audit rows have a
  floor of `AUDIT_LOG_RETENTION_DAYS` (1,825), and nothing deletes them.

### Routing

- **Leave `OSRM_BASE_URL` empty and routes are straight-line estimates.** Every route records
  `route_source`, so it is visible which kind a route was.
- **For road distances,** run OSRM on a maps VM from an India extract and set
  `OSRM_BASE_URL=http://<maps-vm>:5000`. The four commands that build an extract are in the
  `osrm` block of `docker-compose.yml`.
- **Never use `router.project-osrm.org`.** It is a public demo server and would receive every
  borrower's coordinates. It was the code's default until 2026-09-28. The default is now empty:
  straight-line estimates, and no call made at all.

## Demo host

For a demo host, run the invented demo book (Girivan Finance, Aravalli Field Services). There
are no real people in it. In `deploy/.env.prod`:

```
SEED_FROM_FIXTURE=true
DEMO_MODE=true
DEMO_MASTER_PASSWORD=<16+ characters>
DEMO_MASTER_ACCOUNTS=ananya.iyer@girivanfinance.test,vikram.malhotra@aravallifs.test,piyush.sharma@aravallifs.test
DEMO_MASTER_DISABLE_OTHERS=true
```

- **First start.** The demo book is restored in one transaction, and the three accounts get
  the master password. Every other account keeps an unusable hash.
- **Keep off on any host outside the team:**
  - `DEMO_OTP_ECHO` hands the borrower's OTP to the agent;
  - `DEMO_DEVICE_REBIND` switches device binding off.
- **Never configure Twilio on a demo host.** The demo borrowers have real-format phone numbers.
