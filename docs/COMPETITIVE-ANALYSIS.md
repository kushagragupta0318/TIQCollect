# TIQCollect — Competitive Analysis and Product Gap Assessment

**Date:** 2026-09-08
**Scope:** Field-collections ("feet on street") debt recovery platforms, India-first, with
enterprise-suite and global-decisioning vendors as secondary reference points.
**Method:** Public vendor material and regulatory sources (listed at the end), compared against
the TIQCollect tree as it stands on branch `TIQCollect-v2-2` at `86eb6d0` + working tree.

> Feature claims about TIQCollect below were verified by reading the tree, not by quoting
> `CLAUDE.md`. Where the two disagree, the tree wins and the disagreement is noted.

---

## 0. Verification notes — what was checked in the tree

| Claim | Result |
|---|---|
| Internationalisation | **Absent.** No i18n framework anywhere. Every `locale` hit in `frontend/src` is `toLocaleString` for currency/date formatting. |
| Incentive / commission / payout engine | **Absent.** One string match, in `frontend/src/pages/LandingPage.tsx` (marketing copy). |
| Cash deposit / remittance / reconciliation | **Absent.** All `reconcil` matches are `beat_reconciliation` (plan-vs-GPS distance/duration), a different thing entirely. |
| Legal / notice / SARFAESI / Sec 138 workflow | **Absent.** `Loan.legal_status` is a read-only bank-reported flag. No notice objects, no litigation lifecycle. |
| Agency / branch / cluster / region hierarchy | **Absent.** Tenancy is `Agent.manager_user_id == current_user.id`. There is no agency entity and no org tree. |
| Visit audio capture | **Present, and stronger than documented.** `media_service.py` issues presigned MinIO upload URLs for `audio/webm`, stores agent *and* borrower recordings, serves playback URLs, and transcribes via `core/transcription.py`. |
| SMS / WhatsApp | **Present but one-off.** `notification_service.send_twilio()` (SMS + WhatsApp) and `send_sms()` (OTP). No campaign, schedule, template or unified comms log. |
| Offline support | **Absent**, as documented. No service worker, no IndexedDB, no outbox. |

---

## 1. The competitive set

Three tiers. TIQCollect is compared against all three inside the same buyer evaluation.

### Tier 1 — India FOS-native (the real fight)

| Vendor | Position | What they lead on |
|---|---|---|
| **Credgenics — CG Collect** | Category leader. $50M Series B at **$340M valuation**, **₹226 Cr FY25 revenue (+41.3% YoY)**, 160+ FIs (HDFC, ICICI, Mahindra Finance, IIFL, DMI, Hero Fincorp). Acquired **Arrise (Aug 2025)** to add on-ground agency across 18,000+ pincodes; stated combined ARR target ₹850 Cr in 3 years. | Legal workflow (SARFAESI, Sec 138, NCLT), 22+ vernacular languages, offline, deposit-centre OTP, per-agent collection limits, BYOD, in-app legal notices. |
| **Dista Collect** | Location-intelligence-first collections CRM. | **SPACE** allocation framework (Skill, Proximity, Availability, Capacity, Experience), beat planning, **address cleansing and verification**, risk heatmaps. Claims +35% true visit rate. |
| **Vymo CollectIQ** | Launched May 2025. Mobile-first, built for Indian/Asian field networks. | Guided workflows, intelligent nudges, **integrated incentive management**, dynamic risk score, LMS/CRM/PG integration. |
| **Mobicule mCollect** | Long-standing FOS platform. | Modular, industry-specific compliance (BFSI, telecom, utilities). |
| **Credility goCollect** | MFI / micro-lender focus. | Auto-allocation, real-time UPI receipting, **cash-deposition tracking for reconciliation**. |
| **Collbox FOS** | Lightweight FOS app. | Attendance marking, distance travelled, PTP stock, callback escalation. |

### Tier 2 — Enterprise suites (win on integration depth)

