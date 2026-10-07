# 0015. Payment reversal: two-stage agency→bank, with an atomic unwind

**Status:** Accepted, 2026-10-01 (owner's ruling, relayed by coordinator f8; lane L7, task #2).
**Capabilities:** `payment.reversal.request` (AGENCY_MANAGER), `payment.reversal.approve.agency`
(AGENCY_MANAGER + AGENCY_ADMIN), `payment.reversal.approve.bank` (BANK_ADMIN, sensitive).

## Context

A mistaken collection could not be undone. `PaymentStatus.REVERSED` existed in the enum but nothing
wrote it, so a wrong payment stayed on the case ledger, in the agent's month figures, and — if it
satisfied one — on a PTP, forever. A lender's ops team needs to void a mis-entry; the question was who
may, and how the ledger stays consistent when they do.

An earlier draft used **agency-internal four-eyes** (manager requests, admin approves). That was
recorded with a caveat: two agency staff can collude to reverse a legitimate collection and understate
what the agency owes the bank. The stronger control — the bank giving fiduciary sign-off on money moving
back — was noted as the v2 hardening. The owner chose it for v1.

## Decision

- **Two-stage agency→bank.** The agency raises the reversal and approves its own side; it then routes to
  the BANK, whose sign-off is fiduciary and final. The ledger unwinds only on bank approval. The agency
  cannot both initiate and bless a reversal — the real second pair of eyes is the bank, not a second
  agency account.
  - `PENDING_AGENCY` → (agency approve) → `PENDING_BANK` → (bank approve) → `APPROVED` [unwind runs here]
    | `REJECTED` at either stage (nothing moved).
  - The agency manager may both request and agency-approve (the cap grants AM); there is no agency-internal
    four-eyes, by design — the bank is the control.
- **The bank sign-off is never an agency actor.** Enforced three ways: role separation (a BANK_ADMIN, not
  an agency user), a service check (`bank_approved_by ∉ {agency_requested_by, agency_approved_by}`), and a
  DB CheckConstraint (`bank_approver_is_not_the_agency`).
- **The unwind is atomic and total** (one transaction, on bank approval): the payment goes `REVERSED`;
  `case.collected_amount` and the agent's `current_month_collections` lose the amount (floored at 0);
  the case re-opens (`IN_PROGRESS` if nothing remains, else `PARTIALLY_PAID`) and `resolved_at` clears;
  and any PTP the payment honoured is un-honoured — but only if, with this payment excluded
  (`verified_paid_against` counts VERIFIED only, so marking it REVERSED first drops it), the PTP now
  falls below its committed amount. A PTP honoured by other payments too stays honoured. A reversal that
  returned the money but left the PTP HONORED is the inconsistent-ledger class this project keeps
  closing, so it is all-or-nothing.
- **The bank stage is cross-tenant and goes through l8's scoped RequestContext, not a hand-rolled
  bank_id match.** l8-rls-s1b landed (v2_0026, the `RequestContext` + pre-auth bind), so the bank stage
  is now live: `bank_approve`/`reject` require a BANK-scoped `RequestContext` bound to a `bank_id`
  (`scope.scope == 'BANK'`), and the scoped read bounds the row to `scope.bank_id` — the same `bank_id`
  the `_AGENCY_OWNED` policy keys on. A wrong scope (not BANK, or none) is refused 403 before the ledger
  moves. The write runs on the ordinary `DbSession` (not the read-only `AnalyticsDb`) and never changes
  `bank_id` (WITH CHECK re-evaluates the new row); `tests/pg/test_pg_rls` proves both, AS `tiq_app`.
  Going live was the **owner's explicit authorization on 2026-10-07** (routed to the owner because no
  live coordinator held the seat and every cross-tenant money-path flip on this product is an owner
  call); the earlier `_L8_SCOPE_AVAILABLE` 503 stub is removed.
- **Audited on both stages.** `PAYMENT_REVERSAL_REQUESTED` (request) and `PAYMENT_REVERSED` (the applied
  reversal), appended at the end of the native `audit_action_enum` (its own migration, since a value
  added by `ALTER TYPE` cannot be used in the same transaction). Both rows carry `bank_id` and
  `agency_id` so the bank sees every reversal in its audit trail (C09) and l8's audit tenancy is fed.
- **One live reversal per payment.** A partial-unique index (`status <> 'REJECTED'`) blocks a double
  reversal and two requests racing to the bank.

