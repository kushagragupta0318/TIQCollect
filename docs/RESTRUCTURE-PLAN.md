# Restructure plan

Ordered, small, reversible steps from what [ENGINEERING-AUDIT.md](ENGINEERING-AUDIT.md) measured
to what [ARCHITECTURE.md](ARCHITECTURE.md) describes. Each step is one or a few commits, keeps
behaviour unless it says otherwise, and names the test that proves it.

**Status:** proposal, 2026-09-24, tiqcollect-bb. **Nothing destructive happens before the owner
approves.** The capacity and cost model is §4. The decisions the owner is asked for are §1.

Rules for every step:
1. **Characterise first.** Code with no tests gets characterisation tests *before* it moves.
2. **A refactor commit changes structure, never behaviour.** A behaviour fix is its own commit,
   with a test that fails first.
3. **Every deletion carries its proof in the commit message:** the grep with its hit count, the
   import graph, the route-to-caller map. One logical group per commit, so any one can be
   reverted alone.
4. **Load-bearing controls are never removed without the owner:** JWT `jti` plus device binding,
   bcrypt, single-use quick-login, slowapi limits, presigned MinIO, the SPA catch-all refusing
   `/api`, transcription fallbacks, and the ML invariants (point-in-time features, the one outcome
   labeller, versioned artifacts, the gates, the champion pointer).
5. **Lanes.** Files an in-flight lane is editing are touched only in the integration window
   after that lane merges, agreed through tiqcollect-64.
6. **"Done" means the brief's gates:**
   - lint 0 errors; CI green on every job
   - the backend suite green in a container with a documented env and no `.env`
   - `compileall`, `npm run build` and `npm test` green
   - both Docker setups build and start

---

## 1. Decisions for the owner

