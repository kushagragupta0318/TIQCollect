# ─── CHANGELOG (prototype → product) ───
# New file, 2026-08-24. Covers the manager-facing recovery surface in
# api/v1/endpoints/manager.py — specifically the split between a FACT and an
# ESTIMATE that the 2026-08-24 dry run forced.
#
# The dry run found expected_recoverable_amount (rate_90 x TOTAL OUTSTANDING)
# summing to 229% of what was contractually demandable across the book, and on
# one loan reading Rs 10.65 lakh against Rs 1.18 lakh of real arrears. A manager
# scanning a case list reads a rupee figure as "money to collect"; that one is
# not, because most of it is principal that is not yet due.
#
# So the case list now carries `due_now` — overdue + penal charges, a ledger fact
# — and the estimate lives only in Analytics, where its denominator is spelled
# out beside it. These tests pin that separation, because it is the kind of thing
# a well-meaning later change re-merges.
import pathlib
import re
from types import SimpleNamespace as NS

from app.api.v1.endpoints.manager import _format_case

ENDPOINT = pathlib.Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "endpoints" / "manager.py"


def _code_only(path: pathlib.Path) -> str:
    """The endpoint's source with comment lines stripped.

    Necessary, and slightly funny: the first version of the guard below matched
    its own changelog, which explains WHY min(expected, due) is forbidden. A
    guard that fails on the prose describing the rule is a guard nobody keeps.
    """
    return "\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("#"))


def loan(**over):
    base = dict(
        id="loan-1", loan_account_number="LN0001", loan_account_masked="****0001",
        loan_type="HOME", bank_name="HDFC Bank", sanctioned_amount=2_000_000.0,
        outstanding_principal=1_200_000.0, outstanding_interest=40_000.0,
        penal_charges=5_842.0, total_outstanding=1_298_335.0,
        overdue_amount=118_103.0, emi_amount=18_400.0, tenure_months=240,
        interest_rate=8.5, dpd=35, dpd_bucket="BUCKET_2", status="ACTIVE",
        npa_flag=False, last_payment_date="2026-08-21", last_payment_amount=18_400.0,
        next_due_date="2026-09-01", legal_status="NONE", settlement_status="NONE",
        bank_risk_score=42.0, collection_priority_score=51.0,
    )
    base.update(over)
    return NS(**base)


def customer(**over):
    base = dict(
        id="cust-1", customer_ref="C0001", full_name="R. Kumar",
        phone_primary="9876543210", phone_alternate="8765432109",
        address_line1="1 MG Road", city="Mumbai",
        state="Maharashtra", pincode="400001", latitude=19.07, longitude=72.87,
        risk_category="MEDIUM", risk_score=48.0, cibil_score=610, is_hostile=False,
        do_not_contact=False, requires_female_agent=False, fraud_flag=False,
        customer_segment="SALARIED", language_preference="HINDI",
    )
    base.update(over)
    return NS(**base)


def case(**over):
    base = dict(
        id="case-1", case_number="CASE0001", status="IN_PROGRESS", priority="HIGH",
        target_amount=48_000.0, collected_amount=0.0, allocation_date="2026-08-20",
        visit_count=2, agent_id="agent-1", loan_id="loan-1",
        collection_stage="FIELD", bank_ptp_date=None, bank_ptp_amount=None,
        bank_ptp_status=None, bank_agent_remarks=None,
        is_escalated=False, handover_notes=None, max_visits_allowed=6,
        customer=customer(), loan=loan(),
    )
    base.update(over)
    return NS(**base)


SCORED = {"loan-1": {"recovery_potential": "HIGH", "rate_90": 0.821,
                     "rate_60": 0.527, "rate_30": 0.322, "is_modelled": False}}


# ── Due now is a fact ───────────────────────────────────────────────────────
def test_due_now_is_overdue_plus_penal_charges():
    """The whole point: a number a manager can act on without a model being
    involved. 118,103 arrears + 5,842 penal = 123,945 demandable today."""
    out = _format_case(case(), recovery_map=SCORED)
    assert out["due_now"] == 123_945.0


