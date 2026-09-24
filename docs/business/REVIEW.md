# Business review: TIQCollect on an ordinary working day

*Business lead, 2026-09-24. For the owner. Evidence is in [JOURNEYS.md](JOURNEYS.md) (walked and timed), [PRIORITIES.md](PRIORITIES.md) (all 126 tasks sorted) and [ECONOMICS.md](ECONOMICS.md) (cost and price). Everything here was observed in the running app, read from the demo database (read-only), or checked in code; assumptions are labelled. **There is no production traffic. Every number from the app is demo or synthetic.***

## 1. What matters

1. **Critical: money and evidence have holes a bank auditor will find in minutes.**
   - UPI shows "Payment received" by itself after 10 seconds and waives the transaction ID. The QR pays one hardcoded personal VPA for every tenant.
   - The evidence photo failed to upload and the agent was still told "Visit Recorded". **0 of 2,404** demo visits carry a photo, signature or audio.
   - The *required* escalation notes, witness details, document uploads, cheque photo and check-in selfie are captured on the phone and never saved.
   - The check-in screen always says "Location: Mumbai" and "Liveness check: Passed".

   The product's pitch is verified money and evidence. Fixing these is days of work, and it comes first.
2. **Critical: the field app does not survive a normal Indian field day.** No signal means no work: "Case not found", and a reload gives Chrome's offline page. A wrong map pin locks the visit, and case detail offers no way out. The app is English only. GPS runs flat out from login.
3. **Critical: as coded, every agent loses money.** SMS on Twilio costs ₹4,100–4,500 per agent per month against a seat worth about ₹900 (reconciled with the lead developer's cost model, ECONOMICS.md §0). An Indian DLT gateway brings the whole variable cost to about ₹480–550. Three free public map services are in production use against their policies.
4. **Critical: nine day-one buyer questions have no answer in the plan.** Hindi, DPDP consent and retention, India data residency (every visit sends borrower names and notes to a US LLM), DRA certificates, cash custody and deposits, the DLT SMS route, grievances, bank-format exports and SSO. See PRIORITIES.md, "Missing".
5. **Important: the plan builds depth where there is no data yet.** Monte Carlo, forecasting, an AI agent studio and ten more models are scheduled or in progress while **not one real outcome exists**. The live model reads a borrower-stance input the app never records.
6. **Important: the manager's screens undercut themselves.**
   - The AI briefing quotes a ₹0.9 lakh target under a card showing ₹42.9 lakh.
   - The live map says "15 of 15 reporting" when 13 of them last reported between 3 hours and 16 days ago.
   - The Compliance page says "Enforced" on three rules whose own text says pending or best-effort.
   - The day's "target" makes every day "well behind".
   - Nothing is labelled synthetic.
7. **Important: an agency cannot add or remove an agent.** No screen or API exists. The agency admin logs into an empty manager view with another pool's 597 unassigned cases on it.
8. **Good news worth selling:**
   - The borrower-OTP flow is well designed.
   - "Not met" takes 3–4 taps.
   - The 100 m fence and contact hours are enforced on the server.
   - Every allocation decision is explained.
   - Typed notes survive a dropped signal.
   - The simulator (phone beside manager, events in under 2 s) is a strong demo.

## 2. Real-usage reality check

**What the demo data is.** Measured read-only on the demo database:
- **99.75% of the 2,404 visits were inserted by scripts**, not by the app. Only 6 carry the live app's signature.
- Visits exist on only 10 days of the last month.
- **Only 14 users have ever logged in.** Daily active users are 0–4 agents and 1–2 managers.
- There are 67 GPS pings in total, so the live map is essentially empty.
- There is no attendance table and no product-analytics table.

**What it can still tell us** (demo, used as a sanity check):

| | Demo | Realistic for urban NCR FOS *(assumption)* |
|---|---|---|
| Visits per agent per day | p50 11, p90 14, max 15 (the cap) on feed days | 10–15 urban, 6–10 semi-urban |
| Planned route | 15 stops, 23–79 km, 4.5–6 h | 12–15 stops, 30–60 km on a two-wheeler |
| Cases never visited | 42.5% of cases | High early in a placement; a buyer will ask |
| Promise kept, of matured PTPs | 36% fully, 48% incl. partial | 30–50% is the usual range |
| Promise size | p50 ₹11.1K (p10 ₹2.2K, p90 ₹31.9K) | Plausible for personal/consumer loans |
| Payment size | p50 ₹9.1K; mode mix UPI 25%, cash 21%, NEFT 20%, cheque 15% | Plausible; cash is often higher in semi-urban |
| Payments per agent per day | p50 5 | 2–4 *(flattering in the demo)* |

**Demo defects a buyer would see** (sent to the dev team):
- Gender is blank on all 18 agents, so **926 allocation decisions are blocked** as "needs a female agent".
- 792 visits outside 08:00–19:00 are flagged "within contact hours".
- 400 visits more than 100 m from the address are flagged "geo-verified".
- There are only 602 distinct names across 1,574 customers ("Nikhil Sharma" ×15).
- Case numbers look fake (`DAILY…`).
- Case target equals the EMI on every case.

**Product analytics: none exist.** The audit log is the only usage trail. Instrument these ten events first. Store them in our own table (data residency) with tenant, agent, device, app version and network type on each:

1. `session_start`
2. `visit_form_opened`
3. `visit_submitted`: time in form, outcome, evidence counts, geofence distance
4. `visit_submit_failed`: offline / timeout / geofence / server
5. `geofence_blocked`: distance
6. `otp_sent` / `otp_verified` / `otp_failed`: delivery status, seconds to verify
7. `evidence_upload_failed`
8. `offline_period`: start, end, queued items
9. `case_reassigned`: count, reason
10. `export_downloaded`

These ten answer the pilot's real questions: *is the app used, where do agents give up, and is the evidence real?*

## 3. Decisions for the owner

| # | Decision | Recommendation | Why |
|---|---|---|---|
| a | **ML-1: the model reads a "borrower stance" nobody records.** A: stance picker (+1 tap). B: roll back the model. C: measure first | **A, done so the agent's tap count does not change, plus C in parallel. And for any pilot, run the model in shadow until real outcomes exist.** | The visit form already has a "Borrower Tone" row (Cooperative / Neutral / Hostile, marked "Recommended") in the right place. Today it is only written into the notes text. **Replace it with the stance row** (Will pay / May pay / No commitment / Refuses / Hardship / Dispute), with no default. Pre-select it where the outcome already says it: Refuse to Pay → Refuses; Dispute → Dispute; a hardship reason → Hardship. Most visits then cost **0 extra taps**, and the ambiguous ones cost 1. Add the same row to the call log; the call-round plan needs it anyway. **Lead dev to check** whether a stance pre-filled from the outcome is statistically what the model was trained on (a noisy, independent reading); that is exactly C. Rolling back (B) buys nothing commercially: both models are trained on synthetic borrowers, and no real money moves on either today. |
| b | **Demo names: "Anantya Bank" and `.test` e-mail domains** | **Decided by the owner, 2026-09-24: keep `.test`; the lender becomes a fictional "… Finance Ltd" (no "Bank", not "Anantya").** Name shortlist and agency check in §6. | `.test` is reserved and can never deliver mail. A fictional "Bank" implies an RBI banking licence a demo tenant does not hold; most FOS-heavy lenders are NBFCs anyway. |
| c | **Pilot shape** | **One lender, one agency, one dedicated India deployment, ≤100 agents, 90 days, paid** | Reachable after wave 1: about 50–114 developer-days, roughly 4–8 weeks with three developers. A bank-led, multi-agency pilot needs wave 2 as well, about 7–15 more weeks. Pilot data is also the only route to real model outcomes. |
| d | **Pause P4, P5 (beyond F01) and P6 (beyond H14)**, including the Monte Carlo and backtest work in progress | Pause | They need real history, and no buyer purchases on them. The freed capacity goes to wave 1. |
| e | **Pricing model** | Platform fee per lender (₹1.5 lakh a month) + ₹900 per active agent + messaging at cost; ₹6 lakh paid pilot | See ECONOMICS.md. Not % of recovery. |
| f | **Embedded Collections mode** (B15, A15) | Owner's call | Keep it only if fieldops.transorg.ai stays a product. It costs about a week and adds no standalone buyer value. |
| g | **Remove "RBI Compliant" from the login page** | Remove | RBI certifies no software; a bank's legal team will ask what it rests on. |

## 4. Demo and pilot readiness

**A bank collections head's first 10 minutes, today:**
1. The login page (demo buttons are being removed; "RBI Compliant · © 2025" in the footer).
2. The Overview: "₹0 of ₹42.9L, well behind" at 3 pm, with an AI briefing that disagrees with it.
3. The live map: "15 of 15 reporting", mostly days stale.
4. A case: "ABC Bank", `DAILY2026…`, paise on every amount, and a locked Record Visit.
5. Evidence: no photos anywhere.
6. The Compliance page: "Enforced" labels that its own fine print contradicts.
7. There is no bank view at all.

**What breaks the story, and the fix size:**

| Breaker | Fix | Size |
|---|---|---|
| Placeholder bank and agency names, fake case numbers, 15 people with the same name | B18 fixture, B16 roster | S + L |
| No evidence photos in the book | EVIDENCE-1 fix, plus specimen photos in the fixture | S + in B16 |
| Synthetic numbers without a label | A "Demo data" chip on every KPI surface | S |
| AI briefing contradicting its card | Feed the briefing the card's numbers (C01 later) | S |
| "Well behind" every day | Target = cases due today × expected rate, or show a pace chart instead | S |
| Stale "15 of 15 reporting" | Count agents reporting within 15 minutes | S |
| "Enforced" overclaims | Three labels changed to "Partial" | S |
| A frozen, date-anchored snapshot ("paused" look) | Run the daily feed before each demo, or refresh the fixture | S |
| Slow first load on the dev server | Demo from a production build | S |
| Manager frame at 45% in the simulator | Second screen | 0 |

**Shortest path to a paid pilot:**
1. **This week:** fix payment and evidence honesty (N1). Turn off real texts for demo tenants (B22). Remove "ABC Bank" and "RBI Compliant". Add the demo-data chip.
2. **Weeks 1–6:** wave 1. Offline outbox, Hindi, the Indian SMS gateway, DRA gate, minimal cash deposit, DPDP basics, India hosting, agent create / suspend / reset, password reset, bank-file quarantine, Excel exports.
3. **In parallel, not code:** VAPT by a CERT-In empanelled auditor, a DPA template, a support SLA, and a two-page security note for bank infosec.
4. **Sell:** ₹6 lakh for 90 days, 100 agents, one region.

**Success criteria to agree up front (all measurable with the ten events):**
- ≥85% of agents logging ≥8 visits a day by week 4.
- ≥95% of visits carrying GPS plus a photo.
- Median PTP visit logged in under 2 minutes.
- Zero payments recorded without OTP or signature.
- PTP keep rate and collection efficiency against the lender's own baseline for the same buckets.

## 5. Passed to the dev team (via the coordinator; not re-argued here)

- **PAY-1/2:** UPI auto-"received"; hardcoded VPA.
- **EVIDENCE-1:** upload failure reported as success.
- **ALLOC-G:** blank gender blocks 926 decisions.
- **DATA-R:** contact-hour and geofence flags wrong in the fixture.
- **Also found, for the lead's triage:**
  - captured-but-discarded fields (escalation notes, witness, documents, cheque photo, selfie);
  - hardcoded check-in labels;
  - 15 s client timeout against a 20 s inline LLM call on visit submit (duplicate visits);
  - double transcription;
  - Nominatim every 40 m;
  - GPS from login;
  - post-visit SMS discloses the outstanding amount, including after DECEASED;
  - the "unverified" copy contradicts the server's 403.

## 6. Demo tenant naming — collision check (2026-09-24)

Per the owner's decision (§3b): a fictional lender "… Finance Ltd", `.test` emails kept, and a check on the 9 existing agency names. **Method and its limit:** web search plus targeted MCA/company-registry lookups, checked today. The RBi NBFC list (`rbi.org.in/Scripts/BS_NBFCList.aspx`) is a downloadable Excel/PDF, not a searchable database, so it was **not** cross-checked line by line — that is a formal step for legal before this goes live, not something a web search substitutes for. No trademark-registry search was run (India's IP India portal has no public API); a name that clears web search can still be a registered mark. Treat every "clear" below as "nothing found today", not as a legal clearance.

