# TIQCollect Standalone — Task Planner

The executable breakdown of [STANDALONE-PRODUCT-PLAN.md](STANDALONE-PRODUCT-PLAN.md).
Section refs (§) point into that plan. Created 2026-09-24. Update this file
as tasks move — a task is ticked only when its **Done when** is true.

**Status:** `[ ]` todo · `[~]` in progress · `[x]` done · `[-]` dropped (say why)
**Size:** S = ≤ 1 day · M = 2–5 days · L = 1–2 weeks

---

## Rules that apply to every task

1. **UI rule (§2.5).** Every *new* surface — bank portal, onboarding, AI
   Strategy, Tech Ops, admin, simulator chrome — is a faithful port of the
   Collections Command Center UI: its tokens, fonts, shell, primitives and
   signature components, taken from `collections-platform/command-center/frontend`.
   Existing TIQCollect views are **not** restyled; new screens inside them
   (Manage Agents, agency profile) reuse those views' own components.
2. **Definition of done.** `python -m pytest` green · `python -m compileall app`
   · `npm run build` · `npm test` · no *new* lint errors · a CHANGELOG header
   on every non-obvious file change, with the measured number where there is one.
3. **One definition, one place.** KPIs live only in the KPI catalog, scoping
   only in `scope.py`, permissions only in the capability registry. Anything
   restated gets a tripwire test.
4. **Tenant safety.** Every new endpoint takes `RequestContext` and is covered
   by the cross-tenant behavioural test (A12) the day it lands.
5. **Synthetic honesty.** Every model, forecast and simulator number shown in a
   UI carries the `SYNTHETIC_WARNING` convention until real outcomes exist.
6. **No new logic in `manager.py`.** New code goes to `services/` and new routers.

---

## Milestones

| Milestone | Phases | Demo at the end of it |
|---|---|---|
| **M1 — See it on a phone** | P0 | agent app in a phone frame beside the manager view, live events flowing |
| **M2 — A real multi-tenant product** | P1 | 1 bank, 8 agencies, ~160 agents; no static login; nobody sees another agency's data |
| **M3 — Agencies onboard themselves** | P2 | bank onboards agency → agency admin accepts invite → creates agent → agent logs in |
| **M4 — The bank's Command Center** | P3 | bank portal, pixel-matched to Collections, with agency performance |
| **M5 — Strategy** | P4 | Monte Carlo, forecasts, scenario lab, board pack downloads |
| **M6 — Tech Ops** | P5 | create an AI agent in the UI, run it, trace it; MLOps console |
| **M7 — Showcase AI** | P6 | the ten models and eight agents of §9 |
| **M8 — Field-ready app** | P7 | installable PWA with offline outbox |

P0 runs alongside P1. P3 and P5 may overlap once P1 is done.

---

## P0 — Mobile app simulator (§11.1) · M1

- [x] **P0-01** Namespace the auth store by `?slot=` so two same-origin frames hold two sessions. `authStore.ts`. *S* · **Done when:** agent and manager logged in side by side in one browser, neither logs the other out.
  *Done 2026-09-24 — `lib/sessionSlot.ts`. The slot rides in `window.name`, not sessionStorage: same-origin iframes share their tab's sessionStorage, so it would have collided exactly like the token did. 7 tests.*
- [x] **P0-02** Geolocation provider; move the six direct `navigator.geolocation` calls behind it; accept `postMessage` fixes in simulator mode. *M* · **Done when:** grep finds no `navigator.geolocation` outside the provider.
  *Done 2026-09-24 — `lib/deviceLocation.ts` (NOT `lib/geo.ts`, which already holds the distance maths). A tripwire test enforces the one door. **Not done:** the hardcoded Gurugram fallback in check-in (`AgentHomePage`) and visit submit (`RecordVisitPage`) is still there — removing it changes what a GPS-less agent can do, so it moves to A14.*