def test_due_now_is_present_even_when_the_loan_is_unscored():
    """Arrears do not wait on the scorecard. A loan with no recovery label still
    has money owed on it, and a manager still has to work it."""
    out = _format_case(case(), recovery_map={})
    assert out["recovery"] is None
    assert out["due_now"] == 123_945.0


def test_due_now_handles_missing_amounts():
    """Both columns are nullable in principle; a None must read as zero owed, not
    crash the case list."""
    out = _format_case(case(loan=loan(overdue_amount=None, penal_charges=None)),
                       recovery_map=SCORED)
    assert out["due_now"] == 0.0


def test_due_now_is_not_the_recovery_estimate():
    """THE DISTINCTION THIS FILE EXISTS FOR. On this fixture the estimate would be
    0.821 x 1,298,335 = Rs 10.66 lakh, against Rs 1.24 lakh actually due. If those
    two ever converge, someone has re-merged a fact with a forecast."""
    out = _format_case(case(), recovery_map=SCORED)
    estimate = SCORED["loan-1"]["rate_90"] * 1_298_335.0
    assert out["due_now"] < estimate / 8
    assert out["due_now"] == 123_945.0


# ── The estimate is off the case list ───────────────────────────────────────
def test_case_payload_carries_no_expected_recoverable_amount():
    """Moved to Analytics on 2026-08-24. A rupee figure on a case row is read as
    "collect this", and rate_90 x total outstanding is not that."""
    out = _format_case(case(), recovery_map=SCORED)
    assert "expected_recoverable_amount" not in out


def test_no_rupee_field_on_the_case_derives_from_rate_90():
    """Swept rather than spot-checked: no top-level value may equal the estimate,
    however it were spelled."""
    out = _format_case(case(), recovery_map=SCORED)
    estimate = round(SCORED["loan-1"]["rate_90"] * 1_298_335.0, 2)
    numeric = [v for v in out.values() if isinstance(v, (int, float))]
    assert not any(abs(v - estimate) < 1.0 for v in numeric), out


def test_the_band_still_reaches_the_case_list():
    """Due now leads, the outlook grades it — so the grade has to be there."""
    out = _format_case(case(), recovery_map=SCORED)
    assert out["recovery"]["recovery_potential"] == "HIGH"
    assert out["recovery"]["is_modelled"] is False


# ── No invented estimator anywhere in the endpoint ──────────────────────────
def test_no_derived_collection_estimate_exists_in_the_endpoint():
    """rate_90 is a rate on TOTAL OUTSTANDING. Multiplying it by arrears would be
    arithmetically wrong, and min(estimate, due) would be a second unvalidated
    estimator on a scorecard that already has one untested constant carrying most
    of its level. The agreed shape is a fact plus a graded likelihood, with
    nothing invented in between — asserted against the source so a later
    'helpful' addition fails here."""
    src = _code_only(ENDPOINT)
    forbidden = [
        r"rate_90[^\n]*\*[^\n]*overdue",
        r"overdue[^\n]*\*[^\n]*rate_90",
        r"min\([^\n)]*expected[^\n)]*due",
        r"min\([^\n)]*due[^\n)]*expected",
        r"_recovery_expected_amount\([^\n)]*overdue",
    ]
    for pattern in forbidden:
        assert not re.search(pattern, src, re.IGNORECASE), f"derived estimator: {pattern}"


def test_the_estimate_is_only_multiplied_by_total_outstanding():
    """Every call to the scorecard's multiplication must pass an outstanding
    balance. It is the one definition of the product and its denominator is not
    negotiable per call site."""
    src = _code_only(ENDPOINT)
    calls = re.findall(r"_recovery_expected_amount\((.*?)\)\s*$", src,
                       re.MULTILINE | re.DOTALL)
    assert calls, "expected at least one call in the analytics aggregation"
    for call in calls:
        assert "outstanding" in call, call
