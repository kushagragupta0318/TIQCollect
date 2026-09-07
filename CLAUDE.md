# TIQCollect — Field Collections Platform

Feet-on-street debt recovery: field agents visit delinquent borrowers, log visits
with GPS/photo/signature evidence, collect payments verified by a borrower OTP, and
record PTPs. A nightly engine allocates tomorrow's cases and routes each agent's
day. Managers get analytics, compliance, a live agent map, visit-evidence anomaly
detection and an LLM performance narrative over their own team.

FastAPI + Postgres + Redis + MinIO + Celery on the backend; React 19 + Vite +
TypeScript + Tailwind on the frontend. Python >= 3.12.

**Measured 2026-09-07**, not estimated: 20,892 lines under `backend/app`,
10,982 under `backend/scripts`, 17,863 under `frontend/src`, 8,530 of backend
tests (574, all passing).

> **The working tree is dirty and nothing is committed.** 34 entries are modified,
> new or deleted against `86eb6d0`. If you are reading this in a fresh session, run
> `git status` before assuming the tree matches the last commit.

## Provenance — read this first

This repo was extracted on **2026-08-17** from the `Collections` platform monorepo
(`field-ops-stub/`) via `git subtree split`, so its 20 inherited commits are that
subdirectory's history with paths rebased to root. Everything after `7c30095` was
written here. 86 commits in total.

**Three copies of this codebase exist.** This one is the only one that should be
edited:

| Location | Status |
|---|---|
| **this repo** | source of truth — work here |
| `Desktop/Collections/field-ops-stub/` | frozen. Still built by the platform's `docker-compose.yml` and routed by Caddy at `fieldops.transorg.ai`. Do not edit. |
| `Desktop/TIQCollect/` | the original standalone repo (June, GitHub remotes at `transorg-engineering/TIQCollect`). Stale since 2026-08-03. Shares **zero commits** with this history — the monorepo copy was pasted, not subtree-added. |

That third copy is stale but not worthless: `.github/workflows/ci.yml` and
`backend/scripts/add_recovery_potential.py` came from it. Anything else needed from
it must be copied by hand — the histories cannot be merged.

**Remote: one, personal.** `origin` is
`github.com/sanyasirao-col/TIQCollect-product` — the owner's own account. Do
**not** push to `transorg-engineering`; that was ruled out on 2026-08-17 and
still is. Pushing to the personal remote is fine on explicit instruction.

*(This block used to read "Remotes: none, deliberately — this repo is
local-only." That stopped being true some time before 2026-08-21 and misled
anyone reading it; corrected rather than deleted so the change is visible.)*

**Branch state, verified 2026-09-07.** `origin/main` is still `8d27a14`
(2026-08-17) — `main` has not moved in three weeks. Work lives on the
`TIQCollect-v*` branches; the current branch `TIQCollect-v2-2` is at `86eb6d0`
and **is** pushed (`origin/TIQCollect-v2-2` matches). Nine remote branches exist.

Command Center (in the platform monorepo) consumes this service's
`/api/v1/manager/*` endpoints through a per-agency service login. Its
`FieldAnalytics.jsx` / `FieldCases.jsx` are **hand-maintained ports** of
`ManagerAnalyticsPage.tsx` / `ManagerCasesPage.tsx` — changes here silently drift
from those. Merge procedure and its traps: [docs/MERGING-INTO-PLATFORM.md](docs/MERGING-INTO-PLATFORM.md).

## Running it

```bash
docker compose up -d
```

`backend/.env` is required and gitignored. It was written for the platform and
refers to shared values as `${VAR}`, which no longer resolve — either substitute
real values or delete those lines and rely on the `${VAR:-default}` fallbacks in
`docker-compose.yml`.

Eight services: `postgres` · `redis` · `minio` · `api` · `web` · `celery_worker` ·
`celery_beat` · `seed`. The worker and beat share the API image. Every host port
is overridable by env var; the defaults are:

