# Pilot plan: the first paid pilot

**Target:** one lender, one agency, about 100 field agents, on a dedicated deployment hosted in
India.

**Status:** draft, 2026-09-24. Prepared by tiqcollect-bb (lead developer) on the owner's
pilot-first decision, relayed by tiqcollect-64. The business lead (tiqcollect-fb) validates the
business wording. The commercial reasoning is in the business lead's `docs/business/PRIORITIES.md`
and `ECONOMICS.md`. The engineering sequencing is in [RESTRUCTURE-PLAN.md](RESTRUCTURE-PLAN.md).

`docs/STANDALONE-TASKS.md` is **not** edited on this branch (board rule). The task ids below
(N01–N09) and the pause and drop list are applied to it once, at integration.

**Sizes:** S = 1 day or less · M = 2–5 days · L = 1–2 weeks, in developer-days.
**Lanes:** 43 = data model, auth and tenancy · ce = LLM, PWA and UI shell · d4 = the visit and
payment flow, reports · bb = structure, infrastructure and integration adapters. Lanes are
proposals; the coordinator assigns them.

---

## 1. What a pilot buyer asks for that the plan does not have

The nine items below are the ones a lender or agency raises in the first meeting. Each is a
task with a testable **Done when**.

### N01 · Hindi, then one or two regional languages · L · ce, plus 43 for SMS text

- **What:**
  - An i18n layer in the SPA, with every agent-facing string extracted. Manager and bank screens
    follow.
  - A language picker on the agent profile, saved per user.
  - Hindi translations for the agent app.
  - Hindi borrower SMS and WhatsApp templates. They go through A14's template registry, the one
    place message text lives.
- **Done when:**
  - An agent set to Hindi can finish log in → beat → visit → payment OTP → PTP without seeing an
    English string. This is checked by an e2e run that fails on any untranslated key.
  - The borrower receives the Hindi OTP and receipt templates.
  - Numbers and dates use Indian formatting in both languages.
- **Depends on:** A14 (templates as data). It does not wait for the page splits (RESTRUCTURE-PLAN
  2.7): the keys move with the code.
- **Note:** the dependency is `react-i18next` or an equivalent. It is a new dependency, which
  the coordinator must approve.

### N02 · DPDP Act 2023 basics · L · 43 (data, audit), bb (retention jobs), owner and legal (text)

