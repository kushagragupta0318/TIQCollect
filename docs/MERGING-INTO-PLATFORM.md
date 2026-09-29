# Merging this repo back into the Collections platform

> **HISTORICAL since 2026-09-28.** TIQCollect is a standalone product (ADR 0009); nothing is
> merged into the Collections repo any more. Deploy with [DEPLOY.md](DEPLOY.md). This file is
> kept as the record of how the platform copy was fed, and for the `fieldops.transorg.ai` host
> for as long as it runs. Its Command Center parts (`TIQCOLLECT_AGENCY_ACCOUNTS`, the
> service-login rotation, CC entries in `DEMO_MASTER_KEEP_ACCOUNTS`) apply to that host only.

This repo was extracted from the `Collections` monorepo (`field-ops-stub/`) on
2026-08-17 with `git subtree split`. Work continues in both places, so the two
copies drift. This is how to fold this repo's work back into the platform.

Written 2026-08-21, after doing it once. Read the traps before running anything.

---

## Deploy checklist: TIQCollect-app `0a4513f` → fieldops.transorg.ai (2026-09-28)

The owner runs this. It brings the v1 work to the live site: the three security hotfixes,
batch 1 (the LLM provider seam, PWA, mobile nav), ML-1 borrower stance, lead-structure and
d4's H14.

- **Diff base: `4bff733`.** That is the last tree landed on `COLLECTIONS`, as `e4a1729`. It
  is not the `008433f` in the "State this assumes" table below.
- **Nothing is committed to Collections until every step in part C passes.**

### A. Before touching anything

1. **Back up.**
   - `pg_dump -Fc` of the field-ops database.
   - A copy of `field-ops-stub/backend/.env`.
   - `docker image inspect collections-platform-field-ops --format '{{.Id}}'`, the rollback
     point.
2. **Choose the secrets now, and don't commit them anywhere:**
   - a demo master password, at least 16 characters;
   - one new random password per Command Center service account (step B3).
3. **The demo passwords are public** (Agent@123 / Manager@123 / Admin@123 match the
   hashes in the committed dump; board FX-1). This deploy is what retires them (B2), so
   do all of part B in one sitting.

### B. Code, config, accounts

1. **Merge the code.** Follow "Procedure" below with `4bff733` as the base:
   `git diff --binary 4bff733^{tree} <tiq>/TIQCollect-app^{tree} ...`, applied with
   `git apply -3 --directory=field-ops-stub`.
   - Expect deletions: `api/v1/endpoints/field_ops.py`, `backend/Dockerfile`,
     `scripts/research/`, `docs/rollback/` and others. Traps 5 and 6 apply.
   - **No new Alembic migrations since `4bff733`.**
2. **`field-ops-stub/backend/.env`,** which the API, the worker and beat all read:

   | Variable | Set to | Why · if unset |
   |---|---|---|
   | `DEMO_OTP_ECHO` | **unset** (or `false`) | `true` hands the borrower's payment OTP to the agent. Unset is off |
   | `DEMO_MASTER_PASSWORD` | the new secret (≥ 16 chars) | the demo's one login. Unset: nothing changes, and the published passwords keep working |
   | `DEMO_MASTER_ACCOUNTS` | three demo emails, comma-separated: one admin, one manager, one agent | the only accounts that get the master password |
   | `DEMO_MASTER_KEEP_ACCOUNTS` | the Command Center service-login emails (B3) | never touched by the script. Leave one out and the Command Center's `/field` pages break |
   | `DEMO_MASTER_DISABLE_OTHERS` | `true` | retires every other demo account's password. The script refuses unless every account is `@tiqcollect.in` and the count is the fixture's (21) |
   | `DEMO_UPI_ACCEPT` | **unset** | accepts the fake `DEMO-UPI-` references. Production takes real 12-digit UTRs only |
   | `FORWARDED_ALLOW_IPS` | Caddy's fixed IP (see "Client address behind Caddy" below; needs the compose `ipam` change) | per-user login rate limit. Unset (`127.0.0.1`) means everyone shares one bucket, which is safe but coarse |
   | `PUBLIC_BASE_URL` | `https://fieldops.transorg.ai` | the Twilio voice webhook signature is checked against it. Unset: every call is refused |
   | `UPI_VPA`, `UPI_PAYEE_NAME` | the lender's real collection VPA and name, or **unset** | unset: no QR is offered, and the agent records the UTR by hand |
   | `BORROWER_HELPLINE` | optional | the number in the neutral post-visit message. Unset: that sentence is left out |
   | `TWILIO_*` | **leave unset on the demo site** | fixture borrowers have real-format phone numbers and demo-tenant suppression (B22) has not shipped. With no Twilio, SMS/WhatsApp and in-app calling are off |
   | `COMMAND_CENTRE_API_KEY`, `FIELD_OPS_REQUIRE_API_KEY` | delete | the `/api/field-ops` contract is gone; ignored if left |
   | `WEB_CONCURRENCY` | leave unset (1) for the v1 deploy | the v1 build counts rate limits per process. From the build with RESTRUCTURE-PLAN 1.8 the counts live in Redis and more workers share one limit. Leave `RATE_LIMIT_STORAGE_URI` unset: it then uses `REDIS_URL` |
   | `ANTHROPIC_API_KEY` / `LLM_PROVIDER` | optional; `groq` stays the default | F01 |
   | `TOTP_ENC_KEY` | **not yet** | arrives with A08 (P1). Not read by this build |