| Service | Host port |
|---|---|
| API | `8400` → 8000 |
| Web | `5473` |
| Postgres · Redis · MinIO | `15432` · `16379` · `19000` |

Seeding is **destructive** (`scripts/seed_data.py` drops every table and every
public-schema enum with CASCADE). `docker-entrypoint.sh` gates it on whether
`public.agents` exists, so it runs only on an empty database, and only in the API
container (`RUN_SEED=true`). Straight after a successful seed the entrypoint also
takes the demo baseline snapshot (`scripts/demo_reset --save`) — the only moment
the showcase case is provably clean.

Verify a change with all four, because each catches what the others miss:

```bash
cd backend  && python -m pytest          # 574 tests, ~25s, no DB or network
cd backend  && python -m compileall app
cd frontend && npm run build             # tsc -b + vite — the real typecheck
cd frontend && npm run lint              # 18 errors left (was 53) — see issue 1
```

**Use `npm run build`, never `npx tsc --noEmit`.** The root `tsconfig.json` is a
solution file — project references and no files of its own — so `tsc --noEmit`
resolves nothing and passes unconditionally. It reported success for weeks while
the production build was failing on a type error in `RecordVisitPage.tsx`. Both
were fixed on 2026-09-06 and CI now runs `tsc -b`.

**CI (`.github/workflows/ci.yml`) fails on the frontend lint step, and only that
step.** The backend job (3.12, `compileall`, `pytest -q`) and the frontend
typecheck both pass. The workflow triggers on pushes to `main` and on PRs.

## Layout

```
backend/app/
  api/v1/endpoints/   agent.py (33 routes, 1.1k lines) · manager.py (33, 4.0k)
                      auth.py (5) · field_ops.py (4, Command Centre contract)
                      health.py (2)
  services/           case · visit · payment · otp · auth · agent · media · notification
                      ai_report · demo · fraud · location · repayment · visit_priority
                      planner · global_allocator          (16 files)
  core/               config · security · database · dependencies · errors · geo
                      routing (OSRM + OR-Tools VRPTW) · llm (provider seam)
                      transcription (Whisper seam) · storage (MinIO)
  models/             21 files → 19 mapped tables (two are dead — see below)
  ml/                 repayment_scorecard · recovery_scorecard · visit_priority
                      empirical_bayes · eligibility · allocator · repayment (tier seam)
                      recovery_validation · shadow_evaluator · train_shadow_model
  workers/tasks/      allocation · repayment_scoring · beat_generation · ptp_reminders
                      performance_snapshot · location_retention · transcription
                      demo_daily_feed
  scripts/            seed_data · ingest_daily · synthetic generation + validation
                      demo tooling · one-off repairs
frontend/src/
  pages/agent/        AgentHome · AgentCases · AgentCaseDetail (1.3k)
                      RecordVisit (2.3k) · BeatMap · AgentProfile
  pages/manager/      Overview (1.4k) · Cases (1.2k) · Agents (1.3k)
                      Analytics (1.6k) · Compliance · LiveMap
  pages/auth/         Login · QuickLogin · ManagerBridge
  api · components · contexts · hooks · lib · store · types
docs/                 PLAN.md · MERGING-INTO-PLATFORM.md · case-allocation.{html,pdf}
                      recovery-calibration.html · rollback/ · validation/
ML-PLATFORM-PLAN.md   root, 570 lines, dated 2026-09-07. Planning only — its own
                      header says no code changed. Not a description of what
                      exists; read it as intent, not inventory. Its §1.2 quotes
                      `ml/models/shadow_model_metadata.json`, which is GITIGNORED
                      and regenerated per run, so those numbers cannot be checked
                      from a fresh clone.
```

Domain chain: `Customer → Loan → Case → Visit → {Payment, PTP}`, plus `Beat` (one
agent's routed day), `AllocationRun`/`AllocationDecision` (why every case landed
where it did), `RepaymentSnapshot` (the ML spine), `AgentLocation`, `FraudReview`,
`CallLog`, `AuditLog`, `AllocationSetting`, `AgentPerformance`,
`QuickLoginToken`.

