# One-bank pilot preflight checklist

**Date prepared:** 2026-10-16 · **Branch:** `TIQCollect-app` · **HEAD:** `ded3c1a`
**Scope:** one controlled bank, the feed → quarantine → scoring → allocation path.

Nothing here invents a bank code, an operator identity, a credential, an
environment or an approval. Blanks are blanks on purpose: they are for the
responsible operator to fill in, and an unfilled blank is a **NOT VERIFIED**.

No production deployment has been performed and no live bank data has been
ingested.

---

## A. Raw-PII authorization (Phase 9, RISK-1)

| # | Item | Status | Evidence |
|---|---|---|---|
| A1 | `bank_feed.pii.read` declared, marked sensitive | **PASS** | registry + doc §5.1/§5.2 in step |
| A2 | Held by **BANK_ADMIN only** | **PASS** | `holders == {"BANK_ADMIN"}` |
| A3 | `bank_feed.upload` alone does not imply PII access | **PASS** | BT and SV hold upload, not PII |
| A4 | Both raw paths gated (listing **and** single row) | **PASS** | 2 endpoints, 1 serializer, both tested |
| A5 | Raw absent by default even for an authorized user | **PASS** | no `raw` key unless requested |
| A6 | Unauthorized request returns no raw field | **PASS** | `raw_pii_available: false`, no `raw` |
| A7 | Safe review metadata still served without the grant | **PASS** | reasons, keys, Release all intact |
| A8 | Response fields limited to the approved allow-list | **PASS** | an injected extra column was dropped |
| A9 | PII capability is not a cross-tenant key | **PASS** | other bank's admin → 404 |
| A10 | **Operator has approved BANK_ADMIN-only scope** | **NOT VERIFIED** | sign-off block: `docs/PII-POLICY-SIGNOFF.md` §6 |

## B. Raw-PII audit

| # | Item | Status | Evidence |
|---|---|---|---|
| B1 | Every successful raw read writes one audit row | **PASS** | 3 reads → 3 rows |
| B2 | Action is an existing enum value (`DATA_EXPORT`) | **PASS** | no enum change, no migration |
| B3 | Row carries actor, bank, timestamp, batch, row count, endpoint | **PASS** | asserted field by field |
| B4 | **No borrower PII in audit metadata** | **PASS** | serialized details scanned for every synthetic value |
| B5 | Raw response body is not logged | **PASS** | only field *names* recorded |
| B6 | Withheld read writes no `DATA_EXPORT` | **PASS** | denied → 0 rows |
| B7 | **Fail closed**: audit failure withholds the PII | **PASS** | both paths return no `raw` |
| B8 | Existing `PLACEMENT_CREATED` / `FEED_RELEASE` unchanged | **PASS** | release audit asserted intact |

## C. Tenant isolation

| # | Item | Status | Evidence |
|---|---|---|---|
| C1 | Cross-bank feed history hidden | **PASS** | Phase 8 §7, real HTTP |
| C2 | Cross-bank batch/row = uniform 404 | **PASS** | identical to nonexistent id |
| C3 | Cannot release another bank's row | **PASS** | 404, row untouched |
| C4 | Bank identity never taken from the client | **PASS** | read from authenticated context |
| C5 | Error bodies leak no trace/path/driver | **PASS** | Phase 8 §7 |

## D. Worker, beat and recovery

| # | Item | Status | Evidence |
|---|---|---|---|
| D1 | Worker + beat restart verified | **PASS** | restarted on the dev stack |
| D2 | `feed_cutoff` registered after restart | **PASS** | `celery inspect registered` |
| D3 | All 4 pipeline tasks registered | **PASS** | cutoff, ingest, sweep, scoring, allocation |
| D4 | Beat holds 19:30 / 19:45 / 20:00 IST, one each | **PASS** | tz `Asia/Kolkata` |
| D5 | Recovery sweep observed on its 15-min cadence | **PASS** | fired `04:45:00.000Z` = 10:15 IST, 24 ms |
| D6 | `STALE_CLAIM_MINUTES` unchanged at 60 | **PASS** | asserted by test |
| D7 | Stale claim reclaimed → `RECEIVED` | **PASS** *(simulated clock)* | timestamps backdated, never a 60-min wait |
| D8 | Worker SIGKILL leaves no duplicate side effects | **PASS** | real kill; 0 loans, 0 cases |
| D9 | **Restart performed in the pilot environment** | **NOT VERIFIED** | plan: `docs/PILOT-ENV-VERIFICATION.md` Step 2 |

## E. The workflow, end to end

