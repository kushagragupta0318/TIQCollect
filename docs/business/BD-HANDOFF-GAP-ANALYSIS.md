# TIQCollect — BD Handoff & Gap Analysis

*Business Lead, refreshed 2026-10-06 (supersedes the 2026-10-01 cut). For the Business Development
team who will pitch TIQCollect to banks and NBFCs. Every status claim is backed by a file in this
repository, verified against `TIQCollect-app` at head `6114c37`. This is a **demo**, not a production
deployment, and this document is written so you never oversell it.*

> **Read this box first.** TIQCollect has never processed a real borrower, a real rupee, or a real
> agent's day. Everything you will show runs on an **invented, synthetic demo book** (fictional
> lender *Girivan Finance Ltd*, fictional agencies). All model accuracy numbers come from
> **synthetic borrowers**. SMS/WhatsApp/calls are **switched off** for demo tenants. The AI
> (Groq-backed) is live in the demo and **safe to show** — every LLM call is PII-redacted and
> prompt-fenced (Section 3.6). The product is strong and demos well — but the honesty guardrails in
> Section 3 are not optional. A collections head's own legal and risk team will check every claim.

**What changed since the last cut (all verified in code):**
- The **enriched demo book is loaded**: recovery-scoring and routed beats now exist for *every*
  agency, not just Aravalli; a 14,700-loan performing book; Indian-calendar seasonality
  (`backend/app/demo/{recovery_scoring,beats,performing}.py`, dump rebuilt 2026-10-01).
- **Bank Analytics tabs shipped and are on the demo build** — 4 of 8 tabs live (Exposure,
  Migration, Agencies, Compliance), the other 4 honestly labelled in-progress.
- **Monte Carlo strategy simulator now has a UI** (not just the backend endpoint).
- **Borrower search + nav cleanup** merged (13 unbuilt rail items hidden, routes kept).
- **PII redaction + prompt-fencing across the LLM seam, and ML-promotion role gating (F12)** merged.
- **Customer 360** works (a uuid-serialisation 500 was fixed on 2026-10-06).
- **5 demo master logins** now (Meera Khanna added as AGENCY_ADMIN, 2026-10-06).
- Still **coming, not merged:** payment reversal (two-stage agency→bank) and bank↔agency messaging.

---

## 1. Executive summary (the one paragraph)

TIQCollect is **field-collections software for Indian lenders** — the "feet on the street" layer of
debt recovery. Field agents work a phone app: they check in with GPS, visit delinquent borrowers,
capture photo/signature/voice evidence, take payments that the **borrower confirms with a one-time
OTP**, and log promises to pay. A **nightly engine allocates tomorrow's cases to the right agent and
routes each agent's day** on real road distances, and **every allocation decision is stored with the
reasons it was made**. Agency managers get a live map, analytics, fraud/anomaly detection and an AI
performance write-up. The standalone product adds a **multi-tenant bank portal** so a lender can
place loans with multiple collection agencies, onboard them with four-eyes approval, watch their
performance across an **eight-tab analytics suite**, drill into any borrower (**Customer 360**), and
run a **Monte Carlo strategy simulator** over its book — all on one multi-tenant, row-level-secured
backend. Where TIQCollect is genuinely differentiated is **field evidence, explainable allocation,
fraud detection and model governance**. **Position it as the field-execution and field-evidence
layer that plugs into a lender's existing collections stack — not as a Credgenics-style all-in-one**
(COMPETITIVE-ANALYSIS.md §6).

---

## 2. Capability matrix

Status legend: **Demo-ready** (works in the running demo today, on `TIQCollect-app`) ·
**Backend-only** (engine works, no screen) · **In build** (built, not yet merged to the demo) ·
**Gap** (not built).