### Lender name candidates ("… Finance Ltd")

| Candidate | Checked | Result | Verdict |
|---|---|---|---|
| Meridian Trust Finance Ltd (the current name, "Bank" dropped) | 2026-09-24 | **Collides.** "Meridian Trust Finance" is a real, NCUA-insured US credit union's trade name | **Reject** |
| Sanchay Finance Ltd | 2026-09-24 | **Collides.** Sanchay Finvest Ltd is a real, BSE-listed Indian NBFC (ticker SANCF) | **Reject** |
| Kavach Finance Ltd | 2026-09-24 | **Collides.** Two real, active Indian finance companies: Kavach Financial Services Pvt Ltd (Gurugram) and Kavach (India) Finance and Investment Co Ltd | **Reject** |
| Vindhya Finance Ltd | 2026-09-24 | **Collides.** Vindhya G Micro Finance Pvt Ltd is a real, active Indian NBFC | **Reject** |
| Tunga Finance Ltd | 2026-09-24 | **Borderline.** No exact match, but Tunga Investments / Tunga Share Investments are real, active Indian finance-sector firms with the same first word | **Reject (same sector, one word off)** |
| **Girivan Finance Ltd** | 2026-09-24 | **Clear.** No finance-sector company found; only an unrelated Pune real-estate development ("Girivan") | **Recommend — my pick** |
| **Satpura Finance Ltd** | 2026-09-24 | **Clear.** No finance-sector "Satpura Finance" found; other "Satpura" companies (bio-fertilisers, transport, renewables, foods) are unrelated sectors | **Recommend — second choice** |