3. **Command Center service logins** (the `command-center/backend` env,
   `TIQCOLLECT_AGENCY_ACCOUNTS`, JSON `agency_id → {email, password}`). KEEP accounts
   are never changed by the script, so they still hold whatever password they had. The
   platform's `.env.example` uses `manager1` / `Manager@123`, which is public. For each
   service account:
   - Set a new password. The password travels through the environment, never the
     command line:
     `NEW_PW='…' docker compose exec -e NEW_PW -e EMAIL=<email> field-ops python -c "import os;from app.core.database import SessionLocal;from app.core.security import hash_password;from app.models.user import User;db=SessionLocal();u=db.query(User).filter(User.email==os.environ['EMAIL']).one();u.hashed_password=hash_password(os.environ['NEW_PW']);u.hashed_refresh_token=None;db.commit();print('ok')"`
   - Put the new password into `TIQCOLLECT_AGENCY_ACCOUNTS` and the email into
     `DEMO_MASTER_KEEP_ACCOUNTS` (B2). A KEEP email must not also be a master account;
     the script refuses that.
4. **Twilio console** (only if Twilio is ever enabled): the TwiML App's Voice URL is
   `https://fieldops.transorg.ai/api/v1/agent/voice/outbound` (POST).

### C. Deploy and verify

1. **Rebuild; a restart is not enough.** Run `docker compose build field-ops field-ops-worker
   field-ops-beat`, then `docker compose up -d`, then confirm the new image id (Traps /
   "After merging").
   - The image now runs as **uid 10001**. The platform mounts no volumes into field-ops,
     so there is nothing to chown.
2. **Run `alembic upgrade head`** inside `field-ops`. The entrypoint migrates only right
   after a fixture restore, so a populated database must be migrated by hand. Nothing
   is pending today, and this proves it.
3. **Check, before committing to Collections:**
   - [ ] `curl https://fieldops.transorg.ai/api/v1/health` gives 200; `/api/v1/ready` gives
         200 (it returns 503 now when Postgres or Redis is down).
   - [ ] The field-ops log shows `[demo-logins] master login: 3 set` and no `REFUSED`.
   - [ ] The three master accounts log in with the new password.
   - [ ] `manager1` / `Manager@123` and `agent1` / `Agent@123` get **401**.
   - [ ] Every Command Center `/field` page loads (its service logins work).
   - [ ] From outside: 11 bad logins with 11 different forged `X-Forwarded-For` values end
         in **429**, and a second real client can still try.
   - [ ] An OTP send response has **no `demo_otp`**.
   - [ ] `POST /api/v1/agent/voice/outbound` without a Twilio signature gets 403.
   - [ ] `/manager/ml/health` serves `recovery_risk` 2.2.0.
   - [ ] The worker and beat logs show no PermissionError; beat writes its schedule file.
   - [ ] The SPA loads, and `/manifest.webmanifest` is served.
4. **Rollback:** go back to the recorded image id and the `.env` backup. The database is
   changed only by password hashes (the demo login and B3), and the `pg_dump` restores
   those.

**The non-root image was proved on a throwaway Postgres:**
- the full entrypoint (fixture restore, alembic, demo_reset);
- a `registry.promote` round-trip as uid 10001;
- beat start-up with no `-s`.
Result, measured 2026-09-28 on `d8552ac` (the tree merged as `0a4513f`), in a throwaway
Postgres 16 + Redis 7 that were removed afterwards: **PASS**.
- Image `tiq-prod-check:lead`: 2,998,469,897 bytes, built in 340 s. `docker compose config` OK.
- Runs as `uid=10001 user=app`; `/app` is owned by `app`. No tests in the image, and 24
  artifact files.