| Capability | Status | What BD CAN say | What BD must NOT claim |
|---|---|---|---|
| **Borrower-OTP-verified payments** | Demo-ready | "Payments are confirmed by the borrower's own OTP and get a unique receipt." Idempotent — a retried collection returns the same payment, never a second (commit `f63c8cc`, `5471a67`). | Don't claim the UPI auto-confirm is production-safe — the demo UPI flow auto-marks "received" and uses one payee (REVIEW.md §1). Being fixed before any pilot. |
| **GPS visit logging + geofence + contact hours** | Demo-ready | "100m geofence and the RBI 08:00–19:00 contact window are enforced server-side and applied as real routing time windows; flags are re-derived from each visit's own coordinates and time." (`fixtures/README.md` DATA-R) | Don't say GPS only runs on duty — it runs from login (PILOT-PLAN N02). |
| **Photo / signature / voice evidence capture** | Demo-ready (capture); **absent in the demo book** | "The app captures photo, signature and voice evidence per visit." | **Do not open an old visit expecting photos — the demo book carries no visit evidence objects** (`fixtures/README.md` "Not in it"). Capture a *fresh* visit live to show evidence. |
| **Voice-to-text on visit notes (Hindi+English → English)** | Demo-ready in **dev**; paid provider in prod default | "Agents dictate a note in Hindi or mixed Hindi-English; it's transcribed and translated to English in one pass." (`core/transcription.py`) | **Don't promise it's free/offline in production.** Prod default is paid OpenAI Whisper (PILOT-PLAN N03). Don't quote an accuracy number — none measured on real field audio. |
| **Nightly allocation engine (case ↔ agent match)** | Demo-ready / Backend | "A Hungarian-assignment solver matches cases to agents behind hard eligibility gates, and **every decision is stored with the reason**." (`services/global_allocator.py`) | Don't claim recovery uplift — any uplift figure is **synthetic** (COMPETITIVE §6). |
| **Routing (OSRM road distances + OR-Tools)** | Demo-ready / Backend | "Real road-distance routing with time windows; falls back to Haversine and records which was used." (CLAUDE.md) | Don't claim multi-vehicle fleet routing — `plan_fleet` was dropped (PILOT-PLAN §2). |
| **6 scoring layers + trained recovery_risk GAM** | Backend | See the mandatory language in **Section 3.2.** Champion is `recovery_risk` **2.2.0**, a 15-feature GAM (`ml/artifacts/recovery_risk/champion.txt` = `2.2.0`). | **Never quote real-world performance.** See 3.2. |
| **Fraud / anomaly detection** | Backend / Demo | "Seven evidence-based fraud checks — impossible travel, photo-location mismatch, duplicate photos, short visits, out-of-fence gaming, etc. The demo book injects real breaches (e.g. Awadh: 30% of its Aug visits outside the fence vs 4% elsewhere) so the checks fire." (`fixtures/README.md` ground-truth) | Don't claim it's validated against real fake visits — labels are synthetic. |
| **Live agent map** | Demo-ready, **thin data** | "Managers see agents on a live map." | Demo GPS is thin. Don't dwell on it. |
| **LLM performance narrative + field/portfolio copilot** | Demo-ready, **safe** | "An AI writes a plain-language performance summary over the manager's own team, and a copilot answers portfolio/field questions — every prompt is PII-redacted and fenced." (Section 3.6; `core/redaction.py`, `core/prompting.py`, merge `9d79b17`) | The AI can still occasionally contradict a KPI card. Don't put the two on screen together. Don't say the narrative is a guaranteed fact. |
| **Monte Carlo strategy simulator (E05)** | **Demo-ready (UI + engine)** | "A Monte Carlo simulator rolls the whole book forward month-by-month under a macro scenario and collection levers, returning IFRS-9-staged ECL, roll-rate projections and GNPA% **as bands, never a single number**, with a permanent 'uncalibrated on this book' caveat printed from the engine itself." UI at `frontend/src/bank/pages/strategy/MonteCarloPage.tsx` (merge `948e10f`), engine `app/strategy/`, endpoint `POST /bank/strategy/simulate`. | **It is UNCALIBRATED (ADR 0014)** — the on-screen caveat says so; don't call a simulated figure a forecast. It **abstains** (`INSUFFICIENT_HISTORY`) rather than invent when history is thin, and needs months of real DPD history to mean anything. |
| **Bank portal: Overview KPIs** | Demo-ready | "A bank Overview with live KPIs, robust to a single failing metric (per-KPI savepoint)." (`BankOverviewPage.tsx`, `44884a3`) | Numbers are synthetic (Section 3). |
| **Bank portal: Customer 360** | **Demo-ready** | "A single-borrower 360 view is live, front and back." (`CustomerPage.tsx`, `services/bank/customer_360.py`; the uuid-serialisation 500 was fixed 2026-10-06, `6114c37`). | Synthetic data. |
| **Bank portal: Analytics suite (C04)** | **Demo-ready (4 of 8 tabs)** | "An eight-tab portfolio analytics suite. **Exposure, Migration, Agencies and Compliance are live** and read real materialized views; branch/city/product concentration breakdowns landed on the manager side (`analytics-breakdowns`, `ce2933f`)." (`BankAnalyticsPage.tsx`) | **4 tabs are honestly marked in-progress on screen** — Recovery, Field Operations, Cost to Collect, Concentration say so rather than showing sample data. The transition matrix and agency scorecard are still being polished — demo them, but don't promise pixel-finished. Don't click a tab expecting all eight full. |
| **Multi-agency tenancy + RLS** | Demo-ready (data) / Backend (isolation) | "One bank, many agencies, isolated per tenant — real Agency/Bank entities and Postgres row-level-security policies in place. The demo has Girivan Finance (7 active agencies) plus Kumaon purely to show isolation." (`models/tenancy.py`) | **RLS is in place but not yet *forced* at the DB-role level** — the live tenant wall is still the app layer (`services/scope.py`). Say "tenant-isolated with RLS in place", not "RLS-enforced at the database for every connection". |
| **Four-eyes / maker-checker** | Demo-ready (3 flows) | "Placement apply, agency onboarding/document review, and ML model promotion all use maker-checker — enforced in code *and* a DB CHECK constraint, with a guard stopping one person holding both roles. Demoable end to end with the two Girivan bank logins." (ADR 0010) | **Manual placement is single-actor by design**, and **payment reversal's four-eyes is still in build** (below). Don't claim *every* sensitive action is maker-checker. |
| **ML-promotion role gating (F12)** | **Demo-ready** | "Promoting a model is gated to a dedicated Bank-TechOps capability (`ml.promote`), not every manager, and requires a second, different approver." (`core/permissions.py:174`, `_cap("ml.promote", …, (BANK_TECHOPS,), second_person=True)`) | Don't claim a full RBAC admin UI — it's a capability registry enforced on routes. |
| **Offline outbox + PWA** | Demo-ready (real) | "Installable PWA with a real offline outbox: visits, photos, signature, PTP and call logs queue in IndexedDB and sync in order when signal returns." (`lib/outbox.ts`, ADR 0011) | **Payments/recordings are never queued offline** (the borrower OTP must reach the server live); the SW caches the app shell, not API data. Test in the prod build. |
| **Hindi / regional languages** | **Gap** | "Hindi UI and borrower SMS templates are the top near-term item (N01/N02)." | **The app is English-only today.** Competitors ship 15–22 languages. Don't demo in "Hindi". |
| **Cash custody / deposit reconciliation** | **Gap** | "Cash custody is a planned wave-1 item (N05)." | Not built. Cash is a large share of demo payments — expect this question in the first meeting. |
| **DRA certificate register / allocation gate** | Demo (data) / **Gap (gate)** | "DRA certs are per agent in the roster, 11 expired for the compliance tile; wiring them as a hard allocation gate is planned (N04)." | The **hard allocation gate** isn't wired yet (PILOT-PLAN N04). |
| **Bank-format exports / MIS, SSO (OIDC/SAML)** | **Gap** | "Exports (N08) and SSO (N09) are wave-1." | Not in the demo build. Bank IT will ask about SSO. |
| **Payment reversal (two-stage agency→bank)** | **In build (not merged)** | "Two-stage payment reversal is in active build (branch `l7-pay-reversal`)." | **Not on the demo build.** Don't demo or promise a date. |
| **Bank↔agency messaging** | **In build (not merged)** | "An in-portal bank↔agency messaging thread is in active build." | **Not on the demo build.** Don't demo it. |