`models/document.py` and `models/case_photo.py` are **dead** — absent from
`models/__init__.py` and imported nowhere, so their tables are never created.
`Document` declares `back_populates="documents"` against `Visit`, which has **no
such relationship** (verified: zero occurrences of "documents" in `visit.py`), so
importing it would break the SQLAlchemy mapper for the whole app. Delete them or
wire them up; do not import them casually. (`base.py` is the declarative base, not
a table — that is why 21 files give 19 tables.)

## The scoring layers

Five distinct scores, deliberately not one. This is the part of the codebase most
worth understanding before changing anything in `ml/`.

| Layer | Module | Question | Grain |
|---|---|---|---|
| Repayment likelihood | `ml/repayment_scorecard.py` | will this borrower pay? | loan |
| Recovery potential | `ml/recovery_scorecard.py` | what share comes back, how fast? | loan |
| Visit priority | `ml/visit_priority.py` | which case first? | case |
| Agent competency | `ml/empirical_bayes.py` | who is good at work like this? | (agent, loan type, DPD) |
| Case ↔ agent match | `services/global_allocator.py` | who gets it tomorrow? | pair |

**None of these is a trained model.** Four are hand-weighted scorecards; the fifth
(`empirical_bayes`) is a closed-form shrinkage estimator; the allocator is a
Hungarian assignment. The only trained artifact in the repo is
`ml/train_shadow_model.py`, which is shadow-only — see below.

Three rules hold the set together, and all three are enforced in code rather than
by convention:

- **No output of this system may be an input to it.** `_FORBIDDEN_FEATURE_KEYS` in
  `services/repayment_service.py` **raises** if a feature dict contains one. The
  ban runs both ways: the recovery scorecard never sees the repayment likelihood
  either. Recovery is deliberately *not* `P(pay) × haircut` — kept independent so
  it can say "unlikely to pay, HIGH to recover" about a hostile borrower on a
  secured loan. That disagreement is the most useful thing the pair produces.
- **A factor with no evidence abstains; it never scores zero silently.** Its weight
  is withheld from the coverage denominator, and coverage is *not* renormalised —
  a thin borrower must read as thin, not as confidently average.
- **Nothing hand-weighted may present itself as a model.** `is_modelled` travels on
  the object (`ScoreOutcome`, `RepaymentScore`, and `LLMResult.ai_generated` for
  the LLM seam), so a UI cannot render a scorecard behind an "AI" chip by
  forgetting to check. There is **no AUC, Gini or accuracy figure anywhere** —
  verified; the only such words in the tree are the scorecards saying they have
  none, and the shadow trainer's own holdout metrics.

Both scorecards carry a version string stamped onto every snapshot row —
`scorecard-1.1.0` and `recovery-scorecard-1.1.0`. **A weight, band edge or
factor-definition change is a model change: bump the version.** Rows written under
two versions answer different questions, and a model trained across both without
filtering learns two scorecards at once.

Monotonicity in the recovery scorecard is *structural*: score the eventual 90-day
rate once, then multiply by a bounded speed fraction, so `rate_30 ≤ rate_60 ≤
rate_90` holds for any input and survives any future reweighting.

`RepaymentSnapshot` is what could eventually make a trained model possible: one
frozen, point-in-time row per (loan, day) — features as they stood, the score, and
later the outcome the labeller fills in. Point-in-time correctness is the whole
value: `Loan.dpd`, `PTP.status` and `Loan.last_payment_date` are all overwritten in
place with no history, so a feature read "as it is now" leaks the future. There is
no way to call `build_features` without an `as_of` date, and `is_backfill` marks
rows that cannot be made honest.

`train_shadow_model.py` is **shadow only** — nothing writes a score from it and no
allocation path reads it. The metadata in `ml/models/` is gitignored and currently
reports a *synthetic* run, saying so in its own `SYNTHETIC_WARNING` field. Real
labels cannot mature before 2026-09-23 (30d) / 2026-11-22 (90d).