- The entrypoint restored the fixture, ran migrations and captured the demo baseline, and
  Uvicorn started.
- `/api/v1/health` returned 200, and `/api/v1/ready` returned 200 with database and Redis both ok.
- `registry.promote` 2.2.0 → 1.1.0 → 2.2.0 worked as uid 10001, and the engine loads.
- Beat started and wrote `/app/celerybeat-schedule`, owned by `app`.


---

## P1 deploy: the v2 data model (after the v1 deploy above, never with it)

This section is for the merge that brings the standalone P1 into TIQCollect-app (candidate
`g02 @ e41baba`). Do the v1 deploy above first, and let it run.

**The v2 image refuses to start on a v1 database.** The entrypoint probes the schema
generation. If `public.agents` exists and `workforce.agents` does not, every container (API,
worker, beat) exits 1 with `REFUSING TO START ... holds the v1 schema`, and nothing is
changed. There is no in-place upgrade. The live `fieldops` database must be replaced by a v2
database.

### P1-A. Choose where the v2 data comes from (owner decision)

| Option | What you get | Cost |
|---|---|---|
| **Fresh v2 demo book** (recommended for the demo site) | `fixtures/fieldops-demo-v2.dump`: Girivan Finance (the bank), Aravalli Field Services (the agency), three bank users | Visits and payments recorded on the live v1 site since its fixture are not carried over |
| Transform the live v1 database (`scripts/migrate_v1_to_v2`) | Live v1 history under the v2 roster | Built for the demo book. It **aborts** on any v1 user with no roster mapping, which includes Command Center service logins added by hand. Every password becomes unusable, and every email is renamed to `@aravallifs.test` / `@girivanfinance.test` |

With either option, **the Command Center service logins change.** The v2 roster has no
`manager1@…`-style accounts. Pick a v2 agency-manager account for each Command Center
agency, set its password, and rewrite `TIQCOLLECT_AGENCY_ACCOUNTS` with the new emails.
The B3 one-liner does **not** work on v2, because `users.hashed_refresh_token` is gone and
sessions are rows in `user_sessions`. Use this version instead; the password again comes only
from the environment:
`NEW_PW='…' docker compose exec -e NEW_PW -e EMAIL=<email> field-ops python -c "import os;from app.core.database import SessionLocal;from app.core.security import hash_password;from app.models.user import User;from app.services.auth_service import revoke_user_sessions;db=SessionLocal();u=db.query(User).filter(User.email==os.environ['EMAIL']).one();u.hashed_password=hash_password(os.environ['NEW_PW']);revoke_user_sessions(db,str(u.id),'ADMIN_REVOKED');db.commit();print('ok')"`

### P1-B. Database

1. Keep the v1 database. Create a NEW, empty database for v2 (for example `fieldops_v2`)
   on the same server.
2. Point `DATABASE_URL` for `field-ops`, `field-ops-worker` and `field-ops-beat` at it.
   Rolling back is pointing it back.
3. Fresh book: on first start the API container restores the v2 fixture in ONE transaction,
   applies database settings, and runs `alembic upgrade head`. Worker and beat wait up to
   `DB_WAIT_SECONDS` (default 300) for that.
4. Transform: run it once, by hand, before starting the API:
   `V1_DATABASE_URL=<live v1> DATABASE_URL=<empty v2 at head> python -m scripts.migrate_v1_to_v2`.
5. Every start afterwards runs `scripts.check_migrations`. A database behind the code's
   head refuses to start and prints the command to run. Migrations are never applied at
   boot on a populated database.

### P1-C. New and changed settings