---

## 3. Honesty guardrails (non-negotiable when pitching)

### 3.1 Everything is synthetic / demo
- There is **no real traffic**. The demo book is fictional end to end (lender *Girivan Finance Ltd*,
  *Kumaon Finance Ltd*, invented agencies, borrowers, numbers). Every bank/agency carries
  `is_demo = true` (`fixtures/README.md`).
- Say **"this is a demonstration environment with synthetic data"** on any screen showing numbers.
- **Don't quote operational stats from the demo** (visits/day, keep-rates, recovery) as real client
  results. They are simulator output.

### 3.2 ML performance — exact compliant language
The serving champion is **`recovery_risk` 2.2.0, a 15-feature GAM**, trained **entirely on synthetic
borrowers** (`champion.txt`; CLAUDE.md "scoring layers").

- **Say this:** *"The model is trained and validated on a synthetic book. On that synthetic data the
  live-equivalent discrimination is roughly **Gini 0.48 / KS 36** (0.4796 / 35.74, ADR 0008). We
  will measure real performance during a pilot."*
- **Use the live-equivalent figures (0.4796 / 35.74), NOT the artifact figures (0.5122 / 38.66)** —
  the stronger feature (borrower stance) is never recorded by the product today, so the artifact
  numbers overstate the live product (ML-1, ADR 0008).
