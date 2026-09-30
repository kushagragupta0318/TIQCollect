"""The bank's model pages (endpoints/bank_models.py, services/bank/model_showcase.py).

Pinned: who may see them; tenant and region scoping with the uniform 404;
polarity (the stored probability is P(no payment), both are returned, labelled);
both reason-code formats normalised; abstention returned as abstention; the
live-equivalent figures and the live stance coverage; the six layers with only
the trained model marked as modelled.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.ml.pipeline.registry import resolve_version
from app.models.model_prediction import ModelPrediction
from app.models.tenancy import Bank, Branch
from app.models.user import User, UserRole
from app.services.bank import model_showcase as ms
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id
from tests._placement import make_loan, put_branches_in

BANK2 = test_id("bank:kumaon")
MISSING = test_id("nothing:here")
SERVING = resolve_version("recovery_risk")
DAY = date(2026, 9, 21)


def _user(db, key, role, *, bank, agency=None, phone):
    u = User(id=test_id(f"u:{key}"), email=f"{key}@example.test", phone=phone, full_name=key.title(),
             hashed_password=hash_password("Harbour-Lights-2026"), role=role, bank_id=bank, agency_id=agency)
    db.add(u)
    return u


def _pred(db, loan, *, bank=TEST_BANK_ID, version=SERVING, on=DAY, at_hour=10, p=0.70, band="C", modelled=True,
          stance="NONE", reasons=None, fallback=None, coverage=1.0):
    row = ModelPrediction(
        bank_id=bank, model_name="recovery_risk", model_version=version, artifact_sha256="b6682096" + "0" * 56,
        entity_type="case", entity_id=loan.id, loan_id=loan.id, as_of_date=on,
        scored_at=datetime(on.year, on.month, on.day, at_hour, tzinfo=timezone.utc),
        probability=p if modelled else None, points=520 if modelled else None, band=band if modelled else None,
        is_modelled=modelled, fallback_reason=fallback, features={"latest_disposition": stance, "calls_3m": 0},
        feature_coverage=coverage, scoring_versions={"model_artifact": version},
        reason_codes=reasons if reasons is not None else [
            {"kind": "feature", "rank": 1, "feature": "overdue_amount", "value": "56,840",
             "direction": "increases_risk", "contribution": 0.3238, "text": "debug"},
            {"kind": "feature", "rank": 2, "feature": "arrears_ratio", "value": "2",
             "direction": "decreases_risk", "contribution": -0.2321, "text": "debug"},
        ],
        contributions={"intercept": 0.8967, "overdue_amount": 0.3238, "arrears_ratio": -0.2321, "logit": 0.9884},
    )
    db.add(row)
    return row


@pytest.fixture()
def w():
    engine = make_engine()
    create_schema(engine)
    Session = make_session_factory(engine, info={})
    db = Session()
    db.add(Bank(id=BANK2, code="KFL", legal_name="Kumaon Finance Ltd", display_name="Kumaon Finance",
                timezone="Asia/Kolkata", brand={}, status="ACTIVE", is_demo=True))
    db.flush()
    db.add(Branch(id=test_id("branch:kfl"), bank_id=BANK2, branch_code="GGN044", name="Kumaon Gurugram",
                  is_active=True))
    put_branches_in(db, "GGN", bank_id=TEST_BANK_ID)
    db.flush()
    loans = [make_loan(db, n) for n in (1, 2, 3, 4)]
    foreign = make_loan(db, 11, bank_id=BANK2)
    users = {
        "ba": _user(db, "ba", UserRole.BANK_ADMIN, bank=TEST_BANK_ID, phone="9800000002"),
        "bt": _user(db, "bt", UserRole.BANK_TECHOPS, bank=TEST_BANK_ID, phone="9800000005"),
        "an": _user(db, "an", UserRole.BANK_ANALYST, bank=TEST_BANK_ID, phone="9800000003"),
        "am": _user(db, "am", UserRole.AGENCY_MANAGER, bank=TEST_BANK_ID, agency=TEST_AGENCY_ID, phone="9800000006"),
        "ba2": _user(db, "ba2", UserRole.BANK_ADMIN, bank=BANK2, phone="9800000004"),
    }
    db.commit()

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = override
    try:
        yield {"db": db, "c": TestClient(app), "loans": loans, "foreign": foreign, **users}
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()


def _h(user):
    return {"Authorization": "Bearer " + create_access_token(user.id, user.role.value, "dev-1")}


def _explain(w, user, loan_id):
    return w["c"].get(f"/api/v1/bank/loans/{loan_id}/explanation", headers=_h(user))


# ── GET /bank/models ─────────────────────────────────────────────────────────

def test_models_page_needs_ml_read(w):
    c = w["c"]
    assert c.get("/api/v1/bank/models", headers=_h(w["ba"])).status_code == 200
    assert c.get("/api/v1/bank/models", headers=_h(w["bt"])).status_code == 200
    assert c.get("/api/v1/bank/models", headers=_h(w["am"])).status_code == 403
    assert c.get("/api/v1/bank/models").status_code == 401


def test_the_six_layers_and_only_the_trained_one_is_modelled(w):
    body = w["c"].get("/api/v1/bank/models", headers=_h(w["ba"])).json()
    layers = {layer["key"]: layer for layer in body["layers"]}
    assert set(layers) == {"recovery_risk", "repayment_scorecard", "recovery_scorecard", "visit_priority",
                           "agent_competency", "allocator"}
    assert [k for k, v in layers.items() if v["is_modelled"]] == ["recovery_risk"]
    assert layers["recovery_risk"]["kind"] == "TRAINED_MODEL" and layers["recovery_risk"]["version"] == SERVING
    assert layers["repayment_scorecard"]["version"] == ms.SCORECARD_VERSION
    assert "not a model" in layers["repayment_scorecard"]["evidence"]
    from app.services.global_allocator import GlobalAllocator
    assert f"{GlobalAllocator.PRIORITY_UPLIFT:.0%} extra weight" in layers["visit_priority"]["acts_on"]
    assert "{" not in "".join(l["acts_on"] for l in body["layers"])            # every placeholder filled


def test_the_model_card_quotes_the_live_equivalent_beside_the_artifact(w):
    card = w["c"].get("/api/v1/bank/models", headers=_h(w["ba"])).json()["recovery_risk"]
    assert card["serving_version"] == SERVING
    assert card["artifact_metrics"]["gini"] == 0.5122 and card["artifact_metrics"]["ks"] == 38.66
    assert card["live_equivalent"]["gini"] == 0.4796 and card["live_equivalent"]["ks"] == 35.74
    assert "NO material payment" in card["stored_probability_means"]
    assert len(card["features"]) == 15 and {"code": "latest_disposition", "label": card["stance"]["feature_label"]} in card["features"]
    assert [b["band"] for b in card["bands"]] == ["A", "B", "C", "D", "E"]
    assert card["stance"]["related_features"] == ["latest_disposition", "disposition_recency_class"]


def test_an_artifact_that_will_not_load_is_reported_not_hidden(w, monkeypatch):
    """A deployment whose champion will not load still has one. Saying "unknown"
    would hide an operational fault on the page a bank reads for assurance."""
    monkeypatch.setattr(ms, "serving_version", lambda _model: None)
    card = w["c"].get("/api/v1/bank/models", headers=_h(w["ba"])).json()["recovery_risk"]
    assert card["serving_version"] is None
    assert card["configured_version"] == SERVING and card["artifact_loaded"] is False
    assert card["artifact_metrics"]["gini"] == 0.5122          # still read from the named artifact


def test_the_synthetic_warning_travels_with_the_page(w):
    body = w["c"].get("/api/v1/bank/models", headers=_h(w["ba"])).json()
    assert "synthetic" in body["synthetic_warning"].lower()


def test_stance_coverage_is_this_banks_newest_day_one_row_per_account(w):
    db, loans = w["db"], w["loans"]
    _pred(db, loans[0], on=DAY - timedelta(days=1), stance="WILL_PAY")          # an older day: ignored
    _pred(db, loans[0], stance="NONE", at_hour=9)
    _pred(db, loans[0], stance="MAY_PAY", at_hour=11)                           # a re-plan: newest row stands
    _pred(db, loans[1], stance="NONE")
    _pred(db, loans[2], stance="HARDSHIP")
    _pred(db, loans[3], modelled=False, fallback="only 40% of the model's 15 features were supplied")
    _pred(db, w["foreign"], bank=BANK2, stance="WILL_PAY")                      # another bank: never counted
    db.commit()
    card = w["c"].get("/api/v1/bank/models", headers=_h(w["ba"])).json()["recovery_risk"]
    assert card["stance"] == {**card["stance"], "latest_scoring_day": DAY.isoformat(),
                              "accounts_scored": 3, "accounts_with_stance": 2, "share": round(2 / 3, 4)}
    assert card["abstention"] == {"coverage_floor": 0.6, "latest_day_declined": 1, "latest_day_scored": 3}
    assert card["monitoring"]["first_outcomes_mature_from"] == (DAY - timedelta(days=1) + timedelta(days=30)).isoformat()
    other = w["c"].get("/api/v1/bank/models", headers=_h(w["ba2"])).json()["recovery_risk"]["stance"]
    assert (other["accounts_scored"], other["accounts_with_stance"]) == (1, 1)


def test_a_large_scoring_day_reports_the_share_as_a_sample_and_says_so(w, monkeypatch):
    """Nothing unbounded runs in a request: the counts stay exact, the share is
    measured over a bounded sample and is labelled as one."""
    monkeypatch.setattr(ms, "STANCE_SAMPLE_LIMIT", 2)
    db, loans = w["db"], w["loans"]
    for loan in loans:                                      # 4 accounts, all with a stance
        _pred(db, loan, stance="WILL_PAY")
    db.commit()
    stance = w["c"].get("/api/v1/bank/models", headers=_h(w["ba"])).json()["recovery_risk"]["stance"]
    assert stance["accounts_scored"] == 4                    # exact, from SQL
    assert (stance["share_sampled"], stance["sample_size"], stance["share"]) == (True, 2, 1.0)


def test_no_predictions_yet_is_an_empty_state_not_a_zero_share(w):
    stance = w["c"].get("/api/v1/bank/models", headers=_h(w["ba"])).json()["recovery_risk"]["stance"]
    assert stance["accounts_scored"] == 0 and stance["share"] is None and stance["latest_scoring_day"] is None


# ── GET /bank/loans/{id}/explanation ────────────────────────────────────────

def test_explanation_returns_both_probabilities_labelled_and_normalised_reasons(w):
    db, loan = w["db"], w["loans"][0]
    _pred(db, loan, at_hour=8, p=0.9)
    _pred(db, loan, at_hour=12, p=0.70)                                          # newest wins
    db.commit()
    body = _explain(w, w["an"], loan.id).json()                                  # an analyst has placement.read
    pred = body["prediction"]
    assert pred["p_no_payment"] == 0.70 and pred["p_payment"] == pytest.approx(0.30)
    assert pred["is_serving_version"] is True and pred["stance_recorded"] is False
    first, second = pred["reasons"]
    assert (first["feature"], first["direction"], first["unit"]) == ("overdue_amount", "increases_risk", "log_odds")
    assert first["label"] == "overdue amount" and first["magnitude"] == pytest.approx(0.3238)
    assert (second["direction"], second["signed"]) == ("decreases_risk", pytest.approx(-0.2321))
    assert "text" not in first                                                   # the debug sentence is not served
    assert pred["contributions"]["intercept"] == pytest.approx(0.8967)


def test_scorecard_era_reasons_are_points_lost(w):
    db, loan = w["db"], w["loans"][0]
    _pred(db, loan, version="1.1.0", reasons=[{"points": 108, "feature": "dpd", "points_lost": 31}])
    db.commit()
    reason = _explain(w, w["ba"], loan.id).json()["prediction"]["reasons"][0]
    assert (reason["unit"], reason["direction"], reason["magnitude"]) == ("points_lost", "increases_risk", 31.0)


def test_a_declined_score_is_returned_as_abstention(w):
    db, loan = w["db"], w["loans"][0]
    _pred(db, loan, modelled=False, coverage=0.4, reasons=[],
          fallback="only 40% of the model's 15 features were supplied (floor 60%)")
    db.commit()
    pred = _explain(w, w["ba"], loan.id).json()["prediction"]
    assert pred["is_modelled"] is False and pred["p_no_payment"] is None and pred["p_payment"] is None
    assert pred["band"] is None and "floor 60%" in pred["fallback_reason"] and pred["coverage_floor"] == 0.6


def test_an_unscored_loan_has_no_prediction(w):
    body = _explain(w, w["ba"], w["loans"][1].id).json()
    assert body["prediction"] is None and body["serving_version"] == SERVING


def test_a_prediction_cannot_even_be_written_with_a_tenant_its_loan_does_not_share(w):
    """The ORM refuses the row this service's bank filter defends against, so
    that filter only ever matters for a write that bypasses the ORM (raw SQL, a
    restored dump). Pinned here because the filter alone cannot prove itself:
    dropping it leaves every test green."""
    from app.models.tenancy_listener import TenantMismatchError
    db, loan = w["db"], w["loans"][0]
    _pred(db, loan, bank=BANK2)                              # BANK2's row, this bank's loan
    with pytest.raises(TenantMismatchError):
        db.flush()
    db.rollback()


def test_another_banks_loan_is_the_same_404_as_a_missing_one(w):
    _pred(w["db"], w["foreign"], bank=BANK2)
    w["db"].commit()
    missing = _explain(w, w["ba"], MISSING)
    foreign = _explain(w, w["ba"], w["foreign"].id)
    assert missing.status_code == foreign.status_code == 404
    assert missing.json() == foreign.json()
    assert _explain(w, w["ba2"], w["foreign"].id).status_code == 200


def test_a_region_limited_user_cannot_read_outside_the_region(w):
    from app.models.tenancy import Region
    db = w["db"]
    north = test_id(f"region:{TEST_BANK_ID}:NORTH")
    hrx = test_id("region:hrx-models")
    db.add(Region(id=hrx, bank_id=TEST_BANK_ID, parent_id=north, level="STATE", code="HRX", name="HRX",
                  path="NORTH.HRX"))
    db.flush()
    db.add(Branch(id=test_id("branch:hrx02"), bank_id=TEST_BANK_ID, branch_code="HRX02", name="HRX 02",
                  region_id=hrx, is_active=True))
    db.flush()
    outside = make_loan(db, 9, branch_code="HRX02")
    limited = _user(db, "an-hr", UserRole.BANK_ANALYST, bank=TEST_BANK_ID, phone="9800000009")
    limited.scope_region_id = test_id(f"region:{TEST_BANK_ID}:HR")
    db.commit()
    assert _explain(w, limited, outside.id).status_code == 404
    assert _explain(w, limited, w["loans"][0].id).status_code == 200
    assert _explain(w, w["ba"], outside.id).status_code == 200                   # unlimited


def test_an_agency_user_is_refused(w):
    assert _explain(w, w["am"], w["loans"][0].id).status_code == 403


def test_a_malformed_loan_id_is_the_uniform_404(w):
    r = _explain(w, w["ba"], "not-a-uuid")
    assert r.status_code == 404 and r.json() == _explain(w, w["ba"], MISSING).json()


def test_an_interaction_reason_names_both_inputs_in_words(w):
    db, loan = w["db"], w["loans"][0]
    _pred(db, loan, reasons=[{"kind": "interaction", "rank": 1, "feature": "latest_disposition x arrears_ratio",
                              "value": "NONE / 2", "direction": "increases_risk", "contribution": 0.12}])
    db.commit()
    reason = _explain(w, w["ba"], loan.id).json()["prediction"]["reasons"][0]
    assert reason["kind"] == "interaction"
    assert " together with " in reason["label"] and "latest_disposition" not in reason["label"]
