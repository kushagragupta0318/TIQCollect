# Engineering audit — TIQCollect at `c75053a`

**Date:** 2026-09-24. **Author:** tiqcollect-bb (lead developer). **Status:** evidence only;
nothing in this document has been changed in the code. Decisions and order of work are in
[RESTRUCTURE-PLAN.md](RESTRUCTURE-PLAN.md), the target shape in [ARCHITECTURE.md](ARCHITECTURE.md).

Every number below was measured on 2026-09-24 against `c75053a` (branch `TIQCollect-app`), unless
it is marked *(board)*, meaning it was measured by the coordinator and is cited, not re-derived.
Where a claim could not be measured it says so.

**Classes:** **DELETE** (proven unused; evidence given) · **MERGE** (two or more copies of one
thing become one) · **REWRITE** (same behaviour, better structure) · **MOVE** (same content,
different place) · **KEEP** (examined and kept, with the reason) · **DECIDE** (the owner's call:
it deletes a shipped feature, changes live behaviour or changes a team convention).

**Lanes.** `43` = data model + auth (P1, models/**, alembic, seed, fixture, scope, G07
manager_service) · `ce` = llm, PWA, vite config · `d4` = A10 login, H14 voice, E09 reports,
agent.py, RecordVisitPage · `bb` = this audit. A finding in another lane's files is routed through
tiqcollect-64 and is not edited here.

---

## 1. Summary

### Size, before

| Area | Files | Lines | Of which comments + docstrings |
|---|---|---|---|
| `backend/app` (Python) | 133 `.py` | 39,508 | 31.3% (8,116 comment + 4,250 docstring) |
| `backend/app/ml/artifacts` | 404 | 11.5 MB (1.1 MB is what serving loads) | — |
| `backend/scripts` | 69 | 17,968 | 17.5% |
| `backend/tests` | 70 test files | 24,243 (1,327 test functions) | — |
| `frontend/src` | 111 (105 `.ts/.tsx`) | 26,347 | 17.4% |
| `docs/` + root plans | 57 | 9,444 + CLAUDE.md 2,702 + ML-PLATFORM-PLAN 599 | — |
| Prod image | — | **3.03 GB** (pip layer 1.87 GB) | — |

### What the evidence says, in order of consequence

1. **CI has never been able to pass, and does not run on the branches in use.** The backend job
   sets no environment. `config.py` has no default for `SECRET_KEY`, `DATABASE_URL`,
   `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY` and `COMMAND_CENTRE_API_KEY`. Importing settings raises
   a `ValidationError` (5 fields), so pytest fails at collection. The workflow triggers on pushes
   to `main`, which is the unrelated June history (`b01a9be`), so pushes to `TIQCollect-app` and
   `standalone-*` never run it. CLAUDE.md says "the backend job passes". (§8.2)
2. **Six planner tests call the public OSRM server.** Nothing stubs `router.project-osrm.org`
   (8 s timeout). The suite depends on a third party's uptime and on the network. (§9)
3. **The "19:30 bank ingest" is not scheduled anywhere.** `scripts/ingest_daily.py` (1,081
   lines) is named only in a comment at `celery_app.py:43`. On a real run it also calls
   `Base.metadata.create_all` (`:640`), which makes a third schema authority beside alembic and
   the seed. (§6)
4. **`manager.py` is 5,181 lines: 48 routes, 0 `response_model`, 116 `db.query` inside route
   handlers, 9 commits.** Two routes are 81% the same code. One calendar is written out twice.
   Ten queries group by DPD bucket. The tenant filter is typed by hand in 28 handlers. (§3.4, §4)
5. **Rules restated in many places.** The DPD bucket is written 8× in the frontend, spelled 3
   ways, plus an 8th backend copy that disagrees on negative DPD. "Today" has 4 helpers and
   51 raw `date.today()` calls. There are four permissive copies of "can this agent open this
   case" and three strict ones. (§3.5, §5.4)
6. **The prod image carries about 0.9 GB it does not use.** `xgboost` is imported nowhere; with
   the CUDA NCCL wheel it pulls in, that is 709 MB. `statsmodels`, `geopy`, `pyotp`, `orjson`
   and `pillow` are also imported nowhere. `pytest` ships in prod, while `pyarrow` is used but
   not declared. (§3.7)
7. **At scale the database is dominated by writes nobody reads back.** Per agent-month (model
   in §10 of RESTRUCTURE-PLAN): the GPS trail is 18 MB; `model_predictions` is 7.6 MB, the
   whole pool re-scored and logged on every re-plan, with no retention; `allocation_decisions`
   is 3.1 MB. There are 5 indexes on `agent_locations`, 13 on `model_predictions`, and 2
   identical `created_at` indexes on `audit_logs`. (§3.11)
8. **The connection budget cannot hold.** Each process gets a pool of `pool_size=20,
   max_overflow=40`, against the server's `max_connections=100`. Two API workers plus the
   Celery children can ask for 180+ connections. (§3.11)
9. **Dead or unowned code, about 12,000 lines:**
   - 11 scripts nobody runs (1,409 lines).
   - 8 one-off repairs that have already run (1,178).
   - 35 research scripts (7,388) whose outputs are committed.
   - 23 dead or test-only backend functions (465).
   - A 299-line module reachable only from tests.
   - 5 routes with no caller, and the 446-line `/api/field-ops` router, which has lost its
     consumer.
   - In the frontend: 4 unreachable files, 11 unused exports, and 2 zero-import dependencies.
   - 6 docs nothing links to.
10. **CLAUDE.md is 2,702 lines (169 KB), and every session loads it.** A 27-claim spot-check
    found 8 false and 4 stale. 28 of its parentheticals are corrections of itself. (§7)

### Removable or movable, by class (counts; lines where they are code)

| Class | Backend | Scripts | Frontend | Docs / repo | Needs owner? |
|---|---|---|---|---|---|
| DELETE | ~560 lines (dead functions, imports, constants, `ml/allocator.py`) | 19 files / 2,587 lines | 4 files, 11 exports, 1 dependency, 2 public assets | 4 binaries, 3 docs | no, apart from the items marked DECIDE |
| DECIDE | `field_ops.py` 446, `plan_fleet` 142, 3 routes | 35 research files / 7,388 | `public/collection_dashboard` (d4, A10) | ML artifacts 10.4 MB, `docs/rollback` 1.5 MB, `docs/validation` 2.7 MB, ML-PLATFORM-PLAN | yes |
| MERGE | 12 rules (§3.5), 2 route pairs | 5 own-engine helpers | 14 constant families (§5.4) | PLAN.md into ARCHITECTURE | lanes, at integration |
| REWRITE | `manager.py`, 30 functions over 150 lines | — | 6 pages over 850 lines | CLAUDE.md | CLAUDE.md is a convention → owner |

---

## 2. Live-system and security items

The structure work does not act on these; they are listed so this audit is complete. Owners are
as agreed on the board.

| id | Severity | Finding | Owner / state |
|---|---|---|---|
| SEC-1 | HIGH | The borrower payment OTP is returned to the agent: `otp_service.py:323` echoes `demo_otp` under `DEMO_MODE`, and the platform runs `DEMO_MODE=true` (`field-ops-stub/backend/.env:75`, compose default). An agent can mark a payment borrower-verified without the borrower. Not verified against the running site. | bb: `hotfix/live-security-2` `062e019`, NOT merged, owner decides |
| SEC-2 | HIGH (inferred) | One rate-limit bucket for all users behind Caddy. uvicorn trusts X-Forwarded-For only from 127.0.0.1 (`Dockerfile:38` runs plain uvicorn), and Caddy connects from another container, so the 10/min login limit is shared by everyone. | bb: same branch |
| SEC-3 | HIGH | Demo passwords published and matching the hashes in the committed dump. `fixtures/tables/users.csv` also carries 8 `hashed_refresh_token` values. | *(board FX-1, FX-2)* owner / ops, 43 (B18) |
| SEC-4 | HIGH | `/manager-bridge` auto-logs in as `manager1`. | *(board)* d4 A10 hotfix |
| SEC-5 | HIGH | `POST /agent/voice/outbound` (`agent.py:1071`) dials any client-supplied number, unauthenticated, with no Twilio signature check. | *(board AU-2)* d4 hotfix |
| SEC-6 | MED | Postgres :15432, Redis :16379 (no password) and MinIO :19000/19001 are published on all interfaces in dev compose. Default secrets: `fieldops_dev_pass` (`:42`), `dev_only_secret_change_me…` (`:162`). | bb plan, dev only |
| SEC-7 | MED | The prod container runs as root, has no `HEALTHCHECK`, and uses `npm ci \|\| npm install` (`Dockerfile:5`, which hides a lockfile failure). | bb plan |
| SEC-8 | MED | No Celery task has `time_limit` / `soft_time_limit`. A hung OSRM or LLM call can hold one of the two worker slots through the nightly window. | bb plan |
| ML-1 | HIGH | `borrower_disposition` is never recorded by the product. The 2.2.0 champion's strongest feature is constant (NONE) in production. | *(board)* owner |

---

## 3. Backend

### 3.1 Reachability (import graph from `main.py`, the Celery app and alembic)

33,527 of 39,508 app lines are reachable in production. The rest:

| Module(s) | Lines | Reached from | Class | Evidence / note |
|---|---|---|---|---|
| `ml/simulation/**` (book_simulator, ledger/*) | 3,979 | scripts, tests | KEEP (43) | B16/B17 extend the ledger simulator into the demo generator |
| `ml/recovery_validation.py` | 444 | scripts, tests | DECIDE | used only by the 2026-08 synthetic validation research |
| `ml/train_shadow_model.py` | 386 | scripts, tests | DECIDE | "shadow only"; nothing writes a score from it (CLAUDE.md) |
| `ml/pipeline/report.py` | 382 | scripts | KEEP | writes MODEL_DEVELOPMENT.html. **2.2.0, the champion, has none** — see §7 |
| `ml/allocator.py` (`CaseAllocator`) | 299 | tests only (`test_planner_service`, `test_visit_priority_service`) | DELETE | superseded by `services/global_allocator.py`; prod never imports it |
| `ml/artifacts/.../audit/clean_load_probe.py` | 12 | nothing | DELETE | hardcodes `C:\Users\TransOrg\...`; sits inside `app/`, so it ships in the image |

### 3.2 Dead functions (vulture at 60%, each hit checked by AST and grep across app, scripts, tests and frontend)

**Unused anywhere (DELETE, 87 lines):**
- `core/config.py:215` `sos_contacts_list`, which leaves `SOS_EMERGENCY_CONTACTS` unread
- `core/llm.py:181` `reset_store_for_tests` (ce's file: route to ce)
- `core/routing.py:311` `_haversine_seconds`, `:316` `_haversine_matrix`
- `core/security.py:102` `generate_api_key` (43's file)
- `core/storage.py:92` `key_exists`
- `ml/pipeline/production_dataset.py:299` `cohort_summary`
- `ml/repayment.py:82` `RepaymentScore` (21)
- `services/demo_service.py:134` `has_baseline`
- `services/ml_scoring_service.py:603` `score_many` (13), `:811` `score_and_log` (11)
- `services/planner_service.py:1067` `_calc_distance_km`

**Also DELETE:**
- 7 unused imports: `agent.py:859`, `manager.py:1444,1445,4082`, `case_service.py:290`, `planner_service.py:25,26`.
- Unused constants:
  - `planner_service.py:50-54`: 5 old weights, a second copy of the allocator weights, 0 readers
  - `agent.py:368` `LIVE_STATUSES`
  - `dependencies.py:81` `AnyRole`, `:91` `CommandCentreKey`
  - `model_candidate.py:70` `INERT_STATES`
  - `repayment_snapshot.py:45` `TRIGGER_MANUAL`
  - `pipeline/config.py:689` `SplitName`
- `case_service.py:460` `if True:`.

**Used only by tests (378 lines):**

| Item | Lines | Class | Why |
|---|---|---|---|
| `core/routing.py:589` `plan_fleet` | 142 | DECIDE | CLAUDE.md feature #5: "built and tested, not wired into the nightly run". Keep it only if wiring is on the roadmap |
| `ml/allocator.py:88` `CaseAllocator` | 212 | DELETE | §3.1 |
| `core/security.py:78` `create_agent_verify_token` | 9 | KEEP | G05 (43) wires it onto the ID card |
| `ml/eligibility.py:73` `is_eligible` · `ptp_lifecycle_service.py:124` `is_eligible` | 5 | DELETE | imported but never called at `planner_service.py:25` |
| `gam.py:428` `assign_one`, `events.py:142` `set_store_for_tests`, `routing.py:133` `is_road_data` | 10 | KEEP | test seams |

**Branches that can never run:** `manager.py:3736-3739` compares `ag.specialization` with `"NPA"`
and `"HIGH_BUCKET"`. The enum holds only `SECURED / UNSECURED / BOTH` (`models/agent.py:20`).
REWRITE (43, G07).

### 3.3 Layering

Upward imports, which break the chain `core < models < schemas < ml < services < api/workers`:

| From | To | Sites | Class |
|---|---|---|---|
| `services/agent_service.py:65,252`, `services/case_service.py:89,228,292,543` | `api/v1/endpoints/agent.py` (`_effective_day`, `_visited_today`, `_format_case`) | 6 function-local imports, written that way to dodge a cycle | MOVE the three helpers into the service layer (d4 owns agent.py → integration window) |
| `services/media_service.py:112` | `workers/tasks/transcription` | 1 | KEEP (enqueueing a task is the service's job); alternatively a small `tasks` facade |
| `ml/pipeline/label_comparison.py` | `services/repayment_service` | 1 | KEEP (it compares against that labeller on purpose) |
| `core/audit.py`, `core/dependencies.py` | `models/*` | 3 | KEEP (auth and audit need the user and audit models) |

### 3.4 `api/v1/endpoints/manager.py`

| Measure | Value |
|---|---|
| Lines / routes | 5,181 / 48 (3,814 lines inside handlers) |
| `response_model=` | **0 of 48**. There is no response contract, so the frontend types in `api/manager.ts` (989 lines) are written by hand |
| `db.query(` | 137 in the file, 116 inside route handlers; `db.commit()` ×9 |
| Largest handlers | `analytics` 312 · `get_monthly_report` 288 · `get_latest_allocation_plan` 246 · `ai_briefing` 231 · `agent_ai_insight` 225 · `dashboard` 220 (15 queries) · `_live_monthly_metrics` 210 · `list_agents` 173 |
| Near-duplicate routes | `/analytics/dpd-breakdown` (`:2973`) vs `/agents/{id}/dpd-breakdown` (`:3995`): line similarity 0.81, the same 6 aggregates |
| Copied service logic | `/agents/{id}/availability-calendar` (`:3903`, 85 lines) re-implements `AgentService.availability_calendar` (`agent_service.py:650`) |
| Repeated aggregates | group-by-DPD-bucket ×10 in 7 handlers; `sum(target_amount)` ×12, `sum(collected_amount)` ×8; dashboard and ai/briefing share 6 aggregates |
| Tenant scoping typed by hand | `Agent.manager_user_id == current_user.id` ×29 in 28 handlers; `_require_own_agent` has 4 callers |
| "Today" | `_effective_today(` in 10 handlers, `agent_ids_on_leave(` in 7 with 4 different dates (CLAUDE.md issue 13) |
| Behaviour defect | `:3171` treats `dpd >= 90` as high risk. The canonical NPA line is `> 90` (`models/loan.py:32`) |

Class: **REWRITE** into domain routers plus services. This is task G07 (43) for the scoping
helpers and agent admin; the rest of the split is sequenced in the plan after P1 merges, behind
characterisation tests.

### 3.5 Rules defined more than once

| Rule | Canonical | Other copies (file:line) | Class · lane |
|---|---|---|---|
| DPD → bucket | `models/loan.py:32` `dpd_bucket_for` | `scripts/generate_bank_data.py:141` (**disagrees on negative DPD**), `scripts/generate_synthetic_repayment_history.py:140`, `book_simulator.py:196` (documented exception); inline thresholds `manager.py:3171/3173` (`>=90`), `demo_daily_feed.py:245,272,273,290`, `ledger/simulator.py:1005` (`>=91`) | MERGE · 43 (B16) + G07. The tripwire `test_dpd_bucket.py:122` checks only 4 named files |
| `bank_risk_score` | none | `dpd/120*100` at `seed_data.py:2098,2799`, `demo_daily_feed.py:274`; `dpd/90*50 + …` at `seed_data.py:1600` | MERGE · 43 |
| Agent may open case | none | permissive ×4: `agent.py:474`, `payment_service.py:74`, `case_service.py:561-574`, `visit_service.py:102-116`; strict ×3: `media_service.py:41,67`, `otp_service.py:234-240`, `case_service.py:666`. Payment accepts cases the OTP step refuses | MERGE · 43 (A03 `scope.py`) |
| Manager tenant scope | `_require_own_agent` (`manager.py:61`) | 33 inline filters in manager.py, 12 elsewhere | MERGE · 43 (A03) |
| Today / business date | `leave_service.py:69` `leave_today` (IST) | `agent.py:176` `_effective_day`, `manager.py:83` `_effective_today`, `field_ops.py:147` `_effective_window`; `date.today()` ×51 in 20 files (container clock is UTC); one naive `datetime.now()` at `payment_service.py:70` | MERGE into one calendar module · bb after P1 |
| Resolved / open case statuses | `models/case.py:31` | `agent.py:374`, `case_service.py:104`, `leave_service.py:340`, `manager.py:3521` (as strings), `ingest_daily.py:58,61`; `manager.py:2690` leaves PAID out | MERGE |
| Haversine | `core/geo.py:20` | `routing.py:192,204`, `global_allocator.py:635`, 3 dead copies (§3.2), `seed_data.py:3223` | MERGE |
| Synthetic-warning text | none | 4 literals: `book_simulator.py:853`, `train_shadow_model.py:330`, `build_ledger_dataset.py:86`, `train_recovery_risk_gam.py:332` | MERGE |
| Payment-mode label | none | `payment_service.py:278` (no DD, no BANK_DIRECT); frontend ×3 (§5.4) | MERGE |
| Phone `"+" + normalize_phone(...)` | `notification_service.py:53` | the prefix added at 7 call sites; masking inline at `payment_service.py:275` beside `otp_service.py:222`; loan-account masking inline ×5 | MERGE |
| Redis client | none | built 5 times: `core/events.py:124`, `llm.py:172`, `otp_service.py:153`, `health.py:21`, `endpoints/events.py:62` | MERGE into `core/redis.py` |
| DB engine in scripts | `core/database.py` | 8 scripts build their own; `_resolve_database_url()` pasted into 4; hardcoded credentials in `audit_all_cases_financials.py:11`, `fix_paid_cases.py:14`, `rank_visit_priority.py:39` | DELETE with the scripts (§6) |
| Contact-hour window | `geo.py:28` | the limits are copied at import (`geo.py:16-17`), and `planner_service.py:1047` reads the settings again | REWRITE (read at call time) |
| Logging | structlog, 31 files | stdlib `logging` in 13 (routing, ml/pipeline, ml_scoring_service); `structlog.configure` is never called, so logs are not JSON | REWRITE: one `core/logging.py` |

### 3.6 Settings (`core/config.py`: 102 fields, 528 lines, 2.45 comment lines per code line)

- **Read nowhere (DELETE, or wire):** `AGENCY_NAME`, `AGENCY_RBI_REG` (A14 may wire them),
  `GOOGLE_MAPS_API_KEY`, `FIREBASE_CREDENTIALS_PATH`, `LOCATION_MIN_MOVE_METRES`,
  `LOCATION_MAX_INTERVAL_SECONDS`, `SOS_EMERGENCY_CONTACTS`.
  - `LOCATION_MIN_MOVE_METRES` says it is "served to the app". `location_service.py:47`
    hardcodes 25 and the frontend hardcodes 50 (`locationReporter.ts:35`), so there are three
    values for one rule.
- **Read of a setting that does not exist:** `getattr(settings, "ENVIRONMENT", "")` at
  `otp_service.py:323` (fixed in SEC-1's branch).
- **Required with no default:** 5 fields, and they are the reason CI fails (§8.2). Recommendation:
  tests get their values from `tests/conftest.py`, not from a gitignored `.env`.
- **Read only in tests:** `AUDIT_LOG_RETENTION_DAYS`.

### 3.7 Dependencies and the image

| Requirement | Imports (app/scripts/tests) | Class | Evidence |
|---|---|---|---|
| `xgboost` 2.1.3 | 0/0/0 | DELETE | pulls `nvidia-nccl-cu12` 469 MB; xgboost itself 240 MB |
| `statsmodels`, `geopy`, `pyotp`, `orjson`, `pillow` | 0/0/0 | DELETE | the only statsmodels hit is a comment saying the code avoids it |
| `pytest` | 0/0/58 | MOVE to `requirements-dev.txt` | ships in the prod image |
| `faker` | 0/2/0 | MOVE to the seed image or dev | used only by the seed scripts; the entrypoint runs the seed, so check before moving |
| `matplotlib`, `seaborn` | only `ml/pipeline/eda.py` | KEEP in the worker | reachable in prod: `manager.py:1911` → `lifecycle.py:238` → `train.py:41` → `eda` (auto-retrain writes plots) |
| `faster-whisper` | 1 (lazy) | KEEP, optional | already stripped from prod (`Dockerfile:25`); dev installs it (`Dockerfile.dev:39`) |
| `pyarrow` | 3 sites | ADD (declared) | used by `book_simulator.py:838`, `build_ledger_dataset.py:77`, `test_gam_serving.py:143` |
| `reportlab`, `requests` | scripts only | follows §6 | undeclared; d4's E09 adds reportlab properly |

`backend/Dockerfile` + `backend/pyproject.toml` — **DECIDE (recommend DELETE Dockerfile, shrink
pyproject to tool config).**
- Nothing references them (grep across the repo, CI and the platform compose files).
- `pyproject.toml:3` names `setuptools.backends.legacy:build`, which does not exist, so
  `pip install -e .` cannot build.
- It lists 27 packages where requirements.txt lists 40, and leaves out 12 the app needs.
- But pytest's config (`testpaths`, `asyncio_mode`) lives only there, so those lines must move
  before the file does.

### 3.8 Errors and logging

- There are 72 broad `except Exception` handlers in app. **14 swallow silently** (pass or return
  a constant): `endpoints/events.py:100`, `health.py:24`, `manager.py:3596`, `audit.py:73`,
  `database.py:45`, `core/events.py:154`, `llm.py:200,218,255,309`, `engine.py:246`,
  `ai_report_service.py:106`, `case_service.py:495`, `media_service.py:136`.
- The codebase's own rule ("a swallowed exception that degrades correctly still has to be loud")
  is kept in some places and broken in these.
- Class: REWRITE, one commit per file, each handler logging at WARNING/ERROR with the exception
  type. The fallbacks themselves are kept.

### 3.9 Oversized functions

Of 873 functions, 54 exceed 100 lines and 30 exceed 150. Top ten:

| Function | Lines |
|---|---|
| `ledger/simulator.py:391` `run` | 664 |
| `planner_service.py:193` `plan_next_day` | 616 |
| `global_allocator.py:671` `allocate` | 502 |
| `manager.py:2457` `analytics` | 312 |
| `manager.py:4074` `get_monthly_report` | 288 |
| `book_simulator.py:514` `run` | 272 |
| `train.py:115` `run` | 265 |
| `report.py:122` | 261 |
| `manager.py:4547` | 246 |
| `monitor.py:328` `monitor_model` | 244 |

`plan_next_day` and `allocate` run the nightly engine. They are the highest-value split and also
the highest-risk, which is why they come behind characterisation tests and a before/after run on
a fixed book (plan).

### 3.10 Comment weight

In `backend/app`, 31.3% of lines are comments or docstrings. CHANGELOG headers are only 1,936
lines across 81 files; most of the prose sits in function bodies:
- `core/config.py` 64.8%
- `transcription.py` 61.4%
- `pipeline/config.py` 53.1%
- `global_allocator.py` 49.2%

392 comment lines carry a date. Much of it is the only record of why a number is what it is,
often a measurement. That is the reason the plan proposes *moving* history into ADRs and git
rather than deleting it (DECIDE, §7 of the plan).

### 3.11 Database (demo DB, SELECT-only)

- **Size.** 152 MB in total. Two tables are 73% of it: `model_predictions` 57 MB (34,241 rows,
  1,678 B/row with indexes) and `allocation_decisions` 54 MB (78,809 rows, 690 B/row).
- **Growth.** A nightly run evaluates about 607 cases and allocates about 114 for 8 agents
  (last 10 days). Every evaluated case writes one decision and one prediction per run,
  re-plans included. In the demo, 145 runs over 15 plan dates is about 4.8 runs per manager per
  plan date. `model_predictions` has no retention.
- **Indexes.** Redundant or duplicate indexes on append-heavy tables (43's lane; DATA-MODEL-V2's
  index list should absorb them):
  - `audit_logs`: `ix_audit_logs_created_at` and `ix_audit_created_at` are the **same index twice**.
  - `agent_locations`: 5 indexes, and `ix_agent_locations_agent_id` is a prefix of
    `(agent_id, recorded_at)`.
  - `model_predictions`: 13 indexes. Single-column `model_name` and `model_version` are prefixes
    of the composite index; `band`, `actual_outcome` and `outcome_status` are low-cardinality
    btree indexes.
  - `visits`: `case_id` and `agent_id` are both prefixes of composites.
  - `allocation_decisions`: `case_id` and `run_id` are both prefixes of composites.
  - `repayment_score_snapshots`: `loan_id`, `customer_id` and `as_of_date` are prefixes of
    composites or of the unique key.
- **Connections.** `core/database.py:8` gives each process `pool_size=20, max_overflow=40`;
  the server's `max_connections=100`. There are no `statement_timeout`s (B14 plans them).

---

## 4. API surface (99 routes; every caller traced: frontend, Command Center, e2e, scripts, tests)

78 routes have a frontend caller. 1 is called only from outside (`/health`, by the compose
healthcheck). 8 are called only by tests, 11 by nothing, and 1 is the SPA catch-all. No frontend
call hits a missing route.

| Route | Handler | Class | Evidence |
|---|---|---|---|
| `GET /agent/cases` | `agent.py:377` | DELETE | the only caller is the unimported wrapper `agent.ts:72`; `/agent/cases/ranked` serves the page |
| `POST /agent/cases/{id}/payment-link` | `agent.py:469` | DELETE | wrapper `agent.ts:212` has no importer; 12 grep hits, all definitions |
| `PATCH /agent/customers/{id}/flag` | `agent.py:747` | DELETE | `AgentCaseDetailPage.tsx:290-299` records that the UI was removed |
| `GET /agent/visits/{id}/recording-urls` | `agent.py:790` | MERGE | one of two media-URL routes; neither is used by the UI (see next row) |
| `GET /manager/visits/{id}/media-urls` | `manager.py:3045` | MERGE | test-only; presigns inline instead of going through `MediaService` |
| `POST /agent/voice/outbound` | `agent.py:1071` | KEEP | a Twilio webhook (no in-repo caller by design); security fix is SEC-5 |
| `GET /api/field-ops/{summary,agents,coverage,visits}` | `field_ops.py` (446 lines) | **DECIDE** (recommend DELETE) | the Collections doc says "the §3 contract currently has no consumer"; the Command Center backend now defines its own `/api/field-ops/manager/*`. Deleting also drops the required `COMMAND_CENTRE_API_KEY`. The plan's A15 says "preserve", so this is the owner's call |
| `GET /api/v1/ready` | `health.py:14` | KEEP, wire up | the readiness probe the prod `HEALTHCHECK` should use (SEC-7) |
| `GET /manager/ai/health` | `manager.py:1720` | DECIDE | no caller; F11 (LLM usage dashboard) replaces it |
| `/manager/ml/{health,candidates,…/approve,reject,promote}` | `manager.py:1738-1981` | KEEP | the human promotion gate; F07 (MLOps console) is its UI |
| `GET /verify-agent` | `verify.py:61` | KEEP | G05 mints the token |

External callers: the Command Center backend (`field_ops_manager.py`) calls 11 routes (login,
analytics ×3, agents ×3, monthly report, cases ×3). Every one also has a frontend caller here, so
the Command Center's hand-maintained ports drift silently when these change. Recommendation: a
contract test pinned to those 11 response shapes.

---

## 5. Frontend

### 5.1 Reachability and unused code

- **Unreachable from `main.tsx`** (DELETE): `src/App.css` (184 lines, Vite template),
  `src/assets/{hero.png, react.svg, vite.svg}`.
- **Unused public files:**
  - `public/icons.svg` (template sprite) and `public/assets/Screenshot 2026-08-12 131504.png`
    (139 KB): DELETE.
  - `public/favicon.svg` is byte-identical to `src/assets/transorg-logo.svg`, 58 KB each: KEEP
    one.
  - `public/collection_dashboard/` (72 KB): d4, A10.
- **Exports with no importer** (DELETE):
  - `api/agent.ts:72,212,334`, `api/auth.ts:19,23`
  - `ui/Badge.tsx:107` `RiskBadge`, `ui/Card.tsx:11` `Card`
  - `useLiveEvents.ts:86` `useLiveStatus`, `useMediaQuery.ts:40` `useIsMobile`
  - `apiError.ts:45` `errorCode`
  - `locationReporter.ts:253` `pendingCount` (its docstring says the header shows it; nothing
    does)
  - `types/index.ts:259` `Beat`
- **Other code that goes around the shared paths:** `auth.ts` `logout`/`getMe` have no importer
  while the routes are called directly from `AgentLayout.tsx:227`, `ManagerLayout.tsx:154` and
  `useLiveEvents.ts:47` (MERGE onto the wrappers). A further 18 exports are used only by tests,
  and 65 are used only inside their own file.
- **Dependencies:**
  - `date-fns`: 0 imports, DELETE.
  - `tailwind-merge`: 0 imports at `c75053a`, but **KEEP**, because 43's bank UI port uses it
    (coordinator).
  - `e2e/simulator_acceptance.py` needs Python Playwright, which nothing declares.
- **Tailwind config:** 8 of 9 custom animations are unused, along with the `bucket-*` colours
  (and they differ from `DPD_COLOUR`), `shadow-glow`, `shadow-tint-*` and `text-2xs`. There are
  radix accordion keyframes although radix is not a dependency. DELETE (the main config is
  shared with 43's UI agent; announce first).
- `frontend/README.md` is the stock Vite template. REWRITE it (~20 lines).

### 5.2 Size

| Page | Lines | useState | useEffect | Inline components | Longest body | Natural seams |
|---|---|---|---|---|---|---|
| RecordVisitPage | 2,329 | 27 | 7 | 3 | 1,747 (459-2205) | draft storage 126-173 · `useRecordAndTranscribe` 381-453 · submit 978-1152 · camera modal 1180-1275 · 10 form sections 1330-1911 · photos/docs 1952-2090 |
| ManagerAnalyticsPage | 1,778 | 14 | 8 | 11 | 551 | already 9 cards, move to files |
| ManagerCasesPage | 1,601 | 25 | 2 | 10 | 521 | CaseDetailModal 346-732 · ReassignDialog 236-344 · CaseRow 898-1072 |
| ManagerAgentsPage | 1,381 | 16 | 1 | 12 | 355 | ReallocationModal 1246-1381 · SVG charts 819-1140 |
| AgentCaseDetailPage | 1,351 | 18 | 3 | 7 | 1,040 | 6 tabs at 418/615/746/791/812/845 · Log Call modal 869-1115 |
| ManagerOverviewPage | 858 | 5 | 4 | 8 | 360 | AiBriefingCard 697-793 |

`api/manager.ts` is 989 lines of hand-written types that mirror untyped backend dicts (§3.4).
`AgentLayout` and `ManagerLayout` are 418 lines each and share 115 identical code lines.
React Query is used in 12 files; 3 of the large pages manage server state by hand
(`useQuery` ×0).

### 5.3 Libraries doing the same job

- **Charts:** Recharts (4 files) beside hand-drawn SVG charts in `ManagerAgentsPage:819-1050`,
  `ManagerOverviewPage:589-686` and `AgentCaseDetailPage:1196`.
- **HTTP:** axios (1 file) beside raw `fetch` in `eventStream.ts`, `simSession.ts` and
  `RecordVisitPage` (the MinIO PUTs are legitimately fetch).
- **Maps:** two Leaflet bootstraps: `MapCanvas.tsx:64` and `ManagerLiveMapPage.tsx:194`. The
  live map hardcodes the tile URL (`:196`) and ignores `VITE_TILE_URL`.
- Class: MERGE the maps and the HTTP clients. The charts are DECIDE, because moving them onto
  Recharts is a visible change.

### 5.4 Constants restated (MERGE into `lib/`, one map per concept)

| Concept | Copies |
|---|---|
| DPD bucket label / order / colour | 8, spelled 3 ways ("NPA 90+" vs "NPA (90+)", "Current" vs "Current (0 DPD)"): `Badge.tsx:17`, `todayDpd.ts:24,34,43,50`, `ManagerAnalyticsPage:55,1107`, `ManagerCasesPage:1096,1318` |
| Visit outcome labels | 4 (`AgentCaseDetailPage:93`, `RecordVisitPage:267,278`, `fieldActivity.ts:67`, `ManagerCasesPage:86`) |
| Case status labels / the resolved set | 3 + 3 |
| Payment mode | 3 (`RecordVisitPage:307` lists 5 of 8 modes) |
| PTP status | 2, each covering 3 of the 6 statuses |
| Leave type/status, recovery potential, priority band, tier | 4 · 3 · 2 · 3 |
| Agent status | 11 inline ternaries, no map |
| Live event types | 4 maps; `LiveEvent.type` typed as `string` |
| Enum humaniser `replace(/_/g," ")` | 16 copies in 11 files |
| INR formatting outside `lib/money.ts` | 10 inline K/L divisions, 51 inline `toLocaleString("en-IN")` |
| Date formatting | no helper: one formatter ×5, `monthWords` ×2 |
| "Today" | computed with UTC `toISOString().slice(0,10)` in 9 places. **Before 05:30 IST the frontend's "today" is yesterday**, a behaviour bug in leave, analytics and the visit form |
| API base `/api/v1` | 4 literals; `simSession.ts:22-40` parses the persisted auth store itself |

### 5.5 Quality gates (measured in C:\dev\tiq\lead after a clean `npm ci`)

See `lint`, `test` and `build` in §8.3.

---

## 6. Scripts (69 files, 17,968 lines)

Nothing in `backend/app` runs or imports a script. CI runs none. The entrypoint runs two.

| Class | Files | Lines | Plan |
|---|---|---|---|
| RUNTIME (entrypoint) | `seed_data` (3,449), `demo_reset` | 3,505 | KEEP (seed is 43's, B11/B18) |
| OPERATOR (documented) | `ingest_daily`, `generate_bank_data`, `generate_quick_login_link`, `ml_end_to_end_trace`, `train_models`, `train_recovery_risk_gam`, `train_recovery_risk_v2`, `build_ledger_dataset`, `build_modelling_dataset`, `demo_collect_to_target`, `demo_record_today_plan`, `demo_record_visits` | 4,301 | KEEP; group by purpose (`scripts/ops`, `scripts/ml`, `scripts/demo`); all on `core/database` |
| TEST-SUPPORT | `synthetic_fixture` | 188 | KEEP, or MOVE under tests |
| RESEARCH | 21 under `research/**` + 14 top-level (the synthetic validation study, shadow simulation, value-transform study, phase 2/4 ledger validation, …) | 7,388 | **DECIDE.** Outputs are committed under `ml/artifacts/*/discovery,observability` and `docs/validation`. Recommend: tag `archive/research-2026-09`, then delete. **Two are not research and stay:** `shadow_allocation_ml` (its `build_book` backs `test_allocator_ml_shadow`, which pins the live value transform) and `phase2_ledger_validation` (`test_ledger_phase2`, 43's simulator). `gam_common` is a shim re-exporting `app/ml/pipeline/gam.py`, so its one test repoints. `generate_synthetic_recovery_validation` and `report_synthetic_validation` go together with `test_synthetic_recovery_validation` |
| ONE-OFF REPAIR (already applied; rollback manifests in `docs/rollback/`) | `fix_paid_cases` (raw UPDATE, hardcoded credentials), `migrate_allocation_schema` (DDL outside alembic), `backfill_eb_features`, `repair_case_timeline`, `rescale_to_realistic_emi`, `ptp_lifecycle_backfill`, `respread_pool_cases`, `restamp_case_priorities` | 1,178 | DELETE after the archive tag. `ptp_lifecycle_backfill` has a test (`undo_manifest`) that goes with it |
| NOBODY (0 references, or 1 comment) | `check_cases`, `inspect_emp18`, `inspect_customer_dupes`, `evaluate_ml_metrics`, `export_training_data` ("had never run"), `rank_visit_priority`, `render_doc_pdf`, `generate_single_page_ml_summary_pdf`, `generate_ml_architecture_pdf`, `audit_all_cases_financials`, `test_run_planner` | 1,409 | DELETE |

Also:
- Destructive writers outside the seed: `generate_synthetic_repayment_history:692` and
  `run_synthetic_experiment:89` (`DROP DATABASE IF EXISTS`). They are research and go with it.
- `ingest_daily` is the product's only bank-feed path and nothing schedules it. It should become
  a scheduled Celery task with its own lock (plan; coordinate with 43, B11, which removes its
  `create_all`).

---

## 7. Docs and prose

| Doc | Lines / size | Referenced by | Accurate? | Class |
|---|---|---|---|---|
| `CLAUDE.md` | 2,702 / 169 KB | every session | 8 of 27 checked claims false, 4 stale since `c75053a` (list below); 28 self-corrections, ~151 lines | **REWRITE → ~250-line operational guide** (DECIDE: it replaces a convention) |
| `ML-PLATFORM-PLAN.md` (root) | 599 | CLAUDE.md | plan; names 7 artifacts and 4 flags that were never built | DECIDE (recommend DELETE; the history is in git) |
| `docs/PLAN.md` | 217 | CLAUDE.md | **stale**: weights 35/30/20/15 (code 0.45/0.40/0.05×4), routes `/allocation/simulate` and `/strategy` do not exist, wrong index name, "Sunday does not execute" is false, `file:///c:/Users/TransOrg` link | MERGE the still-true parts into ARCHITECTURE.md; DELETE |
| `docs/MERGING-INTO-PLATFORM.md` | 164 | CLAUDE.md | stale merge base `008433f`; the last two merges are missing | REWRITE (short, current) |
| `docs/case-allocation.{html,pdf}` | 725 / 452 KB | CLAUDE.md | stale formulas (0.20/0.85 clamp, 25,000/300,000 knee) | DELETE, or regenerate from code |
| `docs/feature-list.{docx,pdf}` | 41 / 255 KB | CLAUDE.md | generated 09-18 | DECIDE (business document) |
| `docs/decision-flow.pdf`, `docs/TIQCollect-feature-tracker.xlsx` | 273 / 25 KB | **0 references** | — | DECIDE (recommend DELETE or MOVE to docs/business) |
| `docs/COMPETITIVE-ANALYSIS.md` | 401 | **0** | measured against `86eb6d0` | MOVE to `docs/business/` |
| `docs/CALL-ROUND-PLAN.md` | 245 | **0** | plan | KEEP if still intended, else DELETE (owner) |
| `docs/rollback/**` | 32 files / 1.5 MB | only their own MANIFESTs | data from one-off repairs | DECIDE (archive tag, then delete) |
| `docs/validation/synthetic/**` | 14 files / 2.7 MB | the research scripts | generated | follows the research decision |
| `frontend/README.md` | 73 | 0 | Vite boilerplate | REWRITE |
| `backend/fixtures/README.md` | 49 | 5 | silent about `fixtures/tables/*.csv` | 43 (B18) |
| `ml/artifacts/*/MODEL_DEVELOPMENT.html` | 6 | CLAUDE.md, registry | **none for 2.2.0, the champion**; `PRODUCTION_READINESS_AUDIT.md` cites a missing `gam/models.joblib` | generate for 2.2.0 (`report.write_model_document`) |

**False claims in CLAUDE.md** (checked against the code):
1. `quick_login_token.py` declares two tables. It declares one; `agent.py` is the module with two.
2. `_received_within` is cited at `repayment_service.py:994`. It is at `:1046`.
3. "32 files reference changelog.md/final_changes.md". It is 36.
4. `test_exploration_never_sends_a_case_to_an_ineligible_agent` is cited as a pin. It is not in `tests/`.
5. `test_promotion_settings_are_paired` is cited as a pin. It was removed.
6. It links `docs/recovery-risk-model-audit.html` and `recovery-calibration.html`. Both were deleted in `3547305`.
7. "Read `<model>/<version>/MODEL_DEVELOPMENT.html`". There is none for 2.2.0.
8. "No SSE anywhere". `events.py` serves `text/event-stream` since `c75053a`.

Also: "CI … backend job passes" (false, §8.2), and the nightly schedule block "verified against
celery_app.py" (it has no 19:30 ingest).

`ci.yml:1-5` and 36 other files cite `changelog.md` / `final_changes.md`, which do not exist.

---

## 8. Infrastructure, Docker and CI

### 8.1 Docker

| Item | Finding | Class |
|---|---|---|
| `Dockerfile` (prod, built by the platform) | `COPY backend/ ./` ships `tests/` (1.1 MB), `scripts/` including research (0.95 MB), `fixtures/` (21.4 MB, including the unread CSVs) and `ml/artifacts` (11.5 MB, 1.1 MB of it served) · single uvicorn process, no `--workers` · root user · no HEALTHCHECK · `npm ci \|\| npm install` · Node 20 while `react-router` 8.3.0 needs ≥22.22 | REWRITE (plan step with a before/after size) |
| `Dockerfile.dev` | 3.67 GB; installs faster-whisper by default | KEEP, trim with the requirements split |
| `docker-compose.yml` | `config -q` exits 0 · OSRM and MinIO pinned to `:latest` · no resource limits · stale comments: `:18` and `:23-24` (files that do not exist), `:316` (`field-ops-stub_postgres_data`), `docker-entrypoint.sh:72` and `.env.example:87` (`exec field-ops`; the service here is `api`) | REWRITE (small) |
| `docker-entrypoint.sh` | `alembic upgrade head` runs only right after a fixture restore; **a normal boot on a populated database never migrates** (`:70-72`) · the pg17-client-vs-pg16-server workaround at `:89-92` | 43 (B11); flag it |
| Celery | `--concurrency=2`, `acks_late`, prefetch 1, no time limits (SEC-8), no queue split (a nightly allocation and an ad-hoc transcription share 2 slots) | REWRITE |
| `.dockerignore` | does not exclude `backend/tests`, `backend/fixtures/tables`, `scripts/research` or artifact dev evidence | REWRITE |

### 8.2 CI (`.github/workflows/ci.yml`)

- **Triggers:** `pull_request` and push to `main` only. `main` is the June repo, and
  TIQCollect-app / lane branches never trigger it.
- **Backend:** Python 3.12, `pip install -r requirements.txt`, then `compileall`, then
  `pytest -q`.
  - **It fails at collection**: there is no env and 5 settings are required.
  - It would then call the public OSRM server from 6 planner tests.
  - It runs no alembic check and no Docker build.
- **Frontend:** Node 20, then `npm ci` (engine warning), `tsc -b`, `npm test` and `npm run lint`.
  Lint fails. `npm run build` (vite) is never run, so a Vite-only failure is invisible.
- **Missing:** a `docker compose config` check, the prod image build, and a size budget.

### 8.3 Frontend gates (measured in C:\dev\tiq\lead after a clean `npm ci`, Node 24)

| Gate | Result |
|---|---|
| `npm ci` | 342 packages, 39 s |
| `npm run lint` | **14 problems: 7 errors (all `react-hooks/set-state-in-effect`), 7 warnings (`exhaustive-deps`)**; exit 1. Matches the board baseline |
| `npm test` | 13 files, **121 passed** |
| `npm run build` | OK. Vite 7.1 s, 43 s end to end; `dist` 2.4 MB |

**Bundle.** The entry chunk is 369 KB (120 KB gzip). Recharts (298 KB) and Leaflet (149 KB)
are in separate chunks, and Recharts loads only on manager pages. **The agent's case list
statically imports `CallModal`, which pulls in `@twilio/voice-sdk` (181 KB, 47 KB gzip) before
any call is made.** That is REWRITE (lazy-load on tap) and matters on field phones on 3G/4G.

---

## 9. Tests (backend)

- **Setup:** 70 files and 1,327 test functions. There is no `conftest.py`, and **35 files build
  their own SQLite engine**. 43 is building `tests/_db.py` (B02). 9 files import fixtures from
  other test modules.
- **38 tests in 20 files assert on source text**, which the board's rule 9 bans for new tests.
  Full list: `repayment_task` ×7, `synthetic_recovery_validation` ×3, `contact_hour_audit` ×3,
  and the rest, including the tenancy sweep at `test_manager_endpoints.py:494`.
  - Some are deliberate tripwires ("nobody restates the rule"), and those are kept, labelled as
    tripwires.
  - The rest are REWRITE candidates, handled as the files they guard are touched.
- **Tests that depend on the environment:**
  - Contact hours: `test_geo.py:27,42` and `test_contact_hour_audit.py:172,228,268,289`.
    `core/geo.py:16-17` reads the window at import.
  - `test_compliance_hardening.py:321` (`.env` can override the value it checks).
  - Network: 6 `plan_next_day` calls without `simulate=True`, which reach public OSRM.
  - Two tests open `Path("app/...")` relative to the working directory, so they must run from
    `backend/`.
- **Likely runtime leaders** (read from the code, not timed):
  - `test_retraining_lifecycle` (20 cohorts × 2,600 ORM rows)
  - `test_ledger_phase4_lifecycle` (900 × 20-month ledger)
  - `test_ledger_observability` (11 simulator runs, no module fixture)
  - `test_recovery_risk_pit_audit` (about 8 panel rebuilds)
  - `test_planner_service` (16 planning runs)
  The suite takes about 13 minutes serial *(CLAUDE.md, 2026-09-23)*.
- **Frontend:** 13 vitest files and 121 tests, all on pure modules; nothing mounts a page.

---

## 10. Repository hygiene

- **Tracked content:** 924 files, 45 MB.
  - Generated output: `ml/artifacts` (404 files, 11.5 MB: 180 csv, 142 png), `fixtures/tables`
    (6.5 MB, unread), `docs/validation` (2.7 MB), `docs/rollback` (1.5 MB), and 3 screenshots
    in `public/assets`.
- **Line endings: `.gitattributes:22` `text=auto eol=lf` has no path pattern, so it applies to
  nothing.**
  - `git check-attr` reports text/eol unspecified for `App.tsx`, `package.json` and
    `CLAUDE.md`.
  - With `core.autocrlf=true`, the lead checkout has 453 files as CRLF.
  - Fixing it renormalises hundreds of files, a diff that would conflict with every lane.
  - Class: REWRITE, **last**, after all lanes have merged.
- **`.gitignore`:** 20+ patterns match nothing (certs, venvs, caches, OS files). Harmless. KEEP
  the security-relevant ones and trim the rest.

---

## Appendix A — how this was measured

- **Backend import graph:** a Python `ast` walk over `app`, `scripts`, `tests` and `alembic`.
  Roots were `app.main`, `app.workers.celery_app` and alembic; string references like
  `"app.workers.tasks.x"` count as imports.
- **Dead code:** vulture 2.16 at 60% in three scopes (app, +scripts, +tests), then every
  candidate verified against an AST name index and a word-grep of frontend, compose and the
  Dockerfiles. Framework-registered functions (85 routes, tasks and validators) were excluded.
- **Routes:** `ast` over the decorators; router prefixes resolved from `router.py` and `main.py`.
  Frontend call sites parsed with the TypeScript compiler API, wrappers traced through imports.
  The Command Center repo was searched with ripgrep.
- **Frontend reachability and exports:** the TypeScript compiler API over `src/` from `main.tsx`,
  with `lazy()` imports counted.
- **Database:** `pg_total_relation_size`, `pg_indexes`, `information_schema` and row counts on
  the shared demo database, SELECT-only.
- **Image sizes:** `docker images` and `du` of site-packages inside existing images; nothing was
  built for this audit.

The scripts are kept by the author and can be re-run on request. They are not committed, because
a tool with no reader would contradict the audit.