- **Nucleus FinnOne Neo Collections** — **80+ APIs** into legacy core banking; offline field app.
- **Pennant Technologies** — field disposition codes, on-field receipting.
- **CarmaOne** — positions on **GenAI voice agents in 15+ Indian languages**, auto-generated Sec 138 / SARFAESI documents, Account Aggregator and GST data integration; claims 70% telecom OPEX reduction.

### Tier 3 — Global decisioning (sets the analyst narrative)

- **FICO Debt Manager** — strategy, segmentation, treatment optimisation. A decisioning layer, not an execution system.
- **TrueAccord** — digital-first, ML-driven email/SMS outreach, self-service negotiation.
- **Finvi / Katabat** — automation + AI scoring + multi-channel engagement.

**Structural observation:** every Tier 1 and Tier 2 vendor sells a *waterfall* — digital → tele-calling
→ field — with field as one module. TIQCollect is field-only. That is simultaneously the source of its
depth advantage and its most serious commercial constraint (see §4.7 and §6).

---

## 2. Where TIQCollect is genuinely ahead

These are real, verifiable in the tree, and **not marketed by any competitor above.**

### 2.1 Decision auditability

`AllocationRun` / `AllocationDecision` persist *why* every case landed with every agent, including the
full score breakdown, the hard gates evaluated, and — since 2026-09-08 — the exploration propensity,
seed and eligible-agent count. Competitors ship a black box labelled "AI allocation." Under the RBI
conduct rules taking effect 2026-10-01 (§5), an explainable allocation record stops being a
nice-to-have.

### 2.2 Model governance that would survive a model-risk committee

`ml/pipeline/` implements WOE/IV binning → IV filter → correlation → VIF → SFS → coefficient sign
check → KS/Gini/decile/PSI/calibration gates, writes a generated `MODEL_DEVELOPMENT.html` from the
artifact rather than from the training run, and commits the bundle. The gates are **two-sided**:
`gini_suspicious = 0.60` rejects a model for being *too strong*, because on a book like this that is
almost always leakage. That gate exists because the repo had already produced and reported a
ROC-AUC 1.0000 model on a 40-row test set with nothing able to flag it.

**In the Indian collections category this is close to unique.** Competitors publish a recovery-uplift
percentage. TIQCollect can hand a bank's model-risk function an artifact.

### 2.3 Causal experiment design

Epsilon-greedy exploration at 10%, with `exploration`, `exploration_propensity`,
`exploration_from_agent`, `exploration_n_eligible` and `exploration_seed` recorded per decision,
seeded from the plan date so a re-plan is reproducible. Nobody in this category runs a randomised
slice. Every competitor's agent-fit model is fitted on data their own allocator generated, and no
propensity weighting removes a confound that was never broken.

The cost is **measured, not assumed**: +14.3% mean base-to-case travel at ε=10%, BLOCKED set
unchanged, caseloads exact. The honest framing in the repo — that the +0.03% forecast movement is
evidence the allocator *cannot currently see agent effects*, not evidence the experiment is free — is
the kind of thing that reads well in diligence.

### 2.4 Evidence-based fraud detection

`services/fraud_service.py`: seven finding types over evidence already captured — impossible travel,
overlapping visits, photo-location mismatch, duplicate photos, short visits, far-from-customer, trail
contradiction. Manager review; verdicts stored as future training labels.

**Fake visits and GPS spoofing are the single largest operational loss in FOS collections, and no
competitor in the set above sells detection for it.** This is the most under-marketed asset in the
product.

### 2.5 Routing that is actually routing

OSRM road matrix + OR-Tools, a real multi-vehicle CVRPTW (`plan_fleet`), **RBI contact hours and
borrower preference applied as genuine time windows**, per-leg seconds and metres persisted rather
than recomputed as Haversine, `route_source` recorded so "OSRM answered" and "we guessed" stay
distinguishable, and 02:00 `beat_reconciliation` comparing the planned beat against the GPS trail
with NULL left where there is no evidence. Determinism was chosen over search quality and the
trade-off is written down.

Dista sells "intelligent beat plans." TIQCollect has a solver, and the gap between those is large.

### 2.6 Secondary strengths worth naming in a deck

