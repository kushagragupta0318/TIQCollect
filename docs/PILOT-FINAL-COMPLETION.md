# TIQCollect — final pilot-readiness completion report

**Date:** 2026-10-16 · **Branch:** `TIQCollect-app` · **HEAD:** `ded3c1a`
**Scope:** the bank feed pipeline (upload → ingestion → quarantine → scoring →
allocation) for one controlled bank.

# DECISION: NOT READY — EXTERNAL ACTIONS REQUIRED

Two gates pass. Four require a named person; one requires an identified
environment; one requires watching a real night. **No gate is blocked by an
engineering defect, and no engineering work remains that I can safely perform.**

Statuses below were recalculated from repository evidence, not from earlier
summaries.

---

## 1. Gate status

| Gate | Requirement | Status | Evidence / what is missing |
|---|---|---|---|
| **A** | Formal PII policy approval | **PENDING APPROVAL** | Implementation verified (§2.1). Approver field blank in `docs/PII-POLICY-SIGNOFF.md` §6 |
| **B** | Migration `v2_0041` approval | **PENDING APPROVAL** | Review complete and re-verified (§2.2). Approval table blank in `docs/PHASE9-PROPOSED-MIGRATION.md` |
| **C** | Daily operator, backup, record location | **PENDING OPERATIONAL INPUT** | Assignment table blank in `docs/PILOT-RUNBOOK-FEED.md` §6 |
| **D** | Bank feed contract + sanitized sample | **PENDING EXTERNAL INPUT** | Platform side verified (§2.4); all bank values NOT VERIFIED; no sample received |
| **E** | Verification in the pilot environment | **NOT VERIFIED** | **No pilot environment exists** (§2.5). Commands prepared and dry-run |
| **F** | Real 19:30 / 19:45 / 20:00 IST observation | **NOT VERIFIED** | Nights observed: **0**. Depends on C and E |
| **G** | CLI ingestion race documented | **PASS** | §2.7 |

## 2. Evidence

### 2.1 Gate A — implementation verified, approval not given

Four policy claims, each checked against code this session:

| Claim | Result |
|---|---|
| `bank_feed.pii.read` restricted to BANK_ADMIN | **PASS** — holders `{'BANK_ADMIN'}` |
| Raw PII returned only when authorized **and** audit persisted | **PASS** — gate + audit present; fail-closed path present |
| Audit metadata carries field **names**, never values | **PASS** — `list(RAW_FIELDS_ALLOWED)`; no raw payload in the audit row |
| Safe quarantine review preserved without the grant | **PASS** — withheld via `not_authorised`, no route-level 403 |

Allow-list unchanged at 9 fields. Focused tests: **20 passed** on real
PostgreSQL.

**Passing tests are not approval.** The policy needs a recorded decision from
whoever owns borrower-data policy. Scope was not broadened.

### 2.2 Gate B — reviewed, deliberately unapplied

```
alembic heads   -> v2_0040 (v2) (head)      # single head
alembic current -> v2_0040 (head)           # database at head
v2_0041 in alembic/versions/  -> 0 matches  # correctly contained
perm delta   : ['bank_feed.pii.read']
grant delta  : [('BANK_ADMIN', 'bank_feed.pii.read')]
extra in seed: []
counts: seed 81/156  registry 82/157
```

The test-fixture approach was proven (Phase 11) not to weaken assertions: all
three pass verbatim on the union of both revisions, and the drift detector still
fires when a capability is removed.

**No approval exists in the repository or in the instruction for this task**, so
the migration remains unapplied and no database was modified. Not applied to
make a test green.

### 2.3 Gate C — unassigned

Primary operator, backup and record-filing location are all blank. Because
manual checks replace alerting for this pilot by agreed design, **an unassigned
check means the pilot has no monitoring at all.** The checklist (§6), per-day
form (§6a) and assignment table are ready; no name was invented.

### 2.4 Gate D — platform side closed, bank side absent

Platform-side requirements re-verified from code this session:

