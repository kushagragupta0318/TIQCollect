# Raw borrower PII — access policy and sign-off record

**Subject:** the `bank_feed.pii.read` capability and the raw-PII disclosure path
in the bank feed quarantine workflow.
**Prepared:** 2026-10-16 · **Branch:** `TIQCollect-app` · **HEAD:** `ded3c1a`
**Status: PENDING APPROVAL** (Phase 11 re-verified the implementation; no
authorized approver has been supplied, so the gate stays open).

Re-verified 2026-10-16 (Phase 11): 20/20 focused tests pass on real PostgreSQL,
and the authorized role set is unchanged — `bank_feed.pii.read` is held by
`BANK_ADMIN` only, nothing added to the field allow-list (9 fields).
**Passing tests are not approval:** they show the control works as described,
not that anyone has accepted the policy.

This document records a policy that is **already implemented and tested**, for
an approver to accept, amend or reject. Implementation is not approval: the
scoping decision below was recommended by engineering and chosen in the Phase 9
working session, but no responsible owner has signed it.

---

## 1. What is being protected

`lending.bank_feed_rows.raw` holds a quarantined feed row **exactly as the bank
sent it**. Returning it discloses borrower contact data. Measured in Phase 8,
the fields it can carry are:

| Field | Nature |
|---|---|
| `customer_name` | borrower's name |
| `phone_primary` | borrower's phone number |
| `address_line1` | borrower's address |
| `pan_masked` | masked tax identifier |
| `customer_ref`, `loan_account_number`, `case_number`, `branch_code`, `agency_code` | business keys / routing, not contact data |

It is reachable only through the `include_raw=true` query parameter on two
endpoints, both of which this policy governs:

- `GET /api/v1/bank/feed/{batch_id}/quarantine`
- `GET /api/v1/bank/feed/quarantine/rows/{row_id}`

## 2. The policy

| # | Rule | Enforced by | Verified by |
|---|---|---|---|
| P1 | **BANK_ADMIN is the only role** that may read raw borrower PII. | `core/permissions.py` — `bank_feed.pii.read`, roles `(BA,)` | `test_the_pii_capability_is_declared_and_held_by_bank_admin_only` |
| P2 | **`bank_feed.upload` alone does not grant it.** `BANK_TECHOPS` and the `SERVICE` account hold upload and **not** PII. | two separate capabilities | `test_feed_upload_alone_does_not_imply_raw_pii_access`, `test_tech_ops_asking_for_raw_pii_receives_none`, `test_the_service_account_cannot_read_raw_pii` |
| P3 | **A persisted audit event is a precondition of disclosure.** If the audit row cannot be written, the raw data is withheld (fail closed). | `feed_quarantine_service._audit_raw_read` return value is checked | `test_raw_pii_is_withheld_when_the_audit_row_cannot_be_persisted`, `test_the_single_row_path_also_fails_closed` |
| P4 | **Audit metadata records field names and access context, never borrower values.** | `details` carries `RAW_FIELDS_ALLOWED` names, ids, counts | `test_audit_metadata_contains_no_borrower_pii` |
| P5 | **Unauthorized reviewers keep safe quarantine information** — held rows, reasons, business keys, Release — and receive no borrower contact fields. | PII check lives in the service, not as a route-level 403 | `test_safe_quarantine_metadata_is_still_served_without_the_pii_grant` |
| P6 | Raw data is **absent by default**, even for an authorized caller, unless `include_raw=true` is passed. | `include_raw` defaults to `False` | `test_raw_is_still_absent_by_default_for_an_authorized_user` |
| P7 | Only the **allow-listed fields** may leave; a new column in the bank's file is dropped, not disclosed. | `RAW_FIELDS_ALLOWED` filter in `row_dict` | `test_an_unexpected_extra_column_is_not_leaked` |
| P8 | The capability is **not a cross-tenant key**: another bank's admin holds it and still cannot reach this bank's rows. | tenant scope resolved from the authenticated context | `test_another_banks_raw_pii_cannot_be_reached` |
| P9 | A **withheld** read writes no `DATA_EXPORT` row, so the trail never implies data was handed over. | audit written only on the success path | `test_a_withheld_read_writes_no_data_export_row` |

**Audit event shape** (action `DATA_EXPORT`, an existing enum value — no schema
change): actor id, bank id, timestamp, `entity_type=BankFeedRow`, batch id,
`rows_disclosed`, `endpoint`, `capability`, and the permitted `fields` **by
name only**. Reviewable with the query in `docs/PILOT-RUNBOOK-FEED.md` §7.

## 3. What this policy deliberately does NOT do

- It does not restrict quarantine **review** or **release**. Tech Ops keeps the
  whole operational workflow; only borrower contact fields are withheld.
- It does not add a second PII tier (e.g. unmasked PAN). No such field exists in
  the feed today.
- It does not audit *unsuccessful* raw requests as data access. Those take the
  existing `ROLE_VIOLATION_ATTEMPT` path where a route refuses outright.
- It does not make the audit write and the data read one transaction. See §5.

## 4. Known residual risks

| # | Risk | Severity | Mitigation / status |
|---|---|---|---|
| R1 | A BANK_ADMIN can still read borrower contact data at will. The control is **traceability**, not prevention. | accepted by design | every read is attributable; review query in runbook §7 |
| R2 | `tenancy.permissions` does not yet list the capability, so any report built from those tables under-reports it. | low | no such surface exists; `docs/PHASE9-PROPOSED-MIGRATION.md` |
| R3 | The audit row and the HTTP response are not transactionally coupled. | low | see §5 |

## 5. The failure window, stated plainly

The audit row is committed **before** the response is serialised, so the unsafe
direction — PII disclosed with nothing logged — cannot occur.

The remaining window is the harmless direction: the audit row lands and the
response then fails to reach the caller (connection dropped, client aborted).
The trail may therefore record a read the requester never received. This is
deliberate; the alternative ordering would permit silent disclosure.

They are **not** in a single transaction. `write_audit` commits on its own by
design ("the audit trail records what happened, it does not decide whether it
happens"); Phase 9 departs from that only in treating a `False` return as a
refusal.

## 6. Sign-off

Approving this record means accepting §2 as the policy, §3 as its limits, and
§4–§5 as known residual risk.

> **I approve the raw-PII access policy above: `bank_feed.pii.read` granted to
> BANK_ADMIN only, audit-before-disclosure, fail closed.**

| Field | Value |
|---|---|
| Approver (name) | ____________________ |
| Role / authority | ____________________ |
| Date | ____________________ |
| Decision | ☐ Approved as written ☐ Approved with amendments (below) ☐ Rejected |
| Amendments, if any | ____________________ |

**Amendments requiring engineering work** (do not change these without a new
change request — the scope is enforced in code and pinned by tests):

- adding a role to `bank_feed.pii.read`
- adding a field to `RAW_FIELDS_ALLOWED`
- relaxing the fail-closed behaviour

---

**Until this is signed, `docs/PILOT-PREFLIGHT.md` item A10 remains NOT
VERIFIED.** The implementation is live and tested regardless; what is missing is
the recorded decision, not the control.
