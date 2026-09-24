# ─── CHANGELOG (prototype → product) ───
# New file, 2026-09-24. H14 — services/visit_report_extraction.py and the
# POST /agent/cases/{case_id}/visit-extraction route.
#
# No database and no network. The LLM is stubbed at llm.complete, the seam's
# public contract (core/llm.py is being extended by F01; complete() and its
# LLMResult are what it keeps stable). The route test overrides auth and the
# two case-access helpers, so it does not depend on which tenant columns the
# v2 models make NOT NULL.
#
# The rule-based transcripts are a golden set: each is the kind of sentence
# Whisper returns for a Hindi/English note, translated, and each expectation
# was written before the rule that meets it.
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core import llm
from app.models.visit import VisitOutcome
from app.services import visit_report_extraction as vre

TODAY = date(2026, 9, 24)          # a Thursday
CASE_ID = "5b0c7a52-3d1e-4f6a-9c2b-8e4d1a7f6c30"


@pytest.fixture
def llm_says(monkeypatch):
    """Make llm.complete return what the test says, and record the call."""
    calls = []

    def _set(result):
        def fake(prompt, **kwargs):
            calls.append({"prompt": prompt, **kwargs})
            return result
        monkeypatch.setattr(vre.llm, "complete", fake)
        return calls
    return _set


@pytest.fixture
def no_llm(llm_says):
    return llm_says(llm.LLMResult(status=llm.NOT_CONFIGURED, provider="none",
                                  failure_reason="No API key configured for the selected provider"))


def _by_field(res):
    return {s.field: s.value for s in res.suggestions}


# ── rule-based golden set (LLM not configured) ───────────────────────────────
GOLDEN = [
    ("Met the borrower. He will pay Rs 5,000 on the 30th.",
     {"outcome": "PTP", "ptp_amount": 5000.0, "ptp_date": "2026-09-30", "person_met": "BORROWER"}),
    ("Customer said he will transfer 12000 rupees tomorrow after his salary is credited.",
     {"outcome": "PTP", "ptp_amount": 12000.0, "ptp_date": "2026-09-25", "person_met": "BORROWER"}),
    ("His wife said he lost his job last month and will pay 2 thousand next Monday.",
     {"outcome": "PTP", "ptp_amount": 2000.0, "ptp_date": "2026-09-28", "person_met": "SPOUSE",
      "default_reason": "JOB_LOSS"}),
    ("Borrower said his father is in hospital, he can pay Rs 3000 by 5th October.",
     {"outcome": "PTP", "ptp_amount": 3000.0, "ptp_date": "2026-10-05", "person_met": "BORROWER",
      "default_reason": "MEDICAL"}),
    ("Rs 48,500 is overdue. He agreed to pay Rs 7,500 day after tomorrow.",
     {"outcome": "PTP", "ptp_amount": 7500.0, "ptp_date": "2026-09-26"}),
    ("He will pay 1.5 lakh in 10 days, once the shop sale goes through.",
     {"outcome": "PTP", "ptp_amount": 150000.0, "ptp_date": "2026-10-04"}),
    ("Borrower refused to pay. Says the amount is wrong.",
     {"outcome": "RTP", "default_reason": "AMOUNT_DISPUTED"}),
    ("He will not pay anything and told us to leave.",
     {"outcome": "RTP"}),
    ("House was locked, the neighbour said the family is out of station.",
     {"outcome": "NOT_AVAILABLE", "not_met_reason": "PREMISES_LOCKED", "person_met": "NEIGHBOR"}),
    ("Customer has shifted. The new tenant says this is the wrong address.",
     {"outcome": "ADDRESS_ISSUE", "not_met_reason": "WRONG_ADDRESS"}),
    ("Borrower says he never took this loan.",
     {"outcome": "DISPUTE", "default_reason": "FRAUD_CLAIM"}),
    ("Spoke to the borrower, business is closed since August, no commitment today.",
     {"person_met": "BORROWER", "default_reason": "BUSINESS_FAILURE"}),
]


@pytest.mark.parametrize("text,expected", GOLDEN, ids=[g[0][:40] for g in GOLDEN])
def test_rules_golden_set(no_llm, text, expected):
    res = vre.extract(text, today=TODAY)
    assert res.source == vre.SOURCE_RULES
    assert _by_field(res) == expected
    # every suggestion is backed by words that are really in the note
    for s in res.suggestions:
        assert s.evidence and s.evidence.lower() in text.lower()


def test_a_negated_promise_is_not_a_promise(no_llm):
    res = vre.extract("He said he would never pay Rs 5000 to the bank.", today=TODAY)
    fields = _by_field(res)
    assert "ptp_amount" not in fields and fields.get("outcome") != "PTP"


def test_a_date_said_without_a_year_is_not_rolled_a_year_forward(no_llm):
    """"20/09" said on 24 September is four days ago. It must be rejected as
    past, not turned into 20 September next year."""
    res = vre.extract("He promised to pay Rs 4000 on 20/09.", today=TODAY)
    assert "ptp_date" not in _by_field(res)
    assert any(r.field == "ptp_date" and r.reason == "in the past" for r in res.rejected)


