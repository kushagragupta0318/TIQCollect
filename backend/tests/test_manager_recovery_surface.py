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
# The first answer was to put a ledger fact beside it: the case list carried
# `due_now` — overdue + penal charges — while the estimate lived only in
# Analytics, where its denominator is spelled out.
#
# 2026-08-27 — the fact went too, from the case payload. Correct arithmetic was
# not enough: sitting a column away from "Target / Collected" it was the largest
# number on the row and read as the amount to collect. What survives is the rule
# the dry run established, now enforced in the stricter direction — NO pre-summed
# rupee total on a case payload, fact or estimate. Analytics keeps the pairing
# (renamed `arrears_and_penal`) because a summary card is read as a summary.
#
# These tests pin that, because it is the kind of thing a well-meaning later
# change helpfully re-adds.
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


# ── due_now is GONE from the case payload (2026-08-27) ─────────────────────
# It was added on 2026-08-24 as overdue + penal and was arithmetically correct.
# It still misled, because of where it sat: beside "Target / Collected" on a case
# row it was the largest number present, so it read as the amount to collect.
#
# These tests replace the four that asserted its presence. They pin the ABSENCE,
# because the failure mode now is somebody helpfully re-adding a convenient
# pre-summed total.
def test_the_case_payload_carries_no_due_now():
    """Removed on request: on a case row it read as the collection target."""
    out = _format_case(case(), recovery_map=SCORED)
    assert "due_now" not in out


def test_the_two_components_are_still_available_on_the_loan():
    """Nothing was hidden — only the pre-summed figure. A caller that genuinely
    needs arrears can still add them, and has to say so by doing it."""
    out = _format_case(case(), recovery_map=SCORED)
    assert out["loan"]["overdue_amount"] == 118_103.0
    assert out["loan"]["penal_charges"] == 5_842.0


def test_no_field_on_the_case_equals_arrears_plus_penal():
    """Swept, not spot-checked: a re-added total under any other name — "demandable",
    "payable_now", "arrears_total" — fails here."""
    out = _format_case(case(), recovery_map=SCORED)
    total = 118_103.0 + 5_842.0
    numeric = [v for v in out.values() if isinstance(v, (int, float))
               and not isinstance(v, bool)]
    assert not any(abs(v - total) < 1.0 for v in numeric), out


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
