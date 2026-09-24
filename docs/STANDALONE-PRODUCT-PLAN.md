# TIQCollect Standalone — Product and Engineering Plan

**Status:** proposal, dated 2026-09-24. No code has changed. Every "today"
statement below was read from the code on that date (this repo, and the
Collections platform at `CR dashboard/collections-platform`), with file
references where they matter.

---

## 0. Summary

TIQCollect today is the field-operations module of a larger collections
platform. It has one level of tenancy (a manager and the agents whose
`manager_user_id` points at them), no Bank or Agency entity, no way to create a
user except the seed, and a login page with hardcoded demo credentials.

The standalone product has **three portals over one backend**:

```
            ┌──────────────────────── BANK PORTAL ────────────────────────┐
            │ Command Center · AI Strategy & Tools · Agency Management    │
            │ Agency Onboarding · Tech Ops (AI Agents + MLOps)            │
            └──────────────┬──────────────────────────────────────────────┘
                           │ places cases with, onboards, monitors
        ┌──────────────────┼──────────────────┐
   AGENCY PORTAL      AGENCY PORTAL      AGENCY PORTAL      (today's manager view
   (ABC Collections)  (XYZ Recovery)     (…)                 + Manage Agents tab)
        │                  │
   AGENT APP ×N       AGENT APP ×N                           (today's agent view,
                                                              plus a mobile simulator)
```

Nine workstreams, in this order:

| # | Workstream | Size | Depends on |
|---|---|---|---|
| I | Mobile app simulator (quick win, runs in parallel) | S–M | — |
| A | Tenancy, identity and access (remove static login) | L | — |
| B | Data platform: Postgres schemas, types, views, generator | L | — |
| G | Agency view: Manage Agents tab | M | A, B |
| D | Bank portal: Agency Onboarding and Agency Management | M–L | A, B |
| C | Bank portal: Command Center analytics | L | B |
| E | Bank portal: AI Strategy (Monte Carlo, forecasting, board reports) | L | C |
| F | Bank portal: Tech Ops (AI agent studio, MLOps console) | M–L | A |
| H | ML and agentic AI expansion | L, ongoing | B, F |

Sizes are relative: S is days, M is one to two weeks, L is several weeks.
They are not commitments.

---

## 1. Where we start

The facts that shape the plan.

**Tenancy and auth**
- `UserRole` has three values: `FIELD_AGENT`, `AGENCY_MANAGER`,
  `AGENCY_ADMIN` (`models/user.py:7-10`). `AGENCY_ADMIN` grants nothing extra
  anywhere (known issue 11).
- `Agent.agency_id` is a free `String(50)`, not a foreign key. It is set to
  `"AGENCY-TIQ-001"` for every agent and is read only by `/verify-agent`.
  `Loan.bank_name` is free text and is "ABC Bank" on 100% of rows.
- Scoping joins through `Case.agent_id → Agent.manager_user_id`. Cases,
  loans, visits and payments carry no tenant column.
- **Three cross-tenant leaks will appear the moment there is a second
  agency.**
  - `GET /manager/cases/unallocated` is unscoped (`manager.py:1310`).
  - The nightly planner takes from the global `agent_id IS NULL` pool
    (`planner_service.py:290-303`).
  - Agents can open any unassigned case (`case_service.py:561-571`).
- No endpoint creates a user or an agent. There is no password change or
  reset, no deactivate or suspend path, and `User.is_active` is never
  switched off.
- The refresh-token hash is a single slot per user, so one login invalidates
  another session.
- The "static login" is hardcoded in eight places:
  - `LoginPage.tsx:45-48` and `:135-156` (demo buttons);
  - `LandingPage.tsx` prefill (7 buttons);
  - `ManagerBridgePage.tsx:17-18`, which auto-submits `manager1`;
  - `public/collection_dashboard/script.js`, a static dashboard where every
    agency links to `manager1`;
  - `seed_data.py`;
  - `fixtures/users.csv` and the dump, which include stored refresh-token
    hashes;
  - `docker-compose.yml:14-15` and `fixtures/README.md`.

**Data**
- There are 24 tables and 28 native enums, all in `public`. There are no
  views, no partitions and no triggers.
- PKs are VARCHAR UUIDs. Money is `Float`. At least 14 date or time columns
  are strings, among them `Loan.disbursement_date` and `Case.allocation_date`.
- The Alembic baseline is **empty**. The core tables come from `create_all`,
  and a seeded database has no `alembic_version` row.
- `Loan.dpd` and `Loan.status` are overwritten in place, so there is **no DPD
  history**. The platform's roll-rate matrix, NPA bridge and Monte Carlo all
  rest on a DPD panel, and this repo cannot produce one today.
- Demo book: 1 bank, 1 agency, 18 agents, 1,378 customers, 1,698 cases, NCR
  only. Richer generators exist but are unused: `generate_bank_data.py` covers
  14 banks and 15 cities; the ledger simulator is event-sourced, and
  `materialise.py` already writes into the real schema.
- 34 test files build their own **SQLite** engine. This matters for the
  schema design (§4.6).

**AI and ML**
- Six LLM call sites. All are single completions with no tool calling and no
  multi-step loops (`core/llm.py`: groq, openai or none).
- A mature ML lifecycle already exists: registry, gates, monitoring,
  candidates, four-eyes promotion. It is more mature than the platform's,
  whose "retrain" only writes a ledger entry.

**What the platform's Command Center gives us to port** (full inventory in
§5):
- One KPI engine (`engines/portfolio_pulse.py`) with 12 header KPIs, 5
  analytics tabs, a drill panel and 7 alert rules.
- A Markov Monte Carlo stress tester.
- A 12-week recovery forecast.
- A budget optimiser.
- A client-side jsPDF board pack with 9 sections.
- A LangGraph copilot with 11 tools.

**What it gets wrong, and we must not copy:**
- **Filters are cosmetic.** The persona filter only changes labels, and the
  `?period=` parameter is ignored.
- **Agency data is hardcoded.** There are 5 agencies in a JS file, and every
  one drills to ABC.
- **The Monte Carlo is shaky.**
  - p10 and p90 are per-state percentiles, summed afterwards.
  - Exposure is anchored on `loan_amount`, while the KPI engine uses
    `total_outstanding`.
  - "Accounts affected" is computed as write-off ₹Cr × 50.
- **Costs disagree.** Channel unit costs in the simulator differ from the
  activity ledger's.
- **Agent state is lost on restart.** It is held in an in-memory
  `MemorySaver`.

---

## 2. Target product

### 2.1 Roles