**Before training anything here, look next door.** The Collections platform
already ships trained models at `Desktop/Collections/command-center/backend/models/`
— `risk_scoring`, `recovery_forecast`, `cure_rate_engine`, `bounce_predictor`,
`transition_probability`, `borrower_segmentation`, `outreach_optimizer`, with
seven fitted `.joblib` artifacts and `model_metrics.json` beside them. The
inbound contract to reach them already exists and this repo does not use it:
`GET /api/account-context/{loan_id}` on Command Center `:8000`, §4 of
`Desktop/Collections/FIELD_OPS_INTEGRATION.md`. A calibrated recovery probability
may be a matter of *calling* those rather than fitting new ones — which is also
the only route to a genuinely calibrated number before the 90-day labels land.

## The nightly pipeline

Schedule verified against `workers/celery_app.py`:

```
19:30  scripts/ingest_daily.py     bank CSV → DPD/amounts + bank_action
                                   (PAID_DIRECT · SETTLED · WRITTEN_OFF · RECALL ·
                                   DECEASED) → applies consequence AND labels snapshots
19:45  repayment_scoring           score every loan → roll up to the customer's WORST
                                   loan → snapshot only on change or anchor → attach
                                   matured outcomes → prune unlabelled rows
20:00  allocation                  per manager: pool filter → hard gates → bipartite
                                   solve → OSRM/OR-Tools route → PLANNED beats
05:30  demo_daily_feed             DEMO_MODE only
06:00  morning beat push  ·  09:00 PTP reminders  ·  03:00 location retention sweep
00:00 on the 1st  monthly performance snapshot
```

Fifteen minutes is the entire margin between ingest and allocation, which is why
`RepaymentService._load` bulk-loads instead of querying per loan.

The allocator (`SMART` strategy) is two-stage: a Hungarian solve
(`scipy.linear_sum_assignment`) over a case × capacity-slot cost matrix, then
route-feasibility validation that defers unroutable outliers. Hard gates run
*before* scoring — DNC, hostility, female-agent requirement, 16 km territory, and
**PTP fatigue** (three broken promises to one agent bars *that agent*, never the
case, so the solve picks the next best by itself). Every decision is persisted with
its score breakdown; `LEGACY` is a round-robin kept for comparison.

**The three objective tables each sum to 1.05, not 1.0** (verified: BALANCED is
0.45/0.40/0.05/0.05/0.05/0.05). `argmin` is invariant under positive scaling so no
decision is affected, but a weight documented as 0.45 is really 0.45/1.05 = 42.9%
of the decision. Read the tables as ratios, not percentages.

Full specification: [docs/PLAN.md](docs/PLAN.md).

## Conventions

- Files carry `# ─── CHANGELOG (prototype → product) ───` headers documenting what
  changed and **why**. Follow this when making non-obvious changes — it is the most
  valuable documentation in the codebase, and most of it records defects found by
  *measuring the live book*, not by reading code. Quote the number.
- Those headers cite `changelog.md` and `final_changes.md`. **Neither file exists
  anywhere** — 32 files reference them. Don't hunt for them; the header text is the
  whole record.
- Correct a wrong comment **visibly** rather than deleting it, saying what it used
  to claim and why that misled. See `models/loan.py:recovery_potential` and the
  provenance block above.
- One definition, one place. Most of this repo's worst bugs were two copies of one
  rule drifting apart: two `risk_score` formulas, two `recovery_potential` writers
  (both random), two sets of allocator weights, two `RESOLVED_STATUSES` sets, and
  most recently **seven** copies of the DPD→bucket rule. When you find yourself
  restating a rule, import it instead — and consider a test that asserts nobody
  restates it, because that is what found the seventh copy after reading found six.
- Services raise `AppException` with a typed `ErrorCode`, not free-text detail.
  `main.py` maps it to `{detail, code}`. On the frontend, `lib/apiError.ts` is the
  one reader of that contract.
