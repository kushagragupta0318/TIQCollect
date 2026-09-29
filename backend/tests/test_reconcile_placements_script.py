"""scripts/reconcile_placements: the one-shot end of placements on closed loans."""
from __future__ import annotations

import json
from datetime import date

from app.models.loan import LoanStatus
from app.models.placement import Placement
from app.models.tenancy import AgencyContract
from app.services.placement_service import PlacementService
from scripts import reconcile_placements as script
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory
from tests._placement import cover, make_loan


def test_dry_run_counts_and_the_real_run_ends_them_once(monkeypatch, capsys):
    engine = make_engine()
    create_schema(engine)
    Session = make_session_factory(engine, info={})
    db = Session()
    c = AgencyContract(bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID, contract_no="C/1", status="ACTIVE",
                       start_date=date(2000, 1, 1), end_date=date(2099, 12, 31))
    db.add(c)
    db.flush()
    cover(db, c)
    loan = make_loan(db)
    PlacementService(db).place_new_loan(loan, agency_id=TEST_AGENCY_ID, on=date(2026, 9, 1), source="FEED")
    loan.status = LoanStatus.WRITTEN_OFF
    db.commit()
    monkeypatch.setattr(script, "SessionLocal", Session)

    assert script.main(["--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out.strip())["ended"] == 1
    assert db.query(Placement).one().status == "ACTIVE"

    assert script.main([]) == 0
    assert json.loads(capsys.readouterr().out.strip())["by_status"] == {"RETURNED": 1}
    db.expire_all()
    assert db.query(Placement).one().status == "RETURNED"
    assert script.main([]) == 0 and json.loads(capsys.readouterr().out.strip())["ended"] == 0
    assert script.main(["--bank", "NOPE"]) == 1