- Borrower-OTP-verified payments with unique receipt numbers, Razorpay UPI QR, SMS + WhatsApp receipts.
- Visit audio captured for **both** agent and borrower, stored, transcribed (Whisper seam, visibly
  debugged against real mic audio rather than clean test files).
- LLM behind one seam (`core/llm.py`) with classified failures, caching, per-purpose counters, and an
  `ai_generated` flag that prevents a fallback being passed off as AI. The discipline that nothing
  hand-weighted may present itself as a model (`is_modelled` travelling on the object) is a
  credibility asset in a market where everything is labelled AI.

---

## 3. Where TIQCollect is at parity

- Case allocation with proximity — everyone has it; TIQCollect's is better founded (Hungarian solve
  over a case × capacity-slot cost matrix behind five hard gates) but the buyer cannot see the
  difference in a demo.
- Visit logging with GPS and photo evidence.
- Digital receipts and UPI payment capture.
- Agent live tracking and manager dashboards.
- DPD-bucket and agent-level analytics.

---

## 4. Where TIQCollect is behind

Ranked by how quickly the gap ends an evaluation.

### 4.1 Offline-first — hard disqualifier

Verified absent: no service worker, no IndexedDB, no outbox. An agent cannot complete a visit without
signal. CG Collect, FinnOne Neo, goCollect and every MFI-focused vendor advertise offline as a
headline. Rural, MFI and Tier-3 pilots fail on day one without it.

### 4.2 Multilingual — hard disqualifier

Zero i18n. CG Collect ships **22+ Indian vernacular languages plus Bahasa**; CarmaOne markets 15+.
Field collection agents in Tier-2/3 India do not operate in English. This blocks the exact buyer
segment the product is designed for.

### 4.3 Cash management and deposit reconciliation

No cash-in-hand balance, no per-agent collection limit, no deposit-centre handover, no bank
reconciliation, no cash-in-transit exposure view. CG Collect has deposit-centre OTP verification and
manager-set per-agent collection limits; goCollect markets cash-deposition tracking; Vymo describes
guided bank handover with the ERP/LMS updated automatically once a deposit is confirmed.

**Cash leakage is the largest financial risk a collections head carries.** This is asked in the first
meeting, and TIQCollect currently has no answer.

### 4.4 Incentive / payout engine

Absent. Vymo markets integrated incentive management as core; specialist products (WonderLend
IncentiHub) exist purely for this. It is the highest-stickiness feature in the category because it
touches agent pay — once payouts run through a system, it does not get replaced.

### 4.5 Organisational hierarchy and agency management

Tenancy is manager → agent. There is no agency, branch, cluster or region entity, no DRA-certificate
register, no agency SLA or scorecard. A mid-size NBFC runs 50–200 collection agencies. This is also
the root cause of open issue 8 (analytics have only three dimensions: agent, DPD bucket, month) even
though `Customer.city`, `Loan.loan_type` and `Loan.branch_code` are already on the models and
populated.

### 4.6 Legal and notice workflow

Absent. This is Credgenics' moat: Sec 138, SARFAESI, NCLT, arbitration, Lok Adalat, digital notice
delivery over email/WhatsApp, and — critically for a *field* product — CG Collect lets the agent
**show the legal notice to the borrower on screen during the visit.** Bucket 3+/NPA portfolios are
effectively unsellable without this.

### 4.7 Field-only — the TAM constraint

No tele-calling queue, no dialer, no campaign engine, no digital-first waterfall. Communications are
agent-triggered one-offs. Every competitor sells digital → tele → field as one funnel with shared
strategy. Being field-only caps ACV and positions TIQCollect as *a module inside someone else's
platform* rather than the platform. This is the single largest commercial constraint in the product.

### 4.8 Integration surface

Ingest is a nightly bank CSV plus one Command Center contract. FinnOne Neo cites **80+ APIs**. There
is no documented integration kit, no webhook catalogue, no SFTP/API allocation feed with dedupe and
reconciliation, no LMS write-back contract. Six-week integrations lose to two-week integrations.

### 4.9 Skip tracing and address quality

