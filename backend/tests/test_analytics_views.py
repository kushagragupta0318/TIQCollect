"""B13a: the analytics layer's frozen facts equal their one Python definition.

v2_0007 renders dim_product, dim_bucket and the PTP-kept set as literals (a
migration must not change meaning later). These tests fail the day any of
the Python definitions moves without a new revision. The views' live
evidence (they reconcile exactly with the base tables on the B15 book) is in
the B13a commit."""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

from app.ml.eligibility import _SECURED, _UNSECURED
from app.models.loan import LoanType, dpd_bucket_for

PATH = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "v2_0007_analytics.py"


@pytest.fixture(scope="module")
def mig():
    spec = importlib.util.spec_from_file_location("v2_0007", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_dim_product_is_eligibility_s_secured_and_unsecured_sets(mig):
    rendered = {lt: cls for lt, _, cls in mig.PRODUCTS}
    assert set(rendered) == {t.value for t in LoanType}
    for t in LoanType:
        want = "SECURED" if t in _SECURED else "UNSECURED" if t in _UNSECURED else "MIXED"
        assert rendered[t.value] == want, t


def test_dim_bucket_is_dpd_bucket_for_over_0_to_400(mig):
    """The 'seven copies' rule: generated, never restated. Every DPD 0..400
    falls in exactly the rendered row whose range holds it."""
    for dpd in range(0, 401):
        rows = [b for b, _, lo, hi, _, _ in mig.BUCKETS if lo <= dpd and (hi is None or dpd <= hi)]
        assert rows == [dpd_bucket_for(dpd).value], dpd
    assert [b[4] for b in mig.BUCKETS] == list(range(len(mig.BUCKETS)))           # sort_order
    assert {b[0] for b in mig.BUCKETS if b[5]} == {"NPA"}                            # is_npa


def test_the_kept_promise_set_is_the_scoring_service_s(mig):
    from app.services.ml_scoring_service import PTP_KEPT
    assert set(mig.PTP_KEPT) == {s.value for s in PTP_KEPT}


def test_the_refresher_refreshes_exactly_the_materialized_views_created(mig):
    """B13a's two, then B13b's three (v2_0013)."""
    from app.workers.tasks.analytics_refresh import MATERIALIZED_VIEWS
    b_path = PATH.parent / "v2_0013_analytics_b.py"
    spec = importlib.util.spec_from_file_location("v2_0013", b_path)
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    assert tuple(MATERIALIZED_VIEWS) == tuple(mig.MATERIALIZED) + tuple(b.MATERIALIZED)
    src = PATH.read_text(encoding="utf-8")
    for mv in mig.MATERIALIZED:
        # CONCURRENTLY needs a unique index over plain columns.
        assert f"CREATE UNIQUE INDEX uq_{mv} ON analytics.{mv}" in src
    assert set(b.UNIQUE) == set(b.MATERIALIZED)


def test_every_plain_view_is_security_invoker(mig):
    """So base-table RLS (A13) applies to the caller, not to the view owner."""
    src = PATH.read_text(encoding="utf-8")
    plain = [l for l in src.splitlines() if "CREATE VIEW analytics." in l]
    assert plain and all("security_invoker = true" in l for l in plain)
