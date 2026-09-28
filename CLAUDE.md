# TIQCollect: field collections platform

Field agents visit delinquent borrowers, log visits with GPS, photo and signature evidence,
collect payments verified by a borrower OTP, and record promises to pay (PTPs). A nightly
engine allocates tomorrow's cases and routes each agent's day. Managers get analytics,
compliance, a live agent map, visit-evidence anomaly detection and an LLM performance
narrative over their own team. The standalone plan adds a bank portal and multi-agency
tenancy (`docs/STANDALONE-PRODUCT-PLAN.md`). The current target is a paid pilot
(`docs/PILOT-PLAN.md`).

**Stack:** FastAPI + Postgres 16 + Redis 7 + MinIO + Celery (Python 3.12); React 19 + Vite +
TypeScript + Tailwind.

This file is an operational guide. It holds what is true for more than a week and points at
the rest:

| Question | Read |
|---|---|
| How is the code meant to be shaped, and where does each rule live? | `docs/ARCHITECTURE.md` |
| What is wrong with it today, measured? | `docs/ENGINEERING-AUDIT.md` |
| What are we changing, in what order? | `docs/RESTRUCTURE-PLAN.md`, `docs/PILOT-PLAN.md` |
| Why is X the way it is? | `docs/adr/`, then `git log -- <file>` |
| The data model being built | `docs/DATA-MODEL-V2.md` |

History does not live here (ADR 0006). The previous 2,702-line version is
`git show 495e4c6:CLAUDE.md`.

---

## Repository

- **Integration branch: `TIQCollect-app`.** Work happens on per-session branches in worktrees,
  merged after audit.
- **Remotes.** `origin` = `transorg-engineering/TIQCollect`: branches `TIQCollect-app` and
  `TIQCollect-product` carry this history. On that remote, `main`, `TIQCollect_final` and
  `tiq-demo` are an **unrelated June repo with no common ancestor**; never merge across. A
  second remote, `personal`, carries session branches. Push only on explicit instruction.
- **Three copies of the code exist.**
  - **This repo** is the one to edit.
  - **`collections-platform/field-ops-stub/`** (the Collections monorepo) is built and routed
    to `fieldops.transorg.ai`. It receives this repo through a 3-way patch merge
    (`docs/MERGING-INTO-PLATFORM.md`). Never edit it directly.
  - **`Desktop/TIQCollect/`** is a stale June copy.
- **The Collections Command Center** calls this API only as a manager service login:
  `/api/v1/auth/login` and 11 `/api/v1/manager/*` routes (`command-center/backend/routers/field_ops_manager.py`).
  Its `FieldAnalytics.jsx` and `FieldCases.jsx` are hand-maintained ports of pages here and
  drift silently. The old `/api/field-ops/*` contract was deleted on 2026-09-24 (no caller).

## Running it

```bash
docker compose up -d                      # api :8400, web :5473, postgres :15432, redis :16379, minio :19000
docker compose --profile routing up -d    # plus self-hosted OSRM (needs a map extract; see the compose block)
```

- **`backend/.env` is optional.** The compose file sets everything the app needs to start. It
  was written for the platform, and its `${VAR}` references resolve there, not here.
- **An empty database is filled from `backend/fixtures/fieldops-demo-v2.dump`,** the committed
  v2 demo book. `docker-entrypoint.sh` restores it in one transaction and then runs
  `alembic upgrade head`; a v1 database or a v1 dump is refused. `fieldops-demo.dump` is the v1
  book, kept only as the input of `scripts/migrate_v1_to_v2`. Provenance and the refresh recipe
  are in `backend/fixtures/README.md`.
- **Two Docker setups; keep both working.**
  - Dev: `docker-compose.yml` + `Dockerfile.dev`, hot reload over bind mounts.
  - Prod: `Dockerfile`, one non-root container serving the built SPA and the API, built by the
    platform. The same image runs the Celery worker and beat.
  - A change to what they consume (`requirements.txt`, `package.json`, the entrypoint, env
    names) must say in its commit what it does to each.
- **Demo-only behaviour has its own switch and is off by default:** `DEMO_MODE`,
  `DEMO_OTP_ECHO`, `DEMO_REHEARSAL_MODE`. Never enable `DEMO_OTP_ECHO` on a deployment people
  outside the team can reach: it hands the borrower's payment OTP to the agent.
- **Behind a reverse proxy, set `FORWARDED_ALLOW_IPS` to the proxy's own address,** or every
  user shares one login rate-limit bucket (`docs/MERGING-INTO-PLATFORM.md`).

## Verifying a change

```bash
cd backend  && python -m pytest -q          # no .env, no network needed: tests/conftest.py
cd backend  && python -m compileall -q app scripts
cd frontend && npm run build                # tsc -b + vite. NEVER `npx tsc --noEmit` (it checks nothing here)
cd frontend && npm test
cd frontend && npm run lint
```