No address verification, no address cleansing, no phone-quality scoring, no alternative-data trace.
Dista sells address cleansing as a headline; Spocto (Yubi) is built on alternative-data tracing of
untraceable borrowers.

This is also **the fix for TIQCollect's own failed model**: `contact_risk` reaches Gini 0.190 against
a 0.25 floor precisely because the product stores no address or phone quality and the panel has no
time-of-day. The repo already diagnosed this correctly as a data problem rather than a model problem —
address quality is the data.

### 4.10 Real-time, repossession, voice AI

- **Push:** verified — no WebSocket, no SSE, polling only. "Live Recovery Command Center" is a stretch.
- **Repossession / asset custody** for secured books (auto, CV, gold): seizure workflow, custody chain,
  yard, valuation, auction. Absent.
- **Voice AI:** CarmaOne markets GenAI voice agents in 15+ languages; the industry cites up to 80%
  contact-cost reduction and 7× right-party contact. TIQCollect has the LLM and Whisper seams already
  and could reach this cheaply — but should not, before §4.1 and §4.2 (see §7).

---

## 5. The regulatory wedge — and TIQCollect is ~70% built for it

**RBI's draft Responsible Business Conduct / recovery-agent directions take effect 2026-10-01.**
The IIBF DRA de-empanelment deadline (2026-07-01) has already passed.

### What the rules require, per agent and per contact

| Requirement | TIQCollect today |
|---|---|
| Recovery agent **IIBF DRA-certified before deployment** | ❌ No certificate register, no expiry, no deployment block |
| **All recovery calls recorded**, retained for RBI inspection | 🟡 Visit audio captured and stored; call recording not covered; **retention policy unverified** |
| **Physical visit log: date, time, location, outcome** | ✅ Best-in-class — GPS + photo + signature + audio + outcome |
| Agent **carries written authorisation**, identifies self, gives grievance contact | ❌ `create_agent_verify_token` mints the QR; **no `/verify-agent` route exists** (open issue 2) — the anti-impersonation control is inert |
| Contact only **08:00–19:00** | ✅ Enforced, and now applied as routing time windows |
| No family (unless guarantor), no workplace without consent, no public humiliation | 🟡 DNC and consent enforced; the rest is unmodelled |
| **Board-approved recovery policy**, configurable | ❌ Thresholds hardcoded (open issue 11) |
| **Published list of recovery agencies** | ❌ No agency entity (§4.5) |
| **Data minimisation** — name, contact, outstanding, security address only | ❌ Agents see the full Borrower 360 |
| Grievance capture and redressal tracking | ❌ Absent |
| Evidence trail per complaint | 🟡 Audit log emits **8 of 22** declared actions (open issue 3); `VISIT_RECORDED`, `PTP_SET`, `CASE_ASSIGNED`, `DATA_EXPORT` are declared and never written |

### Why this is the opportunity

**Nobody in the competitive set sells RBI Responsible Business Conduct compliance as an evidenced
product.** They sell hard-coded calling windows and DND checks — the easy half. The hard half is
proving, per borrower complaint, exactly who contacted whom, when, from where, under what
authorisation, and with what evidence.

TIQCollect already captures the hardest inputs (GPS, photo, signature, **audio of both parties**,
geofence, OTP-verified payment). The missing pieces are cheap and mostly scaffolded:

1. **DRA certificate register wired as a sixth allocator hard gate.** An uncertified or lapsed agent
   becomes un-allocatable *by construction*. The five existing gates already live in one place
   (`eligible_agents`, built as the cost matrix is constructed) — this is one more predicate there,
   and it inherits the existing test that re-checks every gate independently on every explored case.
2. **`/verify-agent`** — closes open issue 2 and satisfies the identification requirement in the same
   change. The token minting already exists.
3. **Audit-trail completion** — closes open issue 3. The 14 unwritten action types are the difference
   between claiming an evidence trail and having one.
4. **Compliance Evidence Pack**: one signed export per borrower complaint containing every contact,
   its channel, timestamp, agent identity and DRA certificate, GPS, photo, audio, outcome and
   authorisation reference. This is the sellable artifact, and competitors' thinner field evidence
   cannot produce it.
