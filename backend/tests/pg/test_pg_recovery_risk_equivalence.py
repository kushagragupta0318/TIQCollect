"""v2_0039 + the aggregated recovery-risk read return exactly what the Python
loop returned, and do it without reading the prediction history.

A performance rewrite of a money term is only worth having if the money does
not move. The gate here is the one test_pg_visit_to_pay_equivalence.py sets for
v2_0021: the implementation this replaced, kept as a literal in this file, run
beside the new one against the same real Postgres book, and the two numbers
compared to the paisa -- for every bank in the fixture, so a one-tenant book
cannot hide a scoping change.

The old shape is WHY this exists: 122,674 prediction rows streamed to Python to
find 7,844 loans' latest scores, a 3.6 MB external merge sort, 15.4 s measured
against a 15 s statement_timeout -- so GET /bank/strategy/cash-forecast
answered a single user with a 500 (QueryCanceled).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.strategy import cash_forecast as CF
from tests.pg.test_pg_demo_fixture import db, sql_text  # noqa: F401 -- the restored fixture

pytestmark = pytest.mark.filterwarnings("ignore")

NEWLINE = chr(10)

#: The implementation v2_0039 replaced, verbatim from before the change. Kept
#: here rather than imported, because the point is to compare against
#: something the new code cannot influence.
OLD_SQL = text("""
    SELECT mp.loan_id, mp.probability, l.overdue_amount
      FROM ml.model_predictions mp
      JOIN lending.loans l ON l.id = mp.loan_id
     WHERE mp.bank_id = :bank
       AND mp.model_name = 'recovery_risk'
       AND mp.is_modelled = TRUE
       AND mp.probability IS NOT NULL
       AND mp.as_of_date >= :recency_start
       AND l.status = 'ACTIVE'
       AND l.overdue_amount > 0
       AND NOT EXISTS (
             SELECT 1 FROM collections.ptps p
              JOIN collections.cases c ON c.id = p.case_id
              WHERE c.loan_id = mp.loan_id
                AND p.status = 'ACTIVE'
                AND p.committed_date >= :as_of
                AND p.committed_date < :horizon_end
           )
     ORDER BY mp.loan_id, mp.as_of_date DESC, mp.scored_at DESC
