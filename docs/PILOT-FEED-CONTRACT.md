# Pilot feed contract — to be completed by the bank / integration owner

**Prepared:** 2026-10-16 · **Status: PENDING EXTERNAL INPUT** — no pilot bank
schema, volume or sample has been supplied.

Phase 11 re-verified every **platform-side** value below against the code
(figures printed from `feed_upload_service`, `feed_quarantine_service` and
`placement_service`, not transcribed):

```
MAX_UPLOAD_BYTES: 26214400 = 25 MB
MAX_ROWS        : 200000
REQUIRED_HEADERS: ('customer_ref', 'loan_account_number', 'case_number')
RELEASABLE batch statuses: ('PARTIAL', 'LOADED')
REFUSAL_REASONS : 9
  releasable    : LOAN_NOT_OPEN, NO_AGENCY, AGENCY_NOT_ACTIVE,
                  NO_CONTRACT_IN_FORCE, NOT_AUTHORISED, NOT_COVERED,
                  CONTRACT_FULL, PLACED_ELSEWHERE
  NOT releasable: UNKNOWN_BRANCH
```

Platform-side columns are therefore **PASS**. Every bank-specific value remains
**NOT VERIFIED** and is the integration owner's to supply.

Every value in the **"Bank to confirm"** column is **NOT VERIFIED** until the
integration owner fills it in. Nothing here is a guess at the pilot bank's
format: the "What the platform requires" column is read from the code
(`backend/app/services/bank/feed_upload_service.py`,
`backend/scripts/ingest_daily.py`) and is a statement of fact about the
receiving end only.

No borrower data appears in this document. The externally modified
`backend/scripts/sample_daily_feed.csv` was **not** read for content, modified
or used as a sample.

---

## 1. File-level rules — fixed by the platform

| Property | What the platform requires | Where enforced | Bank to confirm |
|---|---|---|---|
| Format | CSV, comma-delimited | `validate_csv` | ☐ NOT VERIFIED |
| Header row | **Required**, first line, exact column names | `validate_csv` | ☐ NOT VERIFIED |
| Encoding | UTF-8 text; a binary file is refused on content, not extension | `validate_csv` | ☐ NOT VERIFIED |
| Max file size | **25 MB** (HTTP 413 above it) | `MAX_UPLOAD_BYTES` | ☐ NOT VERIFIED |
| Max rows | **200,000** | `MAX_ROWS` | ☐ NOT VERIFIED |
| Minimum content | at least one data row; header-only is refused | `validate_csv` | ☐ NOT VERIFIED |
| Submission | `POST /api/v1/bank/feed` by a user holding `bank_feed.upload` | `bank_feed.py` | ☐ NOT VERIFIED |
| Re-submitting the identical file | returns the **same batch**, `duplicate: true`; not ingested twice | unique `(bank, feed_type, business_date, sha256)` | ☐ NOT VERIFIED |

## 2. Required columns — a row without these is SKIPPED

| Column | Required | Meaning | Bank's column name |
|---|---|---|---|
| `customer_ref` | **yes** | the bank's borrower identifier | ____________ |
| `loan_account_number` | **yes** | the bank's loan identifier | ____________ |
| `case_number` | **yes** | the collections case identifier | ____________ |

A row missing any of the three is skipped with `"missing required key fields"`.
These three are also the upload-time header check: a file lacking any of them is
rejected whole (HTTP 422) before staging.

## 3. Columns required for a NEW borrower

A borrower the platform has not seen before is **skipped** unless these are
present and valid. An existing borrower is updated without them.

| Column | Rule | Skip reason if absent | Bank to confirm |
|---|---|---|---|
| `latitude`, `longitude` | both non-zero | `"missing lat/lon for new customer …"` | ☐ NOT VERIFIED |
| `branch_code` | must be one of **this bank's** registered branches | row is **QUARANTINED** as `UNKNOWN_BRANCH` | ☐ NOT VERIFIED |
| `do_not_contact` | if `true` on a new borrower, the row is skipped | `"do_not_contact=true on new customer"` | ☐ NOT VERIFIED |

**Branch registration is a prerequisite.** Every `branch_code` the feed can
contain must exist for the bank before the first feed, or those rows are held
and are **not releasable** through the quarantine workflow (they never create a
loan — see `docs/PILOT-RUNBOOK-FEED.md` §5).

