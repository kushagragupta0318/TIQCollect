# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# New file, 2026-09-28 (P3 D05 fast-forward). list_agency_directory and
# GET /bank/agencies-directory (endpoints/bank_agencies_admin.py).
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.errors import AppException
from app.core.security import create_access_token
from app.main import app
from app.models.tenancy import Region
from app.models.user import User, UserRole
from app.services.bank import agency_service
from tests._db import TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id

_NCR = {"type": "Polygon", "coordinates": [[
    [77.0, 28.4], [77.2, 28.4], [77.2, 28.6], [77.0, 28.6], [77.0, 28.4],
]]}


@pytest.fixture()
def w():
    engine = make_engine()
    create_schema(engine)
    Session = make_session_factory(engine)
    db = Session()

    bank_admin = User(id=test_id("u:bank-admin:dir"), email="deepak.dir@meridiantrust.example",
                      phone="9810007001", full_name="Deepak Rao", hashed_password="x",
                      role=UserRole.BANK_ADMIN, bank_id=TEST_BANK_ID)
    db.add(bank_admin)
    db.flush()

    zone = Region(id=test_id("region:dir:zone"), bank_id=TEST_BANK_ID, level="ZONE", code="NORTH",
                 name="North Zone", path="/north/", coverage_geojson=_NCR)
    db.add(zone)
    db.flush()
    region = Region(id=test_id("region:dir:region"), bank_id=TEST_BANK_ID, parent_id=zone.id, level="REGION",
                    code="NCR", name="NCR", path="/north/ncr/", coverage_geojson=_NCR, latitude=28.5,
                    longitude=77.1)
    other_region = Region(id=test_id("region:dir:other"), bank_id=TEST_BANK_ID, level="ZONE", code="SOUTH",
                          name="South Zone", path="/south/")
    db.add_all([region, other_region])
    db.commit()

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = override
    try:
        yield {"db": db, "bank_admin": bank_admin, "zone": zone, "region": region, "other_region": other_region}
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()


def _h(user: User) -> dict:
    return {"Authorization": "Bearer " + create_access_token(user.id, user.role.value, "dev-1")}


def _agency_with_contract(db, bank_admin, region, *, legal_name="Konkan Recovery Services LLP",
                          loan_type="PERSONAL") -> str:
    out = agency_service.create_draft(db, bank_admin, legal_name=legal_name)
    agency_service.update_coverage_and_contract(
        db, bank_admin, out["agency_id"], max_agents=10, region_ids=[region.id],
        contract_terms=[{"loan_type": loan_type, "dpd_bucket": "BUCKET_1", "commission_pct": 12.5}],
    )
    return out["agency_id"]


def test_directory_lists_an_agency_with_its_contract_coverage_and_products(w):
    db, bank_admin, region = w["db"], w["bank_admin"], w["region"]
    agency_id = _agency_with_contract(db, bank_admin, region)

    rows = agency_service.list_agency_directory(db, bank_admin)
    row = next(r for r in rows if r["agency_id"] == agency_id)
    assert row["contract"]["max_agents"] == 10
    assert [r["region_id"] for r in row["covered_regions"]] == [region.id]
    assert row["authorised_products"] == ["PERSONAL"]
    assert "score" not in row


def test_directory_filters_by_region_matches_descendants_via_path(w):
    """Filtering on the ZONE should also match an agency covering the
    REGION beneath it, via the path prefix — a bank user picking "North
    Zone" expects every agency under it, not just an exact-region match."""
    db, bank_admin, zone, region = w["db"], w["bank_admin"], w["zone"], w["region"]
    agency_id = _agency_with_contract(db, bank_admin, region)

    rows = agency_service.list_agency_directory(db, bank_admin, region_id=zone.id)
    assert any(r["agency_id"] == agency_id for r in rows)


def test_directory_filter_by_region_excludes_uncovered_agencies(w):
    db, bank_admin, region, other_region = w["db"], w["bank_admin"], w["region"], w["other_region"]
    _agency_with_contract(db, bank_admin, region)

    rows = agency_service.list_agency_directory(db, bank_admin, region_id=other_region.id)
    assert rows == []