""")


def _params(as_of: date, bank_id: str) -> dict:
    return {"bank": bank_id,
            "recency_start": as_of - timedelta(days=CF.RECOVERY_RISK_RECENCY_DAYS),
            "as_of": as_of,
            "horizon_end": as_of + timedelta(weeks=CF.HORIZON_WEEKS)}


def _old(session: Session, bank_id: str, as_of: date) -> tuple[float, int]:
    """The Python loop, as it was: newest-first rows, first one per loan."""
    rows = session.execute(OLD_SQL, _params(as_of, bank_id)).all()
    seen: set[str] = set()
    total = 0.0
    for loan_id, probability, overdue_amount in rows:
        if loan_id in seen:
            continue
        seen.add(loan_id)
        total += (1.0 - float(probability)) * float(overdue_amount)
    return total, len(seen)


def _banks(db) -> list[str]:  # noqa: F811
    with db.connect() as c:
        return [str(r[0]) for r in c.execute(text("SELECT id FROM tenancy.banks ORDER BY id"))]


def _as_of(db) -> date:  # noqa: F811
    """The book's own newest prediction date. A fixed today would put the whole
    book outside the 45-day recency window as the fixture ages, and the test
    would then pass by comparing 0 with 0."""
    with db.connect() as c:
        newest = c.execute(text("SELECT max(as_of_date) FROM ml.model_predictions "
                                "WHERE model_name = 'recovery_risk'")).scalar()
    if newest is None:
        pytest.skip("the fixture book carries no recovery_risk predictions")
    return newest


def test_the_aggregate_matches_the_row_by_row_implementation(db):  # noqa: F811
    as_of = _as_of(db)
    banks = _banks(db)
    assert banks, "the fixture should carry at least one bank"
    covered = 0
    for bank_id in banks:
        with Session(db) as s:
            old_total, old_loans = _old(s, bank_id, as_of)
            new_total, new_loans = CF.read_recovery_risk_next_cycle(s, bank_id, as_of=as_of)
        assert new_loans == old_loans, bank_id
        # To the paisa: SUM() in Postgres and a Python += over the same floats
        # can differ in the last bit of a ten-digit rupee figure. A paisa is
        # three orders of magnitude tighter than anything the forecast reports,
        # and loose enough not to fail on float association alone.
        assert new_total == pytest.approx(old_total, abs=0.01), bank_id
        covered += new_loans
    assert covered > 0, (
        "no bank in the fixture has a modelled recovery_risk score on an active overdue "
        "loan with no PTP in the horizon, so this compared 0 with 0 and proved nothing"
    )


def test_the_latest_score_wins_and_a_same_day_tie_is_broken_by_scored_at(db):  # noqa: F811
    """The rule the Python loop got from reading in order: newest as_of_date,
    then newest scored_at. Checked by WRITING three scores for one loan rather
    than hoping the book contains the case -- a re-score on the same day is
    exactly when this matters, because the loan then has two rows with one
    as_of_date and the forecast must use the later one."""
    as_of = _as_of(db)
    with Session(db) as s:
        row = s.execute(text("""
            SELECT l.id, l.bank_id, l.overdue_amount
              FROM lending.loans l
             WHERE l.status = 'ACTIVE' AND l.overdue_amount > 0
               AND NOT EXISTS (SELECT 1 FROM collections.ptps p
                                JOIN collections.cases c ON c.id = p.case_id
                                WHERE c.loan_id = l.id AND p.status = 'ACTIVE'
                                  AND p.committed_date >= :as_of
                                  AND p.committed_date < :horizon_end)
               AND NOT EXISTS (SELECT 1 FROM ml.model_predictions mp
                                WHERE mp.loan_id = l.id AND mp.model_name = 'recovery_risk'
                                  AND mp.as_of_date >= :recency_start)
             ORDER BY l.id LIMIT 1
        """), {"as_of": as_of,
               "horizon_end": as_of + timedelta(weeks=CF.HORIZON_WEEKS),
               "recency_start": as_of - timedelta(days=CF.RECOVERY_RISK_RECENCY_DAYS)}).first()
        if row is None:
            pytest.skip("every qualifying loan in the book already carries a recent score")
        loan_id, bank_id, overdue = str(row[0]), str(row[1]), float(row[2])

        before_total, before_loans = CF.read_recovery_risk_next_cycle(s, bank_id, as_of=as_of)

        # 0.10 must win: same day as 0.90, scored an hour later. 0.50 is
        # yesterday and must lose on as_of_date alone.
        for p, day_offset, hour in ((0.50, -1, 9), (0.90, 0, 9), (0.10, 0, 10)):
            day = as_of + timedelta(days=day_offset)
            s.execute(text("""
                INSERT INTO ml.model_predictions
                       (bank_id, loan_id, model_name, model_version, entity_type, entity_id,
                        as_of_date, scored_at, is_modelled, probability, features, reason_codes)
                VALUES (:bank, :loan, 'recovery_risk', 'test-equivalence', 'loan', :loan,
                        :as_of_date, :scored_at, TRUE, :p, '{}'::jsonb, '[]'::jsonb)
            """), {"bank": bank_id, "loan": loan_id, "p": p, "as_of_date": day,
                   "scored_at": f"{day} {hour:02d}:00:00+00"})

        after_total, after_loans = CF.read_recovery_risk_next_cycle(s, bank_id, as_of=as_of)
        old_total, old_loans = _old(s, bank_id, as_of)
        s.rollback()        # the book is module-scoped and shared; leave it as found

    assert after_loans == before_loans + 1
    # P(pay) = 1 - 0.10 on this loan's overdue amount, and nothing else moved.
    assert after_total - before_total == pytest.approx(0.90 * overdue, abs=0.01)
    # ... and the implementation this replaced agrees, on the same three rows.
    assert after_loans == old_loans
    assert after_total == pytest.approx(old_total, abs=0.01)


def test_the_read_uses_the_index_and_never_scans_a_prediction_row(db):  # noqa: F811
    """The defect in the facts that name it: the old shape sorted 3.6 MB on
    disk and read 122,674 prediction rows from the heap. The new one must do
    neither.

    Asserted on the index's COLUMN SIGNATURE, not on its name: Postgres names
    a partition's own index after the partition, so the plan says
    `model_predictions_p202609_bank_id_model_name_loan_id_as_of__idx` and never
    `ix_model_pred_latest_per_loan` -- a parent-name assertion passes only by
    accident. And a zero-row Seq Scan on an EMPTY future partition is free and
    expected, so what matters is not that no sequential scan appears but that
    no sequential scan reads a row.
    """
    as_of = _as_of(db)
    bank_id = _banks(db)[0]
    with db.connect() as c:
        plan = NEWLINE.join(r[0] for r in c.execute(
            text("EXPLAIN (ANALYZE, BUFFERS) " + str(CF._RECOVERY_RISK_SQL)),
            _params(as_of, bank_id)))

    lines = [line.strip() for line in plan.splitlines()]

    assert "Index Only Scan" in plan, plan
    assert "bank_id_model_name_loan_id_as_of" in plan, plan      # v2_0039's columns
    assert "external merge" not in plan, plan
    # Index-only means the prediction tuples themselves are never visited.
    heap = [line for line in lines if line.startswith("Heap Fetches:")]
    assert heap and all(line == "Heap Fetches: 0" for line in heap), plan

    # Every sequential scan of a partition returned NOTHING -- they are the
    # empty future months, not the book. `rows=` appears twice on a scan line
    # (the estimate, then the actual); the last one is the actual.
    scanned = [int(line.rsplit("rows=", 1)[1].split(" ")[0])
               for line in lines
               if "Seq Scan on model_predictions" in line and "actual" in line]
    assert all(n == 0 for n in scanned), (scanned, plan)