- Tenant scoping is by `Agent.manager_user_id == current_user.id`, still
  hand-repeated at most call sites. `_require_own_agent()` in `manager.py` is the
  shared helper — use it for anything new. A structural test
  (`test_every_manager_route_that_reads_tenant_data_is_scoped`,
  `test_manager_endpoints.py:494`) walks every route in the file and fails on one
  that neither scopes nor is allowlisted. **It is textual**, so it verifies the
  endpoint *mentions* scoping — it cannot see a service that drops it, which is
  exactly how the `export-decisions` leak survived it.
- All LLM calls go through `core/llm.py` — one seam, provider by settings
  (`groq` default, `openai`, `none`), classified failures, Redis-or-memory cache,
  per-purpose counters on `GET /manager/ai/health`. It never raises; callers read
  `ai_generated` and label the fallback rather than passing it off as AI. It
  **does** support `response_format: json_object`, which matters for feature #2
  below. Same shape as `core/transcription.py` for Whisper.
- Root `main.py` is the old `:8300` Command Center dev stub. **Nothing consumes
  it.** Safe to delete. **So is `backend/stub_main.py`** (163 lines, tracked):
  its own docstring says it serves `:8300` from `field-ops-stub/backend`, a path
  in the *other* repo, and nothing here imports or runs it. *(This line used to
  claim stub_main.py was "already gone". It is not — verified 2026-09-07 by an
  AST pass over every import, celery `include=[]` string and router
  registration. Corrected rather than deleted so the wrong claim is visible.)*
  Root `requirements.txt` (two lines) fed only those two stubs; CI and both
  Dockerfiles use `backend/requirements.txt`. `app/schemas/manager.py` is dead
  too — all three of its models are referenced nowhere.
- **Do not regress these**, they are load-bearing and were each fixed once: JWT
  with `jti` + `device_id` binding; bcrypt; single-use quick-login tokens (the
  90-day-reusable-token incident is documented in `core/security.py:1`); slowapi
  rate limiting; presigned MinIO URLs; the SPA catch-all in `main.py` that refuses
  to swallow `/api` paths; and `core/transcription.py`, which is visibly debugged
  against real mic audio rather than clean test files — every filter in it is a
  fallback, not a hard gate, because hard gates made it return empty strings.

## Feature coverage

Specified against a 20-feature *AI-Powered Field Recovery Platform* reference
document. Re-verified against the code on 2026-09-07: **6 built · 10 partial ·
4 missing.**

