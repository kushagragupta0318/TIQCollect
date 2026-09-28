"""The demo roster (app/demo/roster.py, B16): the ONE place a demo name lives.

Pins three things: B15's transformed book keeps every id (the constants moved,
they did not change), the roster matches Appendix C, and every identity
detail is in a valid real-world FORMAT while naming nothing real.
"""
from __future__ import annotations

import re
from collections import Counter

from app.demo import roster as r
from app.models.loan import DPDBucket, LoanType
from app.models.tenancy import AGENCY_STATUSES, DOC_TYPES, REGION_LEVELS


def test_b15_ids_are_unchanged_by_the_move():
    """Literal ids from the fixture B15 built (7fa1cf5): a changed key or
    namespace would orphan every row of Aravalli's history."""
    assert r.BANK["id"] == "d061537b-896b-5ef9-b284-7d33c51904a4"
    assert r.AGENCY["id"] == "8fc42163-143e-50bc-a8ac-74c09168a0ed"
    assert r.CONTRACT["id"] == "8d3aeed7-2047-5cde-8895-10b4876190c9"
    assert r.new_id("region", "NCR") == "52d7ae35-2850-5e0a-b23b-dd7a9de1538c"
    assert r.new_id("user", "ananya.iyer") == "ac579f46-3139-5324-8ec9-1f2299ba166a"


def test_the_transform_reads_the_roster_rather_than_restating_it():
    from scripts import migrate_v1_to_v2 as m
    for name in ("BANK", "AGENCY", "CONTRACT", "COMMISSION", "REGIONS", "BANK_USERS", "V1_STAFF",
                 "MASTER_ACCOUNTS", "AGENCY_DOMAIN", "BANK_DOMAIN", "NAMESPACE_TIQ_V2"):
        assert getattr(m, name) is getattr(r, name), name
    assert m.new_id is r.new_id


def test_appendix_c2_lifecycle_and_workforce():
    girivan = [a for a in r.AGENCIES if a.bank_key == "GIRIVAN"]
    assert [a.key for a in girivan] == ["ARAVALLI", "SARTHAK", "RAJPUTANA", "AWADH", "SAHYADRI",
                                        "SABARMATI", "DECCAN", "COROMANDEL", "HOOGHLY"]
    assert Counter(a.row["status"] for a in girivan) == {"ACTIVE": 7, "SUSPENDED": 1, "PENDING": 1}
    assert r.AWADH.row["status"] == "SUSPENDED" and r.AWADH.row["suspended_at"].date().isoformat() == "2026-09-02"
    assert r.HOOGHLY.n_agents == 0 and len(r.HOOGHLY.missing_docs) == 2 and r.HOOGHLY.invite_sent is not None
    # Appendix C.2's 154, plus the three women who joined Aravalli in 2026-09
    # (coordinator, ALLOC-G option (b)).
    assert sum(a.n_agents for a in girivan) == 154 + 3 and r.ALMORA.n_agents == 8
    assert r.ALMORA.bank_key == "KUMAON" and r.ALMORA.bank_id == r.KUMAON_BANK["id"]
    assert r.SAHYADRI.expiring_docs == {"INSURANCE": 21}
    assert all(s in AGENCY_STATUSES for s in (a.row["status"] for a in r.AGENCIES))


def test_every_bank_and_agency_is_marked_demo_and_every_domain_is_test():
    assert all(b["is_demo"] for b in r.BANKS.values())
    assert all(a.row["is_demo"] for a in r.AGENCIES)
    assert all(d.endswith(".test") for d in r.DEMO_EMAIL_DOMAINS)
    for a in r.AGENCIES:
        for p in a.people:
            assert p["email"].endswith("@" + a.domain) and re.fullmatch(r"\+919\d{9}", p["phone"])


def test_identity_details_are_in_real_formats():
    for a in r.AGENCIES:
        row = a.row
        company_type = "C" if row["entity_type"] == "PVT_LTD" else "F"
        assert re.fullmatch(r"[A-Z]{3}[CF][A-Z]\d{4}[A-Z]", row["pan"]) and row["pan"][3] == company_type, a.key
        assert row["pan"][4] == row["legal_name"][0], a.key          # 5th PAN letter = the name's initial
        assert row["gstin"][2:12] == row["pan"] and len(row["gstin"]) == 15
        if row["entity_type"] == "PVT_LTD":
            assert re.fullmatch(r"U\d{5}[A-Z]{2}\d{4}PTC\d{6}", row["cin"]), a.key
        else:
            assert re.fullmatch(r"[A-Z]{3}-\d{4}", row["cin"]), a.key            # an LLPIN
    # Every new GSTIN carries the real mod-36 check character; Aravalli's is
    # B15's, kept byte-identical (see the roster's changelog).
    for a in r.GENERATED_AGENCIES:
        assert r.gstin(a.row["gstin"][:2], a.row["pan"]) == a.row["gstin"]


