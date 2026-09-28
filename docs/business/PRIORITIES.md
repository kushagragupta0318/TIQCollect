# Priorities, commercially: the task list sorted for a first paid pilot

*Business lead, 2026-09-24. Source: `docs/STANDALONE-TASKS.md`. Every branch holds **126** tasks (the brief said 127; B23 is mentioned on the claims board but not in the file). Effort figures come only from each task's size (S ≤ 1 day, M 2–5 days, L 1–2 weeks), read as developer-days. Technical sequencing is the lead developer's call; this document is the business view.*

## The call in one paragraph

The plan is a sound multi-tenant product plan, but its order follows the architecture, not the first sale. Of the 126 tasks, **58 are must-haves, 21 nice-to-have, 32 can wait, and 5 should be dropped** (2 need an owner decision; 8 are done). Nine things a bank or agency asks about on day one are **not in the plan at all**, starting with Hindi, offline-safe evidence, cash custody, DRA certificates and DPDP. The fastest route to revenue is a **paid pilot with one lender and one agency on a dedicated India deployment**. That needs **wave 1: 20 planned tasks plus 8 missing items, about 50–114 developer-days, roughly 4–8 weeks with three developers.** The bank portal and multi-agency features (wave 2, about 33–76 more working days) can be built during the pilot. Meanwhile P4 (Monte Carlo and forecasting), P5 (AI agent studio) and nearly all of P6 (ten new models) should pause **until real outcomes exist**; there is not one today.

