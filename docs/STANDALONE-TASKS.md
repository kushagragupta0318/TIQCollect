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
- [x] **B02** Model base: native `UUID`, `Money = NUMERIC(14,2)`, metadata `naming_convention`, `__table_args__ = {"schema": …}`. One shared test-engine factory with `schema_translate_map` for SQLite, replacing the 34 ad-hoc engines. *M*
  *Done — `app/models/base.py` (`UUIDType`, `Money`, `Rate`, `JsonDoc`, `uuid_fk` with NO ACTION); the shared factory is `tests/_db.py` (`SCHEMA_MAP` + `schema_translate_map`).*
- [x] **B03** `tenancy`: banks, regions (self-referencing zone → region → state → city), branches, agencies, agency_regions, agency_contracts, agency_documents. *M*
  *Done — `v2_0002_tables` creates all seven (plus `agency_contract_terms`); models in `app/models/tenancy.py`.*
- [x] **B04** `tenancy` identity: users gain `bank_id`/`agency_id`; roles extended (§2.1); role_permissions, user_sessions, user_invites, password_reset_tokens. *M*
  *Done — `v2_0002_tables` (users, permissions, role_permissions, user_sessions, user_invites, password_reset_tokens), `v2_0005` session-revoke/MFA columns, `v2_0008` permission seed.*
- [ ] **B05** `lending`: customers + addresses + contacts, loans (bank FK, branch FK, typed dates, NUMERIC), **loan_instalments**, **loan_dpd_history** (partitioned), bank_feed_batches/rows, bank_actions. *L*
  *(Landed in `v2_0002`/`v2_0003`: loans, loan_instalments, loan_dpd_history partitioned, bank_feed_batches/rows, bank_actions. **Open:** `customer_addresses` / `customer_contacts` — deferred to B23 with their ~50 readers, see `models/customer.py`.)*
- [ ] **B06** `collections`: placements, cases (+`bank_id`, `agency_id`, typed dates), case_assignments, visit_media (split from visits), settlement_offers, disputes, escalations. *L*
  *(Landed: placements, cases with tenant ids and typed dates, settlement_offers, disputes. **Open:** `case_assignments`, `visit_media` (B23, ~110 readers — `models/visit.py`), `escalations` — the capability `escalations.manage` is seeded but the table is not created.)*
