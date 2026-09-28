# Architecture

The target shape of TIQCollect, and the rules that keep it that shape. It is short on purpose.
What the code does *today*, measured, is in [ENGINEERING-AUDIT.md](ENGINEERING-AUDIT.md). How we
get from there to here, step by step, is in [RESTRUCTURE-PLAN.md](RESTRUCTURE-PLAN.md). The data
model is in [DATA-MODEL-V2.md](DATA-MODEL-V2.md). The product scope is in
[STANDALONE-PRODUCT-PLAN.md](STANDALONE-PRODUCT-PLAN.md).

Status: **proposal, 2026-09-24**, for the owner's approval.

---

## 1. Runtime

```
 browser / phone (one SPA: /agent · /manager · /bank)
        │ HTTPS
   reverse proxy (Caddy, docs/DEPLOY.md) ── sets X-Forwarded-For
        │
   api  (FastAPI, N uvicorn workers, stateless) ──── SSE /events/stream
        │         │           │            │
   Postgres     Redis       MinIO        OSRM (self-hosted)     external: LLM · SMS/WhatsApp · Whisper
   (source of   (broker,    (visit       (road matrix +
    truth)       cache,      media,       geometry)
                 OTP, pubsub, reports)
                 rate limits)
        │
   worker (Celery, queues: nightly · default · ml)   beat (one instance)
```

- **The API holds no state.** Anything two workers must agree on lives in Postgres or Redis:
  OTPs, rate-limit counters, event fan-out, the LLM cache.
- **The nightly engine is a chain on the `nightly` queue:** ingest → score → allocate, then route
  per agency. Each step is idempotent for its plan date, takes a per-scope lock, and has a time
  limit. Ad-hoc work (transcription, notifications) goes on `default`. Retraining goes on `ml`.
  One slow queue never starves another.
- **Every external call has a timeout, and none runs inside a test.** OSRM, LLM, SMS and Whisper
  each sit behind one adapter in `core/`, with a fake for tests.

## 2. Backend layers

```
api/  workers/          entry points: thin
   │
services/               use cases; own the transaction
   │
domain/  ml/            rules and models; no HTTP, no commits
   │
models/  schemas/       ORM mapping · request/response shapes
   │
core/                   infrastructure adapters and settings
```

Imports go **down** only. A structural test enforces it (RESTRUCTURE-PLAN step 2.1).