| Variable | Set to | Notes |
|---|---|---|
| `DATABASE_URL` | the new v2 database | P1-B |
| `DEMO_MASTER_ACCOUNTS` | three v2 emails, e.g. `ananya.iyer@girivanfinance.test,vikram.malhotra@aravallifs.test,piyush.sharma@aravallifs.test` | the v1 emails no longer exist |
| `DEMO_MASTER_KEEP_ACCOUNTS` | the new Command Center service-login emails (P1-A) | |
| `DEMO_EMAIL_DOMAINS` | leave the default (`girivanfinance.test,aravallifs.test`) | `DISABLE_OTHERS` refuses if any account is outside these |
| `PRODUCT_MODE` | *(removed 2026-09-28, ADR 0009; ignored if set)* | it was read nowhere |
| `TOTP_ENC_KEY` | a Fernet key from `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`, kept secret | unset: MFA enrolment is refused (`MFA_NOT_CONFIGURED`). Rotating it forces every enrolled user to re-enrol |
| `BANK_MFA_REQUIRED` | unset for the demo | `true` blocks every bank user without TOTP, including the demo bank admin |
| `DEMO_DEVICE_REBIND` | **unset** | it switches device binding off. Start-up is refused without `DEMO_MODE` |
| `DEMO_NOTIFY_ALLOWLIST` | unset, or the team's own numbers | with `DEMO_MODE`, SMS/WhatsApp go only to `DEMO_CONTACT_PHONE` and these numbers (B22). This is what could later let Twilio be enabled on the demo site |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | defaults 5 / 5 | per process. Keep processes × 10 under the server's `max_connections` |
| `API_STATEMENT_TIMEOUT_MS` / `JOB_STATEMENT_TIMEOUT_MS` | defaults 15 000 / 600 000 | |
| `ANALYTICS_DATABASE_URL` | unset (the primary is used) | a read replica, if there ever is one |
| `AGENCY_NAME`, `AGENCY_RBI_REG` | delete | removed from settings, and ignored if left |

Everything in part B of the v1 checklist still applies (OTP echo off, `FORWARDED_ALLOW_IPS`,
`PUBLIC_BASE_URL`, no Twilio).

### P1-D. Verify

- [ ] The field-ops log shows `restoring fixture ... (one transaction)` or `already
      initialised (v2)`, and no `REFUSING`.
- [ ] The worker and beat logs show no `REFUSING`.
- [ ] The three v2 master accounts log in. The bank admin reaches `/bank`.
- [ ] The Command Center `/field` pages load with the new service logins.
- [ ] Every check in part C of the v1 checklist still passes.

**Rollback:** point `DATABASE_URL` back at the v1 database and deploy the previous image.
The v1 database was never touched.

---

## State this assumes

| | |
|---|---|
| This repo | `github.com/sanyasirao-col/TIQCollect-product`, work on **`TIQCollect-v3-1`** *(read `TIQCollect-v1` until 2026-09-17; the `git diff` below must name the branch you are actually merging — `v3` is the same tree minus the ML 2.2.0 commit `7707139`)* |
| Platform | `github.com/transorg-engineering/Collections`, branch `docker-integrated` |
| Common ancestor | **`tiq/TIQCollect-v3-1@008433f`** — the tree merged on 2026-09-21 onto Collections branch `COLLECTIONS` *(read `field-ops-stub@bc8649c` until then; that base is only correct while no merge has landed, and one now has)* |

