# Pilot handoff — the four requests that close the remaining gates

**Date:** 2026-10-16 · **Branch:** `TIQCollect-app` · **HEAD:** `ded3c1a`

See `docs/PILOT-HANDOFF-SUMMARY.md` for the full handoff picture, and
`docs/PILOT-EXECUTION-CHECKLIST.md` for what the operator does once these
requests are answered.

Engineering is complete. Six gates remain and **none of them is code** — each
needs a named person to decide something, supply something, or watch something
happen. This document is the request for each, ready to send.

Send 1–3 in parallel; they do not depend on each other. Request 4 depends on an
environment existing, which is itself Request 3.

---

## Request 1 → whoever owns borrower-data policy (Gate A)

**Subject: Approval needed — raw borrower PII access policy (TIQCollect pilot)**

> Before the one-bank pilot starts, we need a recorded decision on who may read
> raw borrower contact data in the feed quarantine screens.
>
> **What is already built and tested:** reading a quarantined feed row *as the
> bank sent it* — borrower name, phone, address, masked PAN — requires a
> separate capability, `bank_feed.pii.read`, granted to **BANK_ADMIN only**.
> Bank Tech Ops and the machine (SERVICE) account keep the entire review and
> release workflow but never receive borrower contact fields. Every successful
> raw read writes an audit record naming the actor, bank, batch and field names
> — never the values — and if that record cannot be written, the data is
> withheld.
>
> **What we need from you:** confirmation that BANK_ADMIN-only is the policy you
> want, or an amendment. Passing tests show the control works; they are not
> approval.
>
> **To approve:** sign §6 of `docs/PII-POLICY-SIGNOFF.md`. That document also
> lists the residual risks — chiefly that a BANK_ADMIN can still read this data
> at will, so the control is traceability, not prevention.
>
> **If you want it tighter** (a further role split, or auditing denied attempts
> too), say so — those are small changes, but they are changes, and we would
> rather make them before a bank is live than after.

---

## Request 2 → whoever has authority over schema changes (Gate B)

**Subject: Approval needed — Alembic revision v2_0041 (two seed rows)**

> **What it does:** inserts one capability row and one role-grant row into
> `tenancy.permissions` / `tenancy.role_permissions`, so the database's listing
> of capabilities matches the code registry.
>
> **Why it is not urgent but should not be skipped:** authorization already
> works correctly without it — the application reads the capability registry in
> code, not these tables. Verified against the live database, which does **not**
> yet contain the row:
> ```
> BANK_ADMIN -> True    BANK_TECHOPS -> False    SERVICE -> False
> ```
> The cost of skipping it is that `test_the_seed_equals_the_registry` fails **by
> design**, and a failing test that is expected erodes the signal — a real
> future drift would look like the known failure.
>
> **Risk profile:** two `INSERT … ON CONFLICT DO NOTHING` statements. No DDL, no
> enum change, no data rewrite, no meaningful lock, idempotent, reversible, and
> **no change in application behaviour**. Parent is the single current head
> `v2_0040`, so it creates no branch. It can ride with any ordinary release.
>
> **One thing to note:** approving it also approves widening
> `tests/test_permission_seed.py`'s fixture to assemble its expected seed from
> both seed revisions. We proved this does not weaken the test — the three
> assertions pass verbatim on the union, and the detector still fires when a
> capability is removed.
>
> **To approve:** sign the approval table in
> `docs/PHASE9-PROPOSED-MIGRATION.md`. The migration is deliberately **not** in
> `backend/alembic/versions/`, so it cannot be applied by accident before you
> sign.

---

## Request 3 → whoever assigns operational duty (Gates C and E)

**Subject: Two things needed before the TIQCollect pilot can start**