**My pick: Girivan Finance Ltd.** Zero same-sector hits at all, against Satpura's several unrelated-but-noisy namesakes. Both are safe to build on for a demo; only a formal trademark and MCA search (legal, not me) should gate the final choice before anything ships externally.

### The 9 agency names (Appendix C, `docs/DATA-MODEL-V2.md`)

| Agency | Checked | Result | Verdict |
|---|---|---|---|
| Aravalli Field Services Pvt. Ltd. | 2026-09-24 | Clear — no matching company found | **Pass** |
| Sarthak Recovery Services LLP | 2026-09-24 | Weak partial: "Sarthak Sansthan", an unrelated Bhopal NGO. Different sector, different legal form | **Pass** |
| Rajputana Credit Solutions Pvt. Ltd. | 2026-09-24 | Clear — no matching company found | **Pass** |
| Awadh Field Collections Pvt. Ltd. | 2026-09-24 | Weak partial: an Instagram handle `@awadh_collection__`, not a registered company | **Pass** |
| **Konkan Asset Recovery Pvt. Ltd.** | 2026-09-24 | **Collides.** Konkan Credit and Collection Services Pvt Ltd is a real, active agency in the *same region* (Mumbai/Pune/Goa — the demo agency's own coverage) and the *same trade* (collections/verification for banks). The closest collision found in this whole check | **Rename before B16** |
| Sabarmati Collection Services LLP | 2026-09-24 | "Sabarmati" is a real place name (an Ahmedabad locality); no matching *company* found | **Pass** |
| Deccan Resolve Associates Pvt. Ltd. | 2026-09-24 | Clear — no matching company found | **Pass** |
| Coromandel Recovery Partners LLP | 2026-09-24 | Weak partial: Coromandel International is real but is agrochemicals, not collections. Different sector, different suffix | **Pass** |
| Hooghly Credit Management Pvt. Ltd. | 2026-09-24 | Clear — no matching company found | **Pass** |

**One rename needed: Konkan Asset Recovery Pvt. Ltd.** Same region word, same industry, overlapping cities — the one name in the roster that a Mumbai-based buyer could plausibly mistake for a real competitor. Replacement, screened 2026-09-24: **Sahyadri Field Recovery Pvt. Ltd.** (Sahyadri is the same mountain range Konkan sits below, so the regional flavour survives). Web search finds only unrelated Sahyadri entities in other sectors and forms — industries (building materials), hospitals, an agri-produce platform, and several member-owned rural co-operative *credit societies* (a different legal form and business from a Pvt Ltd collections agency, the same distance as the Sarthak/Coromandel "weak partial, pass" rows above). No Pvt Ltd or LLP collections/recovery agency of that name found. **Pass — adopt as the replacement.**

### Second demo tenant — replacing "Northfield Small Finance Bank Ltd"

Northfield Bank is a real US bank, and "… Bank" also breaks the owner's no-banking-licence rule for a fictional demo tenant. Candidates for the second lender, "… Finance Ltd", checked 2026-09-24:

| Candidate | Result | Verdict |
|---|---|---|
| Shivalik Finance Ltd | **Collides.** Shivalik Small Finance Bank is a large, real, well-known regulated Indian bank | **Reject** |
| Nilgiri Finance Ltd | **Collides (close).** Nilgiri Financial Consultants Ltd is a real, active Delhi company in the finance sector | **Reject** |
| Malwa Finance Ltd | **Collides.** Malwa Finance Pvt Ltd is a real, active company (since 1958), same sector, same name | **Reject** |
| **Kumaon Finance Ltd** | **Clear.** No finance-sector company found; other "Kumaon" entities (exports, a state tourism corporation, general enterprises) are unrelated sectors | **Recommend** |

**Recommend Kumaon Finance Ltd** for the second tenant.

### Second tenant's agency — clash found by 43

After the Konkan rename, Appendix C would carry two "Sahyadri…" agencies (the second tenant already has "Sahyadri Recovery Desk LLP", Pune) — confusing in the tenant-isolation demo, whose whole point is that two tenants' data is visibly separate. Renaming the Kumaon-tenant's agency to fit its own region, screened 2026-09-24:

| Candidate | Result | Verdict |
|---|---|---|
| Nainital Recovery Desk LLP (43's suggestion) | **Too close.** Nainital Bank is a large, well-known real regional bank, and it publishes its own "List of Recovery Agents" page — a "Nainital Recovery Desk" reads like it could be that bank's own vendor | **Reject** |
| **Almora Recovery Desk LLP** | **Clear.** No finance or collections company found; other "Almora" entities (a crypto VC fund, a skincare brand) are unrelated sectors. Almora is a Kumaon-division district, same regional fit as Nainital | **Recommend** |

**Recommend Almora Recovery Desk LLP** for Kumaon Finance Ltd's agency, replacing "Sahyadri Recovery Desk LLP".

## Method and footprint

- **Walks:** the main tree's running app, in headless Chrome with a low-end Android profile (360×800, 4× CPU, throttled 4G) and a 1440 px desktop.
- **Database:** read-only SELECTs.
- **Writes, labelled "business walkthrough":** one PTP visit and one PTP on case DAILY20260906C101 (agent EMP0006). Its submit re-optimised that agent's beat.
- **Not touched:** Docker, seeds, migrations and the test suite. SMS, WhatsApp and calls were off.