Permissions are **named capabilities** (for example `agency.onboard`,
`ml.promote`), granted to a role in one table. Routes check capabilities,
never role names. This is what closes known issue 11: today "manager" is the
only privilege level, and `promote` is open to every manager.

| Role | Portal | Scope | Key capabilities |
|---|---|---|---|
| `PLATFORM_ADMIN` | ops console | all banks | create banks, support. Vendor staff only |
| `BANK_ADMIN` | Bank | own bank | everything in the bank portal, onboard or suspend agencies, manage bank users |
| `BANK_ANALYST` | Bank | own bank (optionally region-limited) | Command Center, Strategy tools, reports. Read-only on agencies |
| `BANK_TECHOPS` | Bank | own bank | AI agent studio, MLOps console, `ml.approve`, `ml.promote` (four-eyes kept) |
| `AGENCY_ADMIN` | Agency | own agency | the **master login**: today's manager view plus Manage Agents, manage agency managers, agency profile |
| `AGENCY_MANAGER` | Agency | own team in the agency | today's manager view (existing semantics) |
| `FIELD_AGENT` | Agent | self | today's agent view |
| `SERVICE` | API | per agency or bank | machine accounts, replacing the platform's use of manager passwords (`TIQCOLLECT_AGENCY_ACCOUNTS`) |

### 2.2 Tenancy model

```
Bank ─┬─ Region hierarchy (Zone → Region → State → City/Branch)
      ├─ Agency ─┬─ AgencyContract (products, regions, capacity, commission slab, validity)
      │          ├─ Users (AGENCY_ADMIN / AGENCY_MANAGER)
      │          └─ Agents ── Beats, Visits, Payments, PTPs …
      └─ Loans/Customers ── Placement (loan → agency, dated) ── Case (agency's work item)
```

- `bank_id` is on every tenant row. `agency_id` is on every agency-owned row
  (cases, visits, payments, PTPs, beats, allocation runs).
- Both are **denormalised onto the large tables** on purpose. Every hot index
  and every future row-level-security policy needs them without a join.
- **Placement is the new core concept.** The bank places a delinquent loan
  with an agency for a period. The agency then allocates its placed cases to
  agents, which is today's nightly engine, unchanged in spirit.
- The unassigned pool becomes *per agency*, which closes all three leaks
  in §1.

### 2.3 Deployment modes

`PRODUCT_MODE = standalone | embedded`.

- **Standalone** turns on the bank portal.
- **Embedded** keeps today's behaviour, and the platform's Command Center
  keeps consuming `/api/v1/manager/*` and `/api/field-ops/*`.

The flag is what lets this repo keep serving the platform while becoming a
product.

### 2.4 Frontend shape

One SPA, three route trees, each lazy-loaded with its own layout:
- `/bank/*` is new;
- `/manager/*` is today's manager view, now the agency portal;
- `/agent/*` is today's agent view.

The stack is already right: React 19, React Query, zustand, Recharts 3,
Leaflet, Tailwind. The bank portal reuses it rather than adding a second app.

The backend gets `api/v1/endpoints/bank/` and `services/bank/`. **No new logic
goes into `manager.py`** (known issue 6: 4,667 lines).

### 2.5 UI rule, decided 2026-09-24

**Every new surface looks exactly like the Collections product's Command
Center.** That covers the bank portal (Command Center, AI Strategy, Agencies,
Onboarding, Tech Ops, Admin) and the simulator's chrome.

To get there:
- Command Center's design tokens, fonts, shell (sidebar and header), UI
  primitives and signature components are **ported, not reinterpreted**. The
  signature components are the KPI card flow, analytics tab bar, drill panel,
  alert cards, workspace modal, tables, heat grid and chart theme.
- The source is `collections-platform/command-center/frontend/src`
  (`lib/colors.js`, `lib/chartTheme.js`, `components/ui/*`,
  `components/Sidebar.jsx`, `PulseKpiFlow.jsx`, `DrillPanel.jsx` and more)
  and `shared/design.md`.
- The port lives under `frontend/src/bank/`, scoped to the `/bank` route tree.
- Parity is checked with side-by-side screenshots of each page.

**The existing TIQCollect views are not restyled.** They already follow the
same design concept. New screens *inside* them, such as Manage Agents and the
agency profile, use those views' existing components.

---

## 3. Workstream A — Tenancy, identity and access

### 3.1 Remove the static login

| Remove | Replace with |
|---|---|
| Demo buttons and prefill (`LoginPage`, `LandingPage`) | A plain login form. The landing page's CTAs go to `/login` with no state |
| `/manager-bridge` and `public/collection_dashboard/` | Deleted. The bank portal *is* the cross-agency view |
| Seeded passwords printed in compose and README | Demo accounts still exist in the demo fixture, documented once in `fixtures/README.md`, with **no UI shortcut** |
| Refresh-token hashes in the committed fixture | Scrubbed when the fixture is regenerated |
| "Forgot password" toast, dead "Remember me" | A real flow (below) |

### 3.2 New auth capabilities

**Invitations**
- `user_invites` holds single-use tokens that expire after 72 hours. The
  single-use and hashed-at-rest mechanism copies `used_quick_login_tokens`.
- The invitee sets their own password, so **no plaintext password is ever
  stored or shown twice**.

**Password lifecycle**
- Change, admin reset, and a forced change on first login.
- Self-service reset by SMS or email OTP, reusing `OtpService`.

**Sessions**
- `user_sessions` holds one refresh token per device and replaces the single
  slot.
- The "token reuse detected" defence is kept, per session.
- Admins can list and revoke sessions.

**MFA**
- TOTP for bank roles.
- The `totp_secret` and `totp_enabled` columns already exist and are unused.

**JWT and request context**
- The JWT gains `bank_id`, `agency_id` and `perms`.
- `get_current_user` becomes `get_request_context()` and returns
  `RequestContext(user, bank_id, agency_id, role, perms)`.
- Every query helper takes the context.

**Device binding**
- Finally wired: the fingerprint is written on first agent login.
- It can be reset from Manage Agents.
- It is dormant today (§1).

**Audit**
- Every new action writes an `AuditLog`: `USER_INVITED`, `USER_CREATED`,
  `PASSWORD_RESET`, `AGENCY_ONBOARDED`, `AGENCY_SUSPENDED`,
  `PLACEMENT_CREATED`, `SESSION_REVOKED` and similar.
- All writes go through `core/audit.write_audit`.

### 3.3 Scoping, done once

- `_require_own_agent` generalises into `scope.py`, with `agents_in_scope(ctx)`,
  `cases_in_scope(ctx)` and so on. This is the "one definition" rule applied
  to the tenancy leaks.