- **What:**
  - Notice and consent text for borrower contact and for recordings, shown and stored with the
    visit. A recording without recorded consent is refused.
  - A **retention schedule per data class**, held as settings with named owners, enforced by
    jobs. Today GPS is kept 90 days and media is never deleted.
  - Data-principal requests (access, correction, erasure), handled as an admin workflow, each
    step audited.
  - A **breach runbook** (docs, including who notifies whom and within what time).
  - **Data minimisation:** the agent sees only what the visit needs.
  - **No location tracking before check-in** (BL-4, awaiting the owner's decision).
- **Done when:**
  - Every data class in DATA-MODEL-V2 has a retention entry, and a test proves the job enforces
    it.
  - A request round-trip is audited end to end.
  - The runbook is committed.
  - The consent text version is stored on every visit and every recording.
- **Depends on:** B09 (immutable audit), B06 (`visit_media`), and the owner's answers on BL-4
  and D10.

### N03 · India data residency and a per-lender LLM switch · M · ce (llm), 43 (tenant setting), bb (deployment)

- **What:**
  - A written commitment and deployment recipe for hosting in an India region.
  - A per-lender AI setting: `off` (the rule-based fallbacks that already exist, labelled as not
    AI), `india_hosted` (an endpoint in India), or `global`.
  - No borrower field reaches a provider the lender has not allowed. Today every visit sends the
    borrower's name, loan details and the agent's notes to a US-hosted model
    (`ai_report_service.py`).
- **Done when:**
  - With a lender set to `off`, a full visit and payment day makes **zero** outbound LLM calls
    (a test counts them).
  - Every AI output shows the provider and region that produced it.
  - The deployment doc names the region for every stateful service.
- **Depends on:** F01 (provider seam), B03 (the lender entity).

### N04 · DRA certificate register as an allocation gate · M · 43

- **What:**
  - DRA certificate number, issuer, validity dates and the scanned certificate on the agent
    record.
  - A **hard gate in the allocator**, beside DNC, hostility and territory, so an agent without a
    valid certificate is never allocated a case, and the decision records why.
  - An expiry warning 30 days ahead on the agency screen.
- **Done when:**
  - An agent whose certificate has expired receives no case in a planning run (test).
  - Every blocked assignment shows the reason `DRA_EXPIRED` or `DRA_MISSING`.
  - The register can be exported (N08).
- **Depends on:** B07 (workforce), A04 (per-agency planner). The gate goes where the other hard
  gates are built, `global_allocator.eligible_agents`.

### N05 · Cash custody · L · d4 (flow and UI), 43 (tables)

- **What:**
  - Cash in hand per agent, fed by verified CASH payments.
  - A per-agent limit that the agency sets. Over the limit, cash collection is blocked until a
    deposit is made.
  - A deposit handover: amount, branch or bank, and a slip photo, which produces a receipt.
  - Reconciliation of collected against deposited, per agent and per day, with differences
    flagged.
- **Done when:**
  - At any moment an agency can see each agent's cash in hand.
  - A deposit clears the balance and produces a receipt.
  - A day that does not reconcile shows on the manager overview.
  - Cash collection is refused over the limit (test).
- **Depends on:** B06 (payments on the typed schema). Cash is 21% of demo payments.

### N06 · Indian DLT SMS gateway and a per-lender message policy · M · bb (adapter), 43 (policy and templates)

- **What:**
  - A DLT-registered Indian SMS provider behind the existing `notification_service` seam: sender
    ID, template ids, delivery receipts. Twilio stays only as a fallback, off by default.
  - A **per-lender message policy** saying which messages go out. The post-visit borrower
    message is **default off**, pending the owner's D10.
  - Demo tenants never send (B22).
- **Done when:**
  - The OTP, receipt and PTP-reminder SMS go through the DLT provider with a registered template
    id.
  - A delivery receipt is stored per message.
  - The per-lender policy switches each message type, with a test per type.
  - The cost per agent-month is measured on the pilot and compared with the ₹102 model
    (RESTRUCTURE-PLAN §4).
- **Depends on:** A14 (message text as data), B22 (demo suppression), D10 (the owner).
- **Why it matters:** on Twilio, messaging is about ₹3,120 per agent-month, against a seat price
  near ₹900.

### N07 · Grievance capture and closure · M · 43 (tables), d4 (agent and manager UI)

- **What:**
  - Capture a complaint from the agent (at the door), the agency or the lender.
  - A workflow: received → acknowledged → investigating → resolved or rejected, with an owner and
    SLA timers per state.
  - An **evidence pack per complaint**: the case's visits, recordings, GPS trail around each
    visit, payments and audit rows, exported as one file.
- **Done when:**
  - A complaint can be raised, assigned, answered and closed.
  - Every state change is audited.
  - SLA breaches show on the agency and lender screens.
  - The evidence pack for a case builds in under 30 s and contains every visit's media links and
    GPS points.
- **Depends on:** B06 (`disputes`, promoted from a table to a workflow), B09.

### N08 · Bank-format exports · M · d4

- **What:** the reports a lender's MIS team asks for on day one, built on the E09 report engine
  (already built on d4's branch):
  - the collections register
  - the visit log with GPS and photo links
  - the PTP register
  - attendance
  - payment receipts as PDF
  - the DRA register (N04)
- **Done when:**
  - Each export downloads as XLSX (receipts as PDF) for a date range and agency.
  - Every export writes a `DATA_EXPORT` audit row.
  - The column order matches a template the pilot lender signs off.
  - The totals equal the manager overview for the same range (test).
- **Depends on:** E09 (report engine, done on d4's branch), B07 (attendance).

### N09 · SSO for bank users (OIDC first, SAML second) · M · 43

- **What:**
  - OIDC login (Azure AD / Okta / Google Workspace) for bank and agency staff.
  - Just-in-time provisioning mapped to roles and capabilities, allowed only for pre-registered
    email domains.
  - SAML after that.
  - Field agents keep password plus device binding (A09).
- **Done when:**
  - A bank user signs in through the lender's OIDC provider and lands in their role with the
    right capabilities.
  - A disabled account in the lender's directory cannot sign in at its next session refresh.
  - Every SSO login is audited.
- **Depends on:** A01, A02 (capabilities, request context), A05 (sessions).

### Not code, but needed before contract (owner)

- A VAPT report from a CERT-In empanelled auditor. Allow 2–4 weeks elapsed.
- A data processing agreement.
- A BCP and disaster-recovery statement with restore-tested backups.
- A support SLA.

The engineering inputs to the BCP (backups, restore test, RPO/RTO measured on the pilot
deployment) are a bb task once the pilot environment exists.

### Related, already in flight (not new ids)

- **Payment and evidence honesty** (the business lead's N1): the UPI auto-confirm, the hardcoded
  payee address, the "Liveness passed" copy, and saving what the agent already captures. The UPI
  part is in d4's owner-approved `hotfix/live-security`. The rest goes to d4 as a follow-up.
- **Product analytics events** (the business lead's N11): ten events to our own table, so a
  pilot can prove adoption. Proposed for ce once F01 closes. It is not one of the nine.

---

## 2. Paused and dropped

**Paused** means not started or stopped where it is: kept in the task list, resumed after 60–90
days of pilot data. **Dropped** means removed at integration, with the reason recorded.

| Task(s) | Decision | Reason |
|---|---|---|
| **P4:** E01–E04, E06, E07, E10–E12 | **Pause** | Transition matrices, Monte Carlo, backtests and forecasts need months of real DPD history, and `loan_dpd_history` (B05) only starts collecting it at go-live. E02/E04 are being built now by 43's background agent; stop at the next commit. |
| E09 (report engine) | **Keep, narrowed** | Already built on d4's branch. It is the base for N08; XLSX first, PPTX last. |
| **E05** simulator UI, **E08** Scenario Lab | **Drop** | A lender's risk team owns IFRS-9 and stress testing and will not use ours. |
| **P5:** F02–F05, F07–F09, F11 | **Pause** | An agent runtime, registry, evals and MLOps console with nothing real to run on. F01 stays as done. |
| **F10** (bank-feed quarantine), **F12** (restrict model promotion) | **Recommend: keep active.** Owner to confirm | The business lead rates both pilot must-haves. The first real bank file will contain duplicates and DPD jumps. Today any manager can change the live model for every tenant (known issue 11). Both are S–M. |
| **F06** AI Agents UI | **Drop** | A lender will not build AI agents inside a vendor's collections app. |
| **P6:** H01–H13, H15–H18 | **Pause** | Each needs real outcomes: the first 30-day labels mature from 2026-10-08, 90-day from 2026-11-22. Scored against the generator's ground truth, they only prove the generator. H14 (voice → report) continues. |
| **H06** next-best-action bandit | **Drop** | A bandit with no outcome data. If needed, a transparent rules table comes first. |
| **UI06** pixel-parity harness | **Drop** | Buyers do not compare the bank portal to Command Center screenshot by screenshot. |

## 3. Order, from the engineering side

1. **Now, in flight:**
   - the three hotfixes (d4 `hotfix/live-security`, bb `hotfix/live-security-2`, ce
     `hotfix/spa-containment`)
   - B22 and A14 (no real texts, no "ABC Bank")
   - RESTRUCTURE-PLAN Wave 1 (CI, test independence, lean image, dead code)
2. **With P1's identity work:** N04 (DRA gate) and N06 (DLT gateway). Both are small and change
   what the pilot costs and whether it is legal to run.
3. **After P1 merges:** N05 (cash), N08 (exports), N03 (AI switch and residency), N01 (Hindi,
   agent app first), N02 (DPDP basics).
4. **During the pilot:** N07 (grievances) and N09 (SSO), together with the thin bank portal.

A pilot environment (one India-region deployment, restore-tested backups, a self-hosted maps
VM, the DLT provider) is a bb task. It starts once the owner names the lender and hosting
provider.