Branch codes to be registered: ____________________________________________

## 4. `agency_code` — the requirement Phase 8 measured

**This is the most likely cause of a first-day failure.** The asynchronous
worker ingests an uploaded feed with the bank's code only; there is no
file-level agency default on the upload path.

> **Each row must carry its own `agency_code`**, naming an agency that works for
> this bank. A row without it is refused `NO_AGENCY` and quarantined.

| Item | Bank to confirm |
|---|---|
| `agency_code` present on every row | ☐ NOT VERIFIED |
| The exact code string(s) the file will use | ____________________ |
| Those codes exist as ACTIVE agencies for this bank | ☐ NOT VERIFIED |
| Each named agency has a contract **in force** covering the loans' regions | ☐ NOT VERIFIED |

Without a contract in force, rows are quarantined as `NO_CONTRACT_IN_FORCE`.
Those **are** releasable once the contract exists — unlike `UNKNOWN_BRANCH`.

## 5. Business date and timezone

| Property | Platform behaviour | Bank to confirm |
|---|---|---|
| Business date source | the optional `business_date` form field on upload; otherwise the **IST** calendar date at ingestion | ☐ NOT VERIFIED |
| Timezone | all pipeline dates are **IST (UTC+05:30)** | ☐ NOT VERIFIED |
| Meaning of the date | which day's book the file represents | ☐ NOT VERIFIED |
| Expected upload time | before **19:30 IST** (the cutoff check); allocation runs 20:00 IST | ☐ NOT VERIFIED |
| One file per business date? | the platform allows several; each is its own batch | ☐ NOT VERIFIED |

If the bank's "business date" means the previous working day, that must be
stated here — the gate matches a feed to the business date it is stamped with,
so a mismatch reads as "no feed for today" and **blocks allocation**.

## 6. Volume

Needed to confirm the nightly window holds. Measured throughput on the
reference environment: **~30 rows/s** (31.5 ms/row), so ~28,500 rows fit the
19:30→20:00 window. Above ~25,000 rows/night this must be re-measured.

| Metric | Bank to supply |
|---|---|
| Minimum rows/night | ____________ ☐ NOT VERIFIED |
| Typical rows/night | ____________ ☐ NOT VERIFIED |
| Maximum rows/night | ____________ ☐ NOT VERIFIED |
| Expected growth over the pilot | ____________ ☐ NOT VERIFIED |

## 7. Identifier uniqueness assumptions

The platform **upserts on business keys**. Confirm these hold in the bank's data,
because they determine whether a re-sent row updates or creates:

| Assumption | Platform behaviour if true | Bank to confirm |
|---|---|---|
| `customer_ref` is unique per borrower, stable over time | the borrower is updated, not duplicated | ☐ NOT VERIFIED |
| `loan_account_number` is unique per loan, stable | the loan is updated in place | ☐ NOT VERIFIED |
| `case_number` is unique per case | the case is not re-opened | ☐ NOT VERIFIED |
| A borrower may hold several loans | supported | ☐ NOT VERIFIED |
| Identifiers are never reused for a different entity | **required** — reuse would silently overwrite | ☐ NOT VERIFIED |

## 8. Handling of missing, duplicate and invalid rows

Platform behaviour, for the bank's awareness:

| Case | What happens |
|---|---|
| Missing one of the three key fields | row **SKIPPED**, reason recorded, rest of the file continues |
| Missing lat/lon on a new borrower | row **SKIPPED** |
| Unknown `branch_code` on a new loan | row **QUARANTINED** (`UNKNOWN_BRANCH`) — not releasable; fix the branch and re-ingest |
| No/invalid `agency_code`, or no contract | row **QUARANTINED** — releasable once the cause is fixed |
| Duplicate `loan_account_number` **within one file** | upload-time warning; the **last occurrence wins** |
| Entire file re-uploaded unchanged | same batch returned, `duplicate: true`, no second ingestion |
| Any row quarantined or skipped | batch ends **PARTIAL**, not `LOADED` |
| File refused at validation | batch **REJECTED**; nothing entered the loan book |

| Question for the bank | Answer |
|---|---|
| Acceptable quarantine rate before the feed is treated as faulty | ____________ ☐ NOT VERIFIED |
| Will the bank re-send corrected rows, or a full replacement file? | ____________ ☐ NOT VERIFIED |

