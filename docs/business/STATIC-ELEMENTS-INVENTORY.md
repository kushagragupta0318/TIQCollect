# Static / fake / placeholder element inventory — demo readiness

Consolidated across all three views (Bank portal, Agency-manager app, Field-agent app).
Synthesised from `docs/business/QA-SWEEP.md` and **re-verified by opening each cited `file:line`**
on 2026-10-01 (branch `TIQCollect-app`). "Static" here is the owner's broad definition: a
control that (a) does nothing, (b) shows hardcoded/fabricated data, (c) renders "Not built yet",
or (d) is visually present but non-functional.

**Scope note.** Pure empty-book gaps that are only about the demo-book date (manager/agent
"today" = 2026-09-22, stale Live Map, Smart Order blanking, un-matured Performance Index) are
**excluded** — they are the auto-roll / current-date-allocation work, not static UI. They are
listed once at the end for completeness. Everything in the tables below is static *regardless of
data*.

Class legend: **dead-click** (does nothing) · **fake-data** (hardcoded/fabricated) ·
**placeholder** ("Not built yet") · **non-functional** (present but broken).

---

## Bank portal

| Element | Location (file:line) | What it does now | Class | Severity | Remediation + who |
|---|---|---|---|---|---|
| Overview KPI cards (all 12) | `frontend/src/bank/pages/BankOverviewPage.tsx:51-52` + `BankApp.tsx:35-37` | Flagship dashboard's primary interaction. Each card navigates to `/bank/analytics?tab=…`; `analytics` is not in `BUILT_PAGES`, so it lands on the "Not built yet" placeholder | dead-click | **BLOCKER** | **lane 14** (hide/disable for demo) or build analytics (C04–C05) |
| Left-rail nav — 14 placeholder items | `frontend/src/bank/BankApp.tsx:35-37,67-72` → `BankPlaceholderPage.tsx:21` | Only `overview` / `agencies/placement` / `governance/models` (+ explicit onboard/directory/performance/customers) are built; Analytics, Alerts, Monte Carlo, Cash Forecast, Scenario Lab, Board Reports, AI Agents, MLOps, Data Quality, Usage & Cost, Bank Users, Regions, Settings, Audit all render "Not built yet" | placeholder | **HIGH** | **lane 14** (D07 + nav hide) — hide paused/dropped sections per PILOT-PLAN §2 |
| Agency Directory rows | `frontend/src/bank/pages/directory/AgencyDirectoryPage.tsx:150-152` | `openRow` navigates only when `status === "PENDING"`; the 8 ACTIVE + 1 SUSPENDED rows are dead clicks (D07 profile not built) | dead-click | **HIGH** | **lane 14** (D07 agency profile) — or gate demo to PENDING rows |
| Placements "Expected P(pay)" column | `frontend/src/bank/pages/BankPlacementPage.tsx:369-372` | Renders `—` for every row; `expected_recovery_prob` is null for 0/10095 placements (and `model_prediction_id` null → synthetic banner never shows). Whole column reads broken; not date-dependent | non-functional (empty column) | **HIGH** | Backfill `model_prediction_id`/`expected_recovery_prob` via **branch l6-rebuild** — confirm l6 fills these; otherwise **UNASSIGNED** |
| Top search bar (Ctrl/Cmd+K) | `frontend/src/bank/layout/BankSearchBar.tsx:3-4,18` | Indexes pages only (`PAGE_ENTRIES`); borrower/account lookup not wired — a borrower name returns "No results" from a prominent, functional-looking control | non-functional | MEDIUM | **UNASSIGNED** (needs account-search endpoint) |
| `/bank/customers/:customerId` route | `frontend/src/bank/BankApp.tsx:78` | Route exists but nothing in the bank tree emits it (only `?loan=` entry is used) | dead route (harmless) | NONE | None — not reachable in demo |

---

## Agency-manager app

