"""B13b (v2_0013): the frozen facts equal their one Python definition, and the
scoped wrappers carry the tenant predicate. Postgres behaviour is in
tests/pg/test_pg_analytics_b.py."""
from __future__ import annotations

import importlib.util
import pathlib
from datetime import date

import pytest

from app.models.loan import PORTFOLIO_STATES, DPDBucket, LoanStatus, portfolio_state

PATH = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "v2_0013_analytics_b.py"


@pytest.fixture(scope="module")
def mig():
    spec = importlib.util.spec_from_file_location("v2_0013", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_dim_portfolio_state_is_the_python_state_space_in_order(mig):
    assert tuple(s[0] for s in mig.STATES) == PORTFOLIO_STATES
    assert [s[2] for s in mig.STATES] == list(range(len(PORTFOLIO_STATES)))
    assert {s[0] for s in mig.STATES if s[3]} == {"NPA_SUB", "NPA_DOUBTFUL"}
    assert {s[0] for s in mig.STATES if s[4]} == {"WRITTEN_OFF", "RESOLVED"}


def test_the_kept_promise_set_is_the_scoring_service_s(mig):
    from app.services.ml_scoring_service import PTP_KEPT
    assert set(mig.PTP_KEPT) == {s.value for s in PTP_KEPT}


@pytest.mark.parametrize("bucket, status, npa_since, as_of, want", [
    ("CURRENT", "ACTIVE", None, date(2026, 9, 1), "CURRENT"),
    ("BUCKET_1", "ACTIVE", None, date(2026, 9, 1), "SMA_0"),
    ("BUCKET_2", "ACTIVE", None, date(2026, 9, 1), "SMA_1"),
    ("BUCKET_3", "ACTIVE", None, date(2026, 9, 1), "SMA_2"),
    ("NPA", "ACTIVE", None, date(2026, 9, 1), "NPA_SUB"),                       # unknown start: never doubtful
    ("NPA", "NPA", date(2025, 9, 2), date(2026, 9, 1), "NPA_SUB"),               # one day short of 12 months
    ("NPA", "NPA", date(2025, 9, 1), date(2026, 9, 1), "NPA_DOUBTFUL"),
    ("NPA", "NPA", date(2024, 2, 29), date(2025, 2, 28), "NPA_DOUBTFUL"),        # Postgres clamps to month end
    ("BUCKET_1", "NPA", None, date(2026, 9, 1), "NPA_SUB"),                     # the status says NPA
    ("NPA", "WRITTEN_OFF", date(2020, 1, 1), date(2026, 9, 1), "WRITTEN_OFF"),   # terminal status wins
    ("BUCKET_2", "SETTLED", None, date(2026, 9, 1), "RESOLVED"),
    ("CURRENT", "CLOSED", None, date(2026, 9, 1), "RESOLVED"),
])
def test_portfolio_state(bucket, status, npa_since, as_of, want):
    assert portfolio_state(bucket, status, npa_since, as_of) == want


def test_every_bucket_and_status_maps_to_a_state():
    for b in DPDBucket:
        for st in LoanStatus:
            assert portfolio_state(b.value, st.value, None, date(2026, 9, 1)) in PORTFOLIO_STATES


def test_every_scoped_view_filters_on_the_tenant_and_exposes_no_sentinel(mig):
    for view, (mv, cols) in mig.SCOPED.items():
        sql = mig._scoped_sql(view, mv, cols)
        assert "WHERE bank_id = tenancy.current_bank_id()" in sql, view
        assert "OR agency_id = tenancy.current_agency_id()" in sql, view
        for c in ("agency_id", "region_id"):
            if c in cols:
                assert f"NULLIF({c}, " in sql, (view, c)
        # Runs as its owner: a security_invoker wrapper would need tiq_app to hold SELECT on the MV.
        assert "security_invoker" not in sql, view


def test_the_grants_never_give_the_app_a_materialized_view(mig):
    assert not [o for r, _, o in mig._grant_specs() if r == mig.APP_ROLE and ".mv_" in o]


def test_the_api_analytics_session_is_bound_to_the_callers_tenant(monkeypatch):
    """The *_scoped views read the session's tenant; an unbound session reads nothing."""
    from types import SimpleNamespace
    from app.core import database, dependencies
    from app.models.user import UserRole
    made = []

    class FakeSession:
        def __init__(self):
            self.info, self.closed = {}, False
            made.append(self)

        def in_transaction(self):
            return False

        def close(self):
            self.closed = True

    monkeypatch.setattr(database, "AnalyticsSession", FakeSession)
    user = SimpleNamespace(id="u-7", bank_id="b-1", agency_id="a-2", role=UserRole.AGENCY_MANAGER)
    gen = dependencies.get_tenant_analytics_db(user)
    db = next(gen)
    assert db.info[database.TENANT_CONTEXT] == {"bank_id": "b-1", "agency_id": "a-2", "scope": "AGENCY",
                                                "user_id": "u-7"}
    gen.close()
    assert made[0].closed
