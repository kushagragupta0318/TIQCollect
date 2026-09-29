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
