# Getting started with TIQCollect (for a new developer or a returning owner)

This is the onboarding and handoff layer. It gets you from a fresh clone to a running app,
maps the codebase, and points you at the deeper docs. It deliberately does **not** repeat the
deployment mechanics — those live in [DEPLOY.md](DEPLOY.md) (public deploy) and
[LOCAL-PROD.md](LOCAL-PROD.md) (the production stack on one machine). Start with
[../CLAUDE.md](../CLAUDE.md); it is the operational guide and is kept accurate.

---

## 1. What the product is

TIQCollect is a **field-collections platform**. Field agents visit delinquent borrowers, log
each visit with GPS, photo and signature evidence, collect payments verified by a borrower OTP,
and record promises to pay (PTPs). A nightly engine allocates tomorrow's cases and routes each
agent's day. The domain chain is `Customer → Loan → Case → Visit → {Payment, PTP}`, with a
`Beat` being one agent's routed day.

Managers (agency side) get analytics, compliance, a live agent map, visit-evidence anomaly
detection and an LLM performance narrative over their own team. Six deliberately separate
scoring layers sit under this (repayment likelihood, recovery potential, visit priority, agent
competency, case↔agent match, and the trained `recovery_risk` model that feeds the allocator);
see CLAUDE.md's "scoring layers" section and the ADRs before touching anything in `ml/`.

The **standalone product** ([STANDALONE-PRODUCT-PLAN.md](STANDALONE-PRODUCT-PLAN.md), ADR 0009)
adds a **bank portal** and multi-agency tenancy over one backend, in a three-tier model:
**Bank → Agency → Agent**. A bank places cases with, onboards and monitors multiple agencies
(Command Center analytics, AI strategy, agency management, tech ops); each agency runs today's
manager view plus a Manage Agents tab; each agent runs the field app. Tenant scoping today is
`Agent.manager_user_id == current_user.id`, largely inline; the standalone plan moves it to a
`RequestContext` / `scope.py`.

## 2. Ten minutes from clone to a running app (dev stack)

```bash
git clone <this repo> tiqcollect && cd tiqcollect
docker compose up -d      # api :8400, web :5473, postgres :15432, redis :16379, minio :19000
```

- **No `backend/.env` is required.** The compose file sets everything the app needs to start;
  `backend/.env.example` documents every setting.
- **The database fills itself.** An empty Postgres is restored from the committed v2 demo book
  `backend/fixtures/fieldops-demo-v2.dump` by `docker-entrypoint.sh` (one transaction), then
  `alembic upgrade head` runs on top. A v1 database or v1 dump is refused. Provenance and the
  refresh recipe are in `backend/fixtures/README.md`.
- **Open:**
  - http://localhost:5473 — the app (Vite dev server, hot reload)
  - http://localhost:8400 — the API on its own, plus `/docs`
- **Log in** with the demo master-login accounts (bank admin, agency manager, field agent).
  These three accounts share one password, **`DEMO_MASTER_PASSWORD`, which is private and is NOT
  in the repo** — the fixture holds no usable hash for anyone else. Get the password from the
  owner / the private secrets store; do not commit it. To open the **field agent** app against
  the fixture you also need `DEMO_DEVICE_REBIND=true` (the fixture agent has a bound device;
  see LOCAL-PROD.md §4). The demo account emails are listed in LOCAL-PROD.md §4.

> Dev vs. prod: this section is the **dev** stack (`docker-compose.yml` + `Dockerfile.dev`, hot
> reload over bind mounts). The **prod** stack (`Dockerfile` + `docker-compose.prod.yml` +
> `deploy/Caddyfile`, one non-root image behind Caddy) is in LOCAL-PROD.md / DEPLOY.md. Both
> must be kept working; a change to what they consume must say what it does to each.

## 3. Map of the codebase

### `backend/app/`

