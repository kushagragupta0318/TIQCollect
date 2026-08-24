-- ═══════════════════════════════════════════════════════════════════════════
-- ROLLBACK ARTIFACT — recovery rollout of 2026-08-24
--
-- Captured BEFORE the first controlled recovery snapshot write, against the
-- live database, through a read-only connection.
--
--   database              fieldops @ localhost:15432
--   alembic migration     f4b7d9c1e832
--   recovery scorecard    recovery-scorecard-1.1.0
--   repayment scorecard   scorecard-1.1.0   (unchanged)
--   RECOVERY_WRITE_LABEL  false             (gate SHUT at capture time)
--
--   loans                             525
--   snapshot rows at capture          523
--   snapshot rows dated 2026-08-24     0
-- ═══════════════════════════════════════════════════════════════════════════

-- ───────────────────────────────────────────────────────────────────────────
-- SECTION 1 of 2 — UNDO TODAY'S SNAPSHOT WRITE
--
-- SAFE TO RUN. This file touches exactly one table and cannot reach
-- loans.recovery_potential: that restore lives in a SEPARATE file
-- (02-restore-loan-recovery-potential.sql) precisely so that running this one
-- can never restore loan labels by accident.
--
-- WHY A PLAIN DELETE IS COMPLETE HERE
-- At capture time the snapshot table held 523 rows and 0 of them
-- were dated 2026-08-24. The grain is (loan_id, as_of_date) with a unique
-- constraint, so today's scoring run can only INSERT — it cannot modify a
-- pre-existing row. Deleting today's date therefore restores the table exactly,
-- with nothing to put back.
--
-- The rows carry BOTH the repayment likelihood and the recovery fields, because
-- one row is one loan on one day. Deleting them removes today's repayment
-- snapshot too. That is correct: it was written by the same run.
-- ───────────────────────────────────────────────────────────────────────────

BEGIN;

-- Look before deleting. Expect 525 after the controlled write, 0 if it never ran.
SELECT count(*) AS rows_to_delete
FROM repayment_score_snapshots
WHERE as_of_date = '2026-08-24';

DELETE FROM repayment_score_snapshots
WHERE as_of_date = '2026-08-24';

-- Expect 523 — the pre-write count.
SELECT count(*) AS rows_remaining FROM repayment_score_snapshots;

COMMIT;