| Package | Holds | Must not |
|---|---|---|
| `api/v1/endpoints/<area>/` | One router per product area: `auth`, `agent` (field app), `agency` (today's manager view), `bank`, `events`, `health`, `verify`. A handler parses input, resolves the `RequestContext`, calls **one** service function, and returns a declared `response_model` | query the ORM, commit, compute a business number, or import another router |
| `workers/tasks/` | One task per schedule entry, carrying its time limit and queue. A task resolves its scope, calls a service, and logs a summary | contain business logic |
| `services/<area>/` | Use cases such as "record a visit", "plan tomorrow" or "the manager dashboard". They own the session's commit, call other services, and read the scoped queries | reach into `api/`, or read `settings` for a rule that belongs in `domain/` |
| `domain/` *(new, small)* | Pure business rules with no I/O: the business calendar (IST date, the demo "effective day"), money and phone formatting, status sets, enum labels | touch the database or the network |
| `ml/` | Scoring (`ml/pipeline/engine`, the feature adapter) and training (`ml/pipeline/*`). `ml/simulation/` is the synthetic data generator; tests and scripts import it, `api/` and `services/` never do | write to business tables |
| `models/` | SQLAlchemy mapping, enums, and rules intrinsic to a table (`Loan.dpd_bucket_for`) | query anything |
| `schemas/` | Pydantic models per router area: request bodies and every response | hold logic |
| `core/` | `config` (the only reader of the environment), `database`, `redis`, `storage`, `llm`, `routing` (OSRM), `notifications` (SMS/WhatsApp), `logging`, `security`, `audit`, `ratelimit`, `events` | know about a business concept |

`scripts/` holds operator command-line tools only. They are grouped as `ops/` (ingest, quick-login
link), `ml/` (training, datasets) and `demo/` (seed, reset, recorders). Each uses
`core.database`, and each write defaults to a dry run. Research and one-off repairs live in git
history, not in the tree.

## 3. One definition per rule

This repo's worst defects were two copies of one rule drifting apart. The table below is the
register: a rule lives in the one place listed, everything else imports it, and a tripwire test
guards each one that has already been copied once.

| Rule | Lives in |
|---|---|
| Who may see what (tenant scope, "an agent's case") | `services/scope.py` (A03) |
| What a role may do | the capability registry (A01) |
| KPIs (formula, label, unit, direction) | `services/bank/kpi_catalog.py` (C01) |
| DPD → bucket | `models/loan.dpd_bucket_for` |
| Business date, "today", contact hours | `domain/calendar.py` |
| Case, PTP and visit status sets | `models/case.py`, `models/ptp.py`, `models/visit.py` |
| Enum → display label (backend) | `domain/labels.py` |
| Enum → label and colour (frontend) | `frontend/src/lib/labels.ts` |
| Money and phone formatting | `domain/format.py` · `frontend/src/lib/format.ts` |
| Model outcome label | `ml/pipeline/outcomes.py` (its only writer) |
| Allocator weights and value transform | `services/global_allocator.py` constants |
| Settings | `core/config.py` (the only reader of `os.environ`) |
| Synthetic-data warning text | `ml/SYNTHETIC_WARNING` |

## 4. Frontend

```
src/
  app/            router, providers, layouts (one shell component, two nav configs)
  api/            one HTTP client (axios) + one module per area; types generated from OpenAPI
  lib/            pure helpers: format, dates (IST), labels, geo, polyline — unit-tested
  components/ui/  primitives (Button, Card, Badge, Modal, Table …)
  features/<area>/<feature>/   the components, hooks and pure logic of one feature
  pages/          route components that compose features — aim for under 300 lines
  bank/           the Command Center port (scoped styles, §2.5 of the product plan)
```

- **Server state goes through React Query only.** A page never holds fetched data in `useState`.
- **One HTTP client.** `fetch` is used only for presigned MinIO uploads and the SSE stream, both
  in `lib/`.
- **"Today" is the IST business date from `lib/dates.ts`**, never `toISOString().slice(0,10)`.
- **Heavy libraries load when used.** The voice SDK loads when the agent taps call; charts load
  on manager routes; maps load on map routes.

## 5. Data

- **Postgres is the source of truth**, organised as one database with schemas per domain
  ([DATA-MODEL-V2.md](DATA-MODEL-V2.md)).
- **Append-heavy tables are range-partitioned by month and have a retention rule:**
  `agent_locations`, `allocation_decisions`, `model_predictions`, `audit_logs`,
  `loan_dpd_history`. Retention is a setting with a documented owner, never "forever by
  accident".
- **An index exists because a query needs it,** and it leads with the tenant column. A
  single-column index that is a prefix of a composite one is not kept.
- **Connections:** API workers × pool size + worker processes × pool size must stay below the
  server's limit, with PgBouncer in front in production.
- **Media is stored in MinIO/S3 by key,** served by presigned URL, and moved to a colder
  storage class by a lifecycle rule once it passes the retention the owner sets.

## 6. Configuration, security, operations

- **Configuration.** One `Settings` class. In production a missing secret stops the process at
  start-up. Tests set their own values in `tests/conftest.py` and never read a developer's
  `.env`. A demo-only behaviour gets its own `DEMO_*` switch, default off, and is never switched
  on by `DEMO_MODE` alone.
- **Behind a proxy,** the client address comes from `X-Forwarded-For`, and only from the
  networks in `FORWARDED_ALLOW_IPS`. The rate limiter keys on that address and stores its counts
  in Redis.
- **Two Docker setups, both kept:** dev (`docker-compose.yml` + `Dockerfile.dev`, hot reload, no
  `.env` needed) and prod (`Dockerfile`: one image serving the built SPA and the API, run as a
  non-root user, with a `HEALTHCHECK` on `/api/v1/ready`). Production dependencies are
  `requirements.txt`; test and research tools live in `requirements-dev.txt`.
- **Logging.** structlog emits JSON with a request id. Every swallowed exception is logged at
  WARNING or above with its type.

## 7. Tests

| Layer | What | Where |
|---|---|---|
| Pure | `domain/`, `ml/` maths, `frontend/src/lib` | fast, no I/O |
| Service | use cases on the shared test engine (`tests/_db.py`, B02) | SQLite |
| Postgres | partitions, RLS, materialised views, migrations | `tests/pg/` (B19) |
| API contract | every route's `response_model` | TestClient |
| Frontend | pure modules, then component tests for each page's key states | vitest + testing-library |
| Journey | login → visit → payment → manager sees it | Playwright (`frontend/e2e`) |

- **No test touches the network.** OSRM, LLM, SMS and Whisper are faked in `conftest.py`.
- **A test executes behaviour.** Reading source text is allowed only as a labelled tripwire for
  §3's "nobody restates this rule".
- **A refactor commit keeps every test green unchanged.** A behaviour fix is its own commit with
  its own failing-first test.

## 8. Comments and documentation *(proposal: DECIDE, it replaces a CLAUDE.md convention)*

- **A comment says why, in at most three lines, next to the code it explains.** A measured
  number that justifies a constant stays in that comment.
- **No new CHANGELOG headers or "this used to say…" paragraphs.** History lives in commit
  messages (which already carry the measurements) and in **ADRs**: `docs/adr/NNNN-title.md`,
  one decision each, dated, with context, the decision, the evidence and its consequences.
- Existing headers are not deleted in bulk. When a file is next restructured, its header's
  still-relevant decisions move into an ADR and the header goes in the same commit.
- **CLAUDE.md is an operational guide** of about 300 lines or fewer: what the product is, how to
  run it, how to verify a change, the layer and rule map (this file), and pointers. It holds
  nothing that is only true for a week.

## 9. How we work

- **Model use.** Opus for architecture, security, data-model and ML decisions. Sonnet subagents
  for implementation, tests and review. Haiku (`tiq-explorer`) for search, counting and log
  reading.
- **Before handover:** `tiq-self-review`, then `tiq-verify`. One full test suite runs
  machine-wide at a time (`.claude/fullsuite.lock`, `-n 4` at most).
- **Worktrees live outside OneDrive** (`C:\dev\tiq\…`). The shared Docker stack is never
  restarted by a lane.
- **A number in a commit message, doc or comment was measured,** and says where and when.
