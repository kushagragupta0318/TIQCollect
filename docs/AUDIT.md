# TIQCollect — Audit & Feature Gap Analysis

Assessed 2026-08-17 against the codebase at extraction (commit `7c30095`). Every
claim below was verified by reading the code, not inferred from filenames.

Two parts: **A** — production-readiness defects. **B** — coverage against the
*AI-Powered Field Recovery Platform* 20-feature reference document.

---

## Part A — Production readiness

### A1. Three unscoped manager endpoints (critical)

Every manager endpoint scopes to the caller's team via
`Agent.manager_user_id == current_user.id`, hand-repeated **14 times**. Three
places missed it. Because Command Center connects with a *per-agency service
account* and agencies are separately owned, these are cross-tenant leaks.

| Endpoint | Location | Defect |
|---|---|---|
| `GET /manager/compliance` | `manager.py:743` | Authenticated but scoped to nobody. Counts `total_visits`, `out_of_hours`, `geo_violations`, `sos_active` across every agent on the platform. |
| `GET /manager/ai/monthly-report` | `manager.py:2054` | `db.query(Agent.id).all()` under a comment reading *"All agent IDs"*. Feeds an LLM report with other agencies' data. |
| `PUT /manager/agents/{agent_id}/status` | `manager.py:1782` | Filters on `Agent.id` only, no ownership check. **Any manager can set any agent ON_DUTY/OFF_DUTY.** Write-side IDOR — the worst of the three. |

`GET /manager/agents/{agent_id}/availability-calendar` (`manager.py:1870`) also
lacks the check; its docstring says "any agent's", so it may be deliberate, but it
doesn't hold in a multi-agency deployment.

**Fix:** replace the repeated `my_agent_ids` list with one dependency
(`OwnedAgentIds`, plus `require_owned_agent(agent_id)` for path params). Patching
three call sites leaves the next one to be forgotten the same way. Write the
tenancy test suite first — manager A must not read or mutate manager B's agents.
That single test would have caught all four.

### A2. Offline support is a UI illusion

`AgentLayout.tsx:121` toasts *"You're offline — actions will queue"*; line 279
renders *"Offline — visits will sync when reconnected"*. Neither is true. There is
no service worker, no `manifest.json`, no IndexedDB, no PWA plugin, no outbox — the
only queue in the frontend is the axios 401-refresh queue.

For feet-on-street collections this is a data-loss path: an agent completes a visit
in a basement or rural area, taps submit on a 2082-line form having captured photos,
signature and cash — and it is silently lost. The banner actively tells them it's safe.