## 9. Contacts and escalation for a malformed feed

| Role | Name / channel | Hours |
|---|---|---|
| Bank-side feed owner (sends the file) | ____________ | ____________ |
| Bank-side technical contact (fixes the file) | ____________ | ____________ |
| Platform-side recipient of a malformed-feed report | ____________ | ____________ |
| Agreed response time for a rejected feed | ____________ | ____________ |

**None of these is filled in. Escalation for a malformed feed is therefore
UNOWNED.**

## 10. Sample validation

The platform offers a **dry run** that writes nothing:
`POST /api/v1/bank/feed/validate`. It reports missing headers, row count,
in-file duplicates and blocking errors.

| Step | Status |
|---|---|
| Bank supplied a synthetic or sanitized sample | ☐ **NOT VERIFIED** — none supplied |
| Sample passed `POST /bank/feed/validate` | ☐ NOT VERIFIED |
| Sample ingested end-to-end in a non-production environment | ☐ NOT VERIFIED |
| Quarantine/skip outcomes reviewed against §8 | ☐ NOT VERIFIED |

**Do not validate with real borrower data.** A sanitized sample with synthetic
names, phones and addresses is sufficient — the platform does not need real
values to prove the format.

---

## 11. Sample request and the validation procedure (prepared, not executed)

### The request, to be sent by the platform-side owner

> **Requested:** one **synthetic or sanitized** daily-feed file representative of
> the pilot bank's real format — same columns, same header spelling, same
> encoding and delimiter, same `agency_code` values, 20–200 rows.
>
> **Must NOT contain real borrower data.** Names, phones, addresses and tax
> identifiers must be fabricated or masked. The platform does not need real
> values to prove the format, and a sanitized file keeps the exchange out of
> scope for borrower-data handling entirely.
>
> **Please also complete §§1–9 of this document**, in particular §4
> (`agency_code`) and §6 (volume).

| Field | Value |
|---|---|
| Requested from | ____________________ |
| Requested by / date | ____________________ |
| Channel used | ____________________ |
| Received / date | ☐ not received |

### What will be run when it arrives

Dry run first — it writes nothing and creates no batch:

```bash
curl -s -X POST "$BASE/api/v1/bank/feed/validate" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@sanitized-sample.csv" | jq .
```

Expected shape: `ok`, `rows`, `headers`, `missing_headers`, `blocking`,
`warnings`, `duplicate_rows_in_file`.

Then, in a **non-production** environment only, a full ingestion to classify
every row. The report below is filled from the batch and its staging rows —
**counts and reasons only, never a borrower value**:

```sql
-- per-row outcome for the sample batch
select status, coalesce(dq_errors->-1->>'reason','-') as reason, count(*)
from lending.bank_feed_rows
where batch_id = :batch_id
group by 1, 2 order by 3 desc;

-- the batch's own summary
select status, rows_total, rows_accepted, rows_quarantined, rows_skipped
from lending.bank_feed_batches where id = :batch_id;
```

### Sample validation report (to be completed)

| Outcome | Count | Notes |
|---|---|---|
| Accepted (loan + case created or updated) | ____ | |
| Skipped — missing one of the 3 key fields | ____ | |
| Skipped — missing lat/lon on a new borrower | ____ | |
| Skipped — `do_not_contact=true` on a new borrower | ____ | |
| Quarantined — `UNKNOWN_BRANCH` (**not releasable**) | ____ | register the branch, re-ingest |
| Quarantined — `NO_AGENCY` / invalid `agency_code` | ____ | releasable once fixed |
| Quarantined — `NO_CONTRACT_IN_FORCE` / coverage / cap | ____ | releasable once fixed |
| Final batch status | `______` | `LOADED` only if nothing was held |
| Validated by / date | ____________ | |

A sample that ends `PARTIAL` is **not** a failure — it tells the bank exactly
which rows need format or reference-data fixes before the first real feed.

---

## Sign-off

| Field | Value |
|---|---|
| Completed by (bank / integration owner) | ____________________ |
| Date | ____________________ |
| Platform-side reviewer | ____________________ |
| All sections confirmed | ☐ yes ☐ no — outstanding: ____________ |

**Until §§1–9 are completed and §10 validated, `docs/PILOT-PREFLIGHT.md` items
E12 and F3 remain NOT VERIFIED.**
