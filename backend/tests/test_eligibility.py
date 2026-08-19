# ─── CHANGELOG (prototype → product) ───
# New file, 2026-08-19. Covers ml/eligibility.py — the allocation rules that
# decide who may be sent to whom. Two of them are legal obligations
# (do_not_contact, requires_female_agent) and neither was enforced anywhere
# before today, so they are tested first and hardest.
#
# Same no-DB style as test_payment_service.py: these are pure functions over
# SimpleNamespace stand-ins, so nothing here needs a database.
from types import SimpleNamespace

from app.ml.eligibility import (
    BLOCKED_DO_NOT_CONTACT,
    BLOCKED_NEEDS_FEMALE_AGENT,
    agent_block_reason,
    case_block_reason,
    is_eligible,
    is_female,
    language_fit,
    match_score,
    specialisation_fit,
)
from app.models.agent import AgentSpecialization
from app.models.loan import LoanType


def agent(gender=None, spec=AgentSpecialization.BOTH, languages=None):
    return SimpleNamespace(
        gender=gender,
        specialization=spec,
        languages_spoken=languages if languages is not None else ["HINDI"],
    )


def customer(dnc=False, needs_female=False, language="HINDI"):
    return SimpleNamespace(
        do_not_contact=dnc,
        requires_female_agent=needs_female,
        language_preference=language,
    )


def loan(loan_type=LoanType.PERSONAL):
    return SimpleNamespace(loan_type=loan_type)


# ── is_female ────────────────────────────────────────────────────────────────
def test_female_recognised_in_common_forms():
    for value in ("F", "female", "FEMALE", " Female ", "woman"):
        assert is_female(agent(gender=value)) is True, value


def test_unknown_gender_is_not_female():
    """The column is nullable and every pre-existing row is NULL. Treating
    unknown as female would silently defeat the check on day one."""
    assert is_female(agent(gender=None)) is False
    assert is_female(agent(gender="")) is False


def test_male_is_not_female():
    assert is_female(agent(gender="M")) is False
    assert is_female(agent(gender="male")) is False


# ── do_not_contact — case must never be allocated ────────────────────────────
def test_do_not_contact_blocks_the_case():
    assert case_block_reason(customer(dnc=True)) == BLOCKED_DO_NOT_CONTACT


def test_ordinary_customer_is_allocatable():
    assert case_block_reason(customer()) is None


def test_missing_customer_does_not_block():
    """A case with no customer row is a data problem, not a contact
    prohibition — it must not be silently swallowed as a DNC block."""
    assert case_block_reason(None) is None


# ── requires_female_agent — agent must be female ─────────────────────────────
def test_female_only_customer_rejects_male_agent():
    reason = agent_block_reason(agent(gender="M"), customer(needs_female=True))
    assert reason == BLOCKED_NEEDS_FEMALE_AGENT


def test_female_only_customer_rejects_unknown_gender_agent():
    reason = agent_block_reason(agent(gender=None), customer(needs_female=True))
    assert reason == BLOCKED_NEEDS_FEMALE_AGENT


def test_female_only_customer_accepts_female_agent():
    assert agent_block_reason(agent(gender="F"), customer(needs_female=True)) is None
    assert is_eligible(agent(gender="F"), customer(needs_female=True)) is True


def test_no_requirement_accepts_any_agent():
    for g in (None, "M", "F"):
        assert agent_block_reason(agent(gender=g), customer()) is None


# ── specialisation — a preference, not a bar ─────────────────────────────────
def test_secured_agent_fits_secured_loan():
    a = agent(spec=AgentSpecialization.SECURED)
    for t in (LoanType.HOME, LoanType.AUTO, LoanType.GOLD):
        assert specialisation_fit(a, loan(t)) is True, t


def test_secured_agent_does_not_fit_unsecured_loan():
    a = agent(spec=AgentSpecialization.SECURED)
    for t in (LoanType.PERSONAL, LoanType.CREDIT_CARD, LoanType.MICROFINANCE):
        assert specialisation_fit(a, loan(t)) is False, t


def test_both_specialisation_fits_everything():
    a = agent(spec=AgentSpecialization.BOTH)
    for t in LoanType:
        assert specialisation_fit(a, loan(t)) is True, t


def test_business_loan_fits_any_specialisation():
    """Business facilities are secured or unsecured depending on the deal, so
    excluding either specialisation would be a guess."""
    for spec in (AgentSpecialization.SECURED, AgentSpecialization.UNSECURED):
        assert specialisation_fit(agent(spec=spec), loan(LoanType.BUSINESS)) is True


def test_missing_loan_does_not_restrict():
    assert specialisation_fit(agent(spec=AgentSpecialization.SECURED), None) is True


# ── language — a preference, not a bar ───────────────────────────────────────
def test_language_match_is_case_insensitive():
    assert language_fit(agent(languages=["hindi"]), customer(language="HINDI")) is True


def test_language_mismatch_detected():
    assert language_fit(agent(languages=["ENGLISH"]), customer(language="URDU")) is False


def test_agent_with_no_languages_never_matches():
    assert language_fit(agent(languages=[]), customer(language="HINDI")) is False


def test_customer_with_no_preference_always_matches():
    assert language_fit(agent(languages=[]), customer(language=None)) is True


# ── match_score — ranking behaviour ──────────────────────────────────────────
def test_perfect_match_scores_highest():
    a = agent(languages=["HINDI"], spec=AgentSpecialization.UNSECURED)
    assert match_score(a, customer(language="HINDI"), loan(LoanType.PERSONAL)) == 3.0


def test_no_match_scores_zero():
    a = agent(languages=["ENGLISH"], spec=AgentSpecialization.SECURED)
    assert match_score(a, customer(language="URDU"), loan(LoanType.PERSONAL)) == 0.0


def test_language_outranks_specialisation():
    """A conversation that cannot happen is worth less than one held by a
    slightly less specialised agent — so language must win when they conflict."""
    speaks_but_wrong_training = agent(
        languages=["HINDI"], spec=AgentSpecialization.SECURED)
    right_training_but_silent = agent(
        languages=["ENGLISH"], spec=AgentSpecialization.UNSECURED)
    c, l = customer(language="HINDI"), loan(LoanType.PERSONAL)
    assert match_score(speaks_but_wrong_training, c, l) > match_score(
        right_training_but_silent, c, l)


def test_score_never_gates_eligibility():
    """A zero score must still be allocatable — otherwise a case with no
    well-matched agent would be stranded rather than merely sub-optimal."""
    a = agent(languages=["ENGLISH"], spec=AgentSpecialization.SECURED)
    c = customer(language="URDU")
    assert match_score(a, c, loan(LoanType.PERSONAL)) == 0.0
    assert is_eligible(a, c) is True