- [ ] **B07** `workforce`: agents (agency FK, typed timestamps), attendance, agent_devices; agent_locations partitioned by month. *M*
  *(Landed: agents with the agency FK, agent_devices, agent_locations LIST(is_sos) → RANGE(recorded_at) monthly. **Open:** `attendance` — `v2_0007` records that the analytics layer derives the day's attendance from approved leave until it exists.)*
- [ ] **B08** `planning`: placement_runs/decisions, **beat_stops** replacing `ordered_case_ids`, tenant ids on allocation tables, allocation_decisions partitioned. *M*
  *(Landed: placement_runs, placement_decisions, tenant ids on the allocation tables, allocation_decisions partitioned by date. **Open:** `beat_stops` — deferred to B23 with its 58 readers of `ordered_case_ids` / `route_legs`.)*
- [ ] **B09** `ml`, `ai`, `strategy`, `audit` schemas; audit_logs partitioned with UPDATE/DELETE revoked + trigger. *M*
  *(Landed: all ten schemas in `v2_0001`, `audit.audit_logs` partitioned in `v2_0003`, and `v2_0012` grants it SELECT+INSERT only to `tiq_app`/`tiq_jobs`. **Open:** no UPDATE/DELETE trigger, so immutability holds only for the non-owner roles — the owner still bypasses it, which is CLAUDE.md known issue 6.)*
- [ ] **B10** Lookup tables for free-text domains (`legal_status`, `settlement_status`, decision outcome, objective, leave type). *S*
  *(Landed in `v2_0002`: legal_statuses, settlement_statuses, allocation_outcomes, allocation_objectives, placement_outcomes, collection_stages, bank_action_types. **Open:** `leave_types`.)*
- [ ] **B11** Real Alembic v2 baseline (`include_schemas`, version table in `public`); `seed_data.py` stops calling `create_all` — closes known issue 5. *M*
  *(Landed: `alembic/env.py` sets `include_schemas=True` and `version_table_schema="public"`; `ingest_daily.py`'s `create_all` is gone. **Open:** `scripts/seed_data.py:1305` still does `drop_all` + `create_all`, so schema authority is still split — CLAUDE.md known issue 4.)*
- [x] **B12** Partition maintenance task: create next 3 months, detach past retention; replaces the row-delete location sweep. *S*
  *Done — the policy and every operation in `core/partitions.py`; the schedule is `workers/tasks/partition_maintenance.py` at 01:30 IST. The 03:00 row sweep is kept until this has run in production.*
- [x] **B13** `analytics` schema: `dim_*` views, the five `mv_*` materialized views (§4.4), `REFRESH … CONCURRENTLY` task at 20:30. *L* B13a `v2_0007`; B13b `v2_0013`: portfolio, transitions, agency scorecard, the five `*_scoped` views, `v_visit_to_pay`, `strategy.cost_rates` (DATA-MODEL-V2 §6.4).
- [x] **B14** DB config: API `statement_timeout`, read-only analytics session + replica hook, PgBouncer-safe (`SET LOCAL` only). *S*
  *Done — `core/database.py`: `SET LOCAL statement_timeout` on every begin, `use_job_statement_timeout()` for the workers, and an analytics engine on `ANALYTICS_DATABASE_URL` (replica when set) whose every transaction is `SET LOCAL transaction_read_only = on`.*
- [x] **B15** `scripts/migrate_v1_to_v2.py`: today's fixture → v2 (ABC Bank, ABC Collections, manager1 → AGENCY_ADMIN, dates parsed, dpd history back-filled with `is_backfill`). *M*
  *Done — `scripts/migrate_v1_to_v2.py` + `tests/test_migrate_v1_to_v2.py`; step 1 of the committed v2 dump. **Wording stale:** the tenants it produces are Girivan Finance Ltd / Aravalli, not "ABC" — A14 and B16-B18 replaced the placeholder brand.*
- [x] **B16** Generator: extend the ledger simulator with tenancy, 4 zones / ~14 cities, **latent agency and agent skill**, contact time-of-day, settlement offers, disputes, complaints, injected compliance breaches — and a ground-truth manifest. **Onboards the Appendix C roster with every invented detail** (identity, people, coverage, contract + commission slab, specimen documents in MinIO, DRA register, onboarding audit history, lifecycle mix: 7 active / 1 suspended / 1 onboarding). *L*
  *Done 2026-09-28 (merge `e3610dc`) — one roster module `app/demo/roster.py`, the world/book builders in `app/demo/`, latent agency and agent skill + contact hour in `app/demo/latent.py`, and the ground-truth manifest `fixtures/fieldops-demo-v2.truth.json`. Girivan + 9 agencies, ~13.2k loans, 162 agents.*
- [x] **B17** `materialise.py` writes v2; profiles `dev` / `demo` / `stress` (§4.7). *M* · deps B16
  *Done — the three profiles are `PROFILES` in `scripts/generate_demo_v2.py` (dev: 4 agencies ≤ 10 agents; demo: the whole roster; stress: 6× agents, never committed). They live there rather than in `ml/simulation/ledger/materialise.py`, which stays the ledger's rewindable loader.*
- [ ] **B18** Regenerate `fieldops-demo.dump`; entrypoint checks `workforce.agents`; fixtures README rewritten (states the roster is fictional, lists the realistic logins of Appendix C.5); refresh-token hashes scrubbed. **Done when:** a grep of the fixture and seed finds no "ABC", "Test Bank", "Synthetic Bank", `manager1@` or `agent0` placeholder. *S*
  *(Landed: `fixtures/fieldops-demo-v2.dump` at `v2_0016`, `docker-entrypoint.sh` probes `to_regclass('workforce.agents')`, README rewritten, fixture secret-free. **Open:** the grep gate still fails on `scripts/seed_data.py` — "ABC Bank" (lines 6, 435, 1530-1531) and `manager1@tiqcollect.in` (line 1315). The v1 seeder was never converted; it goes with B11.)*
- [x] **B22** Demo-tenant safety: `is_demo` on banks/agencies; NotificationService suppresses SMS/WhatsApp/email for demo tenants (logged, not sent); a test proves an invented number is never dialled. *S*
- [x] **B19** `tests/pg/` suite + Postgres service in CI: partitions, RLS, mv refresh, v1→v2 transform. *M*
  *Done — `backend/tests/pg/` (11 modules: partitions, RLS, schema, analytics A and B, agency scorecard/effect, activation lock, bank overview, demo fixture) and the `backend-pg` CI job on `postgres:16` with `TIQ_PG_TEST_URL`. The v1→v2 transform is covered on SQLite by `tests/test_migrate_v1_to_v2.py`.*
- [ ] **B13c** Bank business-day calendar (weekends, holidays) per bank; then offer business-day windows in `v_visit_to_pay` and SLA measures (B13b audit: today they are calendar days). *S*
- [ ] **B20** ML adapter on typed columns; the Phase 3 equality harness (78 tests) and PIT tests green throughout. *M*
- [ ] **B21** Stress profile loaded; baseline query timings recorded in `docs/DATA-MODEL-V2.md`. *S*

### P1-A Tenancy, identity, access (§3)

- [x] **A01** Capability registry + `require_perm()` dependency; role → permission seed. *M*
  *Done — `core/permissions.py` (`CAPABILITIES`, `ROLE_CAPABILITIES`, `require_perm()`, which raises `KeyError` on an undeclared code) and the seed in `v2_0008_permission_seed`. 37 `require_perm` uses across `api/`.*
- [x] **A02** `RequestContext` and JWT claims (`bank_id`, `agency_id`, `perms`); migrate `get_current_user` callers. *M*
  *Done — `core/request_context.py`. `role` / `bank_id` / `agency_id` and `perms` are read from the DB-loaded user, not from the token's claims, so the context can never disagree with the registry.*
- [ ] **A03** `scope.py` (`agents_in_scope`, `cases_in_scope`, …); replace inline `manager_user_id` filters; **close the three leaks** (unallocated pool, planner pool, agent access to unassigned cases). *L*
  *(Landed: `services/scope.py` with `agents_in_scope`, `cases_in_scope`, `agent_case_or_404`, `today_beat_cases`, `agencies_in_scope`, `region_limit_path` — and the three leaks are closed, with the 404/403 oracle removed too (see the file's changelog). **Open:** 33 inline `Agent.manager_user_id == current_user.id` filters remain in `endpoints/manager.py`; they go with G07 / RESTRUCTURE-PLAN 2.4.)*
- [x] **A04** Planner draws per-agency pools; the nightly task loops agencies, then managers. *M*
  *Done — `workers/tasks/allocation.py` groups ACTIVE managers by agency and plans each agency's book; `PlannerService` binds its candidate pool to the manager's own `agency_id`.*
- [ ] **A05** `user_sessions`: refresh token per device, reuse detection per session, list and revoke. *M*
  *(Landed: `tenancy.user_sessions`, one refresh token per device slot, per-session reuse detection (`REUSE_DETECTED`, `auth_service.py:436`), revoke-on-logout / password-change / device-reset, and a `SESSION_REVOKED` audit row. **Open:** no "my sessions" list or per-session revoke endpoint for the user — `/auth` has only login, quick-login, refresh, logout, me.)*
- [x] **A06** Invites: create / accept, single-use, hashed at rest, 72 h. *S*
  *Done — `services/invite_service.py`: `INVITE_TTL = 72h`, stored only as `token_sha256`, acceptance is a compare-and-swap. Routes on `accounts.py` (`/invites/preview`, `/invites/accept`).*
- [x] **A07** Password change, admin reset, forced change on first login, OTP self-reset. *M*
  *Done — `services/password_service.py` + `accounts.py`: `/password/change`, `/password/forgot` → `/password/forgot/verify` (SMS code) → `/password/reset`, and `User.must_change_password` returns a FIRST_LOGIN ticket instead of a session.*
- [x] **A08** TOTP MFA for bank roles (columns already exist). *S*
  *Done — `services/mfa_service.py` (enrollment, code claim with replay protection, disable) + `/mfa*` routes and `pages/auth/MfaSetupPage.tsx`.*
- [x] **A09** Device binding actually written on first agent login; reset endpoint. *S*
  *Done — `v2_0009`/`v2_0010` device secret + bound flag; `auth_service` binds on first login and matches the secret thereafter; reset via `POST /manager/agents/{id}/reset-login`.*
- [x] **A10** **Remove the static login** — the eight places in §1: LoginPage demo buttons, LandingPage prefill, `/manager-bridge`, `public/collection_dashboard/`, credentials printed in compose/README. A grep test forbids hardcoded credentials in `frontend/src`. *S*
  *Done 2026-09-24 — `/manager-bridge` and `public/collection_dashboard/` are gone; the grep tripwire is `frontend/src/noHardcodedCredentials.test.ts`.*
- [x] **A11** Frontend auth pages: clean login, set-password (invite), change-password, forgot-password; role-based home incl. `/bank`. *M*
  *Done — `pages/auth/`: LoginPage, SetPasswordPage, ResetPasswordPage, ForgotPasswordPage, MfaSetupPage, AccountSecurityPage, QuickLoginPage. The one home rule is `lib/roles.ts` `guardRedirect`, used by ProtectedRoute, `BankApp`, LoginPage and QuickLoginPage.*
- [x] **A12** Cross-tenant behavioural test: 2 banks × 2 agencies, every GET as every principal, no foreign row. *M*
  *Done — `tests/test_cross_tenant_behavioral.py` (346 lines): a parametrised sweep of `ROUTES` as every principal, plus the aggregate-counts test that another tenant's new cases and payments move nothing.*
- [x] **A13** Postgres RLS on tenant tables + `BYPASSRLS` job role — only after A12 is green. *M* Step 1 (`v2_0012_rls`, branch `a13-rls`): policies enabled and proven as `tiq_app`, not enforced for the API (DATA-MODEL-V2 §8.6).
- [ ] **A13b** **OWNER-gated.** Enforce RLS: API and workers log in as `tiq_app` / `tiq_jobs` (`JOBS_DATABASE_URL`), SECURITY DEFINER pre-auth lookups, tenant on the analytics session, MV refresh ownership, then `FORCE`. Plan: DATA-MODEL-V2 §8.6 step 2. Prerequisites from the Opus audit: system/NULL-bank audit rows via SECURITY DEFINER or tiq_jobs (pg test); tenant on the analytics session and workers as tiq_jobs; token-table SELECTs behind SECURITY DEFINER. *M*
- [x] **A14** Brand as data: "ABC Bank" in SMS/WhatsApp/receipts/UPI QR (7 files), "Agency Manager" header, RBI reg. no. on the ID card. Also the hardcoded Gurugram coordinates used when GPS fails at check-in (`AgentHomePage`) and visit submit (`RecordVisitPage`) � carried over from P0-02. *S*
- [ ] **A15** ~~`PRODUCT_MODE = standalone | embedded`; `/api/field-ops` contract preserved;~~ `SERVICE` role accounts replace manager-password service logins. *S* *(2026-09-28: `/api/field-ops` deleted (D4); `PRODUCT_MODE` removed, ADR 0009. The SERVICE role is kept, unwired.)*
- [x] **A16** New audit actions wired through `write_audit` (invites, sessions, agency lifecycle, placements). *S*
  *Done — invites (`USER_INVITED` / `INVITE_ACCEPTED` / `INVITE_REVOKED` / `USER_CREATED`), sessions (`LOGIN`, `LOGIN_FAILED`, `MFA_FAILED`, `SESSION_REVOKED`), agency lifecycle (`AGENCY_ONBOARDED`, `DOCUMENT_UPLOADED` / `_VERIFIED` / `_REJECTED`, `v2_0011`) and placements (`PLACEMENT_ENDED`, `v2_0016`). Known issue 6's unwritten actions are the v1 set, not these.*

---

## P2 — Agency onboarding and Manage Agents · M3

### Bank side (§6.1)

- [ ] **D01** `services/bank/agency_service.py` + `/api/v1/bank/agencies`: create draft, update, presigned document upload, activate, suspend, renew. *M*
  *(Landed: `services/bank/agency_service.py` + `endpoints/bank_agencies_admin.py` — create draft, update identity, update coverage + contract, presign/confirm document, four-eyes verify/reject, and auto-activate (`_maybe_activate`). **Open:** suspend and renew, which the service's own header defers to the agency-profile lifecycle (G04/D04).)*
- [x] **D02** Onboarding wizard, six steps (identity, coverage, contract, documents, master login, review). **Command Center UI.** *L*
  *Done (merge `93a6639`) — `frontend/src/bank/pages/onboarding/`: `OnboardAgencyWizardPage.tsx`, `WizardStepper.tsx` and the six steps. Documents go through a presigned PUT with a signed upload token.*
- [x] **D03** Master-login invite → accept → agency `ACTIVE`; audited. *S* · deps A06
  *Done — `agency_service.invite_master_login()` → A06's accept → `_maybe_activate()` flips the agency to ACTIVE once the documents are verified; `AGENCY_ONBOARDED` audit rows at each step. `tests/pg/test_pg_agency_activation_lock.py` pins the activation race.*
- [ ] **D04** Offboard with four-eyes: recall open placements, re-place, archive. *M*

### Agency side (§10) — existing view's components

- [ ] **G01** `/api/v1/agency/agents`: create, edit, suspend/reactivate (reassign prompt), reset password, reset device, transfer manager, bulk CSV import with preview, seat limit. *L*
  *(Landed: `endpoints/manager_agents_admin.py` + `services/agent_management_service.py` — create, edit, suspend, reactivate, and `reset-login` (password + device in one, under an admin-credential-link rate limit). **Open:** transfer manager, bulk CSV import with preview, and the seat limit — no code for any of the three.)*
- [x] **G02** **Manage Agents** tab: table (with email/phone), create drawer with base-location map picker, row actions. *L*
  *Done (merge `6d0c4f2`) — `pages/manager/ManagerAgentsPage.tsx` with `CreateAgentDrawer` (`components/map/BaseLocationPicker`), `EditAgentDrawer` and `SuspendAgentModal`.*
- [ ] **G03** AGENCY_ADMIN manages agency managers. *M*
- [ ] **G04** Agency profile page (contract, commission, SLA) + placements received / recalls. *M*
- [ ] **G05** ID card shows the verification QR — finishes known issue 2. *S*
  *(The public check endpoint `GET /verify` and `components/ui/AgentIDCard.tsx` both exist, but the card's back face draws `MiniQR`, a **deterministic pseudo-QR grid** over the plain string `tiqcollect:agent:{id}:{card}` — not a scannable code and not the signed token `/verify` expects. The card is not yet verifiable.)*
- [x] **G06** Mobile nav: 4 primary tabs + "More" sheet. *S*
  *Done (merge `73d60a5`) — `components/layout/managerNav.ts` (`MOBILE_PRIMARY` / `MOBILE_MORE`), with `managerNav.test.ts` proving the split loses and duplicates nothing.*
- [ ] **G07** Extract `manager_service.py` for the scoping helpers and agent admin. *M*
  *(Agent admin did move out, to `services/agent_management_service.py`, and the scoping helpers to `services/scope.py`. **Open:** there is no `manager_service.py` and `endpoints/manager.py` is still 5,230 lines with 33 inline tenant filters — RESTRUCTURE-PLAN 2.4.)*
- [ ] **P2-E2E** Playwright: onboard agency → accept invite → create agent → agent logs in on the simulator → receives a beat after the nightly run. *M*

---

## P3 — Bank portal: Command Center + Agency Management · M4

### UI parity foundation (§2.5) — do these first

- [~] **UI01** Extract the Command Center UI spec to `docs/ui/COMMAND-CENTER-UI-SPEC.md`: every token (colours, DPD colours, chart series), fonts, radii, shadows, glass/blur values, motion, the shell, every `components/ui` variant, number formatters; plus a reference screenshot set of each CC page. *M*
  *Spec written 2026-09-24 (pulled forward for the simulator chrome). Key finding: CC **renders** indigo `#4F46E5`, system font, weights capped at 600, outlined buttons — its `design.md` ("Apple Glass", `#1677FF`) is overridden by an unlayered block at the end of its `index.css`. Both apps are Tailwind 3.4.19; the collisions are token names, so the port is a second Tailwind config with `important: ".bank-root"` (§7 of the spec). **Open:** the 30 reference screenshots (§8).*
- [x] **UI02** Port tokens, fonts, chart theme and global utilities to `frontend/src/bank/theme`, **scoped to `/bank`** so existing views are untouched (reconcile any Tailwind-version difference via scoped CSS variables). *M*
  *Done (merge `61856d2`) — `src/bank/theme/`: `colors.ts`, `chartTheme.ts`, `format.ts` (+ tests), under a second Tailwind config scoped with `important: ".bank-root"`.*
- [x] **UI03** Port `components/ui/*` primitives to TypeScript under `src/bank/ui`, same variants and class strings. *M*
  *Done (merge `61856d2`) — `src/bank/ui/`: badge, button, card, dialog, input, label, select, separator, sidebar, table, textarea, with the variant class strings split out (`buttonVariants.ts`, `badgeVariants.ts`).*
- [x] **UI04** Bank shell: sidebar (sections of §5.1), header, page container — replicating CC's `Sidebar.jsx` and App shell. *M*
  *Done (merge `61856d2`) — `src/bank/layout/`: `BankLayout`, `BankSidebar`, `BankTopBar`, `BankSearchBar`, `navigation.ts` (the one list the sidebar and `BankApp`'s router both read, so they cannot disagree).*
- [x] **UI05** Port the signature composites: KPI card flow (`PulseKpiFlow`), analytics tab bar, `DrillPanel`, `DecisionAlerts`, `WorkspaceModal`, tables, heat grid, formatters. *L*
  *Done (merge `61856d2`) — `src/bank/components/`: `PulseKpiFlow`, `DrillPanel`, `DecisionAlerts`, `WorkspaceModal`, `DataTable`, `analytics.tsx`, `charts.tsx`, `portfolioVisuals.tsx`, `visualMath.ts`. The components are ported; the data behind `DrillPanel` and `DecisionAlerts` is still C05/C06.*
- [ ] **UI06** Visual parity harness: Playwright screenshots of CC and the bank portal side by side per page; reviewed diffs. Re-run on every P3–P5 page. *M*
  *(Not started — there is no Playwright dependency in `frontend/package.json`; `frontend/e2e/` holds only the Python simulator-acceptance script.)*

### Command Center (§5)

- [x] **C01** `services/bank/kpi_catalog.py`: every KPI once — SQL expression, label, basis text, unit, direction, drill dimension. *M* · deps B13
  *Done (merge `bee86de`) — `services/bank/kpi_catalog.py`: 12 `KpiDef`s, each with its SQL, label, unit, direction and `drill` target, plus `available_views()` so a missing view abstains instead of guessing.*
- [x] **C02** `KpiFilter` + global filter bar in the URL; test that every filter changes every dependent KPI. *M*
  *Done — `services/bank/kpi_filter.py` (backend) and `bank/components/kpiFilter.ts` + `useKpiFilter.ts` + `FilterBar.tsx` (frontend). The filter round-trips through `useSearchParams`, so a filtered view is a shareable URL. Tests: `bank/components/kpiFilter.test.ts`, `tests/test_bank_overview.py`, `tests/pg/test_pg_bank_overview.py`.*
- [x] **C03** Overview: 12 header KPIs (§5.3), narrative, totals. *M*
  *Done (merge `bee86de`) — `GET /api/v1/bank/overview` + `GET /bank/filters`, rendered by `bank/pages/BankOverviewPage.tsx` with the 12 KPIs, the book's totals and the rule-based narrative (labelled by `generated_by`).*
- [ ] **C04** Analytics, eight tabs (§5.4): Exposure, Migration, Recovery, Field Operations, Agencies, Cost to Collect, Concentration, Compliance. *L*
  *(Not started — every nav item but `overview` and `agencies/*` still renders `BankPlaceholderPage`.)*
- [ ] **C05** Drill endpoint + panel (8 stats, splits, top 15; + agency/region/agent dims). *M*
  *(The `DrillPanel` component is ported (UI05) and each KPI already declares its `drill` target, but there is no drill endpoint.)*
- [ ] **C06** Alerts: 7 ported rules + 4 field rules, alert cards page. *M*
  *(`DecisionAlerts` is ported (UI05); no rules exist in `services/bank/`.)*
- [ ] **C07** Performance: < 800 ms p95 on `stress`. *M*

### Agency Management (§6.2–6.3)

- [x] **D05** Agency directory: table + coverage map; sort/filter by zone, region, state, city, product, status, contract expiry, score. *M*
  *Done (merge `93a6639`) — `GET /bank/agencies-directory` + `GET /bank/regions`, rendered by `bank/pages/directory/AgencyDirectoryPage.tsx`.*
- [x] **D06** Agency scorecard (`mv_agency_scorecard_monthly`), **Recovery vs Expected**, Agency Performance Index with peer-group shrinkage; leaderboard by region. *L*
  *Done (merge `4331a2a`) — `services/bank/agency_scorecard.py`, `expected_recovery.py` (the one definition of the Recovery-vs-Expected denominator) and `agency_effect.py`, which shrinks through `ml.empirical_bayes.eb_shrink` rather than its own copy (`901b3a8`). Routes `GET /bank/agencies/{id}/scorecard` and `/bank/agencies-leaderboard`; UI `bank/pages/performance/AgencyPerformancePage.tsx`; `tests/pg/test_pg_agency_scorecard.py`.*
- [ ] **D07** Agency drill: the agency's own analytics, read-only, bank-scoped. *M*
- [x] **D08** Manual placement: filter loans → place with agency within capacity and coverage. *M*
  *Done (merge `8189876`) — `services/manual_placement_service.py` + `placement_read_service.py`, `endpoints/bank_placements.py` (`/loans`, `/agencies`, `/preview`, `POST ""`, `/{id}/recall`), UI `bank/pages/BankPlacementPage.tsx`. Feed-driven ends (`PAID_DIRECT` / `SETTLED` / `WRITTEN_OFF` / `DECEASED` / recall) close the placement, and `scripts/reconcile_placements.py` is the one-shot backfill.*
- [x] **D09** Placement engine v1: hard gates, scored min-cost-flow solve, `simulate` mode, `placement_decisions` with reasons, recall rules. *L*
  *Done (merge `8189876` + `6e952a2`, ADR 0010) — `services/bank/placement_engine.py`: the gates as one pure `judge()` over facts, a min-cost-flow solve, `PLACEMENT_EXPLORATION_RATE` ε-exploration, `placement_decisions` with reasons, contract-driven recall rules, and a four-eyes apply behind a confirm step (`/bank/placements/runs`, `/runs/{id}/decisions`, `/runs/{id}/apply`).*

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

- [x] **F01** `core/llm.py`: `anthropic` provider (Claude Sonnet 5 / Haiku 4.5), tool calling for Anthropic and OpenAI-compatible (Groq); fakes for tests. *M*
  *Done 2026-09-24 (merge `73d60a5`) — `core/llm.py`: an `anthropic` provider on the official SDK (two tiers), a provider-neutral `chat()` translated to Anthropic content blocks or OpenAI `tool_calls`, `json_schema=` structured output, and `FakeLLM` + `use_fake()`. The tool **loop** is deliberately F02's, not the seam's.*
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
  *(Half done: `core/permissions.py:173-174` declares both capabilities as `BANK_TECHOPS`-only, sensitive and second-person. **Open:** the routes still take `ManagerOnly` (`endpoints/manager.py:1925`, `:1973`) with no `require_perm`, so the exposure is live — every manager can still promote a model.)*

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
- [x] **H14** Voice → structured visit report (feature #2). *M*
  *Done (merge `b538dc5`) — `services/visit_report_extraction.py` 1.2.0 behind `core/transcription.py`: month/year parsing, stance-aware apply, `CUSTOMER_TAG_DECEASED`.*
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

- [x] **I01** PWA: manifest, icons, service worker (`vite-plugin-pwa`); installs over `HTTPS=1` on a LAN phone. *M*
  *Done 2026-09-24 (merge `73d60a5`) — `vite-plugin-pwa` in `vite.config.ts`, `lib/pwaConfig.ts` + `swLifecycle.ts` + `registerServiceWorker.tsx` (all tested), icons in `frontend/public/`.*
- [x] **I02** Offline outbox: IndexedDB queue for visits, photos, payments; replay on reconnect (feature #15). *L*
  *Done (merge `cf8dcfa`, ADR 0011) — `lib/outbox.ts` + `outboxIdb.ts` + `outboxRunner.ts`: visits with their photos and signature, and call logs and PTPs, queued in IndexedDB with a `client_submission_id` and a partial UNIQUE index per agent (`v2_0015`); server-clock capture stamps, `LATE_SYNC` / `SYNC_WITHHELD` manager signals, and a per-login read cache. **Deliberate change from this wording:** payments are never queued — the borrower's OTP has to reach the server — so they stay live with idempotency ids (ADR 0011 §"Never queued: payments").*
- [ ] **I03** *(optional)* Capacitor Android APK. *M*

---

## Cross-cutting, every phase

- [ ] **X01** CLAUDE.md updated at each milestone — measured counts, corrected visibly, never silently.
- [ ] **X02** CI: add `tests/pg`, the UI parity screenshots and the Playwright E2Es as they land.
  *(`tests/pg` landed as the `backend-pg` job on `postgres:16` (merges `787c9b8`, `22303b7`). The UI parity screenshots (UI06) and the Playwright E2Es (P2-E2E) have not landed, so there is nothing to add yet.)*
- [ ] **X03** Security review of each phase's diff before its milestone closes (auth and tenancy phases especially).
- [ ] **X04** Demo script per milestone in `docs/demo/` — the clicks that show it working.

---

## Progress

Reconciled against the code and `git log` on 2026-09-30 at `3d3e2dc`; the
counts below are `grep -c` on this file, not carried forward. "Part" counts
items still `[ ]` whose note says what landed and what remains.

| Phase | Tasks | Done `[x]` | In progress `[~]` | Part | Open |
|---|---|---|---|---|---|
| P0 | 8 | 6 | 2 | 0 | 0 |
| P1 | 40 | 25 | 0 | 10 | 5 |
| P2 | 12 | 4 | 0 | 4 | 4 |
| P3 | 19 | 11 | 1 | 2 | 5 |
| P4 | 12 | 0 | 0 | 0 | 12 |
| P5 | 12 | 1 | 0 | 1 | 10 |
| P6 | 18 | 1 | 0 | 0 | 17 |
| P7 | 3 | 2 | 0 | 0 | 1 |
| X | 4 | 0 | 0 | 1 | 3 |
| **Total** | **128** | **50** | **3** | **18** | **57** |

P1 is 40, not the 38 of the earlier table: B13c and A13b were added after it
was written.

**Milestone reality.** M2 (P1) is close: the data platform's remaining gap is
the B23 table split (customer addresses/contacts, visit_media, beat_stops,
case_assignments, attendance, escalations) and `seed_data.py`'s `create_all`.
M3 (P2) has the bank half — onboarding wizard, master-login invite, activation
— but not the agency half (G03, G04, G05, G07) or the E2E. M4 (P3) has the UI
foundation, Overview, the whole of Agency Management bar the drill, but none
of Analytics, Drill or Alerts.

### Landed with no task id

Work that shipped in this window and that no task on this list covers. It
needs task ids, or a line in the plan saying it is out of scope:

- **The standalone production stack** (merge `2bd334d`, ADR 0009):
  `docker-compose.prod.yml`, `deploy/Caddyfile`, `backend/scripts/create_first_admin.py`,
  `docs/DEPLOY.md`. ADR 0010 records the S3-compatibility probe.
- **The maps rework** (merges `96af078`, `9c86b79`): Mapbox tiles with an OSM
  fallback (`lib/mapTiles.ts`), marker clustering above 40 points with SOS
  never clustered, live-map poll-diff, Douglas-Peucker polyline simplify
  (`lib/simplify.ts`), and an XSS hotfix on every Leaflet HTML sink
  (`lib/html.ts`).
- **B22's sibling work**: `scripts/reconcile_placements.py`, the demo daily
  feed's authorised-(DPD, product) rule, and the `is_demo` outbound suppression
  already ticked under B22.

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