> **(a) Name the daily-check operator — this is the one I would not start
> without.**
>
> This pilot has **no automated alerting**, by agreed design. A daily manual
> check replaces it. That means until someone owns the check, the pilot has *no
> monitoring at all* — not weaker monitoring. If a feed gets stuck or a night is
> blocked, nobody finds out until field agents open an empty app.
>
> We need three things, none of which we can supply ourselves:
> - **Primary operator** (runs a ~5-minute check each working day after 20:15 IST)
> - **Backup operator** (a primary with no backup is a single point of failure on
>   the pilot's only monitoring)
> - **Where each day's completed record is filed** (a record with no filing
>   location cannot be reviewed or audited)
>
> The checklist and the per-day form are in `docs/PILOT-RUNBOOK-FEED.md` §6 and
> §6a. The assignment table to complete is in §6.
>
> One check deserves naming: if the "duplicate active placements" query ever
> returns a row, a loan is being chased by two agencies at once. That is
> stop-the-line, not a backlog item, and needs an escalation contact.
>
> **(b) Identify the pilot environment.**
>
> There is currently no identified pilot environment. The only running stack is
> the development stack, and `deploy/.env.prod` is explicitly labelled a LAN
> demo host ("NOT a public deployment: no TLS, DEMO_MODE on, demo book
> restored, throwaway secrets"). We will not treat either as a pilot
> environment, and we will not guess credentials or connect to anything
> unidentified.
>
> Once you tell us which environment and authorize access, the verification
> sheet is ready to run: `docs/PILOT-ENV-VERIFICATION.md`, Appendix. Every
> command in it has been dry-run on the dev stack, so the expected outputs in
> that document are real, not invented.
>
> Note the mandatory step after any deployment: **restart the Celery worker and
> beat, then confirm `feed_cutoff` is registered.** Celery loads its task list
> at boot, so a newly added task is not dispatchable until a restart — beat
> would fire at 19:30 and the dispatch would fail.

---

## Request 4 → the pilot bank's integration owner (Gate D)

**Subject: Feed format, volume and a sanitized sample — TIQCollect pilot**

> To receive your daily overdue-loan file we need two things: the format
> confirmed, and one sample file to validate against it.
>
> **Please complete `docs/PILOT-FEED-CONTRACT.md` §§1–9.** It is a form; the
> platform-side column is already filled in and is fixed (CSV, UTF-8, header
> row required, 25 MB and 200,000 rows maximum, three mandatory columns:
> `customer_ref`, `loan_account_number`, `case_number`).
>
> **Three points that most often cause a failed first day:**
>
> 1. **Every row must carry `agency_code`.** Our ingestion resolves the
>    collecting agency per row. A row without a valid one is held as
>    `NO_AGENCY` — and if the whole file omits the column, the whole file is
>    held. Those rows are recoverable once the code is corrected, but the first
>    night produces no field work.
> 2. **A new borrower needs `latitude`/`longitude` and a known `branch_code`.**
>    Missing coordinates skip the row. An unrecognised branch code quarantines
>    it as `UNKNOWN_BRANCH` — and those rows are **not** recoverable through the
>    review screen: the branch must be registered and the file re-sent. Please
>    send the full list of branch codes the feed can contain.
> 3. **Tell us what your "business date" means** and in which timezone. Our
>    pipeline works in IST, and matches each file to the business date it is
>    stamped with. If your date means the previous working day, we need to know
>    before the first feed — a mismatch reads as "no feed today" and stops the
>    nightly allocation.
>
> **Also needed: expected minimum, typical and maximum rows per night.** We have
> measured ~30 rows/second on the reference environment, so roughly 10,000 rows
> fits the processing window comfortably. Above about 25,000 we need to
> re-measure before go-live.
>
> **The sample must be synthetic or sanitized.** Same columns, same header
> spelling, same encoding, same `agency_code` values — 20 to 200 rows is plenty.
> Please **do not** send real borrower names, phone numbers, addresses or tax
> identifiers: we do not need real values to prove the format, and a sanitized
> file keeps this exchange out of scope for borrower-data handling entirely.
>
> On arrival we run a dry-run validation that writes nothing, then report back
> exactly which rows would be accepted, skipped or quarantined and why — as
> counts and reasons, never as borrower data. The procedure is in §11 of the
> same document.
>
> **Finally, §9 of the contract needs contacts:** who sends the file, who fixes
> it when it is malformed, and the agreed response time.

---

## Then, and only then — Gate F

Once Gate C has a named operator and Gate E has an identified environment, book
one evening's observation of the real schedule: **19:30 → 19:45 → 20:00 IST**.

Use `docs/PILOT-NIGHT-RUN-RECORD.md`. Two things worth knowing in advance:

- **A night where allocation correctly blocks is a successful observation.** It
  proves the schedule fires and the safety gate works. Record it, then observe a
  second night with a feed present for the full success path.
- **Do not adjust the schedule or bypass a gate to get a green result.** The
  point of the observation is to find out what actually happens.

Also outstanding, and quick: **test-restore one backup**
(`docs/DEPLOY.md` §6). The procedure is documented; nobody has yet proven a
backup restores. An untested backup is an assumption.

---

## What is NOT waiting on anyone

For completeness, so nothing is double-chased. All verified against real
PostgreSQL:

- The feed pipeline: upload → validate → async ingestion → quarantine review →
  release → scoring → allocation gating.
- Concurrency safety: ten simultaneous claims yield one winner; two concurrent
  releases yield one placement and one case.
- Crash recovery: a real `SIGKILL` mid-processing leaves no duplicate loans or
  cases; a stranded batch is reclaimed and reprocessed safely.
- Tenant isolation and raw-PII authorization, over real HTTP with two banks.
- DEFECT-8.1 (simultaneous manual CLI ingestion of one file) documented with
  measured evidence and the standing workaround: **one at a time**.

71 automated acceptance tests pass on real PostgreSQL. No open engineering
defect blocks the pilot.