- **On the shared machine, use the `tiq-verify` skill.** It runs these in a throwaway
  container with a safe env.
- **One full test suite at a time, machine-wide.** Take `.claude/fullsuite.lock` for any full
  pytest run or `docker build`, use `-n 4` at most, and never run tests inside the shared
  `fieldops_dev_*` containers.
- **Name targeted-run containers** `--name <lane>-<purpose>-<HHMM>`.
- **Measure counts, don't copy them.** Lines, tests and routes come from a command, not from a
  number in a doc.
- **CI** (`.github/workflows/ci.yml`) runs on `TIQCollect-app`, `standalone-*`, `lead-*` and
  `hotfix/**` pushes and on PRs. Jobs: backend (compileall + pytest), frontend (build, test,
  lint), docker (compose config, prod image build, size budget).

## Layout

```
backend/app/
  api/v1/endpoints/  agent.py (field app) · manager.py (agency view, being split: RESTRUCTURE-PLAN 2.4)
                     auth.py · events.py (SSE) · verify.py (public ID-card check) · health.py
  services/          use cases; own the transaction
  ml/                scorecards, the trained-model pipeline (ml/pipeline), the synthetic
                     generator (ml/simulation), committed model artifacts (ml/artifacts)
  models/ schemas/   SQLAlchemy mapping · Pydantic shapes
  core/              config · database · security · storage · llm · routing (OSRM + OR-Tools) ·
                     transcription · events (Redis pub/sub) · audit · ratelimit · geo
  workers/tasks/     Celery tasks (thin)
backend/scripts/     operator CLIs: seed, ingest, demo tooling, model training, validation
backend/tests/       pytest; tests/conftest.py fixes the environment and blocks the network
frontend/src/        api/ · lib/ (pure, tested) · components/ · pages/{agent,manager,auth,simulator} · store/
docs/                ARCHITECTURE · ENGINEERING-AUDIT · RESTRUCTURE-PLAN · PILOT-PLAN ·
                     STANDALONE-PRODUCT-PLAN · STANDALONE-TASKS · DATA-MODEL-V2 · adr/ · ui/
```