A merge was completed once on 2026-08-21 and then **deliberately deleted**
without being pushed. **The first merge that actually landed was 2026-09-21**:
`TIQCollect-v3-1@008433f` applied onto `docker-integrated@9e7e6a8` on the
branch `COLLECTIONS` — 803 files, 2 conflicts (`docker-compose.yml` env
comments; `LandingPage.tsx` copy, this repo's wording kept). Every
platform-side typography change since `bc8649c` was verified present in the
result line by line. **The next merge must diff from `008433f`'s tree, not
from `bc8649c`** — the command below is written for that.

## Procedure

```bash
cd Collections
git fetch origin
git checkout -b merge-tiq origin/docker-integrated
git remote add tiq "../TIQCollect-product"     # or the GitHub URL
git fetch tiq

git config core.longpaths true                 # Windows only — see trap 6

git diff --binary 008433f^{tree} tiq/TIQCollect-v3-1^{tree} \
    -- . ':(exclude)CLAUDE.md' ':(exclude).gitignore' ':(exclude).github' \
    > /tmp/tiq.patch                                # --binary: see trap 5

git apply -3 --directory=field-ops-stub /tmp/tiq.patch
git diff --name-only --diff-filter=U          # conflicts to resolve by hand
```

`git apply -3` is a real three-way merge: it keeps the platform's own changes
wherever this repo did not touch the same lines, and leaves conflict markers
where both moved. After editing a conflicted file you must `git add` it — the
index keeps the unmerged stages until you do, even once the markers are gone.

## Traps

**1. The base is `bc8649c:field-ops-stub`, NOT the subtree split point.**
The first attempt used the split point (`tiqcollect-only`, `7c30095`) and
silently dropped a commit. `index.css` was unchanged between the split and this
repo's tip, so it never entered the patch — leaving the platform's older copy,
which had branched before that work existed. It compiled and looked fine, and
the loss was only caught by grepping for a known class name. Verify a couple of
known-changed files after every merge.

The base stays `bc8649c` only while no merge has landed. Once one does, the base
becomes whatever was merged last.

**2. `git subtree pull` does not work here.** The split was taken bare, without
a matching `git subtree add`, so no linkage is recorded and git refuses with
"unrelated histories". Use the 3-way apply.

**3. Exclude the standalone-repo artifacts.** `CLAUDE.md` states this code must
not be pushed to the org remote, which is false inside Collections. GitHub only
reads workflows at the repository root, so `.github/workflows/ci.yml` would be
inert at `field-ops-stub/.github/`. `field-ops-stub/.gitignore` deliberately
defers to the monorepo's root file. `docs/` is fine to bring across.

**4. Divergence costs.** At two weeks apart the merge was 61 files with only two
conflicts, both one-liners. It will be worse the longer the gap. *(At five weeks
apart, 2026-09-21, it was 803 files and still only two conflicts — the
platform had touched 10 files under `field-ops-stub/` in that time, all
Docker or typography, and this repo had already absorbed the typography.)*

**5. `git diff` needs `--binary`.** Since 2026-09-08 the repo commits ML
artifacts — ~400 PNG / joblib / pickle files under `backend/app/ml/artifacts/`.
Without `--binary`, `git diff` writes a `Binary files differ` stub for each and
`git apply` fails on every one with *"cannot apply binary patch ... without
full index line"*. Found on 2026-09-21; the first dry run failed on all of
them. The patch is ~21 MB with the flag.

**6. Windows path length.** `ml/artifacts/recovery_risk/2.2.0/evaluation/shape_functions/...`
under `Collections/field-ops-stub/` exceeds 260 characters and `git apply`
reports *"Filename too long"* and skips the file. `git config core.longpaths
true` in the Collections checkout before applying.

## After merging

- `python -m compileall app` in `field-ops-stub/backend`
- `npm run build` in `field-ops-stub/frontend` — **not `npx tsc --noEmit`**,
  which this line used to say. The root `tsconfig.json` is a solution file with
  no files of its own, so `tsc --noEmit` resolves nothing and passes
  unconditionally; it reported success for weeks while the production build was
  broken (CLAUDE.md, "Running it"). `npm run build` runs `tsc -b`, which is the
  real typecheck.
- `npm test` in `field-ops-stub/frontend` — 90 vitest tests since 2026-09-17,
  all over pure modules the ported pages depend on (see the ledger below).
- **Apply new migrations.** The platform database is built by `seed_data.py`
  (`drop_all`/`create_all`), which never stamps Alembic — so `alembic_version`
  may not exist and new tables silently will not appear. Check with
  `SELECT version_num FROM alembic_version;`, `alembic stamp <matching rev>` if
  missing, then `alembic upgrade head`.
- **Rebuild the image, do not just restart.** `docker compose up -d` recreates
  containers from the existing image and looks almost identical in the output.
  Confirm with `docker image inspect collections-field-ops --format '{{.Created}}'`
  — the container's own created time tells you nothing about the code inside.

### Client address behind Caddy (FORWARDED_ALLOW_IPS), from 2026-09-24

The login rate limit (10 per minute per client) keys on the client's address. That address is
read from `X-Forwarded-For` **only when the request comes from an address listed in
`FORWARDED_ALLOW_IPS`**. The default is `127.0.0.1`. On the platform, Caddy connects from its
own container, so until this is set every user shares one login bucket.

Pin Caddy to a fixed address and trust that address alone. uvicorn matches IPs and CIDRs,
never hostnames, so `caddy` or `field-ops` will not work.

```yaml
# collections-platform/docker-compose.yml
networks:
  default:
    ipam:
      config:
        - subnet: 172.30.0.0/24
services:
  caddy:
    networks:
      default:
        ipv4_address: 172.30.0.10
```

```
# field-ops-stub/backend/.env  (read by field-ops, field-ops-worker, field-ops-beat)
FORWARDED_ALLOW_IPS=172.30.0.10
```

- **Never a range, and never `*`.** If every entry in the header is trusted, uvicorn takes the
  leftmost one, which the client wrote. Behind NAT (published ports, Docker Desktop, an SNAT
  load balancer) a client can arrive from a private address.
- **Check:** from outside, 11 failed logins with 11 different forged `X-Forwarded-For` values
  must end in `429`. From two different real clients, both must still get `401`.

## Related drift — the hand-maintained ports

`command-center/frontend/src/pages/FieldAnalytics.jsx` and `FieldCases.jsx` in
the platform are hand-maintained ports of `ManagerAnalyticsPage.tsx` and
`ManagerCasesPage.tsx` here. They are copy-paste, not imports, so nothing breaks
on merge — they just quietly fall further behind.

**The merge brings the backend but not the ports.** Every `/api/v1/manager/*`
endpoint the pages below need lands in `field-ops-stub/backend` through the
procedure above, and Command Center already reaches that router through its
per-agency service login — so after a merge the DATA is there and the JSX is
what lags. The ledger below is what has to be re-ported, kept current on this
side because the port lives on the other and nobody porting it can see this
repo's history.

### Port ledger

Each row: what changed on the page here, the endpoint it reads, and the pure
module (with its tests) that a port can copy verbatim — the modules are plain
TypeScript with no React in them, so they translate to the platform's JSX
tooling by stripping the types.

**`ManagerAnalyticsPage.tsx` → `FieldAnalytics.jsx`**

| date | change | endpoint | pure module to copy |
|---|---|---|---|
| 2026-09-16 | Header KPIs read the portfolio snapshot (`kpis.total_collected_lakhs` / `total_target_lakhs` / `overall_collection_rate_pct`), not 6-month sums; "on current portfolio" label | `GET /manager/analytics` (unchanged) | — |
| 2026-09-16 | "ABC Collections" heading removed; Recovery outlook legend "Arrears + penalties" → "Current expected", footnote reworded | — | — |
| 2026-09-16 | **Case Pipeline** donut under Collection by DPD Bucket: resolved / in progress / not started, grouped by `models/case.py RESOLVED_STATUSES`; status chips link to `/manager/cases?status=` | `GET /manager/dashboard` → `case_status_counts` | `pages/manager/casePipeline.ts` (+ `.test.ts`); ring is `DonutCard.tsx` |
| **2026-09-17** | **Collection by Payment Mode** — full-width third row of the money grid, after Team Duty / Leave. Left: one 100% composition bar (mode segments, cash in the status amber) with a legend and the cash / digital shares. Right: six stacked columns, one per month, cash / digital / paper (cheque + DD) — the trend the card exists for (cash share 3% → 30% on the 2026-09-17 book). Follows the same `month=YYYY-MM` click as the DPD card, and clicking a column selects that month. Hidden while an agent is selected. *(Earlier the same day it was seven full-width bars, one per mode; replaced before commit.)* | **`GET /manager/analytics/payment-modes?month=YYYY-MM`** (new; VERIFIED payments by the manager's agents; `BANK_DIRECT` excluded because it can never be an agent collection; `monthly[]` is the last six months and is NOT narrowed by `month`) | `pages/manager/paymentModes.ts` (+ `.test.ts`); the columns are Recharts `BarChart`, the composition bar plain divs |
| 2026-09-17 | Scroll-reveal on each card of the money grid and the AI report (`components/ui/Reveal.tsx`) | — | copy the component; it is a 60-line IntersectionObserver wrapper |

**`ManagerCasesPage.tsx` → `FieldCases.jsx`**

| date | change | endpoint |
|---|---|---|
| 2026-09-16 | `?status=` and `?bucket=` URL seeds for the status / DPD dropdowns (the overview's donuts link here) | `GET /manager/cases` (unchanged) |
| 2026-09-17 | `?activity=<stage>&activity_window=today\|7d\|30d[&visit_outcome=…]` and `?ptp_due_from&ptp_due_to` seeds, forwarded server-side, shown as applied-filter chips | `GET /manager/cases` gained `activity`, `activity_window`, `visit_outcome`, `ptp_due_from`, `ptp_due_to` |

**Not ported and not expected to be** (no platform counterpart exists):
the overview's Field Plan strip, Field Activity funnel, Promises card and
Today's Cases by DPD; the `/manager/beat-plan` page; the live-map Navigate
button. If Command Center grows an overview, `pages/manager/fieldActivity.ts`
and `todayDpd.ts` are the modules to start from, and `GET
/manager/dashboard/field-activity?window=` is the endpoint — it and the Cases
list resolve the funnel through ONE service (`services/field_activity_service.py`),
so a port must not re-derive a stage from case state client-side.

### How to keep this ledger honest

Add a row here in the same commit that changes either page. A port that is
done should be recorded as done *with the platform commit hash*, not deleted —
the next merge's author needs to know what was already carried across.
