"""Bank Alerts (plan §5.4, task C06) on the committed demo book: at least
one rule fires on a real, distressed collections book, every alert is
shaped the way DecisionAlerts.tsx expects, and a mismatched bank sees
nothing of Girivan's.
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
        pytest.skip("the fixture does not carry the five analytics *_scoped views yet")
    return db


def test_at_least_one_rule_fires_on_the_real_book(views):
    """A collections book with a real delinquent tail should trip SOMETHING
    — GNPA past 5% is close to a certainty on this book, but the test does
    not hard-code which rule, only that the feed is not silently empty."""
    from app.demo.roster import BANK
    from app.services.bank.alerts import compute_alerts
    with _session(views, BANK["id"]) as s:
        alerts = compute_alerts(s, s, BANK["id"])
    assert alerts, "no alert fired on a book that should have at least a GNPA breach"
    for a in alerts:
        assert a["severity"] in ("critical", "warning", "info")
        assert a["id"] and a["title"] and a["summary"] and a["basis"]
        assert a["metrics"] and all({"label", "value"} <= set(m) for m in a["metrics"])
        assert a["actions"] and all({"label", "target"} <= set(x) for x in a["actions"])


def test_alerts_sort_most_severe_first(views):
    from app.demo.roster import BANK
    from app.services.bank.alerts import compute_alerts
    with _session(views, BANK["id"]) as s:
        alerts = compute_alerts(s, s, BANK["id"])
    order = {"critical": 0, "warning": 1, "info": 2}
    severities = [order[a["severity"]] for a in alerts]
    assert severities == sorted(severities)


def test_gnpa_breach_fires_with_the_configured_threshold_in_its_own_text(views):
    from app.core.config import settings
    from app.demo.roster import BANK
    from app.services.bank.alerts import compute_alerts
    with _session(views, BANK["id"]) as s:
        alerts = compute_alerts(s, s, BANK["id"])
    gnpa = next((a for a in alerts if a["id"] == "gnpa_breach"), None)
    assert gnpa is not None, "GNPA breach did not fire on the real book"
    assert f"{settings.ALERT_GNPA_PCT * 100:.0f}%" in gnpa["title"]


def test_another_bank_sees_only_its_own_alerts(views):
    """Every rule reads bank_id = the session's own tenant; a session bound
    to Kumaon must never see a Girivan-flavoured title or figure."""
    from app.demo.roster import BANK, KUMAON_BANK
    from app.services.bank.alerts import compute_alerts
    with _session(views, BANK["id"]) as s:
        girivan = compute_alerts(s, s, BANK["id"])
    with _session(views, KUMAON_BANK["id"]) as s:
        kumaon = compute_alerts(s, s, KUMAON_BANK["id"])
    girivan_ids = {a["id"] for a in girivan}
    kumaon_ids = {a["id"] for a in kumaon}
    # Same RULE ids can legitimately fire for both banks (GNPA breach is a
    # rule name, not a Girivan-specific fact) — what must never happen is
    # one bank's AGENCY appearing in the other's alert text.
    from app.demo.roster import AGENCY  # Aravalli, Girivan-only
    for a in kumaon:
        assert AGENCY["code"] not in a["title"] and AGENCY["code"] not in a["summary"]
    assert girivan_ids or kumaon_ids   # sanity: the fixture gave us something to compare


def test_a_rule_that_raises_is_skipped_not_fatal(views, monkeypatch):
    """One bad rule must not take the whole feed down (compute_overview's
    own per-KPI savepoint discipline, extended here to two sessions)."""
    from app.demo.roster import BANK
    from app.services.bank import alerts as A

    def boom(adb, bank_id):
        raise RuntimeError("deliberate rule failure")

    monkeypatch.setattr(A, "_roll_forward_spike", boom)
    monkeypatch.setattr(A, "RULES", (A._gnpa_breach, A._efficiency_drop, A._sla_miss,
                                     A._fraud_and_fence, A._pending_placement, boom))
    with _session(views, BANK["id"]) as s:
        alerts = A.compute_alerts(s, s, BANK["id"])   # must not raise
    assert isinstance(alerts, list)