Domain chain:
- `Customer → Loan → Case → Visit → {Payment, PTP}`
- Around it:
  - `Beat` (one agent's routed day)
  - `AllocationRun` / `AllocationDecision` (why each case landed where it did)
  - `RepaymentSnapshot` (point-in-time scores)
  - `ModelPrediction` (every served model score)
  - `AgentLocation`, `FraudReview`, `CallLog`, `AuditLog`, `LeaveRequest`

## Conventions that hold the codebase together

- **One definition per rule** (ADR 0001, register in ARCHITECTURE §3). Import; never restate.
- **Comments say why, in three lines or fewer.** The history of a change goes in its commit
  message, with what was measured and what was not run. Decisions go in `docs/adr/`. No new
  CHANGELOG headers (ADR 0006).
- **Errors.** Services raise `AppException` with a typed `ErrorCode`. The frontend reads it
  through `lib/apiError.ts`.
- **Tenant scoping.** Today it is `Agent.manager_user_id == current_user.id`, largely inline;
  `_require_own_agent()` in `manager.py` is the shared helper. The standalone plan moves it to
  `scope.py` with a `RequestContext` (A02/A03).
  - `test_every_manager_route_that_reads_tenant_data_is_scoped` is **textual**. It cannot see a
    service that drops the scope.
- **External seams, one each:**
  - LLM: `core/llm.py`. It never raises; callers label fallbacks via `ai_generated`.
  - Speech-to-text: `core/transcription.py`.
  - SMS and WhatsApp: `services/notification_service.py`. Best-effort: it never raises and
    returns `bool`.
  - Routing: `core/routing.py`, with a Haversine fallback and a recorded `route_source`.
  - Storage: `core/storage.py`, presigned URLs only.
- **Twilio.** `TWILIO_WHATSAPP_FROM` defaults to Twilio's *sandbox* number. No Twilio
  credentials go anywhere a demo book runs: fixture borrowers have real-format phone numbers.
- **Load-bearing controls. Never remove any of these without the owner:**
  - JWT with `jti` plus device binding; bcrypt
  - single-use quick-login tokens
  - slowapi limits on the public auth routes
  - presigned MinIO URLs
  - the SPA catch-all refusing `/api`
  - the transcription fallbacks
  - the ML invariants in ADR 0005 and ADR 0007

## The scoring layers

Six scores, deliberately separate:

| Layer | Where | Kind |
|---|---|---|
| Repayment likelihood | `ml/repayment_scorecard.py` | hand-weighted scorecard (`scorecard-1.1.0`) |
| Recovery potential (rate 30/60/90) | `ml/recovery_scorecard.py` | hand-weighted scorecard (`recovery-scorecard-1.1.0`) |
| Visit priority | `ml/visit_priority.py` | hand-weighted |
| Agent competency | `ml/empirical_bayes.py` | closed-form shrinkage |
| Case ↔ agent match | `services/global_allocator.py` | Hungarian assignment over a scored cost matrix |
| **recovery_risk** P(pay next cycle) | `ml/pipeline/*`, artifact in `ml/artifacts/recovery_risk/` | **trained**; feeds the allocator's `prob_recovery` (ADR 0002) |

- **The serving champion is `recovery_risk` 2.2.0,** a 15-feature GAM. Read
  `ml/artifacts/recovery_risk/champion.txt`; don't trust any doc for this.
- **Its artifact figures are OOT Gini 0.5122 / KS 38.66.** The **live-equivalent** figures are
  **0.4796 / 35.74**, because the borrower-stance feature is never recorded by the product
  (ML-1, ADR 0008). Quote the live-equivalent figures until stance capture ships.
- **Everything trained here is trained on synthetic borrowers,** and says so.
- **Invariants** (ADR 0005): point-in-time features, no outputs as inputs, abstain rather than
  impute, `is_modelled` on every result, version bumps on any weight change.
- **Switches** in `core/config.py`:
  - `ML_SCORING_ENABLED` (on)
  - `ML_MODEL_VERSION` (`champion`)
  - `ML_AUTO_RETRAIN_ENABLED` (stops at human approval; ADR 0007)
  - `ALLOCATOR_EXPLORATION_RATE` (0.10; ADR 0003)
  - `REPAYMENT_WRITE_RISK_SCORE` and `RECOVERY_WRITE_LABEL` (both off)
- **Retraining is automatic up to PENDING_APPROVAL.** Promotion needs two different people.
  Monitoring waits for 500 matured account-days; the first labels mature from 2026-10-08.
- **Before changing anything in `ml/`,** read the artifact's development report
  (`MODEL_DEVELOPMENT.html` or `PRODUCTION_READINESS_CLOSURE.md` in the version folder) and
  ADRs 0002, 0004, 0005, 0007 and 0008.

## Scheduled work (IST, `app/workers/celery_app.py`)

```
19:15  model_outcomes.attach_model_outcomes      label matured predictions, compare labellers, monitor
19:45  repayment_scoring                          score every loan, snapshot on change
20:00  allocation.run_nightly_allocation          per manager: pool → gates → solve → route → PLANNED beats
02:00  beat_reconciliation                        plan vs GPS actuals (before the 03:00 sweep)
03:00  location_retention                         delete GPS rows older than LOCATION_RETENTION_DAYS
00:05  ptp_lifecycle · 00:10 leave_housekeeping   (leave sync also runs at API start-up)
05:30  demo_daily_feed (DEMO_MODE only) · 06:00 morning beat push · 09:00 PTP reminders
00:00 on the 1st  monthly performance snapshot
```

**The bank-file ingest, `scripts/ingest_daily.py`, is not scheduled.** It runs by hand.
Scheduling it as the first step of a nightly chain is RESTRUCTURE-PLAN 2.9. No stage records
its duration yet, so the nightly window has never been timed.

## Known issues (open)

Full list with evidence: `docs/ENGINEERING-AUDIT.md`. The short form:

1. **Lint fails:** 7 `react-hooks/set-state-in-effect` errors in `ManagerAnalyticsPage` (3),
   `AgentCaseDetailPage` (1) and `RecordVisitPage` (3).
2. **`manager.py` holds its business logic in the route layer,** with no typed responses. The
   split is in RESTRUCTURE-PLAN 2.4.
3. **"Today" is computed several ways.**
   - Backend: `_effective_today`, `_effective_day`, `leave_today`, and raw `date.today()`.
     Leave is sometimes decided against the last plan date instead of the calendar date.
   - Frontend: 9 places use a UTC date, which reads as yesterday before 05:30 IST.
   - The fix is RESTRUCTURE-PLAN 2.3.
4. **Schema authority is split.** Alembic, `seed_data.py` (`create_all`) and `ingest_daily.py`
   (`create_all`) all create tables, and a normal boot of a populated database never runs
   `alembic upgrade`. The P1 data model (43) replaces this.
5. **`AGENCY_ADMIN` grants nothing extra,** so every manager can promote a model. F12 closes
   this.
6. **Audit trail:** some declared audit actions are never written, and immutability is by
   convention only. Scoping reads drop system-written rows.
7. **Two rollout gates are off by design:** `REPAYMENT_WRITE_RISK_SCORE` and
   `RECOVERY_WRITE_LABEL`. Every manager surface reads snapshots.
8. **Analytics have three dimensions** (agent, DPD bucket, month). There is no breakdown by
   branch, city or product, although the columns exist.
9. **The prod image carries unused heavy dependencies** (xgboost and others). RESTRUCTURE-PLAN
   1.7 removes them.