| # | Feature | | Where it stands |
|---|---|---|---|
| 1 | AI Field-Agent Copilot | ✅ | `/agent/cases/{id}/visit-strategy` — LLM brief with a rule-based fallback, and the agent is told which one they got |
| 2 | AI Voice → Automatic Visit Report | 🟡 | STT is real and hardened; the report is prose built from *already-structured* fields. Nothing extracts disposition / PTP amount / date out of the speech — the agent still types all of it. **`core/llm.py` already supports JSON-mode output**, so this is one structured-output call away. Highest-value gap on the list |
| 3 | AI Recovery Priority Score | ✅ | `ml/visit_priority.py` — recoverable value, urgency around the NPA line, effort spent. Scorecard, not a model |
| 4 | AI Next-Best-Action Engine | ❌ | No endpoint. Nothing chooses visit vs call vs reminder vs settle vs escalate |
| 5 | Recovery-Optimized Route Planning | 🟡 | The *assignment* weights expected recovery. The *sequence* inside a beat is pure travel-time TSP with no reference to recovery at all |
| 6 | Borrower 360° Profile | ✅ | `AgentCaseDetailPage`, 6 tabs. Disputes are still a visit outcome, not an object with a lifecycle |
| 7 | AI Recovery Probability & Expected Recovery | 🟡 | `ml/recovery_scorecard.py` computes rate 30/60/90 + `expected_recoverable_amount`, snapshotted and surfaced. Hand-weighted and **uncalibrated** — no real outcome matures before 2026-11-22 |
| 8 | AI Settlement Recommendation | ❌ | `loan.settlement_status` is a read-only bank flag. No range, no policy, no approval workflow |
| 9 | AI Agent Performance Intelligence | ✅ | Performance, AI insight, reallocation plan, monthly report, leaderboard, DPD and attendance breakdowns |
| 10 | AI Fraud & Anomaly Detection | ✅ | `services/fraud_service.py` — 7 finding types (impossible travel, overlapping visits, photo-location mismatch, duplicate photos, short visits, far-from-customer, trail contradiction) over evidence already captured. Manager review; verdicts stored as future training labels. Rules, not a model, deliberately |
| 11 | AI Compliance Monitor | 🟡 | Rules genuinely enforced (RBI hours, 100m fence, DNC, consent). No AI pattern analysis; thresholds hardcoded, not configurable |
| 12 | Live Recovery Command Center | 🟡 | Map and location trail exist. **Nothing is push-based** — verified: no WebSocket, no SSE anywhere in the tree, only polling. The three "WebSocket" hits are all comments |
| 13 | Recovery Risk Radar | ❌ | Every input already exists and is already on the wire. **Pure frontend work — cheapest item on this list** |
| 14 | Digital Payment & Instant Receipt | ✅ | Unique receipt numbers, Razorpay UPI QR, SMS + WhatsApp receipts, borrower-OTP verification. No reconciliation workflow |
| 15 | Offline-First Field App | ❌ | The banner is honest and text drafts persist per case, but — verified — there is **no service worker, no IndexedDB, no outbox** anywhere. An agent still cannot complete a visit without signal |
| 16 | Evidence & Immutable Case Timeline | 🟡 | Capture is thorough. The audit trail is 8 of 22 actions (issue 4) and there is no unified timeline view — evidence is scattered across tabs |
| 17 | Customer Engagement Hub | 🟡 | Agent-triggered one-offs only. No campaigns, scheduling, PTP follow-up automation, templates or unified comms log |
| 18 | Smart Work Queue & Gamification | 🟡 | The queue is real. Gamification is manager-side only — the agent cannot see their own standing |
| 19 | Recovery Forecasting & Portfolio Analytics | 🟡 | Analytics are strong and all historical or current-state. **No forecasting anywhere** |
| 20 | Continuous Learning & Management Insights | 🟡 | The machinery exists — snapshots, labeller, shadow trainer, a validation framework written before the outcomes. The loop is not closed: nothing has been trained on a real outcome yet |

The four missing items split cleanly: **#13 and #15 need no ML at all**; #4 and #8
depend on judgement layers that do not exist yet.

## Known issues — open

1. **The frontend lint job fails — 18 errors, all `react-hooks/set-state-in-effect`.**
   Down from 53 on 2026-09-06; the other 35 were fixed on 2026-09-07 (20
   `no-explicit-any`, 7 unused vars, 2 needless exports, and four that were real
   bugs — see the fixed list below). What remains is concentrated in the six
   largest pages, `ManagerAnalyticsPage` alone holding 7. Each is a
   behaviour-affecting restructure on pages with **no test coverage at all**
   (issue 6), which is why they were not swept up with the rest. This is the only
   thing between CI and green.
2. **No `/verify-agent` endpoint exists.** `core/security.py:78`'s
   `create_agent_verify_token` mints a signed token for the QR on the agent's ID
   card, and its own docstring describes "the public /verify-agent endpoint" that
   validates it. Verified: no such route exists anywhere in `api/`. A borrower
   scanning the card has nothing to check it against, so the anti-impersonation
   control is inert. The Compliance page states this rather than claiming ID
   verification.
3. **The audit trail is mostly declared and unwritten.** `AuditLog` defines 22
   action types; **8 are emitted** — `LOGIN`, `LOGIN_FAILED`, `LOGOUT`,
   `TOKEN_REFRESH`, `DEVICE_MISMATCH`, `PAYMENT_VERIFIED`, `PTP_UPDATED`,
   `ANOMALY_REVIEWED`. `VISIT_RECORDED`, `PTP_SET`, `CASE_ASSIGNED`,
   `BEAT_GENERATED`, `DATA_EXPORT` and nine others are defined and never written.
   Immutability is convention only — no trigger, no revoked grant.