- The textual scoping test (`test_every_manager_route_that_reads_tenant_data_is_scoped`)
  is kept. A **behavioural** test is added beside it: two banks and two
  agencies are seeded, and every GET route is called as each principal and
  must return no foreign row. The textual test cannot see a service that
  drops the scope. That blind spot is how `export-decisions` leaked.
- **Postgres row-level security comes second, as defence in depth.**
  - Each request's transaction runs `SET LOCAL app.bank_id` and
    `SET LOCAL app.agency_id`.
  - Policies are keyed on those settings.
  - Nightly jobs run as a `BYPASSRLS` role.
  - It is enabled only after the application-level scoping is proven, so a
    policy bug cannot masquerade as an app bug.

---

## 4. Workstream B — Data platform

### 4.1 Schemas, not separate databases

The request was "create a DB for agents, customers, bank data…". This plan
recommends **one Postgres database with one schema per domain**:
- Postgres cannot join or foreign-key across databases.
- Postgres cannot run a transaction across databases.
- Almost every screen in this product joins agents, cases and loans.

Schemas give the same separation (ownership, grants, backup scope and
navigability), plus real constraints. If a domain ever needs its own server,
a schema is a clean unit to move.

| Schema | Owns | Tables (★ = new) |
|---|---|---|
| `tenancy` | who is who | banks★, regions★ (self-referencing hierarchy), branches★, agencies★, agency_regions★, agency_contracts★, agency_documents★, users, user_sessions★, user_invites★, role_permissions★ |
| `lending` | the bank's book | customers, customer_addresses★, customer_contacts★, loans, loan_instalments★, loan_dpd_history★, bank_feed_batches★, bank_feed_rows★ (staging), bank_actions★ |
| `collections` | the work | placements★, cases, case_assignments★ (agent history), visits, visit_media★, payments, ptps, call_logs, settlement_offers★, disputes★, escalations★ |
| `workforce` | agents | agents, agent_performance, agent_locations, attendance★, leave_requests, agent_devices★ |
| `planning` | allocation and routing | placement_runs★, placement_decisions★, allocation_runs, allocation_decisions, allocation_settings, beats, beat_stops★ |
| `ml` | models | model_registry★ (DB mirror of the artifact pointers), model_predictions, model_candidates, repayment_score_snapshots, monitoring_runs★, feature_definitions★ |
| `ai` | LLM agents | agent_definitions★, agent_versions★, tool_registry★, agent_runs★, agent_steps★, approvals★, llm_calls★ |
| `strategy` | simulations and reports | simulation_runs★, simulation_results★, forecast_runs★, forecast_points★, reports★ |
| `audit` | append-only trail | audit_logs |
| `analytics` | read models (§4.4) | `dim_*` views, `mv_*` materialized views, `v_*` views |

**Tables that change shape**
- **`loan_dpd_history` is the most important new table.** It holds one row
  per (loan, month-end), plus a daily row for the current month. It is what
  turns "`Loan.dpd` is overwritten in place" into a real delinquency panel.
- That panel is required by the transition matrix, the NPA bridge, roll and
  cure rates, the Monte Carlo and the `NO_HISTORY_FEATURES` that the ML
  config excludes today.
- `loan_instalments` holds the schedule, so DPD becomes derivable instead of
  asserted. This is the same arithmetic the ledger simulator already uses.
- `beat_stops` replaces `beats.ordered_case_ids` (a JSON array with no FK).
- `visit_media` replaces about 30 per-photo columns on `visits`.

### 4.2 Type and integrity fixes, done in the same pass

- **Dates and times:** every `String(10)` date becomes `DATE` and every
  `String(50)` timestamp becomes `TIMESTAMPTZ`. That covers 14+ columns,
  including `Loan.disbursement_date`, the string that caused live failure #1.
- **Money:** `Float` becomes `NUMERIC(14,2)`.
- **IDs:** VARCHAR ids become native `UUID`.
- **JSON:** `JSON` becomes `JSONB` everywhere.
- **Free-text domains get lookup tables:** `legal_status`,
  `settlement_status`, `allocation_decisions.outcome`,
  `allocation_settings.objective`, `beats.leave_type`. Lookup tables are used
  instead of more native enums, because `ALTER TYPE … ADD VALUE` has already
  been needed four times and cannot run inside a transaction on older
  servers. Existing enums stay.
- **Foreign keys:** `ON DELETE` is set on every FK. Today, cases, visits and
  payments have none.
- **Model/DB drift:** the drift already found (the partial index
  `ix_agents_gender` exists only in a migration) is reconciled.

### 4.3 Scale features

- **Declarative monthly range partitions** on the append-heavy tables:
  - `workforce.agent_locations`
  - `planning.allocation_decisions` (78,809 rows in the demo already)
  - `ml.model_predictions`
  - `audit.audit_logs`
  - `lending.loan_dpd_history`
  
  A Celery task creates future partitions and detaches expired ones. That
  replaces the row-delete retention sweep for locations.
- **Indexes lead with the tenant**, as `(bank_id, …)` and
  `(agency_id, status, …)`, which is what the §3.3 scoping queries hit.
- **Audit logs become immutable** by revoking UPDATE and DELETE from the app
  role, plus a trigger. Today immutability is "convention only" (known
  issue 3).
- **Database settings:** `statement_timeout` for API connections; a separate
  read-only connection for analytics endpoints, with a hook for a read
  replica; PgBouncer-ready settings, meaning no session state outside
  `SET LOCAL`.

### 4.4 Analytics layer: SQL, not in-memory pandas

The Command Center loads everything into pandas at startup and caches it until
restart. That caps it at the size of RAM, and no number can change while the
process lives. This plan uses a small star schema in `analytics` instead:

- **Dimensions** (views): `dim_date`, `dim_region`, `dim_agency`, `dim_agent`,
  `dim_product`, `dim_bucket`.
- **Materialized views**, refreshed `CONCURRENTLY` by a 20:30 task after
  ingest and allocation:

  | View | Grain |
  |---|---|
  | `mv_portfolio_daily` | bank × region × agency × product × bucket × day |
  | `mv_bucket_transitions_monthly` | the transition matrix, per segment |
  | `mv_agency_scorecard_monthly` | agency × region × month |
  | `mv_field_activity_daily` | agency × agent × day |
  | `mv_collections_daily` | agency × day |

- **Live views** for today's figures: `v_case_360`, `v_today_field_activity`.

