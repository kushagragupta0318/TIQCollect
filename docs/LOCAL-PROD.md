# Running TIQCollect as a local production server

The production stack on one machine, with no public DNS and nothing billable. This is the
target the owner asked for: **every feature working locally first, deployed publicly only once
it is market-ready.** For a real public deployment, read [DEPLOY.md](DEPLOY.md) — this file is
the same stack with local TLS and local names.

Verified end to end on 2026-09-30 (Windows 11, Docker Desktop, 16 GB). What broke is in
[§5](#5-defects-found), and it is the part worth reading first.

---

## 1. What you get

```
browser ──443──> caddy (172.30.0.10, Caddy's own local CA)
                   ├─> api:8300       the built SPA + /api/v1
                   └─> minio:9000     visit media, pre-signed URLs
private network:  postgres 16 · redis 7 · minio · worker · beat (one)
```

- **One image**, three roles (API, Celery worker, beat), running as non-root `app`.
- **Caddy is the only published service** (80/443). Postgres, Redis and MinIO are not
  reachable from the host at all.
- **Local names:** `tiq.localhost` and `files.tiq.localhost`. Chromium and Firefox resolve
  `*.localhost` to loopback themselves, so **no hosts-file edit and no admin rights**.
- **Nothing billable:** no LLM key, no Twilio, no Mapbox token, no hosted routing. Every one
  of those seams degrades and says so ([§6](#6-what-degrades-with-no-paid-services)).

## 2. Bring it up from nothing

```bash
git clone <this repo> tiqcollect && cd tiqcollect
cp deploy/.env.prod.example deploy/.env.prod
```

Edit `deploy/.env.prod`. For a local server:

```ini
TIQ_VERSION=localprod
APP_DOMAIN=tiq.localhost
FILES_DOMAIN=files.tiq.localhost
ACME_EMAIL=local@tiq.invalid          # unused with tls internal, still required
TIQ_TLS_DIRECTIVE=tls internal        # Caddy's own CA instead of Let's Encrypt
SECRET_KEY=<64 random chars>          # python -c "import secrets;print(secrets.token_urlsafe(64))"
POSTGRES_PASSWORD=<32 random chars>
MINIO_ROOT_USER=tiq-media
MINIO_ROOT_PASSWORD=<32 random chars>
TOTP_ENC_KEY=<fernet key>             # python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
WEB_CONCURRENCY=2
OSRM_BASE_URL=                        # empty: straight-line estimates, no external calls
LLM_PROVIDER=none
SEED_FROM_FIXTURE=false               # true restores the demo book; see §4
```

Leave every `TWILIO_*`, `VITE_MAPBOX_TOKEN` and `DEMO_*` line empty for a clean install.
`MINIO_IMAGE` can stay unset — the compose default is our public ghcr mirror (ADR 0012).

```bash
C="docker compose -p tiq-localprod -f docker-compose.prod.yml --env-file deploy/.env.prod"
$C build api          # ~2.5 min warm, ~6 min cold; image ≈3.08 GB
$C up -d
$C logs -f api        # wait for "[entrypoint] empty database — creating the v2 schema"
```

**Then create the first bank and its admin.** An empty install has no users, and nothing else
creates one:

```bash
$C exec api python -m scripts.create_first_admin --bank-code GIRIVAN \
    --bank-name "Girivan Finance Ltd" --bank-display "Girivan Finance" \
    --email admin@example.test --name "Asha Rao" --phone 9876543210
```

It prints a **single-use link, once**, valid 72 hours. Open it, set a password, sign in. The
token is stored only as a sha256 — it is not recoverable from the database, so if you lose the
line, re-run after deleting the admin. A second run on a bank that already has an admin
refuses and changes nothing.

### Trust Caddy's local CA (once)

Until you do, browsers warn and `curl` needs `-k`.

```bash
$C cp caddy:/data/caddy/pki/authorities/local/root.crt ./caddy-local-root.crt
```

Import `caddy-local-root.crt` into **Windows → Manage user certificates → Trusted Root
Certification Authorities**. Firefox keeps its own store (Settings → Certificates → Import).

With `curl`, either trust it or resolve and skip verification:

```bash
curl -k --resolve tiq.localhost:443:127.0.0.1 https://tiq.localhost/api/v1/ready
```

### Check it

- [ ] `/api/v1/health` → 200, `/api/v1/ready` → 200 with `database: ok`, `redis: ok`,
      `rate_limit_storage: redis`.
- [ ] `https://tiq.localhost/` serves the app; `/agent`, `/manager`, `/bank`, `/login`,
      `/simulator` all serve.
- [ ] `$C logs worker beat` shows no `REFUSING` and no `PermissionError`.
- [ ] `$C exec api python -m scripts.check_migrations` exits 0.
- [ ] 11 bad logins in a minute → **429** (per client, because `FORWARDED_ALLOW_IPS` is pinned
      to Caddy's fixed IP).
- [ ] An OTP send response carries no `demo_otp`.

## 3. Day-to-day

```bash
$C ps                      # state and health of all seven services
$C logs -f api             # JSON request log, one line per request
$C down                    # stop, keep data
$C down -v                 # stop and delete the volumes — everything goes
```

The nightly engine (IST) runs inside this stack: 19:15 outcome labelling, 19:45 scoring, 20:00
allocation and routing, 02:00 beat reconciliation, 03:00 GPS retention. The bank-file ingest is
**not** scheduled; run it by hand with `$C exec api python -m scripts.ingest_daily --file <path>`.

## 4. The demo book, for a populated walkthrough

An empty install has nothing to look at. To smoke the product with data, set:

```ini
SEED_FROM_FIXTURE=true
DEMO_MODE=true
DEMO_MASTER_PASSWORD=<16+ chars>
DEMO_MASTER_ACCOUNTS=ananya.iyer@girivanfinance.test,vikram.malhotra@aravallifs.test,piyush.sharma@aravallifs.test
```

and `$C down -v && $C up -d`. The committed fixture restores in one transaction and migrations
apply on top. Measured: **2 banks, 10 agencies, 199 users, 165 agents, 13,214 loans, 10,434
cases, 10,107 placements, 41,431 visits.** The three accounts above get that password (bank
admin, agency manager, field agent); every other account keeps an unusable hash.

**To open the field agent app you also need `DEMO_DEVICE_REBIND=true`.** The fixture's agent
has a bound device, so signing in from a new browser is refused with
`Device not authorized` and audited as `DEVICE_MISMATCH` — the control working, not a bug. On a
real install the agent enrols their own device on first login and this never arises. That flag
switches device binding off, so **never set it on anything real**; start-up refuses it without
`DEMO_MODE`.

## 5. Defects found

Measured on the stack above with the demo book loaded. **Breaks** means a user hits an error;
**degrades** means a feature falls back and says so.

### P0 — the bank portal's landing page returns 500

```
GET /api/v1/bank/overview   ->  500   after 16.8s (repeatable)
api log: psycopg2.errors.QueryCanceled: canceling statement due to statement timeout
```

`API_STATEMENT_TIMEOUT_MS` is 15 000, so the request is cancelled mid-query and surfaces as a
raw 500 with no useful message. **This is the first screen a bank user sees.**

The statement that is running when it dies, captured three times from `pg_stat_activity` at
3.0 s, 8.3 s and 12.2 s:

```sql
SELECT AVG(CASE WHEN paid_within_7d THEN 1.0 ELSE 0.0 END), COUNT(*)
FROM analytics.v_visit_to_pay
WHERE bank_id = '…' AND customer_met AND visit_date BETWEEN '2026-09-01' AND '2026-09-15'
```

That is the **`visit_to_pay` KPI**. The `analytics` schema has five materialized views
(`mv_*`), all populated, and eleven of the twelve overview KPIs read them and return in
milliseconds. **`v_visit_to_pay` is a plain `VIEW`**, so that one KPI aggregates live across
41,431 visits joined to payments and spends the whole request's budget. Materialising it beside
its five siblings is the obvious fix.

*Limit on this diagnosis:* the slow plan could not be reproduced in `psql`, because the
`analytics` views are tenant-scoped and return zero rows without the app's `app.bank_id`
binding. The statement and the view type are measured; the query plan is not.

### P1 (security) — an agency manager can reach model-promotion routes (F12)

Measured with fresh tokens on the running stack. Nothing was promoted: the promote probe uses a
non-existent candidate id, so the status distinguishes *authorised* from *blocked* without
changing anything.

| As | `GET /manager/ml/candidates` | `GET /manager/ml/health` | `POST /manager/ml/candidates/<fake>/promote` |
|---|---|---|---|
| BANK_ADMIN | 403 blocked | 403 blocked | 403 blocked |
| **AGENCY_MANAGER** | **200** | **200** | **409** |

The role gate on these routes is `AGENCY_MANAGER`/`AGENCY_ADMIN`, not the bank-side role the
capability intends. Two things follow:

- **The 409 is not the role guard working.** Its message is *"model promotion is disabled while
  the shared demo login is active"* — a demo-mode safety net. Authorisation passed and the
  handler ran. On a deployment without the shared demo login, that net is absent.
- **`/manager/ml/health` hands an agency tenant the model internals:** artifact SHA-256, all 15
  feature names, calibration method and segment edges, training-data hash, and the serving
  `host:pid`. Promotion rewrites `champion.txt` for the whole deployment, so this is one tenant
  reading and potentially changing another's scoring.

### Slow, not yet broken

| Endpoint | Time | Note |
|---|---|---|
| `bank/overview` | 16.8–19.3 s | fails, above |
| `bank/placements` | 4.9 s | the next one to break if the timeout tightens |
| `bank/agencies` | 1.5 s | |
| `bank/agencies-directory` | 1.3 s | |

Everything else measured under 1 s. All agency/manager endpoints returned 200 on this book.

### Not defects, but worth knowing

- **`/api/v1/events/stream` is SSE and holds the connection open** — 33 minutes in one sweep
  before I stopped it. Correct for SSE; do not put it in an automated endpoint sweep, and note
  that a 15-minute access token expires mid-stream.
- `device_id` on login has an 8-character minimum (422 below that).
- `/manager/ai/monthly-report` requires a `month` query parameter (422 without it).

## 6. What degrades with no paid services

Every one of these was checked on the running stack, and each says what it is:

| Seam | With nothing configured | Honest about it? |
|---|---|---|
| LLM (`LLM_PROVIDER=none`) | `ai/health` reports `configured_provider: none`, `active_provider: none`, `key_present: false`, `primary_usable: false`. `ai/briefing` still returns a headline, key insight and recommended actions from the rule-based path, tagged **`ai_generated: false`** | **Yes** |
| Routing (`OSRM_BASE_URL` empty) | Straight-line distance and time estimates instead of road routes; every route records `route_source` | By design; not exercised in this pass |
| SMS/WhatsApp (`TWILIO_*` empty) | No message is sent; the service returns `false` rather than raising, and callers surface it as `receipt_sent` / `sms_sent` | By design; not exercised in this pass |
| Map tiles (`VITE_MAPBOX_TOKEN` empty) | OpenStreetMap tiles instead of Mapbox, with a build-log warning | Not exercised in a browser in this pass |

The LLM case is the one that matters most for a demo, and it behaves correctly: the fallback is
labelled rather than presented as AI.

## 7. What is missing for market-ready

In the order I would fix them:

1. **`bank/overview` 500** (§5). The bank portal's front page fails on a realistic book.
2. **F12 route guard** (§5). One tenant can read and reach another's model machinery.
3. **Row-level security is not enforced.** The policies exist (`v2_0012`) but the API connects
   as the tables' owner, which bypasses them; tenant isolation rests on application scoping
   alone. Step 2 of DATA-MODEL-V2 §8.6 moves the API onto `tiq_app`.
4. **No load test.** Worker counts, pool sizes and the statement timeout are estimates — and
   §5 shows the timeout is already wrong for at least one real query.
5. **No SMS provider.** Borrower OTPs, receipts and the post-visit message need a
   DLT-registered Indian sender. Until then, payment verification by OTP cannot be demonstrated
   to a real borrower.
6. **No offline field app.** An agent cannot record a visit without signal.
7. **The MinIO image is frozen** at `RELEASE.2025-09-07` and gets no security fixes, and the
   mirror is **amd64-only** (ADR 0012). Acceptable while local and not internet-reachable;
   revisit before any public deployment.
8. **CSP is report-only.** Switch it to enforcing once the SPA has been checked against it.
9. **Backups are not configured here.** DEPLOY.md §6 has the script; a local server holding
   anything you care about needs it too.

## 8. Rollback and reset

- **Code only:** set `TIQ_VERSION` back, `$C up -d`.
- **Start over completely:** `$C down -v` then bring it up again. Everything goes, including
  uploaded media.
- **Keep the data, rebuild the app:** `$C build api && $C up -d`. Migrations never run at boot
  on a populated database; every container refuses to start on a revision mismatch and prints
  both revisions, so run `alembic upgrade head` deliberately (DEPLOY.md §5).