- **Never say or imply:** "proven on real borrowers", "X% recovery uplift", "best-in-class accuracy".
- **Lead with governance instead:** two-sided quality gates (a model can be rejected for being
  suspiciously *too* strong, i.e. leakage), a drift monitor, two-person promotion, and an artifact
  you can hand a bank's model-risk committee. Genuinely rare in this category and **true today**.

### 3.3 Messaging / Twilio
- **No real SMS, WhatsApp or calls go out in the demo** — suppressed for demo tenants; WhatsApp runs
  on Twilio's sandbox number (`fixtures/README.md`).
- Production needs an Indian DLT-registered gateway (PILOT-PLAN N06). Don't promise live borrower
  messaging at the demo's cost.

### 3.4 Compliance wording
- **Do not say "RBI Compliant"** — RBI certifies no software. Frame as *"built to evidence RBI
  Responsible-Business-Conduct requirements"* and be ready to name the gaps (DRA gate, grievance
  workflow, full audit trail).

### 3.5 Don't let diligence surprise you
A technical buyer's engineers may look. Known items (all being worked): a large `manager.py` route
file, two schema authorities, demo-password hashes in history (ENGINEERING-AUDIT.md). None are
deal-enders, but don't claim "production-hardened".

### 3.6 The AI is safe to demo — and here is why (new)
The Groq key is live in the demo, so the AI narrative and copilot really run. Every LLM call goes
through one seam (`core/llm.py`) that **redacts PII and fences the prompt** before anything leaves
the box (`core/redaction.py` 227 lines, `core/prompting.py`, wired into `ai_report_service`,
`case_service`, `visit_report_extraction`, and the agent/manager copilot routes; merge `9d79b17`,
covered by `test_llm_redaction.py` / `test_ai_report_prompt_safety.py`). **You can say:** "borrower
PII is stripped and the prompt is fenced against injection before any model call." **Don't** say the
model output is guaranteed accurate — it's a narrative aid, labelled `ai_generated`.

---

## 4. Gap list, ranked by pitch impact

1. **English-only** — hard disqualifier for the target buyer; competitors ship 15–22 languages.
   Still the single biggest demo gap. *(N01/N02.)*
2. **Evidence is missing from the demo book** — the pitch is verified evidence, yet the book carries
   no visit photos/signature/audio. **Always demo a fresh capture, never an old visit.**
3. **Payment honesty holes in the demo** — UPI auto-"received", single payee. Being fixed first;
   don't present as finished. *(REVIEW §1.)*
4. **Cash custody, DRA allocation gate, grievance workflow, bank-format exports, SSO** — all day-one
   buyer asks, all gaps. *(PILOT-PLAN N04–N09.)*
5. **Four of eight Analytics tabs are in-progress**, and payment reversal + bank↔agency messaging are
   **not merged** to the demo build. Show the four live tabs and the three four-eyes flows; present
   reversal/messaging/the other tabs as "shipping next".
6. **Monte Carlo is uncalibrated** — the UI shows it, but don't let the bands read as a forecast.
7. **Audit trail is partial** — visit/payment/PTP actions are written; some agency-lifecycle and
   placement actions are declared but not yet written. Don't claim a complete audit trail.
8. **RLS is in place but not DB-role-forced** — say "tenant-isolated with RLS in place".
9. **Manager screens can occasionally contradict themselves** (AI narrative vs KPI card). Don't put
   two contradicting numbers on screen together.
10. **PWA, not a native app** — offline outbox works, but a PWA can't track GPS screen-off; expect an
    APK ask before ~500 agents.
11. **Field-only product** — no tele-calling/dialer/campaign waterfall. Position as the field layer.

---

## 5. 10-minute demo script (3 roles)