| Path | What lives here |
|---|---|
| `api/v1/endpoints/` | HTTP routes: `agent.py` (field app), `manager.py` (agency view, being split), `bank.py` / `bank_agencies_admin.py` / `bank_placements.py` (bank portal), `manager_agents_admin.py`, `accounts.py`, `auth.py`, `events.py` (SSE), `verify.py` (public ID-card check), `health.py` |
| `services/` | Use cases; own the transaction |
| `ml/` | Scorecards, trained-model pipeline (`ml/pipeline`), synthetic generator (`ml/simulation`), committed artifacts (`ml/artifacts`) |
| `models/` `schemas/` | SQLAlchemy mapping · Pydantic shapes |
| `core/` | Cross-cutting seams: `config`, `database`, `security`, `storage`, `llm`, `routing` (OSRM + OR-Tools), `transcription`, `events` (Redis pub/sub), `audit`, `ratelimit`, `geo`, `permissions`, `request_context`, `errors`, `partitions` |
| `workers/` | Celery tasks (thin) and the schedule (`celery_app.py`) |
| `demo/` | Demo-only behaviour (gated behind `DEMO_*` switches) |

`backend/scripts/` holds operator CLIs (seed, ingest, model training, validation,
`create_first_admin`). `backend/tests/` is pytest; `tests/conftest.py` fixes the environment and
blocks the network. `backend/tests/pg/` is the Postgres-only suite (run in CI's `backend-pg`).

### `frontend/src/`

| Path | What lives here |
|---|---|
| `api/` | Typed API clients (`axios.ts`, `agent.ts`, `manager.ts`, `bank.ts`, `auth.ts`) |
| `lib/` | Pure, tested helpers (`apiError.ts`, `pwaConfig.ts`, `mapTiles.ts`, …) |
| `components/` | Shared React components |
| `pages/` | Route screens: `pages/agent`, `pages/manager`, `pages/auth`, `pages/simulator` (mobile-app simulator), plus `LandingPage.tsx` |
| `bank/` | The **bank portal** is its own mini-app (`BankApp.tsx`, `bank/pages`, `bank/components`, `bank/layout`, `bank/theme`) — see the discrepancy note below |
| `store/` | Zustand stores (`authStore.ts`, `sosStore.ts`) |
| `contexts/`, `hooks/`, `types/`, `assets/` | React contexts, hooks, shared types, static assets |

> **Discrepancy:** the task and some docs imply `frontend/src/pages/bank`. In the actual repo
> the bank portal lives at **`frontend/src/bank/`** (its own themed sub-app with its own
> `bank/pages`), not under `pages/`. Likewise the endpoint layer has more files than CLAUDE.md's
> Layout snippet lists (`bank.py`, `bank_agencies_admin.py`, `bank_placements.py`,
> `manager_agents_admin.py`, `accounts.py`).

## 4. Verifying a change

```bash
cd backend  && python -m pytest -q          # no .env, no network needed (tests/conftest.py)
cd backend  && python -m compileall -q app scripts
cd frontend && npm run build                # tsc -b + vite. NEVER `npx tsc --noEmit` (checks nothing here)
cd frontend && npm test
cd frontend && npm run lint                 # note: lint currently fails — 7 known react-hooks errors (CLAUDE.md, issue 1)
```

On the shared machine use the **`tiq-verify`** skill (runs these in a throwaway container with a
safe env) and respect the full-suite lock (`.claude/fullsuite.lock`, `-n 4` max).

**CI** (`.github/workflows/ci.yml`) runs on `TIQCollect-app`, `standalone-*`, `lead-*`,
`hotfix/**` and several lane-branch patterns, plus PRs. Jobs:
- **backend** — `compileall` + `pytest` (with a Redis service).
- **backend-pg** — the Postgres-only suite (`tests/pg`) against `postgres:16`.
- **frontend** — `npm run build` (real typecheck + bundle), `npm test`, then `npm run lint`.
- **docker** — `docker compose config`, prod image build, and an image-size budget (≤ 3.3 GB).

## 5. Key docs index

| Read this when you want to know… | Doc |
|---|---|
| How the code is meant to be shaped, where each rule lives | [ARCHITECTURE.md](ARCHITECTURE.md) |
| What is wrong today, measured | [ENGINEERING-AUDIT.md](ENGINEERING-AUDIT.md) |
| Why X is the way it is (decisions) | [adr/](adr/) then `git log -- <file>` |
| The data model being built | [DATA-MODEL-V2.md](DATA-MODEL-V2.md) |
| What we're changing and in what order | [RESTRUCTURE-PLAN.md](RESTRUCTURE-PLAN.md), [PILOT-PLAN.md](PILOT-PLAN.md) |
| The standalone Bank→Agency→Agent vision + task planner | [STANDALONE-PRODUCT-PLAN.md](STANDALONE-PRODUCT-PLAN.md), [STANDALONE-TASKS.md](STANDALONE-TASKS.md) |
| Public deployment | [DEPLOY.md](DEPLOY.md) |
| The production stack on one machine (with measured defects) | [LOCAL-PROD.md](LOCAL-PROD.md) |
| The offline field app plan | MOBILE-APP-PLAN.md *(not yet written — planned)* |

## 6. Demo delivery: showing the app to a client from this laptop

### Option A — local production stack (you drive, on this machine)

Bring up the prod stack per [LOCAL-PROD.md](LOCAL-PROD.md) and open **https://tiq.localhost**
(and `https://files.tiq.localhost` for media). For a populated walkthrough set
`SEED_FROM_FIXTURE=true`, `DEMO_MODE=true`, `DEMO_MASTER_PASSWORD=…` and the demo accounts
(LOCAL-PROD.md §4). This is the truest demo: Caddy TLS, non-root image, the nightly engine
running inside the stack. Trust Caddy's local root CA once (LOCAL-PROD.md §2) so browsers stop
warning. Known state before you show it: the bank-portal overview 500 is fixed at `v2_0017`,
and F12 (an agency manager can reach model-promotion routes) is still open — see LOCAL-PROD.md §5.

### Option B — Cloudflare Tunnel, so the client uses their own device (and can install the PWA)

The frontend is already an installable **PWA** — `vite-plugin-pwa` is wired in
`frontend/vite.config.ts` (`VitePWA(pwaOptions)`, options in `src/lib/pwaConfig.ts`), giving a
manifest, icons and an app-shell service worker in the `vite build` output. So a client who
reaches the site over HTTPS can "Add to Home Screen" and get an app-like icon during the demo.

`cloudflared` puts an HTTPS URL in front of your local prod stack with no port-forwarding:

```bash
# 1. Install cloudflared (winget install --id Cloudflare.cloudflared, or the .msi).
# 2. Bring up the local prod stack first (Option A / LOCAL-PROD.md); confirm https://tiq.localhost works.
# 3. Quick, throwaway tunnel (random *.trycloudflare.com URL, no Cloudflare account needed):
cloudflared tunnel --url https://tiq.localhost --no-tls-verify
#    --no-tls-verify is needed because tiq.localhost uses Caddy's local CA, which Cloudflare
#    does not trust. cloudflared prints a https://<random>.trycloudflare.com URL — share that.
```

For a repeatable named URL instead, use a Cloudflare account: `cloudflared tunnel login`,
`cloudflared tunnel create tiq-demo`, map a hostname on a domain you control in Cloudflare, then
`cloudflared tunnel run tiq-demo`. The client opens the URL on their phone and installs the PWA
from the browser menu.

**Honest caveats — read before you open a tunnel:**
- A tunnel exposes a **real, running instance** to the public internet for as long as it is up.
  Tear it down (Ctrl-C) the moment the demo ends.
- With `DEMO_MODE` on, the **demo master password is a live credential** and three real roles
  share it. Treat it as production access: rotate it after a public demo, and never paste it
  where the tunnel could log it.
- **Never point a tunnel at anything holding real borrower data.** The demo book is synthetic
  borrowers with real-format (not real) phone numbers; that is the only thing safe to expose.
  Keep every billable/outbound seam off (no Twilio, no live OTP echo) as LOCAL-PROD.md describes.