| Class | Tasks | Effort (dev-days) | Meaning |
|---|---|---|---|
| Done | 8 | — | P0-01/02/03/05/06/07, B01, A10 (on d4's branch) |
| **Must, wave 1 (pilot)** | 20 + 8 new | 50–114 | Without it an agent cannot work a day, or a lender cannot sign a pilot |
| **Must, wave 2 (sell to banks)** | 38 + 3 new | 100–228 | Without it a bank's procurement or infosec says no to a production contract |
| Nice-to-have | 21 | 39–90 | Helps a demo or a user, but no deal depends on it |
| Defer | 32 | 93–203 | Right idea, wrong time: it needs real data, scale or a second tenant first |
| Drop (this stage) | 5 | 22–45 | No buyer will use it |
| Owner decides | 2 | 3–6 | B15 and A15: only needed if the embedded Collections mode stays |

---

## Missing from the plan: what a buyer asks on day one

| # | Item | Tag | Who asks, and why | My size |
|---|---|---|---|---|
| N1 | **Payment and evidence honesty.** Remove the UPI auto-"received" and the hardcoded VPA; make uploads fail loudly; stop the check-in screen claiming "Mumbai" and "Liveness passed"; save the escalation notes, witness details, documents and cheque photo the agent already captures | Critical | Every bank auditor. Evidence and verified money are the product's pitch, and today both have holes (JOURNEYS §1a) | M |
| N2 | **Hindi UI**, then 1–2 regional languages, plus Hindi borrower SMS templates | Critical | Every agency outside metros. Field agents do not work in English. Competitors ship 15–22 languages | L |
| N3 | **DPDP Act 2023 basics.** Consent/notice text for borrower contact and recordings; a retention schedule per data class (media is never deleted today, while GPS is deleted at 90 days); access, correction and erasure requests; a breach runbook; the agent sees only what the visit needs (RBI data minimisation); no tracking before check-in | Critical | Bank legal and infosec, in the first questionnaire | L |
| N4 | **India data residency, plus a per-tenant LLM switch.** Commit to India-region hosting. Every visit today sends the borrower's name, loan details and the agent's free-text notes to a US-hosted LLM (Groq, `ai_report_service.py`). Give each lender "AI off" or an India-hosted model | Critical | Bank infosec / RBI outsourcing rules | M |
| N5 | **DRA certificate register as an allocation gate.** An agent without a valid IIBF DRA certificate cannot be allocated a case | Critical | RBI recovery-agent rules; the IIBF deadline has passed. Cheap, because the allocator's hard gates already live in one place | M |
| N6 | **Cash custody.** Cash in hand per agent, a per-agent limit, a deposit handover with a receipt, and collected vs deposited reconciliation | Critical | Collections heads, in the first meeting. Cash is 21% of demo payments | L |
| N7 | **Indian DLT SMS gateway plus a per-lender message policy.** Twilio costs about 50× the domestic rate (ECONOMICS.md). The post-visit SMS discloses "Outstanding: Rs.X" to whoever holds the phone, including after a DECEASED visit | Critical | Unit economics and RBI third-party disclosure | M |
| N8 | **Grievance/complaint capture and closure**, with an evidence pack per complaint | Important | RBI Fair Practices; the plan's "disputes" is a table, not a workflow | M |
| N9 | **Bank-format exports.** Collections register, visit log with GPS and photo links, PTP register, attendance, receipts as PDF | Important | The bank's MIS team, from day one; E09 is broader and later | M |
| N10 | **SSO (OIDC/SAML) for bank users** | Important | Bank IT; TOTP (A08) alone will not pass for staff logins | M |
| N11 | **Ten product-analytics events** to our own table (REVIEW.md §2) | Important | Us: without them a pilot cannot prove adoption | M |
| — | Not code: a **VAPT report from a CERT-In empanelled auditor**, a data processing agreement, a BCP/DR statement and a support SLA | Critical for contract | Bank vendor-risk team; allow 2–4 weeks elapsed | external |

Deliberately **not** on this list: legal notices / SARFAESI, a tele-calling dialer, campaign engines, repossession, and an incentive engine. Each is real, but none blocks a first field pilot. Incentives are the stickiest of them; revisit after the pilot.

---

## The 126 tasks

Legend: **M1** = must, wave 1 · **M2** = must, wave 2 · **N** = nice-to-have · **Df** = defer · **Dr** = drop · **Dec** = owner decides · ✔ = done or in progress.

### P0: mobile simulator (8)
| Task | Size | Class | Why |
|---|---|---|---|
| P0-01/02/03/05/06/07 | — | ✔ | Built; the simulator is the best demo asset we have |
| P0-04 (open bits) | M | N | Sample camera images and an offline toggle. The offline toggle only matters once I02 exists; do it with I02 |
| P0-08 | M | M1 | The OTP payment journey must be tested end to end. The fake-UPI defect is what happens when it isn't |

### P1-B: data platform (22)
| Task | Size | Class | Why |
|---|---|---|---|
| B01 | M | ✔ | |
| B02 | M | M2 | Money as `NUMERIC` and real dates. Banks' auditors check rounding, and today paise leak onto screens ("₹14,56,045.3") |
| B03 | M | M2 | Banks, agencies and regions exist only as data here. Required to sell to a bank; a single-agency pilot can run without it |
| B04 | M | **M1** | Sessions, invites and password resets underpin A05–A07, which the pilot needs |
| B05 | L | M2 | DPD history is what makes roll rates and bank MIS possible, **and it only accumulates from go-live**. Start before the pilot if it fits, so pilot data has history |
| B06 | L | M2 | Placements and assignment history, plus `visit_media`. **Scope out** settlement_offers and escalations until those workflows exist |
| B07 | M | M2 | Attendance does not exist today; agencies pay on it. Add the DRA fields here (N5) |
| B08 | M | M2 | Tenant ids and `beat_stops`. Placement runs/decisions can wait for D09 |
| B09 | M | M2 | Immutable audit is the part a bank's auditor asks for; the ml/ai/strategy schemas can wait |
| B10 | S | N | Engineering hygiene, invisible to buyers |
| B11 | M | M2 | One migration path; bank IT will ask how upgrades are applied |
| B12 | S | M2 | Only if partitions ship |
| B13 | L | M2 (reduced) | Dim views plus 2 of the 5 materialised views (agency scorecard, field activity). The rest waits for scale |
| B14 | S | N | Timeouts are cheap insurance; not a buyer item |
| B15 | M | **Dec** | Needed only if the Collections embedded deployment must migrate its live data. It adds no buyer value on the demo fixture |
| B16 | L | M2 (reduced) | The demo needs several agencies, female agents, specimen evidence photos and honest timestamps. **Defer** the latent-skill ground truth; it only serves future ML |
| B17 | M | M2 | `dev` and `demo` profiles; `stress` waits |
| B18 | S | **M1** | The demo story: no "ABC", no placeholder names, a clean fixture |
| B19 | M | M2 | Evidence of tenant isolation for bank diligence |
| B20 | M | M2 | Keeps model serving alive through the schema change (engineering dependency) |
| B21 | S | Df | Stress timings matter before a 1,000-agent client, not a 50-agent pilot |
| B22 | S | **M1** | A demo must never text a real-format number. Critical |

### P1-A: tenancy, identity, access (16)
| Task | Size | Class | Why |
|---|---|---|---|
| A01, A02 | M, M | M2 | Named capabilities and a request context: bank, agency and agent roles |
| A03 | L | M2 (M1 if two agencies share the pilot) | Closes the three leaks. The agency-admin screen already shows another pool's 597 cases |
| A04 | M | M2 | Per-agency planning |
| A05 | M | **M1** | A lost or shared phone must be revocable from the office |
| A06 | S | **M1** | Invites, so nobody reads out a password |
| A07 | M | **M1** | Password reset. Without it, the first week of any pilot is support calls |
| A08 | S | M2 | MFA for bank users (pair with N10 SSO) |
| A09 | S | **M1** | One agent, one device: stops login-sharing, which is common in FOS |
| A10 | S | ✔ | |
| A11 | M | **M1** (forgot / change password pages only) | |
| A12 | M | M2 | The behavioural cross-tenant proof that bank diligence asks for |
| A13 | M | Df | Row-level security is defence in depth once a second bank shares a deployment |
| A14 | S | **M1** | "ABC Bank" on receipts to a real lender's borrowers ends the pilot. Includes the UPI name and VPA and the Gurugram fallback |
| A15 | S | **Dec** | Keep only if the Collections embedded mode stays. Service accounts replacing manager passwords is right either way |
| A16 | S | **M1** | Audit on invites, sessions and resets: RBI evidence |

### P2: onboarding and Manage Agents (12)
| Task | Size | Class | Why |
|---|---|---|---|
| D01, D03 | M, S | M2 | A bank onboarding agencies, with DRA and agreements on file |
| D02 | L | N (pilot) / M2 (GA) | For one or two pilot agencies, onboard by hand. The six-step wizard is a demo piece until a bank runs 20+ agencies |
| D04 | M | Df | Offboarding is rare; do it manually with four eyes on paper |
| G01, G02 | L, L | **M1 (reduced to create / edit / suspend / reset: M + M)** | An agency cannot run a week without adding and removing agents. Bulk import and transfer come in wave 2 |
| G03 | M | M2 | |
| G04 | M | N | A read-only contract page; the agency already knows its contract |
| G05 | S | **M1** | ID-card QR: RBI identification requirement, cheap |
| G06 | S | N | ✔ in progress (ce) |
| G07 | M | N | Engineering; the lead's call |
| P2-E2E | M | M2 | |

### P3: bank portal (19)
| Task | Size | Class | Why |
|---|---|---|---|
| UI01 | M | N | Finish the spec only |
| UI02, UI03, UI04 | M ×3 | M2 | A bank portal needs a theme, primitives and a shell. ✔ in progress (43's agents) |
| UI05 | L | N (reduced) | Port KPI cards, tables and the drill; skip the rest |
| **UI06** | M | **Dr** | A pixel-parity screenshot harness on every page. Buyers do not compare us to Command Center |
| C01 | M | M2 | One KPI definition. The AI briefing contradicting its own card is what happens without it |
| C02 | M | M2 | Filters that change numbers |
| C03 | M | M2 (reduced) | 6–8 KPIs that pilot data can support, not 12 |
| C04 | L | M2 (reduced) | Field Ops, Agencies, Recovery, Compliance. Exposure, Migration, Concentration and Cost wait for real history |
| C05, C06 | M, M | N | Drill panel and alerts: take the two field alerts (untouched past SLA, efficiency drop) first |
| C07 | M | Df | 800 ms p95 at 1,000 agents; a pilot needs "under 3 s on 4G" |
| D05 | M | M2 | An agency directory |
| D06 | L | M2 (reduced) | Plain scorecard. **Defer** "Recovery vs Expected" and the shrunk Performance Index: both rest on a model trained only on synthetic borrowers |
| D07 | M | N | |
| D08 | M | M2 | Manual placement with CSV upload is how every bank places cases today |
| D09 | L | Df | An optimiser with no outcome data |
| K01 | M | M2 | Bank users, regions, audit |

### P4: AI Strategy (12)
| Task | Size | Class | Why |
|---|---|---|---|
| E01–E04, E06, E07 | M/L | **Df** | Transition matrices, Monte Carlo, backtests and forecasts all need months of real DPD history, which B05 only starts collecting. **E02/E04 are being built now (43's background agent). Recommend pausing them.** |
| E05, E08 | L, L | **Dr** | Simulator UI and Scenario Lab. A bank's risk team owns IFRS-9 and stress tests and will not use ours |
| E09 | L | M2 (XLSX first) | Exports are day-one; PPTX is last. ✔ d4 next |
| E10 | M | M2 (Monthly MIS + Agency Review only) | Board, Risk and Audit templates later |
| E11, E12 | M, S | N | AI narrative only after C01; scheduled email is cheap |

### P5: Tech Ops (12)
| Task | Size | Class | Why |
|---|---|---|---|
| F01 | M | N ✔ | Done enough; do not extend it. N4 (India-hosted model) matters more than a second US provider |
| F02–F05, F07, F08 | M/L | Df | An agent runtime, registry, evals and an MLOps console with nothing real to run on |
| **F06** | L | **Dr** | A bank will not build AI agents inside a vendor's collections app |
| F09, F11 | M, S | N | |
| F10 | M | **M1** | The first real bank file will have duplicates and DPD jumps. Quarantine saves the pilot's first week |
| F12 | S | **M1** | Today any manager can change the live model for every tenant. Close it, or switch promotion off |

### P6: ML and agentic AI (18)
| Task | Size | Class | Why |
|---|---|---|---|
| H14 | M | N ✔ | Voice to report. Worth it only in Hindi and only with the agent confirming (open HIGH defects on negation and dates). A PTP is 3–4 taps anyway |
| H13 | M | N | A compliance auditor over transcripts is a strong story, **after** call recording with consent exists |
| H01–H05, H07–H12, H15–H18 | — | **Df** | Every one needs real outcomes (the first 30-day labels mature from 2026-10-08; 90-day from 2026-11-22) or a pilot's data. Scored against the generator's ground truth, they prove the generator |
| **H06** | L | **Dr** | A bandit over visit/call/settle/escalate with no outcome data. Do a transparent rules table first, if at all |

### P7: field-ready app (3)
| Task | Size | Class | Why |
|---|---|---|---|
| I01 | M | **M1** ✔ (code on disk) | Installable app |
| I02 | L | **M1** | The offline outbox. Critical: JOURNEYS §1a |
| I03 | M | N (pilot) → M2 (scale) | Agencies' phones are often managed, and a PWA cannot track GPS with the screen off. Expect to need an APK before 500 agents |

### Cross-cutting (4)
X02 (CI) and X03 (security review per phase) are M2/M1: bank diligence reads them. X04 (a demo script per milestone) is **M1**: it is the sales tool. X01 is engineering hygiene.

---

## Over-built for the stage

1. **ML depth before a single real outcome.** Six scoring layers, a trained champion, a retrain lifecycle, and 10 more models plus 8 agents planned. The live champion reads a borrower-stance input the product never records (ML-1). Everything is scored against synthetic ground truth. Pause P6 except H14; put the effort into capturing real outcomes (N11, the stance picker) so the models have something true to learn from by December.
2. **P4 strategy tooling** (Monte Carlo, IFRS-9, scenario lab, 13-week forecast) is being built now. Nobody buys it without their own history, and banks already own it.
3. **An AI agent studio** (F02–F06) and **pixel parity with Command Center** (UI06).
4. **Scale machinery ahead of scale**: stress profile, partitions, row-level security and p95 at 1,000 agents, while the first pilot is 50–150 agents.

## Recommended order (for the owner)

1. **Now:** N1 (payment and evidence honesty) and B22/A14 (no real texts, no "ABC Bank"). These are days, not weeks.
2. **Wave 1, with P1's identity work (B04):** I01/I02, N2 Hindi, N7 SMS, N5 DRA, N6 cash (minimal), N3 DPDP basics, N4 residency, G01/G02 (reduced), A05–A09/A11/A16, F10, F12, P0-08, N9 exports, B18, X04. **Then sell the pilot.**
3. **Wave 2, during the pilot:** the rest of P1, a thin P2/P3 bank portal (directory, scorecard, manual placement, exports, bank users), SSO, grievances.
4. **After 60–90 days of pilot data:** revisit P4 and P6 against real numbers.