| Element | Location (file:line) | What it does now | Class | Severity | Remediation + who |
|---|---|---|---|---|---|
| "Apply Plan" (reallocation drawer footer) | `frontend/src/pages/manager/ManagerAgentsPage.tsx:1492-1500` | `onClick` fires `toast.success("Reallocation plan logged…")` and closes the drawer — the flagship reallocation gesture commits nothing | dead-click / fake | **HIGH** | **lane 57** |
| "Send Message" (agent card) | `frontend/src/pages/manager/ManagerAgentsPage.tsx:773-778` | `onClick={() => toast.success(\`Message sent to …\`)}` — no message is sent | dead-click / fake | MEDIUM | **lane 57** |
| SOS "Call" link | `frontend/src/pages/manager/ManagerAgentsPage.tsx:273-279` | `href={\`tel:${a.employee_code}\`}` — dials the **employee code**, not a phone number; a dead dial in the SOS-response panel (safety path) | non-functional | **HIGH** (safety) | **lane 57** |
| "Pending Actions" fallback card | `frontend/src/pages/manager/ManagerOverviewPage.tsx:438-444` | When the AI briefing is absent, shows fabricated figures: "Cases pending first visit" = `Math.round(cases_assigned * 0.18)`, "Escalated cases" = hardcoded `3` | fake-data | MEDIUM | **lane 57** |
| "Compliance & Deferred" pill / summary | `frontend/src/pages/manager/TomorrowAllocationCard.tsx:573-577` (summary) vs `:667-674` (pill) | Summary labels `total_cases_deferred` as "(N cap)" beside `total_cases_blocked`; the Deferred pill separately counts `outcome.startsWith("DEFERRED")`. The two "deferred/blocked" counts can disagree and the "cap" label is misleading | fake-data / mismatch | LOW-MEDIUM | **lane 57** |
| Analytics branch/city/product breakdown | known issue #8, `docs/ENGINEERING-AUDIT.md` (columns exist, no UI split) | Only agent / DPD-bucket / month dimensions; branch/city/product never render though columns exist | missing-feature | LOW | **UNASSIGNED** (not in lane 57) |

---

## Field-agent app

| Element | Location (file:line) | What it does now | Class | Severity | Remediation + who |
|---|---|---|---|---|---|
| Check-in "Location: Mumbai, Maharashtra" | `frontend/src/pages/agent/AgentHomePage.tsx:248` | Hardcoded city string on the captured-selfie panel regardless of the real GPS fix | fake-data | **HIGH** | **FIXED on `l7-n1-evidence-honesty`** (`b255227`: shows the GPS fix it has) — pending merge |
| Check-in "Liveness check: Passed" | `frontend/src/pages/agent/AgentHomePage.tsx:250` | Hardcoded pass verdict; no liveness is actually run | fake-data | **HIGH** | **FIXED on `l7-n1-evidence-honesty`** (`b255227`) — pending merge |
| Fake selfie SVG fallback | `frontend/src/pages/agent/AgentHomePage.tsx:60` | When the camera is unavailable, injects a hardcoded base64 "👋" SVG as the "selfie" | fake-data / placeholder | MEDIUM | **FIXED on `l7-n1-evidence-honesty`** (`9cd2a06`: no selfie at check-in until B07) — pending merge |
| "No Route Active" quick-action tile | `frontend/src/pages/agent/AgentHomePage.tsx:205` | Rendered (dimmed) when no beat; `onClick={() => {}}` — a visible card that does nothing | dead-click | LOW-MEDIUM | **NOT** fixed by l7-n1 (still present there at line 179) → **f2 agent-flow work / UNASSIGNED** |
| Contact-hours banner | `frontend/src/components/layout/AgentLayout.tsx:432-440` | Outside 8AM–7PM IST, shows "Visit recording is disabled" and blocks recording | non-functional (by-design, RBI) | LOW | By-design — narrate; only bites if demo runs after 7PM / before 8AM IST |

---

## Excluded: empty-book / date-gated (covered by auto-roll, not static UI)

Verified real but caused by the paused demo book's date, and resolved by a current-date
allocation run / l6-rebuild — **not** listed above:

- Manager + agent "today" resolve to 2026-09-22 (`manager.py:85-111` `_effective_today`;
  `agent.py:194-208` `_effective_day`) — no beats 09-23…10-01.
- Agent "Smart Order" blanks the case list (`scope.py:159,179` `beat_date == access_day()`,
  no beat for 2026-10-01) — HIGH *for the demo* but a data-date gap, fixed by seeding today's beat.
- Manager Live Map ~10 days stale (`agent_locations` max 2026-09-21) — self-heals with a live session.
- Bank Agency Performance Index "Not enough data" (matures 2026-10-08).
- Bank Customer-360 band / "Why this score" mostly unscored (model scores on pool entry).

---

## Top static items still UNASSIGNED

For the coordinator to place:

1. **Bank top search bar borrower/account lookup** (`BankSearchBar.tsx:3-4,18`) — prominent
   Ctrl/Cmd+K control, no account-search endpoint. No owner.
2. **Bank Placements "Expected P(pay)" empty column** (`BankPlacementPage.tsx:369-372`) —
   100% empty across the table; needs `l6-rebuild` to confirm backfill, else no owner.
3. **Agent "No Route Active" dead tile** (`AgentHomePage.tsx:205`) — not covered by
   `l7-n1-evidence-honesty`; candidate for f2's agent-flow work, otherwise no owner.
4. **Manager Analytics branch/city/product breakdown** (ENGINEERING-AUDIT #8) — missing
   dimension, outside lane 57. No owner.