| # | Item | Status | Evidence |
|---|---|---|---|
| E1 | Dry-run validation writes nothing | **PASS** | batch count unchanged |
| E2 | Upload stages and reports `RECEIVED`, not "ingested" | **PASS** | 0 loans in book at that point |
| E3 | Duplicate upload → same batch, no second dispatch | **PASS** | `duplicate: true` |
| E4 | Asynchronous ingestion produces `PARTIAL` accurately | **PASS** | 1 of 2 rows held |
| E5 | Duplicate task delivery does not reprocess | **PASS** | `claimed: false`, book unchanged |
| E6 | Quarantine review shows the recorded reason | **PASS** | `UNKNOWN_BRANCH` |
| E7 | `UNKNOWN_BRANCH` is unreleasable | **PASS** | `NOT_RELEASABLE`, row untouched |
| E8 | Eligible row released via the existing placement path | **PASS** | `placements.source='FEED'` + real case |
| E9 | Scoring gate reads persisted facts | **PASS** | verdict flips when rows are written |
| E10 | Allocation refuses on missing/stale upstream | **PASS** | `BLOCKED_UPSTREAM` row per manager |
| E11 | Correct IST business date used throughout | **PASS** | 00:30 IST rollover asserted |
| E12 | **Pilot bank's real file format validated** | **NOT VERIFIED** | form to complete: `docs/PILOT-FEED-CONTRACT.md` |

## F. Throughput

| # | Item | Status | Evidence |
|---|---|---|---|
| F1 | Successful-path throughput measured | **PASS** | ~30 rows/s; 31.5 ms/row at n=2000 |
| F2 | Fits the 19:30→20:00 window at pilot volume | **PASS** | ≤10,000 rows ≈ 5.2 min |
| F3 | **Pilot bank's nightly volume known** | **NOT VERIFIED** | expected rows/night: ____________ |
| F4 | Re-measure required above ~25,000 rows/night | **noted** | linear scaling unverified above 2,000 |

## G. Daily manual operation

| # | Item | Status | Evidence |
|---|---|---|---|
| G1 | Runbook documents all required checks | **PASS** | runbook §6, 10 checks |
| G2 | Monitoring queries verified to find their conditions | **PASS** | 7/7 on real Postgres |
| G3 | Escalation path documented | **PASS** | runbook §6 escalation |
| G4 | Recovery without re-uploading documented | **PASS** | runbook §5 |
| G5 | Raw-PII review query documented | **PASS** | runbook §7 |
| G6 | **Named daily-check owner** | **NOT VERIFIED** | record form: runbook §6a |
| G7 | **Named escalation contact** | **NOT VERIFIED** | contact: ____________ |
| G8 | **Where the daily outcome is recorded** | **NOT VERIFIED** | location: ____________ |
| G9 | Automated alerting | **absent by decision** | accepted for one-bank pilot |

## H. Known defects accepted

| # | Item | Status |
|---|---|---|
| H1 | DEFECT-8.1 (concurrent CLI ingest race) documented with workaround | **PASS** |
| H2 | Workaround explicit: never run two manual ingests of one feed | **PASS** (runbook §8) |
| H3 | Proposed fix documented separately, not applied | **PASS** |
| H4 | RISK-3 (no stage durations) accepted for the pilot | **NOT VERIFIED** — operator sign-off: ____________ |

## I. Schema parity

| # | Item | Status |
|---|---|---|
| I1 | Authorization enforced from the code registry | **PASS** — proven against the un-synced live DB |
| I2 | `tenancy.permissions` mirror one capability behind | **known gap** — `docs/PHASE9-PROPOSED-MIGRATION.md` |
| I3 | `test_the_seed_equals_the_registry` fails by design | **expected** — must not be silenced |
| I4 | **v2_0041 reviewed and applied** | **NOT VERIFIED** — review complete (Phase 10); approval block: `docs/PHASE9-PROPOSED-MIGRATION.md` |

---

## Conditions that still prevent pilot access

These are the **NOT VERIFIED** rows that need a person, not more engineering:

1. **G6 / G7 / G8 — the daily check is unowned.** No name, no backup, no place
   to record outcomes. Since manual checking *replaces* alerting here, an
   unowned check means the pilot has no monitoring at all.
2. **A10 — BANK_ADMIN-only PII scope not formally accepted.** Implemented and
   tested; the scoping decision needs recording.
3. **I4 — migration v2_0041 not reviewed.** Does not block authorization
   (proven), but leaves the DB mirror behind and one test failing by design.
4. **E12 / F3 — the pilot bank's file format and volume are unknown.** Rows
   must carry `agency_code` or every row is held `NO_AGENCY`; volume above
   ~25,000/night needs re-measurement.
5. **D9 — restart verified on the dev stack only.** Repeat in whichever
   environment the pilot runs, before the first feed.

Items 1 and 2 were the two accepted Phase 8 conditions. Everything technical
behind them is now implemented and verified; what remains is sign-off.