Every KPI in §5 is defined **once**, as a SQL expression in
`services/bank/kpi_catalog.py`. The API, the board report and the copilot's
tools all read that catalog, which is the "one definition, one place"
convention applied to metrics. Each KPI record carries its label, formula
text (shown as a tooltip, like the platform's `basis`), unit, direction
(higher is better or worse) and drill dimension.

### 4.5 Migrations

The empty baseline is the problem to fix first. Five steps:

1. **New baseline.** Generate a real `v2_baseline` that creates every schema,
   table, view and partition from the models. `seed_data.py` stops calling
   `create_all`, which closes known issue 5, and `alembic upgrade head`
   becomes the only path.
2. **Transform.** A one-time transform (`scripts/migrate_v1_to_v2.py`) reads
   today's fixture and writes v2:
   - "ABC Bank" becomes bank #1;
   - `AGENCY-TIQ-001` becomes the "ABC Collections" agency, NCR region;
   - `manager1` becomes its `AGENCY_ADMIN`, `manager2` its `AGENCY_MANAGER`;
   - string dates are parsed;
   - `loan_dpd_history` is back-filled from `repayment_score_snapshots`
     where they exist, with `is_backfill` marked.
3. **Regenerate the demo.** A new `fieldops-demo.dump` is built from
   v2 + the generator (§4.7). `docker-entrypoint.sh` changes its emptiness
   check from `public.agents` to `workforce.agents`.
4. **Model mapping.** SQLAlchemy classes keep their names. Each gets
   `__table_args__ = {"schema": "…"}`, so ORM call sites do not change. Raw
   SQL and the few `text()` queries are found by grep and fixed.
5. **Type changes land separately.** String → DATE and Float → NUMERIC land
   one migration per table, each with a test that reads before and after.
   These changes feed the ML adapter, and the Phase 3 equality harness (78
   tests) must stay green through them.

### 4.6 Tests

- **SQLite path.** The 34 SQLite test files keep working through
  `schema_translate_map={"tenancy": None, "lending": None, …}`, since SQLite
  has no schemas. Table names are unique across schemas by design, so the map
  cannot collide.
- **Postgres suite.** A new `tests/pg/` suite runs against a real Postgres 16
  (a compose service in CI). It covers partitions, RLS, materialized-view
  refresh, the v1→v2 transform and the §3.3 cross-tenant test. SQLite cannot
  prove any of those.

### 4.7 Data generation, robust and scalable

**Extend what already exists rather than writing a sixth generator.** The
ledger simulator (`ml/simulation/ledger/`) is event-sourced, point-in-time
correct, passes 17 realism checks, and `materialise.py` already writes it into
the real schema. It gains four things:

- **Tenancy.** Banks, a region hierarchy, and agencies with coverage,
  capacity and commission.
- **A latent agency effect.** Each agency and agent gets a hidden skill,
  drawn from a distribution and never written out. This is what gives the
  agency scorecard, the placement optimiser and the case-mix-adjusted ranking
  (§9) a ground truth to be evaluated against. The same pattern made the
  recovery model's ceilings measurable.
- **New channels at known rates:**
  - contact time-of-day, which unblocks `contact_risk` (it failed for want of
    it);
  - settlement offers and acceptances;
  - disputes and complaints;
  - compliance breaches (out-of-hours attempts, geofence failures,
    fabricated evidence).

  Injected at documented rates, every detector has ground truth.
- **Geography.** Four zones and about 14 cities, with coordinates, using the
  lists in `generate_bank_data.py` (Delhi NCR, Jaipur, Lucknow, Chandigarh,
  Mumbai, Pune, Ahmedabad, Bengaluru, Chennai, Hyderabad, Kolkata,
  Bhubaneswar, and more).

| Profile | Banks | Agencies | Agents | Customers | Loans | History | Use |
|---|---|---|---|---|---|---|---|
| `dev` | 1 | 3 | 30 | 3k | 3.5k | 6 mo | local and tests, < 30 s |
| `demo` | 1 + 1 small | 8 | ~160 | 40k | 50k | 18 mo | the committed fixture |
| `stress` | 2 | 25 | 1,000 | 500k | 600k | 24 mo | scalability and index tuning; never committed |

- The second, small bank in `demo` exists only so tenant isolation is
  demonstrable and testable.
- `stress` is the scalability evidence. The acceptance criterion is that
  Command Center endpoints answer in under 800 ms p95 on it, measured with the
  materialized views.
- Every generated row is synthetic. Every surface that shows a model metric
  keeps the repo's `SYNTHETIC_WARNING` convention.

---

## 5. Workstream C — Bank portal: Command Center

### 5.1 Navigation

```
Command Center   Overview · Analytics (8 tabs) · Alerts
AI Strategy      Monte Carlo Simulator · Cash Forecast · Scenario Lab · Board Reports
Agencies         Directory · Performance · Placement · Onboard Agency
Tech Ops         AI Agents · MLOps · Data Quality · Usage & Cost
Admin            Bank Users · Regions · Settings · Audit
```

### 5.2 Global filter bar, which the platform lacks

The filters are:
- Period (MTD, last 30 days, QTD, FYTD, custom);
- Zone, region, state and city;
- Agency;
- Product;
- DPD bucket;
- Secured or unsecured.

They live in the URL, so a view can be shared by link, and every endpoint
accepts them as one `KpiFilter` object. The platform's filters only changed
labels; these change numbers, and a test asserts it. The test sets each
filter and requires every KPI to change or to state that it does not depend
on that filter.

### 5.3 Header KPIs

The platform's 12-card structure is kept: two rows, and each card has a value,
a delta, a basis tooltip and a drill. The definitions are **adapted** from a
lender's whole book to a field-collections operation run through agencies.

**Row 1 — where the book stands**

| KPI | Definition | Replaces or keeps from the platform |
|---|---|---|
| Delinquent Exposure | Σ `total_outstanding`, DPD > 0; sub-line: share of book | keeps "Total At-Risk Exposure" |
| Placed with Agencies | placed exposure ÷ delinquent exposure; sub-line: # active agencies | new: the placement layer |
| Unworked Exposure | placed exposure with no visit or call in the SLA window (default 7 days) | adapts "Contact Coverage" to the field |
| GNPA % | NPA exposure ÷ book exposure; sub-line: NPA accounts | keeps "Accounts in NPA" |
| Roll-Forward Rate | exposure-weighted roll, from `mv_bucket_transitions_monthly` | keeps; now built on real history |
| Cure Rate | exposure-weighted cure | keeps |

**Row 2 — what came back, what it cost, how it was done**

| KPI | Definition | Replaces or keeps from the platform |
|---|---|---|
| Collection Efficiency | verified collections ÷ collectible due in period | keeps; payment status filtered to VERIFIED (the platform used gateway rows) |
| Resolution Rate | placed cases resolved (PAID, closed or settled) ÷ placed cases in cohort | new |
| PTP Keep Rate | honoured ÷ matured promises | keeps; matured-only, as `ptp-outcomes` already does |
| Visit-to-Pay Conversion | met visits followed by a verified payment within 7 days ÷ met visits | new: the field's own conversion |
| Cost to Collect | (agency commission accrued + field cost) per ₹100 recovered | adapts; commission comes from `agency_contracts` |
| Compliance & Integrity | 100 − weighted breaches per 100 visits (out-of-hours attempts, geofence failures, confirmed fraud findings, missing consent) | new; every input already exists in this repo |

**Projected NPA slippage** (exposure × P(→NPA) per pre-NPA bucket) moves to
the Migration tab and the alerts. The platform showed it as a header card.

### 5.4 Analytics tabs

The platform's five tabs are kept, and three field tabs are added.

| Tab | Content |
|---|---|
| Exposure | funnel (book → delinquent → placed → NPA → write-off candidates), DPD ladder, product × bucket heat grid, security cover |
| Migration | transition-matrix heatmap with drill, cure-vs-roll bars, 12-month trajectory, NPA bridge, aging cohorts, projected slippage |
| Recovery | pace against target (cumulative area, target line, daily bars), target vs achieved by bucket, product and agency; PTP keep-rate bands |
| **Field Operations** (new) | visits per agent per day, met rate, coverage within SLA, planned-vs-actual km (from `beat_reconciliation`), beat adherence, attendance |
| **Agencies** (new) | side-by-side scorecards, placed vs resolved, cost per ₹100 by agency |
| Cost to Collect | commission plus field cost, channel economics with the platform's last-touch attribution, cost by bucket |
| Concentration | zone, region, state, city, branch and product. This fixes known issue 8: analytics today break down only by agent, bucket and month |
| **Compliance** (new) | breach types over time, by agency; audit coverage; evidence anomalies |

The drill panel is ported: 8 stats, splits, and the top-15 accounts. It gains
the dimensions `agency`, `region` and `agent`.

Alerts: the platform's 7 rules are ported, as SQL. Four field rules are
added:
- placed cases untouched past SLA;
- an agency's efficiency down more than 20% month on month;
- a compliance breach spike;
- agent capacity below placed volume in a region.

---

## 6. Workstream D — Agency Onboarding and Agency Management

### 6.1 Onboarding wizard

Bank portal → Agencies → Onboard Agency. The steps:

1. **Identity:** legal name, trade name, RBI registration no., PAN, GSTIN,
   registered address, contacts.
2. **Coverage:** regions and cities served (a map picker over the region
   hierarchy), products authorised, DPD buckets authorised.
3. **Contract:** start and end dates; capacity (max placed cases and
   agents); commission slab (per bucket, % of collection); SLA (first visit
   within N days); recall rules.
4. **Documents:** registration certificate, agreement, insurance and police
   verification policy. Uploaded to MinIO, virus-scan hook, expiry dates
   tracked.
5. **Master login:** the `AGENCY_ADMIN` user's name, email and phone. A
   single-use invite link (72 hours) goes out by email or SMS, and the bank
   can also copy it to share. The agency sets its own password on first open.
6. **Review & activate:** the agency is created in `PENDING`. It moves to
   `ACTIVE` when the invite is accepted and the documents are verified. Both
   steps are audited.

**Lifecycle actions:** suspend, which blocks logins and freezes new
placements; offboard, which recalls open placements, re-places them through
the placement engine and archives the agency; renew contract. Every action is
reversible except offboard, and offboard requires a second bank user (the
same four-eyes pattern as model promotion).

### 6.2 Agency Performance

**Directory**
- Sortable and filterable by zone, region, state, city, product, status,
  contract expiry and score.
- Also available as a map view with agency coverage polygons.

**Scorecard** (per agency, from `mv_agency_scorecard_monthly`):

| Metric | Definition |
|---|---|
| Collection Efficiency | verified collections ÷ collectible on placed cases |
| Resolution Rate | resolved ÷ placed (cohort) |
| **Recovery vs Expected** | actual recovery ÷ recovery predicted by `recovery_risk` for the cases the agency was given. **Case-mix adjusted**: an agency handed the hardest book is not punished for it |
| PTP Conversion | honoured ÷ matured |
| Contact Rate | met ÷ visited |
| SLA Adherence | placed cases first-visited within SLA |
| Productivity | visits per active agent per day |
| Cost per ₹100 | commission + field cost ÷ recovered × 100 |
| Compliance Score | as the header KPI, per agency |
| Evidence Integrity | confirmed fraud findings per 100 visits |
| Workforce | active agents ÷ contracted capacity, attrition, leave rate |
| **Agency Performance Index** | weighted composite of percentile ranks *within the peer group* (same region and bucket mix), shrunk by `empirical_bayes` toward the region mean, so a 20-case agency cannot top the table on luck |

The scorecard also shows trends, a leaderboard by region, and a drill into
the agency. The drill uses the agency's own manager analytics, read-only,
which replaces the platform's credential proxy and its hardcoded ABC drill.

### 6.3 Placement

**Manual placement:** filter loans, then place them with an agency, subject
to its capacity and coverage.

**Placement engine** (bank → agency; weekly or nightly):
- It is today's allocator one level up, and it reuses its machinery.
- **Hard gates:** coverage, product and bucket authorisation, contract
  active, capacity.
- **Score:** mix-adjusted agency effect in the segment × capacity headroom ×
  cost, via a min-cost-flow or Hungarian solve.
- An epsilon-greedy slice gives unconfounded agency comparison.
- Every decision is persisted with its reason in `placement_decisions`, the
  same pattern as `allocation_decisions`.
- **Recall rules:** no activity in N days, SLA breach, contract end.

---

## 7. Workstream E — AI Strategy and Tools

### 7.1 Monte Carlo simulator (rebuilt, not ported)

The platform's engine evolves bucket *shares* through one matrix with Gaussian
logit noise. What we keep: the 8-state space (Current, SMA-0/1/2,
NPA-Sub/Doubtful, Written-off, Resolved), the macro sensitivities, the
presets and the IFRS-9 staging. What changes:

**State and uncertainty**
- **Segment-level matrices from our own history.** A transition matrix per
  segment (product × bucket × region) comes from
  `mv_bucket_transitions_monthly`.
- **Parameter uncertainty.** Each path draws its matrices from a Dirichlet
  posterior on the observed counts, so a thin segment is visibly more
  uncertain.
- **Correlated shocks.** Each path draws one systematic macro factor (common
  to all segments) plus a segment-specific idiosyncratic factor. Stress
  therefore moves segments together, as it does in reality.

**Levers the bank controls:**
- placement rate;
- agency capacity (visits per month), which scales cure probabilities through
  a *fitted* elasticity rather than the platform's `0.5 + capacity/100`;
- commission slab;
- settlement discount, with an acceptance curve from the settlement model
  (§9);
- legal threshold;
- write-off policy.

**Cash and outputs**
- Recovered cash per path: transitions to Current or Resolved × balance ×
  recovery fraction drawn from a Beta distribution fitted per bucket.
- **True percentiles.** GNPA %, recovered cash, ECL, write-offs, cost and net
  recovery are computed per path, and *then* p5/p10/p50/p90/p95 are taken.
  This fixes the platform's summed per-state percentiles.
- Exposure is anchored on `total_outstanding`, consistent with the KPI
  engine.

**Outputs shown**
- Fan charts.
- Recovery-at-risk (p5 of net recovery).
- IFRS-9 staging (EAD, PD, LGD, ECL, coverage).
- A tornado chart.
- A 2-axis heatmap.
- Up to 4 saved scenarios compared.
- Standard error of the mean on every headline number, so "run more paths"
  is a visible decision.

**Performance**
- Account-level simulation, vectorised in NumPy: 1,000 paths by default and
  10,000 on request.
- Runs as a Celery job with a progress bar, persisted in
  `strategy.simulation_runs` with its seed and inputs, so it is reproducible.

**Backtest, and this is what makes it credible**
- Start the simulator 6 months back, with no shock, and compare against the
  bucket shares that actually happened.
- Report how often the actual value fell inside the p10–p90 band. A nominal
  80% band should cover about 80%.
- The backtest runs in CI on the `dev` profile.

**Approval flow:** the platform's "approve and memo" is kept. The memo is
produced by the Board Pack Writer agent (§9.2) instead of a template, with
the numbers-from-engine rule.

### 7.2 Forecasting

**13-week cash forecast**, by agency, region and bucket. Built bottom-up from:
- account-level P(pay) (`recovery_risk`) × an amount distribution;
- plus the PTP pipeline: committed amounts × keep probability, on their due
  dates;
- plus seasonality.

It is reconciled against a top-down ETS or SARIMAX forecast (statsmodels is
already installed). The screen shows quantile bands, and a forecast-vs-actual
tracker gives weekly MAPE, so the forecast is judged in public.

**Two other forecasts:**
- **Roll and slippage forecast:** next 3 months of bucket flows, from the
  segment matrices.
- **Capacity forecast:** visits needed for the placed book against agents
  available, including the leave calendar, per region and agency. "North
  needs 22 more agents in October" is the kind of line it produces.

### 7.3 Scenario Lab

The platform's budget optimiser and outreach simulator are ported with one
fix: a single cost table, `strategy.cost_rates`, read by both. Today they use
two different sets of unit costs.

Two new what-ifs:
- "Move X% of region R from agency A to B." This runs the placement engine
  in simulate mode, which the allocator already supports as `simulate=True`.
- "Change the commission slab" → cost to collect and expected recovery.

### 7.4 Board reports (downloadable)

**Generation**
- Server-side, as a Celery job. Output is stored in MinIO and downloaded
  through a presigned URL. Every download writes `DATA_EXPORT` to the audit
  log.
- **Formats:**
  - PDF via ReportLab, which is pure Python with no system libraries and has
    a rupee glyph through an embedded font (the platform had to print "Rs");
  - PPTX via python-pptx with native, editable charts;
  - XLSX via openpyxl, with every table as a sheet.

**Templates**
- Board of Directors, Risk Committee and Audit Committee: the platform's 9
  sections, plus Agencies and Compliance.
- **Agency Review**: one agency's scorecard, safe to share with that agency.
- **Monthly MIS.**

**Narrative sections are written by the LLM under a hard rule: numbers come
from the engine, words come from the model.**
- The writer receives the KPI payload.
- A verifier checks that every number in the prose appears in the payload,
  within rounding. If the check fails, the section falls back to a template.
- Every AI-written section is labelled as AI-written, per the repo's
  `ai_generated` convention.

**Scheduling:** month-end auto-generation, with an email to the named
recipients.

---

## 8. Workstream F — Tech Ops: AI agents and MLOps

### 8.1 AI Agent Studio

The platform has no way to create an agent, and its prompts are hardcoded.
This studio provides:

**Registry.** Each agent has a name, purpose and trigger (cron, event or
manual), a scope (bank, region or agency), a model and provider, a
**versioned** system prompt, and a tool allowlist.

**Tool registry.** Every tool is classified as `read` or `write`. Read tools
include KPI queries, case lookup and agency scorecards. Write tools include
drafting a message, creating an escalation, proposing a recall and opening a
task. **Write tools always go through an approval queue**; no agent acts on
the book directly.

**Guardrails**
- A cap on steps, tokens and rupees per run.
- PII redaction before prompts: PAN and Aadhaar are masked already, and
  phone numbers will be too.
- A JSON output schema per agent.

**Test console.** Run an agent against the `dev` data, see each step.

**Runs and traces.** Every run is persisted in `ai.agent_runs` and
`ai.agent_steps`: messages, tool calls, results, tokens, latency and cost.
The platform keeps this in memory and loses it on restart.

**Evals.** Each agent has a golden set of questions with the facts the answer
must contain. A prompt or model change runs the evals, and the version cannot
go live below its gate. This is the model-gate discipline, applied to
prompts.

**Runtime**
- A thin in-house loop on top of `core/llm.py` (the repo's "one seam"
  rule), rather than adding LangGraph.
- `core/llm.py` gains **tool calling**: Anthropic Messages tool use and
  OpenAI-compatible function calling, which covers Groq.
- It also gains an `anthropic` provider. The recommendation is Claude Sonnet 5
  (`claude-sonnet-5`) for multi-step agents and Claude Haiku 4.5
  (`claude-haiku-4-5-20251001`) for high-volume classification, with Groq
  kept as the fallback.

### 8.2 MLOps console

This puts a face on the lifecycle the repo already has.

**Models**
- Every model with its champion pointer, versions, gate results,
  out-of-time metrics and `MODEL_DEVELOPMENT.html`.
- Serving state per process (`DecisionEngine.serving_state`).

**Monitoring:** the 19:15 digest as charts: Gini, KS, Brier, calibration, PSI
per feature, and the missingness monitor. `not_ready` and
`insufficient_outcome_variation` are shown as what they are, not as
"healthy".

**Candidates:** approve, reject and promote, with four-eyes, now gated on the
`ml.approve` and `ml.promote` capabilities held by `BANK_TECHOPS`. This
closes the known-issue-11 exposure, where every manager can rewrite
`champion.txt`.

**Retrain actually runs:** a Celery job over `production_dataset`. It stops
at `INSUFFICIENT_DATA` honestly, as it does today.

**Prediction explorer:** look up a case and see its score, band, reason codes
and exact contributions (already stored).

**Feature catalog:** every feature, its point-in-time definition, its
coverage and its drift.

**Data quality:** checks on the nightly bank feed (row counts, null spikes,
DPD jumps, duplicate accounts), with a quarantine for rejected rows in
`lending.bank_feed_rows`.

**LLM usage and cost:** per agent and per purpose, from `ai.llm_calls`. This
extends today's counters on `/manager/ai/health`.

---

## 9. Workstream H — ML and agentic AI expansion

Every model ships through the existing pipeline: point-in-time features,
two-sided gates, champion/challenger comparison, monitoring and a
`MODEL_DEVELOPMENT.html`. Every agent ships through the studio's evals. The
generator's injected ground truth (§4.7) is what lets each one be evaluated
honestly.

### 9.1 ML

| # | Capability | Method | Where it shows | Evaluated against |
|---|---|---|---|---|
| 1 | **Case-mix-adjusted agency ranking** | recovery vs `recovery_risk` expectation; hierarchical Bayes shrinkage (extends `empirical_bayes`) | Agency scorecard | latent agency skill: rank correlation |
| 2 | **Placement optimiser** | segment-level agency effect × capacity × cost, min-cost flow, ε-greedy slice | Placement | offline IPW on the exploration slice |
| 3 | **Probabilistic cash forecast** | quantile GBM on account level + PTP pipeline, reconciled with ETS | Cash Forecast | pinball loss, band coverage, MAPE |
| 4 | **Covariate roll-rate model** | multinomial next-bucket model per account (the platform's M1 idea, with PIT features) | Monte Carlo segment matrices, Migration tab | log-loss vs the empirical matrix |
| 5 | **Time-to-pay survival** | discrete-time hazard | case priority, forecast timing | concordance, calibration by week |
| 6 | **Next-best-action** (feature #4, missing today) | contextual bandit (Thompson sampling) over visit, call, SMS, settle, escalate | agent app, manager queue | off-policy evaluation |
| 7 | **Settlement optimiser** (feature #8, missing today) | acceptance-probability curve × amount, within bank policy floors | Settlement desk, Monte Carlo lever | expected recovery vs policy baseline |
| 8 | **Best time to contact** | the `contact_risk` model, now with time-of-day | beat time windows | Gini ≥ 0.25 floor, which it failed without the data |
| 9 | **Visit-evidence anomaly model** | isolation forest over per-agent behaviour, beside the 7 fraud rules | Compliance, fraud review | injected fabricated-evidence rate: precision@k |
| 10 | **Territory design** | H3 hex clustering + capacity balancing | onboarding coverage, agent base assignment | travel km and workload variance |

### 9.2 Agentic AI

| # | Agent | Trigger | Tools | Output |
|---|---|---|---|---|
| 1 | **Portfolio Copilot** (bank) | chat | KPI catalog queries, drill, agency scorecard, forecast, chart spec | grounded answers with charts; every number cites its KPI id |
| 2 | **Daily Strategy Agent** | 07:00 daily | alerts, KPIs, placement simulate | a morning brief plus **proposed** actions (recall, re-place, escalate) in the approval queue |
| 3 | **Agency Review Agent** | month-end | scorecard, trends, compliance | per-agency review and improvement plan, shareable with the agency |
| 4 | **Board Pack Writer** | report job | report payload | narrative sections, with the number verifier (§7.4) |
| 5 | **Compliance Auditor** | after transcription | visit transcripts and notes | RBI Fair Practices flags (threats, abusive language, out-of-scope disclosure) → review items |
| 6 | **Voice → structured visit report** (feature #2 gap) | agent records audio | JSON extraction | pre-fills disposition, PTP amount and date for the agent to confirm |
| 7 | **Borrower Outreach Drafter** | manager request | case 360, policy | messages in the borrower's language, approval required |
| 8 | **Field Copilot v2** | agent opens a case | case 360, NBA model, history | today's visit strategy, made tool-using |

---

## 10. Workstream G — Agency view: Manage Agents

This is today's manager view, now the agency portal. It gains:

**The Manage Agents tab** (`AGENCY_ADMIN`, plus `AGENCY_MANAGER` for their own
team):
- **Table:** name, employee code, phone, email, territory, manager, status,
  last login, device bound, active. `GET /manager/agents` returns no email or
  phone today.
- **Create agent:** personal and contact details, base location picked on a
  map, territory, languages, specialisation, capacity, vehicle, gender, and
  reporting manager. The employee code is generated automatically. The login
  goes out as an invite link by SMS or email, or as a one-time temporary
  password shown once with a forced change.
- **Actions:** edit, suspend or reactivate (suspending prompts to reassign
  open cases), reset password, reset device binding, transfer to another
  manager, and view the ID card.
  - The ID card gets its verification QR. This finishes known issue 2: the
    `/verify-agent` endpoint exists but no card sends anyone to it.
  - `SUSPENDED` is finally set by something. It is declared and never
    written today.
- **Bulk import** from CSV, with a validation preview.
- The contract's seat limit is enforced.

**Also for `AGENCY_ADMIN`:**
- Managers: create and manage agency managers.
- Agency profile: read-only contract, commission and SLA.
- Placements: cases received, recalls, SLA timers.

**Bank-specific text becomes data.** "ABC Bank" is hardcoded in SMS and
WhatsApp text, receipts and the UPI QR (seven files, §1), and
"Agency Manager" plus the RBI registration number are hardcoded in the UI.
All of it becomes bank and agency data.

**Navigation.** The mobile bottom bar already holds 7 items. It becomes 4
primary items and a "More" sheet.

---

## 11. Workstream I — Mobile app simulator

The goal is to see the agent app as a phone on localhost, next to the web
portals, and watch actions flow between them.

### 11.1 Simulator page

`/simulator` is available only when `DEMO_MODE` is on, or to `PLATFORM_ADMIN`.

```
┌──────────────┬───────────────────────────────┬──────────────────────┐
│  PHONE FRAME │   MANAGER / BANK VIEW (iframe) │  EVENT TIMELINE      │
│  Pixel 8 /   │   live map, cases, alerts      │  09:41 check-in      │
│  iPhone 15   │                                │  09:52 GPS ping      │
│  393×852     │                                │  10:05 visit logged  │
│  agent app   │                                │  10:06 payment OTP ✓ │
│  (iframe)    │                                │  → manager map ●     │
├──────────────┴───────────────────────────────┴──────────────────────┤
│ Device controls: GPS (pick / play beat route ×10) · camera sample   │
│ images · network online/offline · battery · device model            │
└─────────────────────────────────────────────────────────────────────┘
```

**Three details decide whether this works:**
- **Both frames would otherwise share one login.** Two iframes on the same
  origin share `localStorage`, and the auth store persists to `tiq_auth`, so
  the manager login would overwrite the agent login. The fix is a
  `?slot=agent` / `?slot=manager` parameter that namespaces the store's
  storage key, which is a small change to `authStore.ts`. The zero-code
  fallback is serving one frame from `localhost:5473` and the other from
  `127.0.0.1:5473`, which the browser treats as separate origins.
- **Device APIs.** The frames get `allow="geolocation; camera; microphone"`.
  The simulator drives GPS through `postMessage` into a small `lib/geo.ts`
  provider. The agent app's six direct `navigator.geolocation` calls move
  behind it, which also removes the hardcoded Gurugram fallback coordinates
  (`28.4595, 77.0266`) from the production path. "Play beat route" walks the
  agent along the OSRM polyline, so check-in geofences pass for real.
- **Live link.** The portals poll today, and there is no WebSocket or SSE
  anywhere (feature #12). The plan adds `GET /api/v1/events/stream` (SSE),
  scoped by tenant and fed from the service commit points that already write
  audit rows. The timeline, the live map and the SOS bell subscribe to it.
  Vite already proxies `/ws`.

**Acceptance:** a visit recorded in the phone frame shows on the manager's
live map and in the timeline within 2 seconds; the payment OTP flow completes
end to end; SOS lights the manager's bell.

The server enforces RBI contact hours, and the simulator deliberately cannot
bypass them.

### 11.2 Then a real phone

1. **PWA:** manifest, icons and a service worker (`vite-plugin-pwa`). The app
   installs on a phone over the existing LAN HTTPS option (`HTTPS=1`).
2. **Offline outbox** (feature #15): IndexedDB queue for visits, photos and
   payments, replayed on reconnect.
3. **Optional:** Capacitor wrap into an Android APK. Everything the agent app
   uses (GPS, camera, microphone, share) is already a web API, so this is
   packaging, not a rewrite.

---

## 12. Sequencing and exit criteria

| Phase | Contents | Exit criterion |
|---|---|---|
| **P0** | Simulator v1 (§11.1) with polling, then SSE | acceptance in §11.1 on the current demo |
| **P1** | Workstream B (schemas, types, baseline, transform, generator) + Workstream A (tenancy, auth, remove static login) | full suite green on SQLite; `tests/pg` green; demo restores as v2 with 8 agencies; no hardcoded credential anywhere (grep test); cross-tenant behavioural test passes |
| **P2** | Onboarding (§6.1), Manage Agents (§10), per-agency pools and planner | onboard an agency → accept invite → create agent → agent logs in on the simulator → gets a beat the next night |
| **P3** | Command Center (§5) + Agency Performance (§6.2) + manual placement | every KPI in the catalog, filters change numbers (test), < 800 ms p95 on `stress` |
| **P4** | Monte Carlo, forecasts, Scenario Lab, board reports (§7) | backtest band coverage reported; PDF, PPTX and XLSX download; the number verifier passes |
| **P5** | Tech Ops studio + MLOps console (§8), LLM tool calling | an agent created in the UI runs, traces and passes evals; promote restricted to `BANK_TECHOPS` |
| **P6** | ML and agentic items (§9) in value order: 1, 2, 3, then agents 1, 2, 5, 6, then the rest | each item passes its gate against the generator's ground truth |
| **P7** | PWA, offline outbox, optional APK | a visit recorded offline syncs on reconnect |

P0 runs alongside P1. P3 and P5 can overlap once P1 lands.

---

## 13. Decisions needed

Each has a recommendation. The plan above assumes the recommended option.

| # | Decision | Recommendation | Why |
|---|---|---|---|
| 1 | Separate Postgres databases vs schemas | **Schemas in one database** | cross-database joins, foreign keys and transactions do not exist in Postgres (§4.1) |
| 2 | One deployment per bank vs multi-bank SaaS | **Multi-bank-ready schema, single-bank demo** | `bank_id` costs little now and a retrofit costs a rewrite; the second small bank proves isolation |
| 3 | Bank portal in the same SPA vs a separate app | **Same SPA, `/bank/*`** | one auth, one API client, one design system, lazy-loaded |
| 4 | LLM for agentic features | **Add the Anthropic provider with tool use; keep Groq as fallback** | today's seam has no tool calling, and the agents need it |
| 5 | Master-login delivery | **Invite link** (temp password as a fallback) | nobody sees or stores another person's password |
| 6 | Simulator | **Browser device frames now; PWA next; Capacitor optional** | works today on localhost with no Android toolchain |
| 7 | Platform integration | **Keep `embedded` mode** | the Collections platform still builds and routes the frozen copy; this keeps the door open |
| 8 | Old demo fixture | **Transform once, then regenerate** | keeps ABC Collections and its history recognisable inside the new, larger book |

---

## 14. Risks

**The schema pass touches everything**
- 24 tables, about 1,363 tests, and the ML point-in-time adapter, which reads
  `Loan` columns whose types change.
- Mitigation:
  - class names are kept;
  - schema moves are separated from type changes;
  - one table per type migration;
  - the Phase 3 equality harness is the gate.

**Scope**
- Nine workstreams.
- Mitigation: phases with exit criteria, and nothing in P6 starts before P3
  is done.

**Invented numbers**
- In board reports and copilot answers.
- Mitigation: the numbers-from-engine verifier, KPI-id citations, and the
  `ai_generated` label.

**Monte Carlo credibility**
- Mitigation: the backtest coverage is published beside the fan chart, not
  hidden in a report.

**Everything is synthetic**
- Every model and simulator figure is synthetic.
- Mitigation: the `SYNTHETIC_WARNING` convention on every surface. The
  ground-truth injection makes evaluation honest; it does not make it real.

**Platform drift**
- Command Center's `FieldAnalytics.jsx` and `FieldCases.jsx` are
  hand-maintained ports of this repo's pages. The bank portal will make them
  diverge further.
- Decide whether the platform consumes the bank portal's APIs instead.

**`manager.py` growth**
- Mitigation: new code goes only into `services/` and new routers. Extracting
  `manager_service.py` begins in P2, because the scoping helpers need a home.

---

## 15. Not in scope

- Payment-gateway changes beyond the bank's name and account becoming data.
- Voice bots or IVR.
- A campaign engine for WhatsApp and SMS: the outreach drafter produces
  one-off messages with approval.
- A native iOS app.
- Real borrower data: nothing here should be pointed at a real book until the
  first real outcomes mature and the `ml/` monitoring has something true to
  say.