4. **The audit-log read path has one deliberate blind spot.** `GET
   /manager/audit-log` and `/audit-log/export` share `_audit_log_query`, so the
   tenant scope cannot be dropped on one path and not the other. But the scope is
   `user_id IN (this manager + their agents)`, and `IN` drops NULLs — so
   system-written rows (a `PTP_UPDATED` when a payment honours a promise) never
   appear. Correct as a default; a real gap nonetheless. The API declares it
   (`excludes_system_rows: true`) and the page says so. Scoping them through
   `PTP → agent → manager` is the obvious extension and is not done.
5. **Two competing schema authorities.** Eight Alembic migrations exist, but
   `seed_data.py` does `drop_all` + `create_all` and — verified — **never touches
   `alembic_version` at all**. `docker-entrypoint.sh` arbitrates by checking for
   `public.agents`. Fine for a demo box; for production `alembic upgrade head` has
   to be the only path.
6. **`manager.py` is 3,967 lines of business logic in the route layer** — 33
   routes and **114 `db.query()` calls** sitting directly in endpoints while a
   working `services/` layer exists and is used by every agent flow. There is no
   `manager_service.py`. This is *why* the tenancy leaks happened: there is no
   single place where "the agents this manager owns" is defined, so it gets
   retyped. (Flagged at 2,316 lines on 2026-08-17 and 3,756 on 2026-09-06; still
   growing.)
7. **Frontend has no test tooling at all** — no vitest, jest, playwright or
   cypress. TS `strict` and eslint are configured, which is a good base. The six
   largest pages are 1.2k–2.3k lines each.
8. **Analytics have three dimensions: agent, DPD bucket, month.** Every `group_by`
   in the manager router is one of those (plus beat date). There is no breakdown by
   branch, city/geography or loan product — though `Customer.city`, `Loan.loan_type`
   and `Loan.branch_code` are all on the models and already populated.
9. **Two rollout gates are closed by default**, and that is deliberate — but it
   means the running system is not doing what a reader of `ml/` might assume:
   `REPAYMENT_WRITE_RISK_SCORE=False` (the scorer never touches
   `Customer.risk_score`) and `RECOVERY_WRITE_LABEL=False` (`Loan.recovery_potential`
   still holds whatever it held before, which on a seeded database is
   `random.choices()` output). Snapshots are written either way, and every manager
   surface reads the snapshot, never the Loan column. `REPAYMENT_REPRICE_OPEN_CASES`
   is a third flag that is honestly reported as *not implemented* rather than
   silently ignored.
10. **The planner throws away the OSRM matrix it just paid for.**
    `planner_service.py:526-527` derives the beat ETA as
    `haversine × 1.15 ÷ 25 km/h + 20 min per stop`, so **the ETA shown to agents
    and managers is never OSRM's answer**. Three service-time constants disagree
    in the same file — `AVG_VISIT_DURATION_MINUTES = 15`, the `20` above, and `25`
    in the exception fallback. Compounding it: `OSRM_BASE_URL` defaults to
    `router.project-osrm.org`, the rate-limited public demo server, and
    `fetch_osrm_matrix` silently falls back to Haversine on any failure — so
    nobody knows how often OSRM is even answering. `core/routing.py` documents
    `matrix_provider` as the swap-in seam and **no call site passes one**.
11. **`RepaymentService._received_within` (`repayment_service.py:994`) sums
    payments across EVERY CASE of the loan.** For the recovery scorecard, which
    predicts a LOAN-level rate, that is correct and intended. It stops being
    correct the moment anything asks a per-agent or per-case question: where a
    loan has had two cases under two agents, the money lands wherever the join
    happens to put it. Nothing reads it that way today — recorded because the next
    thing that wants "how much did this agent recover" will reach for this
    function first, and it will look right.