def test_directory_filter_by_region_rejects_an_unknown_id(w):
    db, bank_admin = w["db"], w["bank_admin"]
    with pytest.raises(AppException) as exc:
        agency_service.list_agency_directory(db, bank_admin, region_id=test_id("region:nonexistent"))
    assert exc.value.status_code == 422


def test_directory_filter_by_region_rejects_a_real_region_from_another_bank(w):
    """Coordinator audit LOW: this filter used to query Region with no
    bank_id scope at all — a real, existing region belonging to a DIFFERENT
    bank was previously accepted and its .path used as the coverage
    filter, leaking that another tenant's region even exists."""
    from app.models.tenancy import Bank
    from tests._db import test_id as _tid
    db, bank_admin = w["db"], w["bank_admin"]
    other_bank_id = _tid("bank:directory-other")
    db.add(Bank(id=other_bank_id, code="DIROTH", legal_name="Directory Other Bank Ltd.",
               display_name="Directory Other Bank"))
    db.flush()
    foreign_region = Region(id=_tid("region:foreign"), bank_id=other_bank_id, level="ZONE", code="FOREIGN",
                            name="Foreign Zone", path="/foreign/")
    db.add(foreign_region)
    db.commit()
    with pytest.raises(AppException) as exc:
        agency_service.list_agency_directory(db, bank_admin, region_id=foreign_region.id)
    assert exc.value.status_code == 422


def test_directory_filters_by_authorised_product(w):
    db, bank_admin, region = w["db"], w["bank_admin"], w["region"]
    personal = _agency_with_contract(db, bank_admin, region, legal_name="Personal Loans Agency",
                                     loan_type="PERSONAL")
    auto = _agency_with_contract(db, bank_admin, region, legal_name="Auto Loans Agency", loan_type="AUTO")

    rows = agency_service.list_agency_directory(db, bank_admin, loan_type="AUTO")
    ids = {r["agency_id"] for r in rows}
    assert auto in ids
    assert personal not in ids


def test_directory_filters_by_contract_expiry(w):
    import datetime
    db, bank_admin, region = w["db"], w["bank_admin"], w["region"]
    out = agency_service.create_draft(db, bank_admin, legal_name="Expiring Soon Agency")
    agency_service.update_coverage_and_contract(
        db, bank_admin, out["agency_id"], region_ids=[region.id],
        start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 10, 1),
    )
    other = _agency_with_contract(db, bank_admin, region, legal_name="Long Contract Agency")
    agency_service.update_coverage_and_contract(db, bank_admin, other, end_date=datetime.date(2028, 1, 1))

    rows = agency_service.list_agency_directory(db, bank_admin, contract_expiring_before=datetime.date(2027, 1, 1))
    ids = {r["agency_id"] for r in rows}
    assert out["agency_id"] in ids
    assert other not in ids


def test_directory_agency_with_no_contract_yet_has_null_contract_and_empty_coverage(w):
    db, bank_admin = w["db"], w["bank_admin"]
    out = agency_service.create_draft(db, bank_admin, legal_name="Just Drafted Agency")

    rows = agency_service.list_agency_directory(db, bank_admin)
    row = next(r for r in rows if r["agency_id"] == out["agency_id"])
    assert row["contract"] is None
    assert row["covered_regions"] == []
    assert row["authorised_products"] == []


def test_http_directory_is_scoped_and_reachable(w):
    db, bank_admin, region = w["db"], w["bank_admin"], w["region"]
    _agency_with_contract(db, bank_admin, region)
    client = TestClient(app)
    r = client.get("/api/v1/bank/agencies-directory", headers=_h(bank_admin))
    assert r.status_code == 200, r.text
    assert len(r.json()) >= 1


def test_http_directory_rejects_a_malformed_region_id_at_the_boundary(w):
    """test_ids.py's tripwire (CI-found): region_id is UUIDQuery now, so a
    string that isn't even a UUID 404s before list_agency_directory's own
    DB lookup ever runs — distinct from a well-formed id naming no real
    region, which is 422 (see test_directory_filter_by_region_rejects_an_unknown_id)."""
    client = TestClient(app)
    r = client.get("/api/v1/bank/agencies-directory", params={"region_id": "not-a-uuid"},
                   headers=_h(w["bank_admin"]))
    assert r.status_code == 404, r.text