| # | Decision | Recommendation | What it unlocks |
|---|---|---|---|
| D1 | Merge `hotfix/live-security-2` (`062e019`): the real client IP behind Caddy, and the OTP echo only under `DEMO_OTP_ECHO` | **Yes.** The demo site's OTP step then needs a real SMS, or someone must set `DEMO_OTP_ECHO=true` there, which knowingly reopens the hole | closes SEC-1, SEC-2 |
| D2 | Comment and doc standard (ARCHITECTURE §8): why-comments of three lines or fewer, history in git + ADRs, no new CHANGELOG headers, CLAUDE.md as a guide of about 300 lines | **Yes.** It replaces the CLAUDE.md convention going forward; existing headers migrate as files are touched | step 1.11, 2.15 |
| D3 | Delete `backend/Dockerfile`; shrink `backend/pyproject.toml` to tool config | **Yes.** Nothing references them, and the build backend they name does not exist | step 1.7 |
| D4 | The `/api/field-ops/*` contract (446 lines + a required secret) | **Delete.** The Collections doc says it has no consumer, and the Command Center now calls `/manager/*` through a service login. It conflicts with A15's "preserve", so this is the owner's call | step 1.6b, one fewer required secret |
| D5 | Research code and data: 21 `scripts/research` files + 14 top-level study scripts, `ml/recovery_validation.py`, `ml/train_shadow_model.py`, their 3 test files (1,054 lines), `docs/validation` (2.7 MB), `docs/rollback` (1.5 MB), 8 one-off repair scripts | **Archive tag `archive/research-2026-09`, then delete.** Kept: the champion's training script, the dataset builders, `phase2_ledger_validation` (43's simulator tests) and `shadow_allocation_ml` (it pins the live allocator's value transform) | about 9,800 lines and 4.2 MB |
| D6 | ML artifacts inside the prod image: 11.5 MB, of which 1.1 MB is served | **Keep them in git; leave the dev evidence out of the image** via `.dockerignore`. Superseded versions (1.0.0, 1.2.0-ledger, 2.0.0-ref-v1spec, the failed contact_risk) move to the archive tag. 1.1.0 stays for rollback | smaller image; nothing served changes |
| D7 | `plan_fleet` (142 lines, built and tested, never wired) | Keep **only if** wiring the multi-vehicle CVRPTW is on the roadmap (feature #5); otherwise delete | — |
| D8 | Docs nothing links to: `decision-flow.pdf`, `TIQCollect-feature-tracker.xlsx`, `COMPETITIVE-ANALYSIS.md`, `CALL-ROUND-PLAN.md`; stale `case-allocation.{html,pdf}`, `PLAN.md`, root `ML-PLATFORM-PLAN.md` | Business docs **move** to `docs/business/` (business lead). Stale engineering docs are **deleted**, their true parts absorbed into ARCHITECTURE and ADRs | step 1.11 |
| D9 | Prediction logging: every re-plan re-logs the whole pool (the monitor already de-duplicates 4.3×), and `model_predictions` has no retention | **Log once per (case, as_of_date); keep 13 months.** It touches the ML feedback loop, so it is the owner's call | about 7 GB/month less per 1,000 agents (§4) |
| D10 | Visit-notification SMS + WhatsApp to the borrower on **every** visit (`visit_service.py:407`) | A business-lead question. With 15 visits a day this is the largest variable cost (§4.4) | — |
| D11 | Frontend test tooling: add `@testing-library/react` + `jsdom` (dev only) | **Yes.** No page has a single test today, and the big pages cannot be split safely without them | step 1.10, 2.7 |
| D12 | Generated API types (`openapi-typescript`, dev only) replacing the 989 hand-written lines in `api/manager.ts` | **Yes, after** the routes have `response_model` (2.4) | step 2.6 |

---

## 2. Wave 1: now, in files no lane owns

Work happens on `lead-structure` (C:\dev\tiq\lead), one PR-sized group per step, with WIP
commits. The order is chosen so that each step makes the next one safer.

| Step | What | Files | Lines −/+ (estimate) | Risk | Proof (behaviour unchanged) | Collides with |
|---|---|---|---|---|---|---|
| **1.1** | **Tests independent of the machine.** `tests/conftest.py` sets the 5 required settings and `CONTACT_HOUR_*` before app import, and autouse fakes block OSRM, LLM, SMS and Whisper from reaching the network | new `tests/conftest.py`; planner tests stop calling public OSRM | −0/+80 | low | **The full suite passes in a container started with `--network none` and no `.env`.** Today that is impossible. Same pass count as the baseline run | 43 owns `tests/_db` and the planner tests: agree the fake's shape first (coordinator) |
| **1.2** | **CI that runs and can pass.** Triggers: push to `TIQCollect-app`, `standalone-*`, `lead-*`, `hotfix/*`, plus PRs and manual dispatch. Backend uses 1.1's env. Frontend adds `npm run build`. New job: `docker compose config -q` + prod image build (not pushed) + an image-size budget. Node 22 | `.github/workflows/ci.yml` | −15/+60 | low | the workflow's commands run locally in the same containers; the first real run happens on the owner's instruction to push | — |
| **1.3** | Delete 11 scripts nobody runs (§6 of the audit) | `backend/scripts/*` | −1,409 | none | 0 references each (grep in the commit); `compileall`; suite unchanged | — |
| **1.4** | Point `test_gam_interaction_constraint` at `app.ml.pipeline.gam`; delete the `gam_common` shim and its `sys.path` hack | 1 test + 1 shim | −40/+2 | none | the same 4 tests pass | — |
| **1.5** | **Frontend dead code:** `App.css`, 3 template assets, `public/icons.svg`, the unused screenshot, the duplicate favicon SVG, 11 unused exports, `date-fns`, unused Tailwind tokens; README rewritten | frontend (tailwind.config.js and package.json are shared: announce) | −~450/+25 | low | `npm run build`, 121 vitest; the built CSS for existing pages is byte-compared before and after | 43's UI agent (tailwind), ce (package.json) |
| **1.5b** | Lazy-load `CallModal` / the voice SDK on tap. A load-timing change, not a behaviour change | `AgentCasesPage.tsx` and the modal's callers | −5/+10 | low | bundle report: the agent case-list chunk loses 181 KB; a manual call still connects | — |
| **1.6** | **Backend dead code in unowned files:** `routing.py` (2 haversine copies), `storage.key_exists`, `production_dataset.cohort_summary`, `ml/repayment.RepaymentScore`, `demo_service.has_baseline`, `clean_load_probe.py`, `ml/allocator.py` (+ its imports in 2 tests) | core/ml/services (unowned parts) | −~420 | low | vulture and grep evidence in the commit; full suite | d4 (`storage.py` adjacent hunk): take the lines apart; 43 for the 2 test files |
| **1.6b** | *(after D4)* delete `field_ops.py`, its mount and `COMMAND_CENTRE_API_KEY` | main.py, config.py, compose, .env.example | −470 | low | grep in both repos; the Command Center's own copy is unaffected | ce touches main.py (SPA block): a separate hunk |
| **1.7** | **Leaner, safer images.** Requirements split (see list below) and a hardened Dockerfile (see list below). *(after D3)* delete `backend/Dockerfile` and shrink `pyproject.toml` to pytest config | `requirements*.txt`, `Dockerfile`, `.dockerignore`, `pyproject.toml` | −60/+60 | **medium** | prod image built before and after, size reported (**target: at least 0.9 GB smaller than 3.03 GB**). In the new image: `/api/v1/ready` 200, the champion loads (`DecisionEngine.serving_state` = 2.2.0), and a retrain smoke run imports `eda`/`optbinning`. Full suite on the dev image. Dev compose still hot-reloads | d4 and ce append to requirements.txt: this step lands **after** their merges, or rebases trivially |
| **1.8** | **Rate-limit counters in Redis** (`storage_uri`), so N workers share one limit. A behaviour change: today it is per process | `core/ratelimit.py` | −2/+6 | low | two app instances sharing one fake Redis: the 11th login across both is 429 | — |
| **1.9** | **Celery queues and time limits** (SEC-8). Queues: `nightly`, `default`, `ml`. Every task gets `soft_time_limit` / `time_limit`; the compose worker listens on all three. A behaviour change | `workers/celery_app.py`, compose worker command | −5/+40 | medium | a unit test reads each registered task's limits and queue; a hung-task test with a 1 s soft limit | 43 may add tasks (B12, B13): new tasks inherit a default |
| **1.10** | **Lint to 0, in the unowned files:** `ManagerAnalyticsPage` ×3 and `AgentCaseDetailPage` ×1 (`set-state-in-effect`). Component tests for those two pages' affected states come first (D11) | 2 pages + tests | −30/+150 | medium | the new component tests pass before and after; lint 7 → 3. The remaining 3 are in `RecordVisitPage` (d4), fixed there or after H14 merges | d4 (RecordVisit) |
| **1.11** | **Docs** *(after D2, D8)*. `docs/adr/` seeded with the decisions CLAUDE.md now carries as prose (below). `MERGING-INTO-PLATFORM.md` rewritten to current. Stale docs deleted or moved. The new CLAUDE.md is drafted here and lands at the integration window (X01) | docs, CLAUDE.md | −~3,800/+~900 | low | every path, command and number in the new CLAUDE.md is checked by a script at commit time (listed in the commit) | all lanes read CLAUDE.md: it lands last in Wave 2 |

Step 1.7 in detail:
- **Requirements split.**
  - Removed, 0 imports each: `xgboost` (with CUDA NCCL, 709 MB), `statsmodels`, `geopy`,
    `pyotp`, `orjson`, `pillow` (still installed transitively by matplotlib).
  - `pytest` moves to `requirements-dev.txt`, where `pyarrow` is also declared.
- **Dockerfile.**
  - Runs as a non-root user, with `HEALTHCHECK` on `/api/v1/ready`.
  - `WEB_CONCURRENCY` sets the uvicorn workers.
  - `npm ci` with no fallback, on Node 22.
  - `.dockerignore` leaves out `backend/tests`, `scripts/research`, `fixtures/tables` and
    artifact `eda/` + `evaluation/*.png` (D6).

ADRs seeded in step 1.11:
- 0001 one definition per rule
- 0002 allocator value transform and its pairing
- 0003 ε-greedy exploration at 10%
- 0004 the model outcome definition
- 0005 champion 2.2.0 and its known limits (KS 38.66 < 39; ML-1)
- 0006 the comment and doc standard

**Expected at the end of Wave 1:**
- about −2,300 lines of dead code (−2,750 with D4; about −12,500 with D4 and D5)
- CI green on backend and build; lint at 3 (all in d4's file)
- the prod image about 0.9 GB lighter
- a suite that needs neither network nor `.env`

---

## 3. Wave 2: the integration window (after ce, then d4, then 43's P1 merge)

This order follows the dependency chain: P1 rewrites every model, and the manager split needs
43's `scope.py` (A03) and the `RequestContext` (A02).

| Step | What | Lines −/+ | Risk | Proof | With |
|---|---|---|---|---|---|
| **2.1** | An **import-direction test** (§2 of ARCHITECTURE) with an allowlist of today's violations; the allowlist can only shrink | +60 | none | fails if a new upward import appears (mutation-checked) | — |
| **2.2** | **Characterisation tests for every agency (manager) and agent route:** golden JSON per route on the fixed test book, plus snapshot tests for the 11 routes the Command Center consumes | +1,200 | none | they are the proof for 2.3–2.5 | 43's `tests/_db` |
| **2.3** | **One calendar.** `domain/calendar.py` (IST business date; the demo effective day, under DEMO_MODE only) replaces `_effective_day`, `_effective_today`, `_effective_window` and 51 raw `date.today()` calls. Then two **behaviour fixes, separate commits:** leave decided on the IST date (CLAUDE.md issue 13), and the frontend's `todayIST()` replacing 9 UTC `toISOString()` dates (a leave or visit dated yesterday before 05:30 IST) | −200/+120 | medium | golden tests unchanged for the refactor; new failing-first tests for each fix | d4 (agent.py) |
| **2.4** | **`manager.py` split** into `endpoints/agency/{dashboard, analytics, agents, cases, allocation, ml, ai, compliance, leave}.py` + `services/agency/*`, one area per commit. Each handler becomes one service call and gains a `response_model` taken from its golden JSON. The two DPD routes become one service function; the calendar copy calls `AgentService` | −5,181/+~4,000 | **high** | 2.2 golden tests byte-identical per area; the tenancy sweep test kept (now reading routers); a Command Center snapshot per area | 43: G07 (`manager_service` scoping + agent admin) and A03. Proposed split of ownership: 43 does scope and agent admin, bb moves the rest, area by area |
| **2.5** | `agent.py` helpers (`_effective_day`, `_visited_today`, `_format_case`) move into services; the 6 upward imports go | −60/+60 | low | golden tests | d4 |
| **2.6** | *(after D12)* OpenAPI → `frontend/src/api/types.gen.ts`; hand-written types deleted per area | −900/+generated | low | `tsc -b` + build | — |
| **2.7** | **Frontend consolidation.** `lib/labels.ts`, `lib/format.ts` and `lib/dates.ts` absorb the 14 restated families. One layout shell with two nav configs (115 duplicated lines). The live map moves onto `MapCanvas`, with `VITE_TILE_URL` honoured in a separate fix commit. The 6 big pages split along the seams in audit §5.2, each behind component tests first | −3,000/+2,200 | medium | component tests per page; build; vitest; lint 0 | d4 (RecordVisit, after H14), ce (ManagerLayout, after G06) |
| **2.8** | **Backend rules, once each:** `core/redis.py` (5 constructors → 1), `core/logging.py` (structlog JSON + request id), haversine (4 copies), phone prefix and masking (7 + 6), synthetic text (4), backend labels | −250/+120 | low | full suite | — |
| **2.9** | **The nightly pipeline, measured.** `ingest_daily` becomes a scheduled `nightly` task (bank file drop → staging), chained ingest → score → allocate with per-agency fan-out (A04). Every run records its duration per stage. **Timed on the `stress` profile (B21)** against the budget in §4.3 | −100/+250 | medium | end-to-end task test on the test book; the stage timings are the deliverable | 43 (B11 removes `create_all`, A04, B21 profile, a duration column) |
| **2.10** | The 14 silent `except Exception` handlers log at WARNING/ERROR with the exception type; fallbacks unchanged | +30 | low | a caplog test per site (the fallback still returns) | ce (llm.py sites) |
| **2.11** | **Split `plan_next_day` (616 lines) and `allocate` (502)** into stages (pool, gates, cost matrix, solve, explore, route, persist) | ±0 net, −2 functions over 500 lines | **high** | fixed-seed before/after on 8 books: **0 changed decisions, identical BLOCKED set, identical forecast** | 43 (A04 touches the planner): after it |
| **2.12** | **Index and retention clean-up** (audit §3.11): duplicate and prefix indexes dropped; *(D9)* predictions logged once per (case, as_of) and kept 13 months; partitions (B12) | migrations | medium | EXPLAIN on the hot queries before and after; insert-rate benchmark on `stress` | 43 (DATA-MODEL-V2 index list) |
| **2.13** | *(after D5, D6)* archive tag, then delete research, one-offs and superseded artifacts; generate `MODEL_DEVELOPMENT.html` for 2.2.0 | −9,800 | low | suite minus the 3 deleted research test files; the champion still serves | — |
| **2.14** | **Line endings:** `.gitattributes` fixed (`* text=auto eol=lf`) + `git add --renormalize`. **Last**, because it touches about 450 files | ±450 files | low | `git diff --ignore-all-space` is empty | everyone: done when no lane is open |
| **2.15** | **CLAUDE.md final** and the before/after line-count report per area, measured the same way as the audit | — | none | a script checks every path in CLAUDE.md | X01 |

Expected at the end of Wave 2:
- no file over 1,500 lines
- no function over 200 lines outside the simulator
- every route typed; every rule in §3 of ARCHITECTURE defined once, with its tripwire
- all gates green, both Docker setups building
- `backend/app` and `frontend/src` each smaller than today, with the numbers reported (not
  promised here)

---

## 4. Capacity and cost model

`M` = measured on 2026-09-24 (demo DB, code, running containers). `A` = assumed, **to be
replaced by a measurement** at the step named. The model is a 90-line calculation (in the
author's scratchpad, reproducible on request); everything below is its output.

### 4.1 Assumptions

| Input | Value | Basis |
|---|---|---|
| Agents / manager | 15 | A (demo: 18 agents, 2 managers) |
| Working days / month | 26 | A |
| Visits / agent / day | 15 | A: `max_cases_per_day` default 15 (M, `models/agent.py:54`); M: 114 allocated per 8 agents per run = 14.3. The demo book *records* only 2.4 per agent-day |
| Payments / visit · PTPs / visit | 0.20 · 0.28 | A · M (demo 663 / 2,404). Demo payments 0.43 treated as inflated |
| Shift | 9 h | A |
| GPS fixes / agent-hour | 300 | A, between M 240 (a stationary fix every 15 s, `locationReporter.ts:36`) and about 400 when moving (a fix per 50 m, `:35`) |
| Location uploads | every 15 s | M (`FLUSH_INTERVAL_MS`) |
| Pool rows per agent per plan run | 76 | M (607 evaluated / 8 agents, 10-day mean) |
| Plan runs per night | 2 | A (demo 4.8, from manual re-plans) |
| Loans per agent · snapshot change rate | 150 · 10%/day | A (demo 93, stress profile 600) · M (4,802 rows / 28 days / 1,674 loans) |
| Bytes per row incl. indexes | predictions 1,678 · decisions 690 · snapshots 3,952 · visits 908 · audit 850 | M (`pg_total_relation_size` / rows) |
| Bytes per location row | 260 | A (57 demo rows too few; heap + 5 indexes) |
| Media per visit | 0.81 MB | A: 3 photos (M: 3 slots) × 180 KB (M: 1280×720 JPEG q 0.88; size A) + half of visits with 90 s of 48 kbps audio + a 20 KB signature. MinIO in the demo is empty, so it cannot be measured |
| LLM | visit report per visit (M: max 250 out), visit strategy on 30% of visits (M: max 1,500), case ranking daily (M: max 900); per manager: briefing daily, 3 insights, a monthly report | A: calls and input sizes |
| LLM prices ($/M tokens in/out) | Groq gpt-oss-120b 0.15/0.60 (A, verify) · Claude Haiku 4.5 1.00/5.00 · Claude Sonnet 5 2.00/10.00 | Anthropic prices from the API reference (cached 2026-06-24) |
| Transcription | $0.006/min | A (OpenAI whisper list price, verify); `TRANSCRIPTION_PROVIDER` defaults to `openai` (M) |
| SMS / WhatsApp per agent-day | 21.6 SMS, 18 WA | M call sites: every visit sends SMS + WA (`visit_service.py:407`); every payment sends OTP SMS (A 1.2 sends, max 4) + receipt SMS + WA |
| SMS price | Twilio international → India ₹7.3 · Indian DLT aggregator ₹0.20 | A (verify with a quote) |
| WhatsApp utility message | ₹0.13 | A |
| FX | ₹88 / $ | A |

### 4.2 Storage

Per agent per month:

| Table / store | Growth | Retention today | At 1,000 agents |
|---|---|---|---|
| `agent_locations` | 18.3 MB (70,200 rows) | 90 days (M) | 55 GB steady; **2.3 M rows/day, deleted row by row by a nightly sweep** |
| `model_predictions` | 7.6 MB (4,552 rows) | **none** | 92 GB/year; about 22 GB/year with D9 |
| `allocation_decisions` | 3.1 MB | none | 38 GB/year |
| `repayment_score_snapshots` | 1.8 MB | 400 days (M) | 24 GB steady |
| `audit_logs` | 0.6 MB | 1,825 days (M) | 36 GB at 5 years |
| visits, payments, PTPs, calls | 0.6 MB | — | 7 GB/year |
| **Postgres, year one** | **32 MB/agent-month** | | **~250 GB** at 1,000 agents; 40 GB at 160; 1.26 TB at 5,000 |
| **MinIO media** | **0.31 GB** | none defined | **3.6 TB/year** at 1,000 agents |

What follows from these numbers:
1. **The GPS trail dominates the database.** Monthly partitions with `DETACH` replace the
   row-delete sweep (B07/B12). The live map reads the latest position from Redis, not from the
   table.
2. **Predictions and decisions grow without bound** and are re-logged on each re-plan: D9, plus
   partitions.
3. **Media is the largest store by a factor of about 15.** It needs a lifecycle rule: audio to a
   cold class after 90 days, and photos kept for the evidence period. That period is a
   compliance decision for the owner, not an engineering default.
4. **Indexes.** Drop the duplicate and prefix indexes (audit §3.11). With 5 indexes on
   `agent_locations` and 13 on `model_predictions`, every insert is a 6- or 14-way write.

### 4.3 Throughput and the nightly window

- **API.** At 1,000 agents on shift, location uploads alone are **67 requests/s**. The prod
  image runs one uvicorn process with 40 sync threads. Size it at 2–4 workers per 2 vCPU after a
  **load test (step 1.7)**; nothing has been measured yet.
- **Connections.** Each process has a pool of 20 + 40. Four API workers and two Celery children
  can open 360 against `max_connections=100`. Set the pool to 5 + 5 per process and put
  PgBouncer in front (B14).
- **Rate limiting** is per process today (in-memory storage); step 1.8 moves it to Redis.
- **The nightly window** (19:30 ingest → 20:00 allocation, 06:00 beat push) **has never been
  timed.** No stage records its duration, and ingest is not even scheduled.
  - Proposed budget at 1,000 agents: ingest ≤ 10 min, scoring ≤ 5 min, allocation plus routing
    for every agency ≤ 20 min.
  - Measured in step 2.9 on the `stress` profile. Until then, "it fits" is an assumption.
- **Celery.** `--concurrency=2` with one queue means a nightly allocation and a batch of
  transcriptions compete for two slots, with no time limit (SEC-8 → step 1.9).
- **OSRM.** The default is the public demo server (`config.py:102`), which cannot carry
  production load (A: usage policy). About 2 calls per beat (A) means about 2,000 per night at 1,000
  agents. Self-host it (the `routing` compose profile already exists). A: 8 GB RAM for a
  state-level extract, about 32 GB for all of India.
- **Redis.** Broker, OTP keys, 1-hour LLM cache, pub/sub fan-out and, after 1.8, limiter
  counters. A: under 1 GB at 1,000 agents. One SSE stream holds one Redis connection (M:
  `endpoints/events.py:62`); fine at manager scale.

### 4.4 Cost per agent per month

₹, at 1,000 agents; the infra line scales down with size.

| Line | Amount | Note |
|---|---|---|
| Infrastructure | ₹132 (₹198 at 160 agents, ₹67 at 5,000) | A: indicative cloud list prices (api, workers, managed Postgres + replica, Redis, self-hosted OSRM, backups). Not a quote |
| Object storage | ₹4 | A: $0.025/GB-month, year-one average |
| LLM | **₹19** Groq · ₹142 Claude Haiku 4.5 · ₹284 Claude Sonnet 5 | 0.59 M input + 0.21 M output tokens per agent-month (A) |
| Transcription | ₹155 | 292 min/agent-month (A) at the OpenAI rate; ₹0 cash with local faster-whisper, paid for instead in CPU (dev default) |
| Messaging, DLT SMS gateway | **₹112 SMS + ₹61 WhatsApp** | 21.6 SMS + 18 WA per agent-day |
| Messaging, Twilio international SMS | **₹4,112 SMS** + ₹61 WhatsApp | the current integration (`notification_service.py`) |
| **Total** | **≈ ₹483** with a DLT gateway and Groq · **≈ ₹4,483** with Twilio SMS | |

**The one number that matters.** Messaging is the only line that can dominate. At Twilio's
international rate the per-visit notification SMS (D10) costs more than everything else
combined, by about 11× (₹4,112 against ₹371). Moving SMS to an Indian DLT-registered gateway is also a TRAI
requirement for commercial A2P traffic (A: verify), so it is likely needed anyway. It fits the
existing single seam in `core/notifications`. The choice of LLM moves the total by at most
₹265.

These figures are the engineering input to the business lead's unit economics. Every `A` above
is a place to put a quote or a measurement before any price is set.

---

## 5. What is not in this plan

- **New features.** Everything in STANDALONE-TASKS stays with its lane.
- **Anything that changes what a live model scores.** D9 changes logging only; retraining or
  promotion stays with the owner.
- **Moving the data model.** That is 43's P1. This plan only sequences around it.
