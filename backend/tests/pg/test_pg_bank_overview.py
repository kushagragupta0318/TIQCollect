"""The bank Overview on the committed demo book, through 43's scoped views
(P3 C01-C03). Skipped, and says so, until the fixture carries those views.

What it holds: the Girivan book yields real figures for every KPI with a
definition; a session set to another bank sees none of Girivan's rows even
when asked for them by id; every filter either changes each KPI it can apply
to or the KPI says it cannot be filtered by it; Aravalli's snapshot is named.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.pg.test_pg_demo_fixture import db, sql_text  # noqa: F401 — the restored fixture

pytestmark = pytest.mark.filterwarnings("ignore")


def _session(engine, bank_id: str) -> Session:
    s = Session(bind=engine)
    s.execute(text("SELECT set_config('app.bank_id', :b, false), set_config('app.scope', 'BANK', false)"),
              {"b": bank_id})
    return s


@pytest.fixture(scope="module")
def views(db):  # noqa: F811
    with db.connect() as c:
        have = c.execute(text("SELECT count(*) FROM information_schema.views WHERE table_schema = 'analytics' "
                              "AND table_name LIKE '%\\_scoped'")).scalar()
    if have < 5:
        pytest.skip("the fixture does not carry the five analytics *_scoped views yet (43's v2_0013)")
    return db


def test_every_defined_kpi_has_a_figure_on_the_girivan_book(views):
    from app.demo.roster import BANK
    from app.services.bank.kpi_catalog import compute_overview
    with _session(views, BANK["id"]) as s:
        ov = compute_overview(s, BANK["id"])
    by = {k["id"]: k for k in ov.kpis}
    missing = {k for k, v in by.items() if not v["available"]}
    assert not missing, {k: by[k]["reason"] for k in missing}
    assert 0 < by["gnpa_pct"]["raw"] < 1 and by["delinquent_exposure"]["raw"] > 0
    assert 0 <= by["compliance_integrity"]["raw"] <= 100
    assert any("snapshot" in n for n in ov.notes), "Aravalli's transformed snapshot must be named"
    assert ov.totals and ov.narrative


def test_collection_efficiency_excludes_an_unread_agency_but_keeps_the_rest(views):
    """Aravalli has no opening reading (fixtures/README): it must drop out of the
    bank-wide figure, not blank it, and be unavailable only when selected alone."""
    from app.demo.roster import BANK, AGENCY  # AGENCY = Aravalli
    from app.services.bank.kpi_catalog import compute_overview
    from app.services.bank.kpi_filter import KpiFilter
    with _session(views, BANK["id"]) as s:
        bank_wide = compute_overview(s, BANK["id"])
        aravalli_only = compute_overview(s, BANK["id"], KpiFilter(agency=AGENCY["id"]))
    by_bank = {k["id"]: k for k in bank_wide.kpis}
    by_arav = {k["id"]: k for k in aravalli_only.kpis}
    assert by_bank["collection_efficiency"]["available"]        # the read agencies carry it
    assert not by_arav["collection_efficiency"]["available"]     # Aravalli alone has no reading


def test_a_session_for_another_bank_sees_none_of_girivans_rows(views):
    """The views filter on the session's bank; asking for Girivan by id from a
    Kumaon session returns nothing, never Girivan's figures."""
    from app.demo.roster import BANK, KUMAON_BANK
    from app.services.bank.kpi_catalog import compute_overview
    with _session(views, KUMAON_BANK["id"]) as s:
        ov = compute_overview(s, BANK["id"])
    assert ov.as_of is None and not any(k["available"] for k in ov.kpis)
    with _session(views, KUMAON_BANK["id"]) as s:
        own = compute_overview(s, KUMAON_BANK["id"])
    assert own.as_of is not None and any(k["available"] for k in own.kpis)


def test_every_filter_changes_each_kpi_it_applies_to_or_says_it_cannot(views):
    """Plan §5.2: set each filter; every KPI must change or state that it does
    not depend on that filter."""
    from app.demo import roster as R
    from app.services.bank.kpi_catalog import compute_overview
    from app.services.bank.kpi_filter import KpiFilter
    with _session(views, R.BANK["id"]) as s:
        base = {k["id"]: k for k in compute_overview(s, R.BANK["id"]).kpis}
        cases = {
            "agency": KpiFilter(agency=R.SAHYADRI.id),
            "product": KpiFilter(product="PERSONAL"),
            "geo": KpiFilter(geo=R.new_id("region", "WEST")),
            "bucket": KpiFilter(bucket="NPA"),
            "security": KpiFilter(security="SECURED"),
            "period": KpiFilter(period="fytd"),
        }
        for dim, f in cases.items():
            got = {k["id"]: k for k in compute_overview(s, R.BANK["id"], f).kpis}
            for kid, b in base.items():
                if not b["available"]:
                    continue
                g = got[kid]
                said = (not g["available"]) and ("cannot be filtered" in g["reason"] or "no reading" in g["reason"])
                # Compare the unrounded figure and its sub-line: West's cure rate is 16.708% against
                # 16.712% overall (235 vs 711 accounts), the same "16.7%" on the card.
                changed = g["available"] and (g["raw"] != b["raw"] or g["sub"] != b["sub"])
                same_by_design = dim == "period" and kid in {"delinquent_exposure", "placed_share", "unworked_exposure",
                                                             "gnpa_pct", "roll_forward_rate", "cure_rate"}
                assert said or changed or same_by_design, (dim, kid, b["value"], g["value"], g.get("reason"))