```
MAX_UPLOAD_BYTES : 26214400 = 25 MB
MAX_ROWS         : 200000
REQUIRED_HEADERS : ('customer_ref', 'loan_account_number', 'case_number')
RELEASABLE batch : ('PARTIAL', 'LOADED')
REFUSAL_REASONS  : 9 — 8 releasable; UNKNOWN_BRANCH not releasable
```

`NO_AGENCY` handling confirmed: the asynchronous worker ingests an uploaded feed
with the bank code only, so **every row must carry its own `agency_code`** or it
is quarantined. Those rows are releasable once the cause is fixed;
`UNKNOWN_BRANCH` rows are not, and require branch registration plus re-ingestion.

No specification, volume or sample has been supplied. Nothing was guessed. The
protected `backend/scripts/sample_daily_feed.csv` was **not read or used**.

### 2.5 Gate E — no pilot environment exists

Checked rather than assumed:

- The only running stack is the **development** stack (`DEMO_MODE=true`,
  database `fieldops`).
- `deploy/.env.prod` exists but its own header states: *"LAN demo … NOT a public
  deployment: no TLS, DEMO_MODE on, demo book restored. Secrets are throwaway
  for this demo host."*

Neither is a pilot environment. Per instruction I did not treat the dev stack as
one, did not connect to any unidentified database and did not guess credentials.

Prepared: `docs/PILOT-ENV-VERIFICATION.md` Appendix — exact commands whose
expected outputs are **real dev-stack results**, plus a result-recording table.
Every box is unticked.

### 2.6 Gate F — zero nights observed

The 19:30 → 19:45 → 20:00 IST sequence has never been watched at its real
scheduled times in any environment. Unit tests, manually triggered tasks and
backdated timestamps are explicitly **not** substitutes. The record form is
`docs/PILOT-NIGHT-RUN-RECORD.md`. The schedule was not changed and no gate was
bypassed.

### 2.7 Gate G — PASS

DEFECT-8.1 remains documented in `docs/PHASE8-ACCEPTANCE.md` with measured
evidence (4 concurrent CLI ingests → 3 raised, 1 succeeded; the book stayed
correct: 1 loan, 1 customer, 1 staging row). Workaround preserved verbatim in
runbook §8: **never run simultaneous manual ingestions of the same feed.** The
scheduled path is protected by the batch claim. Evidence test retained and
passing. Ingestion was not redesigned; `backend/app/models/lending.py` has no
diff.

## 3. Outstanding human approvals and operational inputs

| # | Needed | From | Artifact to complete |
|---|---|---|---|
| 1 | PII policy approval | owner of borrower-data policy — **not known to me** | `PII-POLICY-SIGNOFF.md` §6 |
| 2 | Migration v2_0041 approval | authority over schema changes — **not known to me** | `PHASE9-PROPOSED-MIGRATION.md` approval table |
| 3 | Primary + backup operator, record location | whoever assigns ops duty — **not known to me** | `PILOT-RUNBOOK-FEED.md` §6 |
| 4 | Pilot environment identified + access authorized | same | `PILOT-ENV-VERIFICATION.md` Step 0 |
| 5 | Feed spec, volume, branch codes, sanitized sample | pilot bank's integration owner — **not known to me** | `PILOT-FEED-CONTRACT.md` §§1–9, §11 |
| 6 | One observed scheduled night | designated operator, once 3 and 4 exist | `PILOT-NIGHT-RUN-RECORD.md` |
| 7 | One test-restored backup | environment operator | `DEPLOY.md` §6 |

**Names and responsibilities are recorded nowhere in this report because none
were supplied.** Requests 1–4 are drafted in `docs/PILOT-HANDOFF-REQUESTS.md`.

## 4. Pilot-environment verification results

**None.** No step was executed in a pilot environment, because none exists.

Commands prepared vs executed, and where:

