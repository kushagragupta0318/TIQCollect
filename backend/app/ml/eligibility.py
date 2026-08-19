# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-08-19 — New file. The nightly allocator matched on territory and spare
#   capacity and nothing else, so it used NONE of the eight matching facts the
#   schema already carries. Two of those are legal obligations rather than
#   preferences:
#
#     Customer.do_not_contact       — the bank has told us not to approach this
#                                     customer at all. The allocator would still
#                                     hand the case to an agent and send them.
#     Customer.requires_female_agent — could never be enforced, because nothing
#                                     recorded an agent's gender. Agent.gender
#                                     was added in the same change.
#
#   The other two are match quality, not law, so they rank rather than block —
#   a case must not go unallocated because nobody speaks the preferred language.
#   Blocking on a preference strands cases, which is its own harm.
#
#   Kept as pure functions in one module because the same rules belong in
#   CaseAllocator AND in the manager's reallocation plan, which today does its
#   own territory-only matching (endpoints/manager.py ~line 1966). Copy-pasted
#   scoping is exactly how the cross-tenant bugs in this codebase happened.
# ───────────────────────────────────────────────────────────────────────────
"""Who may be sent to whom — the hard rules, and the ranking preferences."""
from __future__ import annotations

from app.models.agent import Agent, AgentSpecialization
from app.models.customer import Customer
from app.models.loan import Loan, LoanType

# ── Hard-rule outcomes ───────────────────────────────────────────────────────
BLOCKED_DO_NOT_CONTACT = "DO_NOT_CONTACT"
BLOCKED_NEEDS_FEMALE_AGENT = "REQUIRES_FEMALE_AGENT"

# Gender values accepted as female. Stored free-text rather than an enum
# because Customer.gender already is, and the two should read alike.
_FEMALE = {"f", "female", "woman"}

# Loan types whose collateral makes them secured work. BUSINESS is deliberately
# absent: business loans are secured or unsecured depending on the facility, so
# treating them as either would be a guess. They match any specialisation.
_SECURED = {LoanType.HOME, LoanType.AUTO, LoanType.GOLD}
_UNSECURED = {
    LoanType.PERSONAL, LoanType.CREDIT_CARD,
    LoanType.EDUCATION, LoanType.MICROFINANCE,
}


def is_female(agent: Agent) -> bool:
    """True only when the agent is positively recorded as female.

    Unknown is NOT female. The column is nullable and most rows predate it, so
    a permissive default would silently defeat the whole check on day one.
    """
    return bool(agent.gender) and agent.gender.strip().lower() in _FEMALE


# ── Case-level: should this case be allocated at all? ────────────────────────
def case_block_reason(customer: Customer | None) -> str | None:
    """Returns a reason string if the case must not be allocated, else None."""
    if customer is not None and customer.do_not_contact:
        return BLOCKED_DO_NOT_CONTACT
    return None


# ── Agent-level: may THIS agent take THIS case? ──────────────────────────────
def agent_block_reason(agent: Agent, customer: Customer | None) -> str | None:
    """Hard eligibility. Returns a reason string if barred, else None."""
    if customer is not None and customer.requires_female_agent and not is_female(agent):
        return BLOCKED_NEEDS_FEMALE_AGENT
    return None


def is_eligible(agent: Agent, customer: Customer | None) -> bool:
    return agent_block_reason(agent, customer) is None


# ── Preference: how well does this agent fit this case? ──────────────────────
def specialisation_fit(agent: Agent, loan: Loan | None) -> bool:
    """Is the agent trained for this kind of debt?"""
    if loan is None or agent.specialization == AgentSpecialization.BOTH:
        return True
    loan_type = loan.loan_type
    if loan_type in _SECURED:
        return agent.specialization == AgentSpecialization.SECURED
    if loan_type in _UNSECURED:
        return agent.specialization == AgentSpecialization.UNSECURED
    return True          # BUSINESS and anything added later — no basis to exclude


def language_fit(agent: Agent, customer: Customer | None) -> bool:
    """Does the agent speak the language the customer prefers?"""
    if customer is None or not customer.language_preference:
        return True
    spoken = agent.languages_spoken or []
    if not spoken:
        return False
    want = customer.language_preference.strip().lower()
    return any(str(lang).strip().lower() == want for lang in spoken)


# Weights. Language outranks training because a conversation that cannot happen
# is worth less than one held by a slightly less specialised agent — the whole
# job at the doorstep is a conversation.
_W_LANGUAGE = 2.0
_W_SPECIALISATION = 1.0


def match_score(agent: Agent, customer: Customer | None, loan: Loan | None) -> float:
    """0.0–3.0, higher is a better fit. Ranking only — never a bar to entry."""
    score = 0.0
    if language_fit(agent, customer):
        score += _W_LANGUAGE
    if specialisation_fit(agent, loan):
        score += _W_SPECIALISATION
    return score