Either build it (service worker + IndexedDB outbox + **idempotency keys** on
`recordVisit`/`collectPayment` so replays don't double-post) or remove the banner.
The current state is the dangerous option.

Note: `offlineAdapter.ts` in the *original* repo is unrelated — a backend-free demo
build that replays captured snapshots and refuses writes. Not offline-first support.

### A3. `manager.py` is 2316 lines of business logic in the route layer

99 `db.query()` calls sit directly in endpoints while a working `services/` layer
exists and is used by the agent flows. AI briefing, analytics, reallocation and DPD
breakdown are all inline. This is *why* A1 happened — there is no single place where
"the agents this manager owns" is defined. Extract `manager_service.py` mirroring
`case_service.py`.

Frontend equivalents: `RecordVisitPage.tsx` 2082 lines, `ManagerAnalyticsPage.tsx`
1358, `AgentCaseDetailPage.tsx` 1232.

### A4. Test coverage ~5%

523 test lines against ~10,197 backend lines, covering only geo, OTP, payment and
visit services. Nothing covers `manager.py`, auth/JWT, quick-login single-use, or
`ml/allocator.py` / `core/routing.py`. Frontend has **no test tooling at all** — no
vitest, jest, playwright or cypress. TS `strict` is on and eslint is configured,
which is a good base.

Priority order: tenancy suite (A1) → quick-login single-use → geofence and
contact-hour compliance rules.

### A5. Two competing schema authorities

Alembic exists with two migrations, but `scripts/seed_data.py:1132` does
`drop_all` + `_drop_all_enums` + `create_all`. `docker-entrypoint.sh` arbitrates by
checking whether `public.agents` exists. Fine for a demo box; for production
`alembic upgrade head` must be the only path, with the seed reduced to data-only.

### A6. Audit trail is declared but not written

`AuditLog` is well designed — 21 action types, documented *"no updates, no deletes.
Required for RBI compliance"*, deliberately without `TimestampMixin`. But only
**three** call sites write to it: `auth_service.py:25`, `otp_service.py:402`,
`payment_service.py:111`.

Never emitted despite being defined: `CASE_ASSIGNED`, `CASE_UPDATED`,
`VISIT_RECORDED`, `PTP_SET`, `PTP_UPDATED`, `DOCUMENT_UPLOADED`, `SOS_TRIGGERED`,
`SOS_RESOLVED`, `BEAT_GENERATED`, `BEAT_MODIFIED`, `AGENT_STATUS_CHANGED`,
`CONTACT_HOUR_VIOLATION_ATTEMPT`, `ROLE_VIOLATION_ATTEMPT`, `DEVICE_MISMATCH`,
`DATA_EXPORT`. The enum is a plan, not a record.

Immutability is also convention only — no DB trigger, no revoked UPDATE/DELETE
grant.

### A7. Smaller items

- **No forecasting anywhere** — all analytics are historical or current-state.
- **Nothing is real-time** — no WebSocket, SSE or polling. `agent_service.py:55`
  has a comment anticipating a WebSocket handler that was never written.
- **`get_visit_strategy` and `_score_case` call OpenAI per request**, uncached. The
  manager briefing caches 1h; these don't.
- **CI has never run** (see CLAUDE.md).
- **Analytics dimensions** are agent/DPD/month only — no branch, geography or
  product, though `city` and `loan_type` are on the models.

### What is already strong

Worth not regressing: JWT with `jti` + `device_id` binding; bcrypt; single-use
quick-login tokens (with the 90-day-token incident documented and fixed in
`core/security.py:1`); slowapi rate limiting; structlog; typed error codes;
presigned MinIO URLs; GZip; an SPA fallback that refuses to swallow `/api` paths;
and `core/transcription.py`, which is visibly debugged against real mic audio
rather than clean test files.

---

## Part B — Coverage vs the 20-feature reference

**4 built · 9 partial · 7 missing.**

| # | Feature | Status |
|---|---|---|
| 1 | AI Field-Agent Copilot | ✅ Built |
| 2 | AI Voice → Automatic Visit Report | 🟡 Partial |
| 3 | AI Recovery Priority Score | 🟡 Partial |
| 4 | AI Next-Best-Action Engine | ❌ Missing |
| 5 | Recovery-Optimized Route Planning | 🟡 Partial |
| 6 | Borrower 360° Profile | ✅ Built |
| 7 | AI Recovery Probability & Expected Recovery | ❌ Missing |
| 8 | AI Settlement Recommendation | ❌ Missing |
| 9 | AI Agent Performance Intelligence | ✅ Built |
| 10 | AI Fraud & Anomaly Detection | ❌ Missing |
| 11 | AI Compliance Monitor | 🟡 Partial |
| 12 | Live Recovery Command Center | 🟡 Partial |
| 13 | Recovery Risk Radar | ❌ Missing |
| 14 | Digital Payment & Instant Receipt | ✅ Built |
| 15 | Offline-First Field App | ❌ Missing |
| 16 | Evidence & Immutable Case Timeline | 🟡 Partial |
| 17 | Customer Engagement Hub | 🟡 Partial |
| 18 | Smart Work Queue & Gamification | 🟡 Partial |
| 19 | Recovery Forecasting & Portfolio Analytics | 🟡 Partial |
| 20 | Continuous Learning & Management Insights | ❌ Missing |

### Built

**1 · Copilot** — `GET /agent/cases/{id}/visit-strategy` (`agent.py:796`) returns
`best_time_to_visit`, `customer_situation`, `recommended_approach`,
`payment_readiness`, `risk_flags`, `key_leverage_points` and an `opening_line` in
Hindi/Hinglish. Surfaced as the case-detail "strategy" tab. Exceeds the spec — the
local-language opening line isn't asked for. Uncached; no NBA field.

**6 · Borrower 360°** — `AgentCaseDetailPage` with 6 tabs over
Customer/Loan/Case/Visit/Payment/PTP/CallLog/Photo/Document. Gap: disputes are a
visit *outcome*, not an object with a lifecycle.

**9 · Performance Intelligence** — `/manager/agents/performance`,
`/agents/{id}/ai-insight` (LLM, with coaching/reallocation/territory
recommendations), `/agents/{id}/reallocation-plan`, `/ai/monthly-report`,
`AgentPerformance` model, leaderboard, DPD and attendance breakdowns. Missing the
spec's route-efficiency and data-quality dimensions.

**14 · Payment & Receipt** — the strongest feature. Unique `receipt_number` with a
DB constraint, Razorpay `upi_qr` links, SMS + WhatsApp receipts with masked phone,
and **borrower OTP verification of the collection** (`otp_service.py`, 411 lines,
audited and throttled) with a distinct `PENDING_VERIFICATION` state for the
borrower-unreachable path. The OTP step is beyond the spec. No reconciliation
workflow.

### Partial

**2 · Voice → Report — the highest-value gap.** Both halves exist and don't
connect. STT is real and hardened (`core/transcription.py`: provider seam, VAD
filtering, `condition_on_previous_text=False`, no-speech-prob rejection, int8,
domain prompt). But `ai_report_service.generate_visit_report()` produces a
100–150 word **prose summary for the manager, built from already-structured
fields**. Nothing extracts disposition / PTP amount / date / next action *out of the
speech* — the agent still types all of it. The spec's "agent speaks naturally" is
the product differentiator and it's one structured-output call away.

**3 · Priority Score — right machinery, wrong objective.**
`case_service._score_case()` is thoughtful: PTP today +60, verbal pay date today
+50, payment intent +45, best-time-now +35, broken PTP +25, NPA +10, priority bonus
≤+8, hostile −15, DNC −9999, blocked −999; plus badges and an LLM `rank_reason` for
the top 8. But it ranks by **signal recency, not expected value** — outstanding
amount never enters the score.

**5 · Routing — same shape.** `core/routing.py` is genuinely strong: OSRM road-time
matrix + OR-Tools TSP, VRPTW time windows, `forced_next`, nearest-neighbour
fallback, `matrix_provider` seam for a learned travel-time model. But it minimizes
**travel time**, not recovery value. Also `AgentCasesPage.tsx:84` re-sorts by GPS
distance client-side, fighting the server's order.

**11 · Compliance — rules yes, AI no, and leaky.** Real enforcement:
`within_contact_hours` (RBI 8 AM–7 PM) with `CONTACT_HOUR_VIOLATION_ATTEMPT`
auditing, `geo_verified` 100m geofence, `ContactHourBanner`, DNC honoured in
ranking, `consent_given` + `signature_key`. Gaps: rules are hardcoded not
configurable; no AI analysis of interaction patterns or missing-evidence detection;
and the endpoint is the A1 tenancy bug.

**12 · Command Center — dashboard yes, "live" no.** `/manager/dashboard` +
`ManagerOverviewPage` with a 1h-cached AI briefing. Nothing is real-time (A7), and
there's no manager map view — `BeatMapPage` is agent-only, so "geographic activity"
is absent.

**16 · Evidence excellent, timeline absent.** Capture is thorough: 4 photo slots
each with own lat/lon/`accuracy_metres`, selfie, signature, consent, recordings +
transcript, categorised documents, check-in/out GPS and times. But the audit trail
is barely written (A6) and there's no unified timeline view — evidence is scattered
across 6 tabs.

**17 · Engagement Hub** — `notification_service` (Twilio SMS + WhatsApp),
`/cases/{id}/notify`, `/notify-visit`, payment links, receipt sends,
`useVoiceCall`. All agent-triggered one-offs: no campaign engine, no reminder
scheduling, no PTP follow-up automation, no templates, no unified comms log. The
platform's Digi-Tele app does this properly — decide whether to build or delegate.

**18 · Work Queue & Gamification** — queue is real (`/agent/cases/ranked`, badges,
reasons, progress vs target). Gamification is thin: `ranking_score` with a
`RankingRing` gauge and `TierBadge`, manager-side leaderboard only. No missions,
goals, streaks or achievements, and the agent can't see their own standing.

**19 · Analytics strong, forecasting absent** — see A7.

### Missing

**4 · Next-Best-Action** — no endpoint, no action recommendation. Closest are
`recommended_approach` inside visit-strategy (advice, not a decision) and
reallocation-plan (about agents, not cases). Nothing chooses visit vs call vs
reminder vs skip-trace vs settlement vs escalate.

**7 · Recovery Probability — the keystone.** No model. `customer.risk_score`
(default 50.0), `loan.bank_risk_score` and `collection_priority_score` are all
bank-supplied. `loans.recovery_potential` exists as a column but
`scripts/add_recovery_potential.py` fills it with **`random()`**, weighted by DPD
and secured/unsecured — plausible-looking demo data, not a prediction. Nothing
reads it.

**8 · Settlement Recommendation** — `loan.settlement_status` is a bank flag,
read-only, gated on `borrower_verified`. No range calculation, policy constraints or
approval workflow.

**10 · Fraud & Anomaly Detection** — `customer.fraud_flag` is bank-reported;
`FRAUD_CLAIM` is a visit outcome. **Nothing is detected.** All the raw material is
already collected and unused: check-in lat/lon, per-photo lat/lon + accuracy,
check-in/out times, `geo_verified`. Impossible travel, duplicate visits and
suspiciously short visits are plain SQL — no model needed.

**13 · Risk Radar** — every state it needs is already exposed (`risk_category`,
`risk_score`, `fraud_flag`, `npa_flag`, `do_not_contact`, `is_hostile`,
`BROKEN_PTP`, `DISPUTE`, rank badges). No radar/quadrant view exists. **Pure
frontend work on existing data — cheapest item on this list.**

**15 · Offline-First** — see A2.

**20 · Continuous Learning** — no predicted-vs-actual comparison anywhere.
`beat.ml_model_version` is a column with nothing writing to it. Management
"insights" are LLM narration over current data, not a learning loop.

### The structural point

Five of the seven missing features are **one dependency**. There is no prediction
layer: `app/ml/` holds a single file whose own docstring says *"Rule-based Case
Allocator (dummy — no ML model required)"*. #7 blocks #3 and #5 from being
value-optimized, blocks #8, and blocks #20 (nothing to compare against).

**Shortcut worth evaluating:** the Collections platform already ships seven trained
models in `command-center/backend/models/` — M2 risk scoring, M3 recovery forecast,
M7 cure rate. #7 may be a matter of *calling* those rather than training new ones.
The reverse-direction contract already exists (`GET /api/account-context/{loan_id}`,
§4 of the platform's `FIELD_OPS_INTEGRATION.md`) and TIQCollect doesn't use it.

The two missing features needing no ML: **#13** (frontend only) and **#10** (SQL
over data already captured).

---

## Suggested sequence

| | Work | Rough effort |
|---|---|---|
| 1 | Scoping dependency + fix A1 + tenancy test suite | ~1 day |
| 2 | Decide the offline banner: remove now, build properly later | 1 hr / 1–2 wk |
| 3 | Get CI green and running | ~1 day |
| 4 | Voice → structured fields (#2) | ~1 wk |
| 5 | Risk Radar (#13) | ~2–3 days |
| 6 | Anomaly detection (#10) | ~3–4 days |
| 7 | Extract `manager_service.py`; split the 3 largest pages | ~1 wk |
| 8 | Wire in Command Center's models for #7, then re-base #3/#5 on expected value | ~1–2 wk |
| 9 | Finish the audit trail (A6); Alembic as sole authority (A5) | ~3 days |

Items 1–3 are the ones not to ship without.