def test_a_january_date_said_in_december_is_next_year(no_llm):
    res = vre.extract("He will pay Rs 4000 on 5 January.", today=date(2026, 12, 20))
    assert _by_field(res)["ptp_date"] == "2027-01-05"


def test_an_impossible_date_gives_no_suggestion_and_no_crash(no_llm):
    res = vre.extract("He will pay Rs 4000 on 31/02.", today=TODAY)
    assert "ptp_date" not in _by_field(res)
    assert _by_field(res)["ptp_amount"] == 4000.0


def test_amount_above_the_remaining_target_is_rejected_not_clamped(no_llm):
    res = vre.extract("He will pay Rs 50,000 tomorrow.", today=TODAY, remaining_amount=20_000)
    assert "ptp_amount" not in _by_field(res)
    [r] = [r for r in res.rejected if r.field == "ptp_amount"]
    assert r.value == 50000.0 and "remaining target" in r.reason


def test_rules_result_names_why_the_llm_was_not_used(no_llm):
    res = vre.extract("He will pay Rs 500 tomorrow.", today=TODAY)
    d = res.as_dict()
    assert d["ai_generated"] is False
    assert d["source"] == "rules"
    assert d["llm_status"] == llm.NOT_CONFIGURED
    assert "No API key" in d["failure_reason"]
    assert d["version"] == vre.EXTRACTION_VERSION


def test_empty_transcript_asks_nobody(llm_says):
    calls = llm_says(llm.LLMResult(status=llm.OK, data={"outcome": "PTP"}))
    res = vre.extract("   \n ", today=TODAY)
    assert res.source == vre.SOURCE_NONE and res.suggestions == [] and calls == []


# ── LLM path ─────────────────────────────────────────────────────────────────
NOTE = "Met the borrower at his shop. He will pay 6000 on Friday, salary was delayed."


def _ok(data):
    return llm.LLMResult(status=llm.OK, data=data, provider="groq", model="test-model")


def test_llm_answer_is_used_and_labelled(llm_says):
    calls = llm_says(_ok({
        "outcome": "PTP", "ptp_amount": 6000, "ptp_date": "2026-09-25", "person_met": "BORROWER",
        "default_reason": "SALARY_CUT",
        "evidence": {"outcome": "He will pay", "ptp_amount": "pay 6000", "ptp_date": "on Friday",
                     "person_met": "Met the borrower", "default_reason": "salary was delayed"},
    }))
    res = vre.extract(NOTE, today=TODAY)
    assert res.source == vre.SOURCE_LLM and res.ai_generated is True
    assert _by_field(res) == {"outcome": "PTP", "ptp_amount": 6000.0, "ptp_date": "2026-09-25",
                              "person_met": "BORROWER", "default_reason": "SALARY_CUT"}
    assert res.rejected == []
    [call] = calls
    assert call["json_mode"] is True
    assert call["purpose"] == "visit_extraction"
    assert call["temperature"] == 0.0
    assert "2026-09-24" in call["prompt"]            # relative dates resolve against today


def test_llm_values_the_note_does_not_support_are_dropped(llm_says):
    """The hallucination guard: a quote that is not in the transcript means the
    model made the value up."""
    llm_says(_ok({
        "outcome": "PTP", "ptp_amount": 9000,
        "evidence": {"outcome": "He will pay", "ptp_amount": "he promised nine thousand"},
    }))
    res = vre.extract(NOTE, today=TODAY)
    assert _by_field(res) == {"outcome": "PTP"}
    [r] = res.rejected
    assert r.field == "ptp_amount" and r.reason == "supporting words are not in the transcript"


def test_llm_value_with_no_evidence_is_dropped(llm_says):
    llm_says(_ok({"outcome": "PTP"}))
    res = vre.extract(NOTE, today=TODAY)
    assert res.suggestions == []
    assert res.rejected[0].reason == "no supporting words given"


@pytest.mark.parametrize("outcome", sorted(vre.PAYMENT_OUTCOMES))
def test_payment_outcomes_are_never_suggested(llm_says, outcome):
    llm_says(_ok({"outcome": outcome, "evidence": {"outcome": "He will pay"}}))
    res = vre.extract(NOTE, today=TODAY)
    assert res.suggestions == []
    assert "verified payment" in res.rejected[0].reason


def test_payment_outcomes_are_not_offered_to_the_model(llm_says):
    calls = llm_says(_ok({}))
    vre.extract(NOTE, today=TODAY)
    for o in vre.PAYMENT_OUTCOMES:
        assert f"'{o}'" not in calls[0]["prompt"]
    assert set(vre.SUGGESTIBLE_OUTCOMES) | vre.PAYMENT_OUTCOMES == {o.value for o in VisitOutcome}


