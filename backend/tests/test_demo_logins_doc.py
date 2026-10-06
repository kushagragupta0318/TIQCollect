"""scripts/generate_demo_logins_doc.py's pure logic: no Postgres needed here
(its SQL uses FILTER, Postgres-only — tests/pg covers that separately if it
ever gets a fixture). This file pins two bugs caught before the doc shipped:
an agency with several regions was read via LIMIT 1 and showed 0 for
everything; a raw uuid.UUID key from psycopg2 never matched a User.id str,
so every agent showed "—".
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from scripts.generate_demo_logins_doc import _agency_headline, _money, _pct, build_doc


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


@dataclass
class _FakeConn:
    """Returns whichever canned result matches the query's first distinctive
    word after WITH/SELECT, so one fake stands in for both queries this
    script issues."""
    by_marker: dict

    def execute(self, stmt, params=None):
        sql = str(stmt)
        for marker, rows in self.by_marker.items():
            if marker in sql:
                return _FakeResult(rows)
        raise AssertionError(f"no fake response registered for: {sql[:80]}")


def test_agency_headline_sums_across_every_region_not_the_first_one():
    """The mv's grain is (month, bank, agency, region): summing catches the
    LIMIT-1 bug (every generated agency showed 0 collected) dead."""
    conn = _FakeConn({"mv_agency_scorecard_monthly": [
        {"month_start": "2026-09-01", "collected_where_due_known": 40000.0, "collectible_due": 200000.0,
         "verified_collections": 40000.0, "visits": 10, "met_visits": 6, "ptps_matured": 2, "ptps_honoured": 1,
         "active_placements_eom": 5, "agents_active": 2},
    ]})
    h = _agency_headline(conn, bank_id="b", agency_id="a")
    assert h["collection_efficiency"] == pytest.approx(0.2)
    assert h["verified_collections"] == 40000.0 and h["visits"] == 10


def test_agency_headline_is_unavailable_only_when_collectible_due_is_unread():
    conn = _FakeConn({"mv_agency_scorecard_monthly": [
        {"month_start": "2026-09-01", "collected_where_due_known": None, "collectible_due": None,
         "verified_collections": 90000.0, "visits": 30, "met_visits": 20, "ptps_matured": 5, "ptps_honoured": 2,
         "active_placements_eom": 40, "agents_active": 4},
    ]})
    h = _agency_headline(conn, bank_id="b", agency_id="a")
    assert h["collection_efficiency"] is None
    assert h["verified_collections"] == 90000.0            # the real total still shows; only the ratio abstains


def test_agency_headline_missing_agency_returns_none():
    conn = _FakeConn({"mv_agency_scorecard_monthly": []})
    assert _agency_headline(conn, bank_id="b", agency_id="a") is None


def test_agent_lookup_survives_a_native_uuid_key_from_the_driver():
    """psycopg2 hands back uuid.UUID for a uuid column; User.id is a plain
    str throughout the ORM. An unstrung dict key made every lookup miss."""
    real_uuid = uuid.uuid4()          # what the driver would actually return
    conn = _FakeConn({"workforce.agents g": [
        {"agent_id": "ag1", "user_id": real_uuid, "visits": 12, "met_visits": 9, "collected": 50000.0,
         "ptps_live": 3, "ptps_kept": 2},
    ]})
    from scripts.generate_demo_logins_doc import _agent_stats
    stats = _agent_stats(conn, "b")
    assert stats[str(real_uuid)]["collected"] == 50000.0
    assert str(real_uuid) in stats and real_uuid not in stats       # the exact bug: wrong key type


def test_money_and_pct_formatting():
    assert _money(None) == "₹0"
    assert _money(50000) == "₹50,000"
    assert _money(1_250_000) == "₹12.50 L"
    assert _money(120_000_000) == "₹12.00 Cr"
    assert _pct(None) == "not available"
    assert _pct(0.294) == "29.4%"


def _user(uid, email, role, agency_id, full_name=None):
    return SimpleNamespace(id=uid, email=email, role=SimpleNamespace(value=role), agency_id=agency_id,
                           full_name=full_name or email.split("@")[0])


def test_build_doc_lists_only_staff_targets_and_a_performance_spread(monkeypatch):
    """The doc's field-agent rows are exactly staff_targets()' picks (never
    an independently chosen 'best' set the login mechanism didn't grant),
    sorted strong to weak by real collections."""
    import scripts.generate_demo_logins_doc as mod

    bank_id, agency_id = "bank-1", "agency-1"
    weak = _user("u-weak", "weak.agent@x.test", "FIELD_AGENT", agency_id, "Weak Agent")
    admin = _user("u-admin", "admin@x.test", "AGENCY_ADMIN", agency_id, "The Admin")
    bank_user = _user("u-bank", "bank@x.test", "BANK_ADMIN", None, "Bank Person")

    class FakeDb:
        def connection(self):
            return conn
        def query(self, model):
            return self
        def filter(self, *a, **k):
            return self
        def one_or_none(self):
            return SimpleNamespace(id=bank_id, code="TESTBANK")
        def order_by(self, *a, **k):
            return self
        def all(self):
            return [SimpleNamespace(id=agency_id, bank_id=bank_id, code="AGY-X", legal_name="X Recovery Ltd.",
                                    trade_name="X Recovery", status="ACTIVE")]

    conn = _FakeConn({
        "mv_agency_scorecard_monthly": [
            {"month_start": "2026-09-01", "collected_where_due_known": 10000.0, "collectible_due": 50000.0,
             "verified_collections": 10000.0, "visits": 4, "met_visits": 2, "ptps_matured": 1, "ptps_honoured": 1,
             "active_placements_eom": 3, "agents_active": 1},
        ],
        "workforce.agents g": [
            {"agent_id": "ag-weak", "user_id": "u-weak", "visits": 5, "met_visits": 2, "collected": 1000.0,
             "ptps_live": 1, "ptps_kept": 0},
        ],
    })
    monkeypatch.setattr(mod, "staff_targets", lambda db, *, bank_code, agents_per_agency: (
        [bank_user, admin, weak], 1))

    doc = build_doc(FakeDb(), bank_code="TESTBANK", agents_per_agency=4)
    assert "bank@x.test" in doc and "admin@x.test" in doc and "weak.agent@x.test" in doc
    assert "1,000" in doc or "₹1,000" in doc                 # the real collected figure, not invented
    assert "The password" not in doc and "password:" not in doc.lower().replace("shared demo password", "")
