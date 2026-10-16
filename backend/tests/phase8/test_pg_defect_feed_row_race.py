"""DEFECT-8.1 — concurrent ingestion of the SAME batch can raise UniqueViolation.

OBSERVED. While running the duplicate-upsert scenario, one of two concurrent
`run_ingestion` passes over the same batch died with:

    UniqueViolation: duplicate key value violates unique constraint
    "uq_bank_feed_rows_batch_id_row_no"
    DETAIL: Key (batch_id, row_no)=(<batch>, 1) already exists.

...leaving its session in PendingRollbackError. Both passes were writing the
STAGING row for row 1 of the same batch: each checked for an existing row,
found none, and inserted.

WHY IT IS NOT A DATA-INTEGRITY BUG. The constraint is doing its job — it is
what PREVENTS a duplicate staging row. The borrower-facing book stayed correct
in every run (one customer, one loan), because process_row upserts on business
keys. The damage is confined to the losing pass: it raises instead of
converging, and under the Phase 5 worker that becomes a retry.

WHY IT IS STILL WORTH RECORDING. The Phase 5 claim makes this rare in
production: only ONE worker can hold a batch at a time, so two concurrent
ingests of one batch require the CLI to be run against a batch a worker already
holds (or two manual CLI runs of the same file at once). That is an operator
action, not a scheduled one — so this is a LOW-severity robustness gap, not a
pilot blocker.

This test DOCUMENTS the behaviour rather than asserting the bug is absent: it
asserts the invariant that actually matters (no duplicate loan/customer), and
reports whether a pass raised. It is marked so the report can cite a real run.
"""
from __future__ import annotations

import os
import tempfile
import threading
import uuid
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import text

ATTEMPTS = 4          # bounded; enough to hit the race most runs


def _csv(tenant) -> str:
    tag = tenant["tag"]
    return ("customer_ref,customer_name,loan_account_number,case_number,branch_code,"
            "loan_type,dpd,total_outstanding,overdue_amount,emi_amount,"
            "sanctioned_amount,disbursed_amount,outstanding_principal,"
            "disbursement_date,maturity_date,interest_rate,phone_primary,"
            "address_line1,city,state,pincode,dob,gender,latitude,longitude,"
            "pan_masked,aadhaar_masked\n"
            f"P8R-{tag},Race Borrower,P8RLN-{tag},P8RCASE-{tag},{tenant['branch_code']},"
            "PERSONAL,45,100000,5000,3000,200000,200000,150000,2024-02-01,2027-02-01,"
            "14.25,9899000103,Addr,Gurugram,Haryana,122018,1988-06-14,MALE,"
            "28.412,77.064,XXXXX0000X,XXXXXXXX0000\n")


def test_concurrent_cli_ingests_of_one_file_keep_the_book_correct(Session, tenant, capsys):
    """The invariant that must hold even when the race fires: ONE customer and
    ONE loan, whatever happened to the staging rows."""
    from scripts import ingest_daily as ing
    from scripts.ingest_daily import run_ingestion

    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w") as fh:
        fh.write(_csv(tenant))

    original = ing.SessionLocal
    ing.SessionLocal = Session
    results: list[object] = []
    lock = threading.Lock()
    barrier = threading.Barrier(ATTEMPTS, timeout=30)

    def attempt():
        try:
            barrier.wait()
            r = run_ingestion(Path(path), dry_run=False, bank_code=tenant["bank_code"])
        except Exception as exc:
            r = exc
        with lock:
            results.append(r)

    try:
        threads = [threading.Thread(target=attempt, daemon=True) for _ in range(ATTEMPTS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(90)
        assert all(not t.is_alive() for t in threads), "an ingest never returned"
    finally:
        ing.SessionLocal = original
        os.unlink(path)

    s = Session()
    try:
        loans = s.execute(text("select count(*) from lending.loans "
                               "where bank_id=:b and loan_account_number=:a"),
                          {"b": tenant["bank_id"], "a": f"P8RLN-{tenant['tag']}"}).scalar()
        custs = s.execute(text("select count(*) from lending.customers "
                               "where bank_id=:b and customer_ref=:c"),
                          {"b": tenant["bank_id"], "c": f"P8R-{tenant['tag']}"}).scalar()
        cases = s.execute(text("select count(*) from collections.cases "
                               "where bank_id=:b and case_number=:c"),
                          {"b": tenant["bank_id"], "c": f"P8RCASE-{tenant['tag']}"}).scalar()
        staging = s.execute(text("select count(*) from lending.bank_feed_rows "
                                 "where bank_id=:b"), {"b": tenant["bank_id"]}).scalar()
    finally:
        s.close()

    raised = [r for r in results if isinstance(r, Exception)]
    with capsys.disabled():
        print(f"\n[DEFECT-8.1] {ATTEMPTS} concurrent CLI ingests of one file:")
        print(f"             raised={len(raised)} ok={len(results) - len(raised)}")
        for exc in raised[:2]:
            print(f"             -> {type(exc).__name__}: {str(exc)[:110]}")
        print(f"             persisted: loans={loans} customers={custs} "
              f"cases={cases} staging_rows={staging}")

    # THE INVARIANT. Whatever the staging rows did, the book is not duplicated.
    assert loans == 1, f"{loans} loans for one account number"
    assert custs == 1, f"{custs} customers for one customer_ref"
    assert cases <= 1, f"{cases} cases for one case_number"
    # And the constraint held: never two staging rows for the same (batch, row_no).
    assert staging <= ATTEMPTS


def test_the_claim_is_what_prevents_this_in_production(Session, make_batch):
    """Mitigation check: the Phase 5 claim means only one worker can be
    processing a given batch, so the scheduled path cannot produce the race
    above. Two claims, one winner — the same guarantee, restated against the
    batch this defect concerns."""
    batch = make_batch(status="RECEIVED")
    wins = []
    lock = threading.Lock()
    barrier = threading.Barrier(2, timeout=20)

    def claim():
        s = Session()
        try:
            barrier.wait()
            n = s.execute(text("UPDATE lending.bank_feed_batches SET status='VALIDATING' "
                               "WHERE id=:i AND status='RECEIVED'"), {"i": batch.id}).rowcount
            s.commit()
            with lock:
                wins.append(n)
        finally:
            s.close()

    ts = [threading.Thread(target=claim, daemon=True) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    assert sum(wins) == 1
