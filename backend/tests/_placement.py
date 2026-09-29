"""Test helpers for placement coverage (P3 D08).

Coverage fails closed (services/placement_service.py), so a test contract
places nothing until it covers the region of the loan's branch. The suite's
branches (tests/_db.TEST_BRANCH_CODES) carry no region; `cover()` gives them
one region tree and puts the contract's coverage on its root.
"""
from __future__ import annotations

from app.models.tenancy import AgencyRegion, Branch, Region
from tests._db import TEST_BANK_ID, TEST_BRANCH_CODES, test_id

# One chain, dot-joined as demo/world.py writes paths: ZONE > STATE > CITY.
TREE = (("ZONE", "NORTH", None), ("STATE", "HR", "NORTH"), ("CITY", "GGN", "HR"))
PATHS = {"NORTH": "NORTH", "HR": "NORTH.HR", "GGN": "NORTH.HR.GGN"}


def region_tree(db, *, bank_id: str = TEST_BANK_ID) -> dict[str, str]:
    """Create the NORTH > HR > GGN regions once; return code -> region id."""
    ids = {}
    for level, code, parent in TREE:
        rid = test_id(f"region:{bank_id}:{code}")
        if db.get(Region, rid) is None:
            db.add(Region(id=rid, bank_id=bank_id, parent_id=ids.get(parent), level=level, code=code,
                          name=code, path=PATHS[code]))
            db.flush()
        ids[code] = rid
    return ids


def put_branches_in(db, code: str = "GGN", *, branch_codes=TEST_BRANCH_CODES,
                    bank_id: str = TEST_BANK_ID) -> None:
    rid = region_tree(db, bank_id=bank_id)[code]
    for b in db.query(Branch).filter(Branch.bank_id == bank_id, Branch.branch_code.in_(branch_codes)):
        b.region_id = rid
    db.flush()


def cover(db, contract, code: str = "NORTH", *, branch_region: str = "GGN") -> None:
    """Contract covers region `code` (and its subtree); the suite's branches
    sit in `branch_region`."""
    put_branches_in(db, branch_region, bank_id=contract.bank_id)
    rid = region_tree(db, bank_id=contract.bank_id)[code]
    db.add(AgencyRegion(bank_id=contract.bank_id, agency_id=contract.agency_id, contract_id=contract.id,
                        region_id=rid))
    db.flush()


def make_loan(db, n: int = 1, *, bank_id: str = TEST_BANK_ID, dpd: int = 47, branch_code: str = "GGN044",
              loan_type=None, status=None, overdue: float = 24600.0):
    """One customer + loan in `bank_id` (its branch must exist there)."""
    from datetime import date
    from app.models.customer import Customer
    from app.models.loan import Loan, LoanStatus, LoanType
    cust = Customer(id=test_id(f"cust:{bank_id}:{n}"), bank_id=bank_id, customer_ref=f"C-{n:05d}",
                    full_name="Farhan Siddiqui", date_of_birth=date(1988, 6, 14), gender="MALE",
                    pan_masked="XXXXX4821K", aadhaar_masked="XXXXXXXX3307", phone_primary="9899000101",
                    address_line1="C-214, Sector 49", city="Gurugram", state="Haryana", pincode="122018",
                    latitude=28.412, longitude=77.064)
    loan = Loan(id=test_id(f"loan:{bank_id}:{n}"), bank_id=bank_id, loan_account_number=f"LN{n:08d}",
                customer_id=cust.id, loan_type=loan_type or LoanType.PERSONAL, branch_code=branch_code,
                sanctioned_amount=400000.0, disbursed_amount=400000.0, outstanding_principal=260000.0,
                total_outstanding=281000.0, overdue_amount=overdue, emi_amount=12300.0, dpd=dpd,
                status=status or LoanStatus.ACTIVE,
                disbursement_date=date(2024, 2, 1), maturity_date=date(2027, 2, 1), interest_rate=14.25)
    db.add_all([cust, loan])
    db.flush()
    return loan