## Known limits

- **A single-staffer agency has no eligible approver, and that is an honest dead end, not a hole.** The
  bank stage is a different tenant, so the agency can always reach a bank approver; but if an agency and
  its bank contact are the same person in some future configuration, the service refuses self-approval
  (403) rather than permitting it silently. Such a case escalates; it is never swallowed.
- **The RLS form is `_AGENCY_OWNED`, and it needs a shared policy-map change first.**
  `reversal_requests` is the first post-v2_0012 table that is agency-owned and that BOTH tenants write.
  Its policy is v2_0012's existing two-party template, verbatim (l8's v2_0019 reuses it for
  `tenancy.users`):
  `(bank_id = tenancy.current_bank_id() AND (tenancy.current_scope() = 'BANK' OR agency_id = tenancy.current_agency_id()))`
  — note BOTH arms require `bank_id = current_bank_id()`; an agency session matches only within its own
  bank, never on `agency_id` alone. The policy-map test machinery (`tests/test_rls_policy_map.py`) now
  carries an `RLS_AGENCY_OWNED` classification (added by l8), so v2_0029 declares the table through it and
  the "every tenant table has a policy" test is satisfied — the migration no longer has to wait on a
  shared change.
- **Tenant isolation is enforced by the SERVICE today, not by RLS — and this is the honest state of the
  whole platform, not just this feature.** The table carries v2_0012's `_AGENCY_OWNED` RLS policy and the
  per-transaction GUC bind (`app.bank_id`/`app.scope`) is in place, BUT the API connects as the
  `fieldops` role, which is `rolsuper`/`rolbypassrls` — so every row-level policy is bypassed and never
  consulted on an API request (d5's audit of 6114c37). v2_0012's step 2 — moving the API onto a
  non-superuser `tiq_app` login — is unfinished, and until it lands the policies are latent
  defence-in-depth, not the live control. So the boundary that actually holds today is the service:
  `_request_for_bank`, the agency-scope checks, scope.py's helpers. The tenancy test is on THAT, and the
  tests/pg RLS test must `SET ROLE tiq_app` or it proves nothing (the connecting role bypasses policies).
  A reader must not conclude the database enforces tenant isolation on this path today — it does not yet.
- **The stage lock is also service-enforced (f8's ruling), for a second reason.** Even once RLS is live,
  `_AGENCY_OWNED` is one expression for both `USING` and `WITH CHECK`, so it cannot say "the agency may
  write only while status is `PENDING_AGENCY`." The stage lock lives in the service (the status checks in
  `agency_approve` / `bank_approve` / `reject`), backed by the model's DB CheckConstraints. Splitting
  `USING` from `WITH CHECK` to push the stage into the DB is tracked as **post-demo hardening**.
- **The messaging feature's thread table is agency-owned too** (bank + agency), so it takes the same
  `_AGENCY_OWNED` template and the same coarse-RLS limitation above — this ADR's RLS reasoning covers
  both, rather than being restated there.
- **Demo.** Both stages are demoable with existing master logins: the agency manager (Vikram, a master
  account) does request + agency-approve, and the existing BANK_ADMIN master (holds
  `payment.reversal.approve.bank`) does the sign-off. No new master account is required; whether to add
  the AGENCY_ADMIN tier (Meera) for the org chart is a separate owner call, not a prerequisite.

- **A reversal re-opens only a payment-driven close, and touches only the current month's figure**
  (both from d5's read of the unwind). The case status/`resolved_at` are reset only when the case is
  currently `PAID` or `PARTIALLY_PAID`; a case closed for another reason (`CLOSED` / `WRITTEN_OFF` /
  `SETTLED` / legal) keeps its status — a reversal adjusts the ledger but does not drag a written-off
  case back into collections. And `agent.current_month_collections` — a running counter zeroed monthly
  and incremented at collection — is decremented only when the reversed payment is in the current IST
  month; reversing an earlier month's payment must not under-report this one, because the counter never
  held it.

## Consequences

- A wrong collection can be voided, with the bank — not the agency alone — accountable for money moving
  back, and the ledger (case, agent, PTP) stays consistent because the unwind is atomic.
- The agency-collusion risk of the earlier agency-internal design is closed: the bank is the approver.
- The reversal is the first consumer of l8's cross-tenant scope; the messaging feature (bank↔agency
  threads) is the second, and both flip on when l8 merges.
