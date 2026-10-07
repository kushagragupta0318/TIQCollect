# QA sweep — broken / empty / dead UI surfaces before the demo

Method: static (router + api wrappers + backend handlers) plus live DB counts against
`fieldops_dev_postgres` (bank Girivan `d061537b-896b-5ef9-b284-7d33c51904a4`). No login used.
Every claim is backed by a `file:line` or a DB count actually run on 2026-10-01.

**Note on scoped views:** `analytics.*_scoped` and `v_case_360` are RLS/tenant views; raw psql
returns 0 rows until `set_config('app.bank_id',…) / app.scope='BANK'` is set in the same session.
All analytics counts below were taken *with* the bank context set, so "0" elsewhere is a real gap,
not a scoping artifact.

## Headline

- The concrete bug the owner reported (bank Placement → Customer 360 → "No such borrower") **does
  not reproduce in the current code + data.** The list links `l.loan_id` (= `loans.id`,
  `BankPlacementPage.tsx:123`); the detail resolves `Loan.id == loan_id` within the bank
  (`bank_customers.py:49`, `customer_360.py:70-79`). Live check: **16701/16701** unplaced placeable
  loans resolve cleanly to an existing same-bank customer. No id-mismatch found on this path. If the
  owner still hits it, it is environment/region-limit specific, not a code mismatch — flagged for
  live re-confirmation, not as a confirmed code bug.
- The real demo risks are (1) the Overview's clickable KPIs landing on a *placeholder*, (2) most
  bank nav items being "Not built yet" placeholders, (3) the Placements "Expected P(pay)" column
  being **100% empty**, and (4) the agency-directory rows being dead clicks for all non-PENDING agencies.

## Findings (blockers first)