5. **Check the retention interaction now.** The 03:00 location-retention sweep and the recording
   lifecycle must not delete evidence the directions require retained (six-month retention is cited in
   the DRA guidance). `beat_reconciliation` was already scheduled at 02:00 specifically to run before
   that sweep — the same reasoning has to be applied to compliance evidence.

---

## 6. The investor view

### What is settled

The category is proven and funded. Credgenics at a $340M valuation on ₹226 Cr FY25 revenue growing
41% derisks the market question — and simultaneously means "we do collections software" is not a
story. Feature-count parity with Credgenics is not reachable and not worth attempting.

### What is actually differentiated

The defensible narrative is **measurable recovery lift with governance evidence**, not feature
breadth. TIQCollect has the beginnings of exactly this and the competitors do not:

- an artifact-backed model with **two-sided** gates, including one that rejects suspiciously strong
  models;
- a drift monitor with retrain triggers on PSI > 0.25 or a >20% relative Gini drop;
- a **randomised exploration slice** with propensity logged — the only unconfounded evidence anyone in
  this market is collecting;
- an allocator whose every decision is persisted with its score breakdown;
- a documented history of finding defects *by measuring the live book* rather than by reading code —
  the `spec_match` term that had never once been 1.0; the 77.2% of snapshots naming the wrong case;
  the value transform using 3.9% of its available range.

### The one thing missing

**Every number is synthetic, and the repo says so — correctly, in its own `SYNTHETIC_WARNING` fields.**
+28.0% realised recovery across 8 of 8 seeds, Brier 0.2172 → 0.1435, forecast bias +94% → +9.5%,
`recovery_risk` out-of-time Gini 0.5149 — all of it describes the allocator's arithmetic under a
calibrated probability on a simulated book.

One design-partner lender, one quarter, and the exploration machinery that already exists converts
"believable pipeline" into "measured lift on a real book." **That is the highest-value action
available and it requires no new code.** It is also the only route to a genuinely calibrated recovery
probability before the 90-day labels mature on 2026-11-22.

### What technical diligence will find in ten minutes

- **CI is red** — 18 `react-hooks/set-state-in-effect` errors, the only failing step.
- **Zero frontend test tooling** — no vitest, jest, playwright or cypress, across six pages of
  1.2k–2.3k lines each.
- **`manager.py`: 3,967 lines, 33 routes, 114 `db.query()` calls in the route layer** while a working
  service layer exists and is used by every agent flow. The repo's own note is the right one: this is
  *why* the tenancy leaks happened — there is no single place where "the agents this manager owns" is
  defined, so it gets retyped.
- **Two competing schema authorities** — eight Alembic migrations, and `seed_data.py` doing
  `drop_all` + `create_all` while never touching `alembic_version`.

None of these are hard to fix, and all of them are cheaper to fix before a diligence process than
during one.

### Positioning recommendation

Do not position as a Credgenics competitor. Position as **the field-execution and field-evidence layer
that plugs into an existing collections stack**. Credgenics, Spocto and the enterprise suites own
digital and tele; nobody's *field* evidence, allocation science or fraud detection is as good. That is
a wedge-and-partnership motion rather than a head-on one, and it is consistent with the fact that
Command Center already consumes `/api/v1/manager/*` through a per-agency service login.

---

## 7. Recommended sequence

### P0 — deal-blockers (nothing else matters until these ship)

1. **Offline-first**: service worker + IndexedDB + outbox, with conflict handling on visit submission.
2. **i18n**: framework + Hindi first, then five more. Pairs with P1.5 below to actually pay off.
3. **Cash management**: cash-in-hand balance, per-agent collection limit, deposit handover with OTP,
   bank reconciliation, cash-in-transit exposure on the manager dashboard.
4. **DRA certificate register as a sixth allocator hard gate** + **`/verify-agent`** (closes issue 2).

### P1 — differentiators that are cheap because the machinery already exists