- [x] **P0-03** `/simulator` page: Pixel 8 / iPhone 15 frames, agent iframe with `allow="geolocation; camera; microphone"`, manager iframe at 1440 px scaled to fit. Command Center UI (indigo, outlined controls, system font — per UI01). *M* · deps P0-01
  *Done 2026-09-24. Gated on the Vite dev server or `VITE_ENABLE_SIMULATOR=1` rather than `DEMO_MODE` (a build-time flag is what the frontend can see). Bank iframe arrives with P3.*
- [~] **P0-04** Device controls: pick GPS on a map, **play beat route** along the OSRM polyline at ×1–×60, go-to-stop (lands 25 m out, inside the fence), GPS-loss toggle, device picker. *M* · deps P0-02, P0-03
  *Done: all of the above. **Open:** sample camera images and an offline toggle — the frame uses the laptop webcam (or the app's placeholder selfie) and real network.*
- [x] **P0-05** Event timeline via `GET /api/v1/events/recent` (manager-scoped; bank/agency scope with A02). *S*
- [x] **P0-06** SSE `GET /api/v1/events/stream` over Redis pub/sub; published at check-in/out, location batch, visit, payment submitted/verified (inline and deferred OTP), PTP set, SOS raised/cancelled. *M* · **Done when:** events arrive < 2 s after the commit.
  *Done 2026-09-24 — measured **40 ms** publish → client. Found on the way: Starlette 0.41's GZipMiddleware buffers `text/event-stream` indefinitely (a connected stream delivered nothing in 8 s); the stream path is now exempt, with a regression test. 11 backend tests.*
- [x] **P0-07** Manager live map, SOS bell and every cached query react to events; polling stays as the fallback. *S* · deps P0-06
- [~] **P0-08** Acceptance in a real browser (`frontend/e2e/simulator_acceptance.py`): **10/10** — two sessions, stream live, simulated GPS reaches the backend, visit → manager **180 ms** from submit, SOS tap → manager **519 ms** incl. GPS read with a LIVE fix, SOS lights the bell, no console errors. *M*
  *Open: OTP payment end to end (needs the borrower-OTP step scripted), and check-in through the UI (skipped when the agent is already on duty). Note: each run records one NOT_AVAILABLE visit on the demo book, noted "simulator acceptance run".*

---

## P1 — Foundations · M2

### P1-B Data platform (§4)

- [x] **B01** Schema design doc `docs/DATA-MODEL-V2.md`: ERD, table → schema map, every column's v2 type, FK `ON DELETE` rules, partition keys, index list. *M* · **Done when:** reviewed and agreed before any migration is written.
  *Agreed 2026-09-24. 80 tables (24 existing + 56 new, 0 name collisions), 75 capabilities × 8 roles, analytics layer, partitioning, RLS, full 476-column v1→v2 mapping, 8 verification gates. Decisions in §10. **Direction added by the owner:** realistic invented demo data, no placeholder names — the roster is Appendix C (Meridian Trust Bank, 9 agencies, ~160 agents).*
- [ ] **B02** Model base: native `UUID`, `Money = NUMERIC(14,2)`, metadata `naming_convention`, `__table_args__ = {"schema": …}`. One shared test-engine factory with `schema_translate_map` for SQLite, replacing the 34 ad-hoc engines. *M*
- [ ] **B03** `tenancy`: banks, regions (self-referencing zone → region → state → city), branches, agencies, agency_regions, agency_contracts, agency_documents. *M*
- [ ] **B04** `tenancy` identity: users gain `bank_id`/`agency_id`; roles extended (§2.1); role_permissions, user_sessions, user_invites, password_reset_tokens. *M*
- [ ] **B05** `lending`: customers + addresses + contacts, loans (bank FK, branch FK, typed dates, NUMERIC), **loan_instalments**, **loan_dpd_history** (partitioned), bank_feed_batches/rows, bank_actions. *L*
- [ ] **B06** `collections`: placements, cases (+`bank_id`, `agency_id`, typed dates), case_assignments, visit_media (split from visits), settlement_offers, disputes, escalations. *L*
- [ ] **B07** `workforce`: agents (agency FK, typed timestamps), attendance, agent_devices; agent_locations partitioned by month. *M*
- [ ] **B08** `planning`: placement_runs/decisions, **beat_stops** replacing `ordered_case_ids`, tenant ids on allocation tables, allocation_decisions partitioned. *M*
- [ ] **B09** `ml`, `ai`, `strategy`, `audit` schemas; audit_logs partitioned with UPDATE/DELETE revoked + trigger. *M*
- [ ] **B10** Lookup tables for free-text domains (`legal_status`, `settlement_status`, decision outcome, objective, leave type). *S*
- [ ] **B11** Real Alembic v2 baseline (`include_schemas`, version table in `public`); `seed_data.py` stops calling `create_all` — closes known issue 5. *M*
- [ ] **B12** Partition maintenance task: create next 3 months, detach past retention; replaces the row-delete location sweep. *S*
- [x] **B13** `analytics` schema: `dim_*` views, the five `mv_*` materialized views (§4.4), `REFRESH … CONCURRENTLY` task at 20:30. *L* B13a `v2_0007`; B13b `v2_0013`: portfolio, transitions, agency scorecard, the five `*_scoped` views, `v_visit_to_pay`, `strategy.cost_rates` (DATA-MODEL-V2 §6.4).
- [ ] **B14** DB config: API `statement_timeout`, read-only analytics session + replica hook, PgBouncer-safe (`SET LOCAL` only). *S*
- [ ] **B15** `scripts/migrate_v1_to_v2.py`: today's fixture → v2 (ABC Bank, ABC Collections, manager1 → AGENCY_ADMIN, dates parsed, dpd history back-filled with `is_backfill`). *M*
- [ ] **B16** Generator: extend the ledger simulator with tenancy, 4 zones / ~14 cities, **latent agency and agent skill**, contact time-of-day, settlement offers, disputes, complaints, injected compliance breaches — and a ground-truth manifest. **Onboards the Appendix C roster with every invented detail** (identity, people, coverage, contract + commission slab, specimen documents in MinIO, DRA register, onboarding audit history, lifecycle mix: 7 active / 1 suspended / 1 onboarding). *L*
- [ ] **B17** `materialise.py` writes v2; profiles `dev` / `demo` / `stress` (§4.7). *M* · deps B16
- [ ] **B18** Regenerate `fieldops-demo.dump`; entrypoint checks `workforce.agents`; fixtures README rewritten (states the roster is fictional, lists the realistic logins of Appendix C.5); refresh-token hashes scrubbed. **Done when:** a grep of the fixture and seed finds no "ABC", "Test Bank", "Synthetic Bank", `manager1@` or `agent0` placeholder. *S*
- [x] **B22** Demo-tenant safety: `is_demo` on banks/agencies; NotificationService suppresses SMS/WhatsApp/email for demo tenants (logged, not sent); a test proves an invented number is never dialled. *S*
- [ ] **B19** `tests/pg/` suite + Postgres service in CI: partitions, RLS, mv refresh, v1→v2 transform. *M*
- [ ] **B13c** Bank business-day calendar (weekends, holidays) per bank; then offer business-day windows in `v_visit_to_pay` and SLA measures (B13b audit: today they are calendar days). *S*
- [ ] **B20** ML adapter on typed columns; the Phase 3 equality harness (78 tests) and PIT tests green throughout. *M*
- [ ] **B21** Stress profile loaded; baseline query timings recorded in `docs/DATA-MODEL-V2.md`. *S*

### P1-A Tenancy, identity, access (§3)

- [ ] **A01** Capability registry + `require_perm()` dependency; role → permission seed. *M*
- [ ] **A02** `RequestContext` and JWT claims (`bank_id`, `agency_id`, `perms`); migrate `get_current_user` callers. *M*
- [ ] **A03** `scope.py` (`agents_in_scope`, `cases_in_scope`, …); replace inline `manager_user_id` filters; **close the three leaks** (unallocated pool, planner pool, agent access to unassigned cases). *L*
- [ ] **A04** Planner draws per-agency pools; the nightly task loops agencies, then managers. *M*
- [ ] **A05** `user_sessions`: refresh token per device, reuse detection per session, list and revoke. *M*
- [ ] **A06** Invites: create / accept, single-use, hashed at rest, 72 h. *S*
- [ ] **A07** Password change, admin reset, forced change on first login, OTP self-reset. *M*
- [ ] **A08** TOTP MFA for bank roles (columns already exist). *S*
- [ ] **A09** Device binding actually written on first agent login; reset endpoint. *S*
- [ ] **A10** **Remove the static login** — the eight places in §1: LoginPage demo buttons, LandingPage prefill, `/manager-bridge`, `public/collection_dashboard/`, credentials printed in compose/README. A grep test forbids hardcoded credentials in `frontend/src`. *S*
- [ ] **A11** Frontend auth pages: clean login, set-password (invite), change-password, forgot-password; role-based home incl. `/bank`. *M*
- [ ] **A12** Cross-tenant behavioural test: 2 banks × 2 agencies, every GET as every principal, no foreign row. *M*
- [x] **A13** Postgres RLS on tenant tables + `BYPASSRLS` job role — only after A12 is green. *M* Step 1 (`v2_0012_rls`, branch `a13-rls`): policies enabled and proven as `tiq_app`, not enforced for the API (DATA-MODEL-V2 §8.6).
- [ ] **A13b** **OWNER-gated.** Enforce RLS: API and workers log in as `tiq_app` / `tiq_jobs` (`JOBS_DATABASE_URL`), SECURITY DEFINER pre-auth lookups, tenant on the analytics session, MV refresh ownership, then `FORCE`. Plan: DATA-MODEL-V2 §8.6 step 2. Prerequisites from the Opus audit: system/NULL-bank audit rows via SECURITY DEFINER or tiq_jobs (pg test); tenant on the analytics session and workers as tiq_jobs; token-table SELECTs behind SECURITY DEFINER. *M*
- [x] **A14** Brand as data: "ABC Bank" in SMS/WhatsApp/receipts/UPI QR (7 files), "Agency Manager" header, RBI reg. no. on the ID card. Also the hardcoded Gurugram coordinates used when GPS fails at check-in (`AgentHomePage`) and visit submit (`RecordVisitPage`) � carried over from P0-02. *S*
- [ ] **A15** ~~`PRODUCT_MODE = standalone | embedded`; `/api/field-ops` contract preserved;~~ `SERVICE` role accounts replace manager-password service logins. *S* *(2026-09-28: `/api/field-ops` deleted (D4); `PRODUCT_MODE` removed, ADR 0009. The SERVICE role is kept, unwired.)*
- [ ] **A16** New audit actions wired through `write_audit` (invites, sessions, agency lifecycle, placements). *S*

---

## P2 — Agency onboarding and Manage Agents · M3

### Bank side (§6.1)

- [ ] **D01** `services/bank/agency_service.py` + `/api/v1/bank/agencies`: create draft, update, presigned document upload, activate, suspend, renew. *M*
- [ ] **D02** Onboarding wizard, six steps (identity, coverage, contract, documents, master login, review). **Command Center UI.** *L*
- [ ] **D03** Master-login invite → accept → agency `ACTIVE`; audited. *S* · deps A06
- [ ] **D04** Offboard with four-eyes: recall open placements, re-place, archive. *M*

### Agency side (§10) — existing view's components

- [ ] **G01** `/api/v1/agency/agents`: create, edit, suspend/reactivate (reassign prompt), reset password, reset device, transfer manager, bulk CSV import with preview, seat limit. *L*
- [ ] **G02** **Manage Agents** tab: table (with email/phone), create drawer with base-location map picker, row actions. *L*
- [ ] **G03** AGENCY_ADMIN manages agency managers. *M*
- [ ] **G04** Agency profile page (contract, commission, SLA) + placements received / recalls. *M*
- [ ] **G05** ID card shows the verification QR — finishes known issue 2. *S*
- [ ] **G06** Mobile nav: 4 primary tabs + "More" sheet. *S*
- [ ] **G07** Extract `manager_service.py` for the scoping helpers and agent admin. *M*
- [ ] **P2-E2E** Playwright: onboard agency → accept invite → create agent → agent logs in on the simulator → receives a beat after the nightly run. *M*

---

## P3 — Bank portal: Command Center + Agency Management · M4

### UI parity foundation (§2.5) — do these first

- [~] **UI01** Extract the Command Center UI spec to `docs/ui/COMMAND-CENTER-UI-SPEC.md`: every token (colours, DPD colours, chart series), fonts, radii, shadows, glass/blur values, motion, the shell, every `components/ui` variant, number formatters; plus a reference screenshot set of each CC page. *M*
  *Spec written 2026-09-24 (pulled forward for the simulator chrome). Key finding: CC **renders** indigo `#4F46E5`, system font, weights capped at 600, outlined buttons — its `design.md` ("Apple Glass", `#1677FF`) is overridden by an unlayered block at the end of its `index.css`. Both apps are Tailwind 3.4.19; the collisions are token names, so the port is a second Tailwind config with `important: ".bank-root"` (§7 of the spec). **Open:** the 30 reference screenshots (§8).*
- [ ] **UI02** Port tokens, fonts, chart theme and global utilities to `frontend/src/bank/theme`, **scoped to `/bank`** so existing views are untouched (reconcile any Tailwind-version difference via scoped CSS variables). *M*
- [ ] **UI03** Port `components/ui/*` primitives to TypeScript under `src/bank/ui`, same variants and class strings. *M*
- [ ] **UI04** Bank shell: sidebar (sections of §5.1), header, page container — replicating CC's `Sidebar.jsx` and App shell. *M*
- [ ] **UI05** Port the signature composites: KPI card flow (`PulseKpiFlow`), analytics tab bar, `DrillPanel`, `DecisionAlerts`, `WorkspaceModal`, tables, heat grid, formatters. *L*
- [ ] **UI06** Visual parity harness: Playwright screenshots of CC and the bank portal side by side per page; reviewed diffs. Re-run on every P3–P5 page. *M*

### Command Center (§5)

- [ ] **C01** `services/bank/kpi_catalog.py`: every KPI once — SQL expression, label, basis text, unit, direction, drill dimension. *M* · deps B13
- [ ] **C02** `KpiFilter` + global filter bar in the URL; test that every filter changes every dependent KPI. *M*
- [ ] **C03** Overview: 12 header KPIs (§5.3), narrative, totals. *M*
- [ ] **C04** Analytics, eight tabs (§5.4): Exposure, Migration, Recovery, Field Operations, Agencies, Cost to Collect, Concentration, Compliance. *L*
- [ ] **C05** Drill endpoint + panel (8 stats, splits, top 15; + agency/region/agent dims). *M*
- [ ] **C06** Alerts: 7 ported rules + 4 field rules, alert cards page. *M*
- [ ] **C07** Performance: < 800 ms p95 on `stress`. *M*

### Agency Management (§6.2–6.3)

- [ ] **D05** Agency directory: table + coverage map; sort/filter by zone, region, state, city, product, status, contract expiry, score. *M*
- [ ] **D06** Agency scorecard (`mv_agency_scorecard_monthly`), **Recovery vs Expected**, Agency Performance Index with peer-group shrinkage; leaderboard by region. *L*
- [ ] **D07** Agency drill: the agency's own analytics, read-only, bank-scoped. *M*
- [ ] **D08** Manual placement: filter loans → place with agency within capacity and coverage. *M*
- [ ] **D09** Placement engine v1: hard gates, scored min-cost-flow solve, `simulate` mode, `placement_decisions` with reasons, recall rules. *L*

### Bank admin

- [ ] **K01** Bank users (invite, roles, MFA status, sessions), regions editor, bank settings, audit page. *M*

---

## P4 — AI Strategy and Tools (§7) · M5

- [ ] **E01** Segment transition matrices from `mv_bucket_transitions_monthly`; Dirichlet posterior sampler. *M*
- [ ] **E02** Monte Carlo engine: account-level, vectorised, correlated systematic + idiosyncratic shocks, bank levers, Beta recovery fractions, **percentiles per path**, IFRS-9 staging, tornado, 2-axis heatmap, standard errors. Unit tests: mass conservation, stress monotonicity, seed reproducibility. *L*
- [ ] **E03** Celery job with progress; `strategy.simulation_runs` persisted with seed and inputs. *M*
- [ ] **E04** Backtest harness (start 6 months back, report p10–p90 coverage) in CI on `dev`. *M*
- [ ] **E05** Simulator UI — port of CC `RiskSimulator.jsx`: Dashboard, Provisioning, Sensitivity, Compare, Approval & Memo. *L*
- [ ] **E06** 13-week cash forecast: bottom-up + ETS reconciliation, quantile bands, forecast-vs-actual tracker (MAPE). *L*
- [ ] **E07** Roll/slippage forecast and capacity forecast (with leave calendar). *M*
- [ ] **E08** Scenario Lab: one `strategy.cost_rates` table; budget optimiser, outreach simulator, agency-reallocation and commission what-ifs. *L*
- [ ] **E09** Report engine: payload from the KPI catalog; PDF (ReportLab + matplotlib, rupee font), PPTX (python-pptx native charts), XLSX (openpyxl); MinIO + presigned download + `DATA_EXPORT`. *L*
- [ ] **E10** Templates — Board, Risk, Audit, Agency Review, Monthly MIS — and the Board Reports page (port of CC `BoardReportPanel`). *M*
- [ ] **E11** Narrative writer + number verifier + template fallback; `ai_generated` label. *M* · deps F01
- [ ] **E12** Month-end scheduled generation and email to recipients. *S*

---

## P5 — Tech Ops (§8) · M6

- [ ] **F01** `core/llm.py`: `anthropic` provider (Claude Sonnet 5 / Haiku 4.5), tool calling for Anthropic and OpenAI-compatible (Groq); fakes for tests. *M*
- [ ] **F02** Agent runtime: loop, guardrails (steps / tokens / ₹), PII redaction, output schema, persisted `ai.agent_runs` / `ai.agent_steps`, Celery execution. *L*
- [ ] **F03** Tool registry with read/write classes; write tools go through the approval queue. *M*
- [ ] **F04** Agent registry, versioned prompts, triggers (cron via beat, event, manual). *M*
- [ ] **F05** Eval harness: golden sets per agent; a version cannot activate below its gate. *M*
- [ ] **F06** AI Agents UI: list, editor, test console, run traces, approvals inbox. *L*
- [ ] **F07** MLOps console: models, monitoring charts, candidates (approve/promote on capabilities), serving state. *L*
- [ ] **F08** Retrain runs as a Celery job over `production_dataset`. *S*
- [ ] **F09** Prediction explorer and feature catalog. *M*
- [ ] **F10** Bank-feed data-quality checks + quarantine view. *M*
- [ ] **F11** LLM usage and cost dashboard from `ai.llm_calls`. *S*
- [ ] **F12** `ml.approve` / `ml.promote` restricted to `BANK_TECHOPS` — closes the known-issue-11 exposure. *S*

---

## P6 — ML and agentic AI (§9) · M7

Each ML item ships through `ml/pipeline` (PIT features, gates, comparison,
monitoring, `MODEL_DEVELOPMENT.html`) and is scored against the generator's
ground truth. Each agent ships through F05 evals. Order is value order.

- [ ] **H01** Case-mix-adjusted agency ranking — hierarchical Bayes over recovery vs expectation. *L*
- [ ] **H02** Placement optimiser — segment agency effects, ε-greedy slice, IPW evaluation; replaces D09's scoring. *L*
- [ ] **H03** Probabilistic cash forecast — quantile GBM; feeds E06. *L*
- [ ] **H11** Portfolio Copilot (bank) — KPI-catalog tools, charts, KPI-id citations. *L*
- [ ] **H12** Daily Strategy Agent — 07:00 brief + proposed actions into approvals. *M*
- [ ] **H13** Compliance Auditor — RBI Fair Practices flags from transcripts. *M*
- [ ] **H14** Voice → structured visit report (feature #2). *M*
- [ ] **H04** Covariate roll-rate model — feeds E01. *L*
- [ ] **H05** Time-to-pay survival model. *M*
- [ ] **H06** Next-best-action contextual bandit (feature #4). *L*
- [ ] **H07** Settlement optimiser (feature #8) — also a Monte Carlo lever. *L*
- [ ] **H08** Best time to contact — `contact_risk` with time-of-day. *M*
- [ ] **H09** Visit-evidence anomaly model beside the 7 fraud rules. *M*
- [ ] **H10** Territory design — H3 clustering + capacity balancing. *M*
- [ ] **H15** Agency Review Agent (month-end, shareable). *M*
- [ ] **H16** Board Pack Writer (uses E11 verifier). *S*
- [ ] **H17** Borrower Outreach Drafter (multilingual, approval). *M*
- [ ] **H18** Field Copilot v2 (tool-using visit strategy). *M*

---

## P7 — Field-ready app (§11.2) · M8

- [ ] **I01** PWA: manifest, icons, service worker (`vite-plugin-pwa`); installs over `HTTPS=1` on a LAN phone. *M*
- [ ] **I02** Offline outbox: IndexedDB queue for visits, photos, payments; replay on reconnect (feature #15). *L*
- [ ] **I03** *(optional)* Capacitor Android APK. *M*

---

## Cross-cutting, every phase

- [ ] **X01** CLAUDE.md updated at each milestone — measured counts, corrected visibly, never silently.
- [ ] **X02** CI: add `tests/pg`, the UI parity screenshots and the Playwright E2Es as they land.
- [ ] **X03** Security review of each phase's diff before its milestone closes (auth and tenancy phases especially).
- [ ] **X04** Demo script per milestone in `docs/demo/` — the clicks that show it working.

---

## Progress

| Phase | Tasks | Done |
|---|---|---|
| P0 | 8 | 6 (+2 partial) |
| P1 | 38 | 1 |
| P2 | 12 | 0 |
| P3 | 19 | 0 (UI01 partial) |
| P4 | 12 | 0 |
| P5 | 12 | 0 |
| P6 | 18 | 0 |
| P7 | 3 | 0 |
| X | 4 | 0 |
| **Total** | **126** | **7** |

### Found while doing P0 (2026-09-24), fixed or recorded

- **Fixed — the dev frontend silently ran stale code.** File-change events do not
  cross the Windows → Docker bind mount for Vite's watcher: an edit to a served
  module was still absent 3 s later, and a new route stayed missing until the
  `web` container restarted. `VITE_WATCH_POLLING=1` in compose turns on polling
  (`vite.config.ts`); verified an edit and its revert both served within 3 s.
- **Recorded — the backend suite is not green inside the `api` container**, and
  was not before this work: 16 failures + 4 errors, identical at HEAD. Causes:
  the dev `.env` sets `CONTACT_HOUR_START=0` / `CONTACT_HOUR_END=24` (switches
  off the RBI-hours rule 7 tests pin), `test_entrypoint_fixture` expects the
  host's bash layout, and `pyarrow` is not installed (ledger Phase 2). CLAUDE.md's
  "all passing" is a host measurement. 1,464 passed in the container.