| Command | Prepared | Executed | Where |
|---|---|---|---|
| Database identity | ✅ | ✅ | dev stack → `fieldops\|fieldops` |
| Alembic parity | ✅ | ✅ | dev stack → both `v2_0040` |
| `celery inspect registered` | ✅ | ✅ | dev stack → all 5 tasks |
| Beat schedule + timezone | ✅ | ✅ | dev stack → 19:30/19:45/20:00, `Asia/Kolkata` |
| Recovery-sweep observation | ✅ | ✅ | dev stack (Phase 8) → fired `04:45:00.000Z` = 10:15 IST |
| PII holders + `DATA_EXPORT` query | ✅ | ✅ | isolated `fieldops_phase8` |
| Fault-injection suite | ✅ | ✅ | isolated `fieldops_phase8` → 71 passed |
| Sample dry-run validation | ✅ | ❌ | no sample exists |
| Worker/beat restart in pilot env | ✅ | ❌ | no environment |
| Real scheduled night | ✅ | ❌ | never observed |

Dev-stack and isolated-database results are labelled as such and are **not**
counted as Gate E or F evidence.

## 5. Regression tests run this session

| Suite | Result | Why run |
|---|---|---|
| `test_pii_authorization.py` + `test_pg_defect_feed_row_race.py` | **22 passed** | direct evidence for Gates A and G |
| `tests/phase8` (full acceptance, real PostgreSQL) | **71 passed** | handoff baseline |

Per instruction, suites already passing with no code change since were not
re-run. One known failure is **expected by design**:
`test_the_seed_equals_the_registry` fails until v2_0041 is approved and applied.
It was **not** weakened, and the migration was **not** applied to silence it.

## 6. Known risks and workarounds

| # | Risk | Severity | Status |
|---|---|---|---|
| R1 | A BANK_ADMIN can read borrower contact data at will; the control is traceability, not prevention | accepted by design | every read audited and attributable |
| R2 | No automated alerting; a daily manual check is the only monitoring | **HIGH while Gate C is open** | mitigated only once an operator is named |
| R3 | No stage durations recorded; the nightly window has never been measured in production | medium | open; detection of an overrun is not possible today |
| R4 | DEFECT-8.1 — simultaneous manual CLI ingestion of one file | low | documented; workaround: one at a time |
| R5 | `tenancy.permissions` is one capability behind the code registry | low | authorization unaffected (proven); closes with Gate B |
| R6 | Throughput unmeasured above 2,000 rows; ~30 rows/s measured | medium | re-measure above ~25,000 rows/night |
| R7 | No backup has been test-restored | medium | open (item 7 above) |

## 7. Final decision

**NOT READY — EXTERNAL ACTIONS REQUIRED.**

Every technical control the pilot depends on is implemented and verified against
real PostgreSQL, including concurrency safety, crash recovery without duplicate
side effects, tenant isolation, raw-PII authorization with audit-before-
disclosure, and dependency gating before allocation. 71 acceptance tests pass.

What remains is two signatures, three names, one bank's file format and sample,
one identified environment, and one observed night. I cannot supply any of them
without inventing facts, so they are handed over rather than closed.

Of those, two should not be treated as paperwork:

- **Until an operator is named, the pilot has no monitoring at all** — manual
  checks replace alerting here by design.
- **Until one night is observed at the real times, nobody has seen the schedule
  fire where it matters.** Every other proof is a test or a simulation.

## 8. Git safety

Verified at the start and end of this task:

```
branch   : TIQCollect-app
HEAD     : ded3c1a          (unchanged)
staged   : 0
stashes  : 0
modified : 47               (unchanged by this task)
untracked: 49               (47 + this report + PILOT-HANDOFF-REQUESTS.md)
```

No commits, staging, stash, reset, clean, revert, or any other destructive Git
operation. `backend/scripts/sample_daily_feed.csv` remains exactly as found
(8 insertions / 8 deletions) and its contents were never read or used. No
scratchpad or untracked file was deleted.

**No unauthorized deployment, no migration applied, no schema modified, no
production or unidentified database accessed, and no live bank data ingested.**
No borrower PII appears in any artifact produced.