5. **Structured extraction from speech** (feature 2). `core/llm.py` already supports
   `response_format: json_object`; the recordings are already captured and transcribed. Extract
   disposition, PTP amount and PTP date instead of making the agent type them. This is the
   highest-value single feature on the list *and* it is what makes multilingual pay off — the agent
   speaks Tamil, the system writes structured English.
6. **Compliance Evidence Pack** + audit-trail completion (closes issue 3) + unified case timeline
   (feature 16). Verify the retention interaction while doing it.
7. **Recovery Risk Radar** (feature 13) — pure frontend, every input already on the wire. Cheapest
   item on the entire roadmap.
8. **Next-best-action** (feature 4) as a **deterministic policy over the five existing scores**, not
   an LLM. TIQCollect has five scoring layers and no decision layer; a transparent policy table is
   both more defensible and more demoable than a model here.

### P2 — TAM expanders

9. Tele-calling queue + campaign engine — converts a field module into a platform.
10. Legal / notice workflow, with in-app notice display during the visit.
11. Agency / branch / cluster hierarchy + incentive engine (unblocks analytics issue 8 as a side
    effect).
12. Address quality and skip tracing — also the only real path to fixing `contact_risk`.
13. Repossession and asset custody for secured books.

### Explicitly do not

- **Do not chase voice-AI bots before offline and i18n.** It demos well and loses evaluations that
  offline would have won.
- **Do not train more models before real labels mature** (2026-11-22 for 90-day). The pipeline is
  built; what it lacks is outcomes, and no amount of synthetic work supplies them.
- **Do not wire `plan_fleet` into the nightly run** without its own measured release. It changes which
  agent gets which case, which is a behavioural change — the repo's own reasoning, and it is right.
- **Do not fix the 18 lint errors by suppressing them.** They sit on six pages with no test coverage
  at all; the restructure is behaviour-affecting. Test tooling first (issue 7), then the fix.

---

## Sources

- [Credgenics — CG Collect field collections](https://www.credgenics.com/cg-collect-field-debt-collections)
- [Credgenics secures $50m Series B — FinTech Futures](https://www.fintechfutures.com/fintech-start-ups/indian-fintech-start-up-credgenics-secures-50m-series-b-funding)
- [Credgenics funding, revenue and investors — Inc42](https://inc42.com/company/credgenics/)
- [Top 7 debt collection software in India for 2026 — Dista](https://dista.ai/blog/best-debt-collection-software/)
- [Dista — field collections solution](https://dista.ai/solutions/field-collections/)
- [Vymo launches CollectIQ — PR Newswire](https://www.prnewswire.com/in/news-releases/vymo-launches-collectiq-to-transform-debt-collections-for-financial-institutions-302447831.html)
- [Top 10 debt collection software India 2026 — CarmaOne](https://www.carmaone.ai/blog/top-10-debt-collection-software-india-2026)
- [Collbox — FOS field collection system](https://collbox.in/fos)
- [RBI loan recovery rules 2026, device-lock and agent guidelines — CorpLawUpdates](https://www.corplawupdates.in/updates/rbi-loan-recovery-rules-2026-device-lock-guidelines)
- [RBI loan recovery agent rules 2026 — SolvLegal](https://solvlegal.com/blogs/rbi-loan-recovery-agent-rules-2026-india/)
- [DRA code of conduct 2026 — IIBF](https://iibf.store/blog/dra-code-of-conduct)
- [RBI Digital Lending Directions 2025 — Lawrbit](https://www.lawrbit.com/article/reserve-bank-of-india-digital-lending-directions-2025/)
- [FinnOne Neo Collections — Nucleus Software](https://www.nucleussoftware.com/finnone-neo/collections/)
- [Mobicule — debt collection](https://www.mobicule.com/debt-collection/)
- [Credility goCollect](https://credility.in/goCollect)
- [Collections incentive management — WonderLend IncentiHub](https://wonderlendhubs.com/solutions/collections-incentive-management-software/)
- [Outbound voice AI compliance in India — Exotel](https://exotel.com/blog/outbound-voice-ai-compliance/)