| Surface | How reached | Symptom | Root cause (file:line) | Class | Severity |
|---|---|---|---|---|---|
| **Overview KPI cards** | /bank/overview → click any of the 12 KPI cards | Navigates to `/bank/analytics?tab=…`, which is a "Not built yet" placeholder page | onSelect → `BankOverviewPage.tsx:52`; `analytics` not in `BUILT_PAGES` `BankApp.tsx:35-37`; placeholder `BankPlaceholderPage.tsx` | dead-link | **BLOCKER** (the flagship dashboard's primary interaction dead-ends) |
| **Placements tab — "Expected P(pay)"** | /bank/agencies/placement → Placements tab | Every row shows "—"; no expected-recovery value for any placement | `expected_recovery_prob` null for **0/10095** placements (DB); render `BankPlacementPage.tsx:371`; source `placement_read_service.py:221`. `model_prediction_id` also null for 0/10095 → synthetic banner never shows | empty-book / data-gap | **HIGH** (looks broken across the whole table) |
| **Agency Directory rows** | /bank/agencies/directory → click a row | Only `status === "PENDING"` rows navigate; the 8 ACTIVE + 1 SUSPENDED rows do nothing on click (D07 profile not built) | `AgencyDirectoryPage.tsx:150-152` | dead-link / partial feature | **HIGH** (8/10 agencies are dead rows) |
| **Bank nav — 14 of ~20 items** | Left rail | Render "Not built yet" placeholder: Analytics, Alerts, Monte Carlo, Cash Forecast, Scenario Lab, Board Reports, AI Agents, MLOps, Data Quality, Usage & Cost, Bank Users, Regions, Settings, Audit | `BankApp.tsx:35-37` (only overview/placement/models built) + explicit routes for onboard/directory/performance/customers; everything else → `BankPlaceholderPage.tsx` | dead-link (intentional per PILOT-PLAN §2, but live in the demo nav) | **HIGH** (hide or disable before demo) |
| **Top search bar (Ctrl/Cmd+K)** | Any bank page, type a borrower/account name | "No results" — only pages are indexed; account/borrower lookup not wired | `BankSearchBar.tsx:3-4` + `searchIndex` PAGE_ENTRIES only | dead feature | MEDIUM (prominent control that looks functional) |
| **Agency Performance Index / Leaderboard** | /bank/agencies/performance; Directory Performance cell | Headline Performance Index reads "Not enough data to score this agency yet"; leaderboard rows "Not enough data" | estimator needs matured placement-months; monitoring matures from **2026-10-08** (CLAUDE.md); today 2026-10-01; `planning.placement_outcomes` is a 5-row lookup table, not outcomes; `AgencyPerformancePage.tsx:124-126,256-259` | empty-book (genuine, time-gated) | MEDIUM |
| **Customer 360 — model band per case** | Placement → borrower link → case card | Most case cards show "No model score on this case yet" | **1370/10422** v_case_360 rows have `latest_band`/`latest_probability` (DB) | empty-book (by model design: scored on pool entry) | LOW |
| **"Why this score" on Place-loans rows** | /bank/agencies/placement → Place loans → "Why this score" | ~94% of loans show "has not been scored by the recovery-risk model yet" | only **1245/22457** placeable loans have a champion (2.2.0) prediction (DB); `LoanExplanationDialog.tsx:78-84` | empty-book (honest state) | LOW-MEDIUM |
| `/bank/customers/:customerId` path route | (no in-app link emits it) | Route exists but nothing links to it; only `?loan=` entry is used | `BankApp.tsx:78` vs grep of bank tree (only `?loan=` nav at `BankPlacementPage.tsx:123`) | dead route (harmless) | NONE |

## Verified working (not bugs)

- Place-loans → Customer 360 (`?loan=`) resolution: **16701/16701** resolve (DB). Code path sound.
- Overview endpoint + analytics views populated under bank context: portfolio_daily 26691,
  collections_daily 1879, field_activity_daily 33086, scorecard_monthly 252, v_case_360 10422,
  v_today_field_activity 165, v_visit_to_pay 39998.
- All referenced bank backend routes exist: `/bank/overview`, `/bank/filters`, `/bank/models`,
  `/bank/loans/{id}/explanation`, `/bank/agencies-directory`, `/bank/agencies/{id}/scorecard`,
  `/bank/agencies-leaderboard`, `/bank/regions`, placement + engine routes
  (`bank.py`, `bank_models.py`, `bank_agencies_admin.py`, `bank_placements.py`).
- Role entry/redirect wiring is centralized and loop-free (`lib/roles.ts`).
- Champion model present and served: `recovery_risk 2.2.0` (`champion.txt`); 11738 champion predictions in DB.

## Fix owners / lanes

- Overview KPI → placeholder: either build `/bank/analytics` (tasks C04–C05) or make KPI cards
  non-navigating for the demo. Bank UI lane.
- Placements Expected P(pay) empty: backfill `model_prediction_id` / `expected_recovery_prob` on
  placements, or the enriched book (branch **l6-rebuild**) — confirm whether l6 fills these.
- Agency Directory dead rows: gate the demo to PENDING agencies or ship D07 agency profile.
- Nav placeholders: hide paused/dropped sections (PILOT-PLAN §2 pauses E01-E04 / F02-F05, drops
  F06; Monte Carlo E05 dropped) from the demo rail.
- Performance Index emptiness is time-gated (matures 2026-10-08) — expected; narrate, don't "fix".

## Not covered here (delegated, pending)

Manager views (`/manager/*`) and the field agent app (`/agent/*`) were handed to a parallel
sweep agent (same method: list→detail id comparison, empty-book DB counts, dead nav). The
results are now appended below.

---

## Manager app

Method as above: static (router + `managerNav.ts` + `api/manager.ts` + `endpoints/manager.py`)
plus live DB counts on `fieldops_dev_postgres`, 2026-10-01. No login used.

**Structural good news (vs the bank portal).** Every one of the 7 manager nav items resolves to
a real, built page — no "Not built yet" placeholders anywhere in `frontend/src/pages/manager/`
(grep for `Not built|Coming soon|placeholder|Under construction` = **0 matches**). Nav is one
array, `managerNav.ts:36-45`, consumed by both the desktop rail and the phone bar
(`ManagerLayout.tsx:201,309`). Routes all present in `App.tsx:165-171`.

**The "today" the manager sees is 2026-09-22, not 2026-10-01.** `_effective_today`
(`manager.py:85-111`) resolves the dashboard's day to `max(beat_date) <= today`. The demo book
has **no beats between 2026-09-23 and 2026-10-01** (DB: `planning.beats` grouped by date —
latest past set is **2026-09-22 (18 beats)**; the only future set is **2026-10-02 (17)**). So
Overview / Cases / Analytics "today" figures all describe 2026-09-22 and the header date trails
the wall clock by ~9 days. By design for a paused book, not a broken query — but narrate it.

| Surface | How reached | Symptom | Root cause (file:line) | Class | Severity |
|---|---|---|---|---|---|
| **Live Map** | /manager/live-map | Agents render at ~10-day-old positions / "last seen 2026-09-21"; map looks frozen until an agent logs in and reports live | `workforce.agent_locations` **57 rows, max `recorded_at` = 2026-09-21 10:17 UTC** (DB); `ManagerLiveMapPage.tsx` reads that trail | empty-book / stale | **MEDIUM** (if a live agent session is running during the demo their dot updates; otherwise the whole map is stale) |
| **Overview / header date** | /manager/overview | "Today" KPIs and the field-activity feed are dated 2026-09-22, not the real date | `_effective_today` `manager.py:85-111` (`beat_date <= today`), no beats 09-23…10-01 (DB) | empty-book (genuine, book is paused) | LOW-MEDIUM (cosmetic; narrate, or seed a today beat) |
| **Analytics — branch/city/product breakdown** | /manager/analytics | Only agent / DPD-bucket / month dimensions exist; no branch/city/product split though columns exist | known issue #8, `ENGINEERING-AUDIT.md` | missing-feature | LOW |
| **Monthly performance narrative** | /manager/analytics (MonthlyReportSection) | Current-month index is thin until month-end; model/placement maturity note starts 2026-10-08 | `workforce.agent_performance` has **108 rows** (history is present); current month immature | empty-book (time-gated) | LOW |

**Verified working (not bugs) — manager**
- **Field Plan / Tomorrow's allocation** (`/manager/beat-plan`): a PLANNED `allocation_run` for
  **2026-10-02 (249 cases, 17 agents)** plus **17 beats** exist (DB) → card populated.
- **Cases list → detail**: opens an in-app `CaseDetailModal(caseId)` (`ManagerCasesPage.tsx:1362,346`),
  no route hop; `caseId` is `case.id`, resolves via `GET /manager/cases/{id}`. Reassign dialog
  uses `detail.id` (`ManagerCasesPage.tsx:251`). **No id-mismatch, no dead click.**
- **Compliance**: `collections.fraud_reviews` = **35 rows** (DB) → populated.
- **Agents / leave**: `workforce.agents` = **165**, `workforce.leave_requests` present; the
  notification bell deep-links `/manager/agents?leave=1` (`ManagerLayout.tsx:492`).
- Core volumes: `collections.cases` 10422, `visits` 41228, `payments` 22048, `ptps` 6124 (DB).

---

## Agent app

Method as above: `App.tsx` + `AgentLayout.tsx` NAV_ITEMS + `api/agent.ts` + `endpoints/agent.py`
+ `services/scope.py` / `case_service.py`, plus live DB counts, 2026-10-01. No login used.

**No placeholders; all 4 nav items (Home/Cases/Beat/Profile) resolve** (`AgentLayout.tsx:35-40`,
`App.tsx:147-153`). The whole app is driven by the agent's **beat for "today"** — and the demo
book's beats stop at **2026-09-22** with the next set at **2026-10-02** (tomorrow). There is
**no beat for the literal current date (2026-10-01)**. Two different "today" definitions then
diverge, and that split is the agent app's main demo risk:

- **Display surfaces fall back to the latest past beat.** `GET /agent/beat` (`agent.py:355`) uses
  `_effective_day` (`agent.py:194-208`, `max beat_date <= today`) → **2026-09-22**. So Home,
  the Cases list (reads `beat.cases` from `BeatContext`, `AgentCasesPage.tsx:62-117`), and the
  Beat map are **populated**, dated 09-22. Good — not empty.
- **"Today-strict" surfaces use the real calendar day and are empty/blocked.**
  `scope.access_day()` (`scope.py:43-51`) = real IST today = 2026-10-01, and there is no beat.

| Surface | How reached | Symptom | Root cause (file:line) | Class | Severity |
|---|---|---|---|---|---|
| **Smart Order (AI ranking) toggle** | /agent/cases → tap "Smart Order" | The case list goes **empty** ("No Cases Found") the moment the flagship AI-ranking button is pressed | `getRankedCases` → `GET /agent/cases/ranked` (`agent.py:408`) → `today_beat_cases` (`scope.py:159,179`, `beat_date == access_day()`) → `(None, [])` because no beat today; `AgentCasesPage.tsx:37-48,119-137` swaps in the empty ranked list | broken-today / dead-feature | **HIGH** (the headline "Smart Order" demo gesture blanks the screen) |
| **Case grant via beat membership** | /agent/cases/:id or /agent/visit/:caseId for a case the agent does **not** statically own | 404 "Case not found" | `agent_case_or_404` (`scope.py:118-139`) grants a non-owned case only if it is on **today's** beat (`beat_date == access_day()`) — none today | broken-today | MEDIUM (most seeded cases are statically assigned via `Case.agent_id`, so they still open — see below) |
| **Record Visit out of hours** | /agent/visit/:caseId outside 8AM–7PM IST | Visit recording disabled banner | `ContactHourBanner` `AgentLayout.tsx:432-440` (RBI rule) | by-design | LOW (only bites if the demo runs after 7PM / before 8AM IST) |
| **Live GPS header on seeded agents** | any /agent page | address line may read stale until a live fix | `workforce.agent_locations` max 2026-09-21 (DB); live watcher refreshes on open (`AgentLayout.tsx:189-192`) | stale-book | LOW (self-heals on a real session) |

**Verified working (not bugs) — agent**
- **Case detail id resolution is sound**: list passes `c.id` (uuid, `AgentCasesPage.tsx:274`) →
  `getCaseDetail(id)` → `CaseService.case_detail` → `agent_case_or_404` does `parse_uuid` +
  `Case.id == cid` within the agent's agency (`scope.py:124-130`). A case the agent **owns**
  (`case.agent_id == agent.id`, `scope.py:133-134`) opens regardless of beat — so the default,
  statically-assigned cases open fine; it is only beat-only grants that 404 today.
- Case-detail tabs (Strategy / Visits / Payments / PTPs / Photos) each have real empty states,
  not dead ends (`AgentCaseDetailPage.tsx:770,815,836,869`).
- Offline outbox is wired (`AgentLayout.tsx:195-198`, `lib/outboxRunner`), not a dead button.

## Top blockers (manager + agent)

1. **Agent "Smart Order" empties the case list** — `scope.py:179` (`beat_date == access_day()`)
   with no beat for 2026-10-01. HIGH. Fix: seed/allocate a beat for the real current date (or
   the l6-rebuild / a nightly `run_nightly_allocation` for today), or have `today_beat_cases`
   share `_effective_day`'s fallback for the demo. This is the single most visible agent bug.
2. **Manager Live Map is 10 days stale** — `workforce.agent_locations` max 2026-09-21
   (`ManagerLiveMapPage.tsx`). MEDIUM. Run at least one live agent session during the demo, or
   refresh the location trail.
3. **Manager + agent "today" both resolve to 2026-09-22** — `_effective_today`
   (`manager.py:85-111`) / `_effective_day` (`agent.py:194-208`); harmless but the visible date
   trails ~9 days. LOW-MEDIUM; seed a current-date beat to align everything.

Note: unlike the bank portal, the manager and agent apps have **no "Not built yet" placeholders
and no dead nav** — the remaining risks are entirely empty/stale demo-book data on the literal
current date, which a current-date allocation run (or l6-rebuild) resolves.