def test_no_placeholder_or_real_lender_name_is_in_the_roster():
    blob = repr([r.BANKS, [a.row for a in r.AGENCIES], r.NAME_POOLS, r.GIRIVAN_BRANCHES_EXTRA]).lower()
    for bad in ("abc", "test bank", "synthetic bank", "manager1@", "agent0", "meridian", "northfield",
                "hdfc", "icici", "sbi ", "axis bank", "kotak", "konkan"):
        assert bad not in blob, bad


def test_contracts_and_slabs():
    for a in r.AGENCIES:
        c = a.contract
        assert c["agency_id"] == a.id and c["bank_id"] == a.bank_id
        assert 365 <= (c["end_date"] - c["start_date"]).days + 1 <= 731          # a 12-24 month term
        assert 3 <= c["sla_first_visit_days"] <= 7 and 60 <= c["recall_no_activity_days"] <= 90
        assert 500_000 <= c["security_deposit"] <= 2_500_000
        assert set(a.commission) == {b.value for b in DPDBucket}
        rates = [float(a.commission[b]) for b in r.DPD_BUCKETS]
        assert rates == sorted(rates), a.key                                     # deeper buckets pay more
        assert set(a.products) <= {t.value for t in LoanType}
    assert len({tuple(sorted(a.commission.items())) for a in r.AGENCIES}) >= 8   # slabs differ by agency


def test_geography_is_a_consistent_tree_covering_every_served_city():
    for regions in (r.REGIONS + r.GIRIVAN_REGIONS_EXTRA, r.KUMAON_REGIONS):
        codes = {}
        for level, code, _name, parent, _lat, _lon in regions:
            assert level in REGION_LEVELS and (level == "ZONE") == (parent is None)
            assert (level, code) not in codes, code                              # uq(bank, level, code)
            if parent is not None:
                assert parent in {c for (_, c) in codes}, (code, parent)          # parents come first
            codes[(level, code)] = parent
    girivan_cities = {c for (lv, c, *_rest) in r.REGIONS + r.GIRIVAN_REGIONS_EXTRA if lv == "CITY"}
    for a in r.AGENCIES:
        cities = girivan_cities if a.bank_key == "GIRIVAN" else {"PUNE"}
        assert set(a.serves) <= cities, a.key
        assert all(c in r.LOCALITIES and c in r.CITY_CULTURE for c in a.serves)
    assert len(girivan_cities) >= 14 and {z for (lv, z, *_x) in r.GIRIVAN_REGIONS_EXTRA if lv == "ZONE"} \
        == {"WEST", "SOUTH", "EAST"}                                             # + B15's NORTH: four zones
    assert {city for city, _ in r.GIRIVAN_BRANCHES_EXTRA.values()} <= girivan_cities


def test_documents_are_the_c3_list_and_valid_doc_types():
    assert {d for d, _, _ in r.AGENCY_DOCUMENTS} <= set(DOC_TYPES)
    assert len(r.AGENCY_DOCUMENTS) == 7
    assert set(r.HOOGHLY.missing_docs) <= {d for d, _, _ in r.AGENCY_DOCUMENTS}
    assert r.SPECIMEN_FOOTER == "Specimen — fictional demo document"


def test_aravalli_gender_is_explicit_roster_data():
    """Gender is stated per person, never derived from a name (coordinator,
    2026-09-28). The 18 v1 agents are listed by their v1 names; the three
    who joined in September are women with no history."""
    assert len(r.ARAVALLI_V1_AGENT_GENDER) == 18
    assert set(r.ARAVALLI_V1_AGENT_GENDER.values()) <= {"MALE", "FEMALE"}
    assert [g for _n, g, *_x in r.ARAVALLI_NEW_AGENTS] == ["FEMALE"] * 3
    assert all(j.year == 2026 and j.month == 9 and j <= r.ANCHOR_DATE for _n, _g, j, *_x in r.ARAVALLI_NEW_AGENTS)
    assert r.ARAVALLI.n_agents == 21


def test_name_pools_include_women_everywhere():
    """ALLOC-G: the v1 book had no gender at all and 926 decisions BLOCKED on
    'needs a female agent'. Every culture can draw a woman."""
    for culture, pool in r.NAME_POOLS.items():
        assert len(pool["female"]) >= 6 and len(pool["male"]) >= 6 and len(pool["surnames"]) >= 8, culture
    assert set(r.CITY_CULTURE.values()) <= set(r.NAME_POOLS)