@pytest.mark.parametrize("field,value,reason", [
    ("outcome", "PROMISE", "not a valid outcome"),
    ("person_met", "COUSIN", "not a valid person_met"),
    ("ptp_amount", "a lot", "not a number"),
    ("ptp_amount", 0, "must be more than zero"),
    ("ptp_amount", -500, "must be more than zero"),
    ("ptp_date", "30th", "not a date"),
    ("ptp_date", "2026-09-23", "in the past"),
])
def test_llm_values_the_form_could_not_accept_are_rejected(llm_says, field, value, reason):
    llm_says(_ok({field: value, "evidence": {field: "He will pay"}}))
    res = vre.extract(NOTE, today=TODAY)
    assert res.suggestions == []
    assert (res.rejected[0].field, res.rejected[0].reason) == (field, reason)


def test_llm_saying_nothing_is_an_answer_not_a_failure(llm_says):
    llm_says(_ok({}))
    res = vre.extract(NOTE, today=TODAY)
    assert res.source == vre.SOURCE_LLM and res.suggestions == [] and res.failure_reason is None


def test_llm_answer_in_the_wrong_shape_falls_back_to_rules(llm_says):
    llm_says(_ok({"result": {"outcome": "PTP"}}))
    res = vre.extract(NOTE, today=TODAY)
    assert res.source == vre.SOURCE_RULES and res.ai_generated is False
    assert res.llm_status == llm.BAD_RESPONSE
    assert _by_field(res)["ptp_amount"] == 6000.0


@pytest.mark.parametrize("status", [llm.BAD_RESPONSE, llm.RATE_LIMITED, llm.TIMEOUT,
                                    llm.AUTH_FAILED, llm.UPSTREAM_ERROR])
def test_every_llm_failure_falls_back_to_rules(llm_says, status):
    llm_says(llm.LLMResult(status=status, failure_reason=f"simulated {status}"))
    res = vre.extract(NOTE, today=TODAY)
    assert res.source == vre.SOURCE_RULES
    assert res.llm_status == status and res.failure_reason == f"simulated {status}"
    assert _by_field(res)["outcome"] == "PTP"


def test_llm_and_rules_are_never_mixed(llm_says):
    """One source per result, so the label on the panel is true of every value
    in it: the rules would find the amount here, the model chose not to."""
    llm_says(_ok({"person_met": "BORROWER", "evidence": {"person_met": "Met the borrower"}}))
    res = vre.extract(NOTE, today=TODAY)
    assert _by_field(res) == {"person_met": "BORROWER"}


# ── the route ────────────────────────────────────────────────────────────────
@pytest.fixture
def api(monkeypatch, no_llm):
    from app.api.v1.endpoints import agent as agent_ep
    from app.core.database import get_db
    from app.core.dependencies import get_current_user
    from app.main import app
    from app.models.user import UserRole

    seen = {}
    fake_agent = SimpleNamespace(id="agent-aravalli-017")
    fake_case = SimpleNamespace(id=CASE_ID, target_amount=30_000.0, collected_amount=12_000.0)

    def case_or_404(db, agent, case_id):
        seen["case_lookup"] = (agent.id, case_id)
        return fake_case

    monkeypatch.setattr(agent_ep, "_get_agent_or_404", lambda user, db: fake_agent)
    monkeypatch.setattr(agent_ep, "_get_accessible_case_or_404", case_or_404)
    monkeypatch.setattr(vre, "datetime", SimpleNamespace(now=lambda tz=None: SimpleNamespace(date=lambda: TODAY)))
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id="user-1", role=UserRole.FIELD_AGENT, is_active=True)
    app.dependency_overrides[get_db] = lambda: None
    try:
        yield TestClient(app), seen
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)


def test_route_returns_suggestions_checked_against_the_cases_remaining_target(api):
    client, seen = api
    r = client.post(f"/api/v1/agent/cases/{CASE_ID}/visit-extraction",
                    json={"transcript": "He will pay Rs 25,000 tomorrow. Salary cut last month."})
    assert r.status_code == 200, r.text
    body = r.json()
    assert seen["case_lookup"] == ("agent-aravalli-017", CASE_ID)
    assert body["source"] == "rules" and body["ai_generated"] is False
    fields = {s["field"]: s["value"] for s in body["suggestions"]}
    # remaining = 30,000 target − 12,000 collected = 18,000, so 25,000 is refused
    assert "ptp_amount" not in fields
    assert fields["ptp_date"] == "2026-09-25" and fields["default_reason"] == "SALARY_CUT"
    assert any(x["field"] == "ptp_amount" and "18,000" in x["reason"] for x in body["rejected"])


@pytest.mark.parametrize("payload", [{"transcript": ""}, {"transcript": "x" * 5001}, {}])
def test_route_refuses_an_empty_or_oversized_transcript(api, payload):
    client, _ = api
    r = client.post(f"/api/v1/agent/cases/{CASE_ID}/visit-extraction", json=payload)
    assert r.status_code == 422


def test_route_answers_a_malformed_case_id_with_404_not_500(api):
    """Ids become native UUIDs in v2; on Postgres a malformed one would reach
    the database as a DataError. It must stop at the boundary, and look exactly
    like an unknown case."""
    client, seen = api
    r = client.post("/api/v1/agent/cases/not-a-uuid/visit-extraction",
                    json={"transcript": "He will pay Rs 500 tomorrow."})
    assert r.status_code == 404
    assert "case_lookup" not in seen