**Setup:** run from a production build (faster first load). Use the five demo master logins in
`fixtures/README.md` — **never read a password aloud or put it in a deck**. **Aravalli Field Services**
holds the richest book (B15's visits, payments, allocation decisions) and is the agency-manager
login; the generated Girivan agencies (**Sahyadri Field Recovery** and others) now also carry recovery
scores and routed beats. **Avoid Hooghly (PENDING) and Awadh (SUSPENDED)** — empty/edge-case by
design (though Awadh is useful to *show* the fraud/suspension story). If the book looks "paused", run
the daily feed first so dates look current.

**Minutes 0–1 — Frame it.** "This is a demo environment, synthetic data, no real borrowers or
messages. The AI is live but PII-redacted. TIQCollect is the field-execution and evidence layer for
collections." Set expectations before any number appears.

**Minutes 1–4 — The field agent (phone).** Log in as `piyush.sharma@aravallifs.test`.
- Show the routed day / case list.
- **Capture a fresh visit live:** check in (GPS + geofence), record a short Hindi/English voice note
  → watch it transcribe to English, attach a photo + signature, log an outcome and a **promise to
  pay**.
- Take a payment and show the **borrower-OTP confirmation** and the receipt. *(This is the strongest
  3 minutes — real capture, not stale data.)*

**Minutes 4–7 — The agency manager.** Log in as `vikram.malhotra@aravallifs.test` (Aravalli, 17
agents).
- Show the day's plan and **the allocation explanation** ("why this case went to this agent") — a
  genuine differentiator.
- Show the fraud/anomaly findings over captured evidence (and, if asked, the Awadh out-of-fence
  story).
- Glance at the **AI performance narrative** — now safe to show (redacted/fenced) — but don't put it
  beside the KPI card.
- Keep the live map brief (demo GPS is thin).

**Minutes 7–10 — The bank admin.** Log in as `ananya.iyer@girivanfinance.test`.
- Show the **Overview KPIs** and the **agency directory / performance scorecard** (DRA certs,
  consent, out-of-hours are real in the fixture).
- Open the **Analytics suite** → the four live tabs (**Exposure, Migration, Agencies, Compliance**).
  Skip the four in-progress tabs or let their honest in-progress note speak.
- Open a **Customer 360** for the single-borrower view.
- Open the **Monte Carlo Simulator**, run a scenario, and point at the **bands and the uncalibrated
  caveat** — "it refuses to pretend; it needs real history to calibrate."
- Demo the **four-eyes placement**: Ananya plans a placement run; the second bank login
  (`kavya.reddy@girivanfinance.test`) applies it. "One plans, another approves — enforced in the
  database."
- Close on the compliance-evidence story and the roadmap (Hindi, cash custody, exports, the four
  remaining analytics tabs, payment reversal, bank↔agency messaging).

*(Fifth master login: Meera Khanna, `meera.khanna@aravallifs.test`, AGENCY_ADMIN — use only if you
need to show the agency-admin tier.)*

---

## 6. Open product decisions BD should know are pending

- **Borrower-stance capture (ML-1).** The live model wants a "borrower stance" input the app doesn't
  record yet. Until resolved, **the model runs in shadow / advisory** for any pilot.
- **Demo tenant naming.** Fictional *Girivan Finance Ltd* / *Kumaon Finance Ltd*, `.test` domains.
  Formal trademark/MCA clearance is a legal step before anything external.
- **Pilot shape & pricing.** Recommended: one lender, one agency, dedicated India deployment,
  ≤100 agents, 90 days; then a per-lender + per-active-agent + messaging-at-cost model, **not**
  %-of-recovery. BD should quote ranges, not firm prices, until signed off by the owner.
- **Paused by plan:** AI-agent studio and most new ML are paused until real outcomes exist (first
  30-day labels mature 2026-10-08). Don't pitch these as near-term.
- **Not-code gating items before a contract:** CERT-In VAPT report, data-processing agreement,
  BCP/DR statement, support SLA. Allow 2–4 weeks elapsed.

---

*Backing files verified for this cut: `backend/app/ml/artifacts/recovery_risk/champion.txt`,
`backend/fixtures/README.md`, `backend/app/core/{llm,redaction,prompting,permissions}.py`,
`frontend/src/bank/pages/{BankAnalyticsPage,strategy/MonteCarloPage,customer/CustomerPage}.tsx`,
`backend/app/demo/{roster,recovery_scoring,beats,performing}.py`, `CLAUDE.md`, `docs/adr/`,
and `git log` on `TIQCollect-app` (head `6114c37`).*