## Fixed on 2026-09-07, with the measurement

Kept because the numbers are the useful part, and because each was invisible until
something measured it.

- **The allocator's `spec_match` term had never once been 1.0.**
  `global_allocator.py` compared an `AgentSpecialization` (`SECURED` / `UNSECURED`
  / `BOTH`) with a `LoanType` (`HOME` / `AUTO` / `PERSONAL` / …) — **enums that
  share no member** — so the term was the constant 0.5 and the specialisation half
  of `skills_score` was a no-op. Now delegates to
  `ml/eligibility.specialisation_fit`, which both scorecards already imported.
  Measured on the demo book by loading the pre-fix file out of `git show HEAD:`:
  **2 of 227 assignments moved (0.9%)**, 4 cases swapped in and out of the plan,
  expected recovery total **−0.80%**, and the **BLOCKED set was identical (15 of
  15)** — the hard gates did not move, which was the safety property. Pinned by
  `tests/test_global_allocator.py`, which is also the first test coverage that
  file has ever had.
- **`RepaymentSnapshot.case_id` named an arbitrary case of the loan.** `score_loan`
  set it with `next((c.id for c in cases), None)` over a list built with no status
  filter and no `ORDER BY`. Measured on a 700-borrower synthetic book: **4,984 of
  6,459 snapshots (77.2%) named a case whose last visit was a median of 130 days
  earlier**, with up to 9 cases per loan. Not cosmetic —
  `scripts/backfill_eb_features.py` bridges snapshot→agent through this column, so
  the shadow model's own `eb_shrunk_win` feature was looked up via the wrong case
  on the same ~77% of rows. Now `_case_as_of` takes the most recently *created*
  case that existed by `as_of_date`. **Existing rows are not corrected.**
- **Seven copies of the DPD→bucket rule, two disagreeing with the enum's own
  comments.** `models/loan.dpd_bucket_for` is now the only one. No behaviour
  changed: the disagreeing copies lacked branches their DPD ranges (starting at 32
  and 35) cannot reach, which `tests/test_dpd_bucket.py` proves exhaustively
  rather than asserts. Worth doing because `EmpiricalBayesAgentAdjuster` is keyed
  on the bucket. *Reading the code found six; the test that asserts nobody
  restates the rule found the seventh.*
- **Four real frontend bugs**, found while clearing lint: a **conditionally-called
  hook** in `BeatMapPage` (called after two early returns, so hook order changed
  between renders); a **`Date.now()` read during render** in the SLA countdown;
  **two refs written during render** in `useAnimatedValue` (rewriting it revealed
  its state was only ever 0, so the hook collapsed to a delay flag); and — hidden
  by an `as any` — the gallery-upload path stamping photo GPS with only
  `lat/lon/time`, missing `accuracy`, `altitude` and `iso`. Because the submit
  payload reads `form.<x>PhotoGps?.iso ?? new Date().toISOString()`, a gallery
  photo's `captured_at` was recorded as **the moment of submission, not of
  capture** — on evidence attached to a compliance record.

## Fixed on 2026-09-06

Listed only so the next reader does not go looking for them: a `NameError` in
`payment_service._get_accessible_case` (`Agent` used, never imported); a
cross-tenant `GET /manager/allocation/export-decisions` (scoped in the *service*,
because the structural sweep is textual and could not see it); `plan_next_day`
crashing on its own documented default; the CI Python pin; a **broken frontend
production build**; a CI typecheck step that could not fail; the Compliance page's
six fabricated audit rows, replaced by real endpoints; and two scheduled tasks
reporting work they never did — `ptp_reminders` was marking `reminder_sent = True`
without sending, which *removed* those promises from every future run.

*`docs/AUDIT.md` — the 2026-08-17 audit these sections came from — was deleted on
2026-09-06 rather than left to drift beside this file, per the one-definition rule
above. Retrieve it with `git show 674402e:docs/AUDIT.md` (311 lines).*
