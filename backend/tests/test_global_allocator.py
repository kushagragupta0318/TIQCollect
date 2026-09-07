# ─── CHANGELOG (prototype → product) ───
# New file, 2026-09-07. GlobalAllocator had NO tests at all, and this commit
# changes its utility — so the change and the coverage arrive together.
#
# Scope is deliberately narrow: the hard gates (which must never move), and the
# spec_match term (which does move, and by how much is recorded below). The
# scoring curves are not pinned here; that is worth doing and is its own piece
# of work.
from types import SimpleNamespace

import pytest

from app.models.agent import AgentSpecialization, AgentTier
from app.models.loan import DPDBucket, LoanType
from app.services.global_allocator import GlobalAllocator
from app.models.allocation_decision import AllocationOutcome

BASE_LAT, BASE_LON = 28.6139, 77.2090


def make_agent(agent_id, *, lat=BASE_LAT, lon=BASE_LON, gender="M",
               tier=AgentTier.TIER_1, spec=AgentSpecialization.BOTH,
               languages=None, cap=5):
    return SimpleNamespace(
        id=agent_id, employee_code=f"EMP-{agent_id}", gender=gender,
        base_latitude=lat, base_longitude=lon, tier=tier, specialization=spec,
        languages_spoken=["HINDI"] if languages is None else languages,
        max_cases_per_day=cap)


def make_case(case_id, *, lat=BASE_LAT, lon=BASE_LON, target=50_000.0,
              collected=0.0, dnc=False, hostile=False, needs_female=False,
              language="HINDI", loan_type=LoanType.PERSONAL,
              bucket=DPDBucket.BUCKET_2, agent_id=None):
    customer = SimpleNamespace(
        id=f"cust-{case_id}", latitude=lat, longitude=lon, do_not_contact=dnc,
        is_hostile=hostile, requires_female_agent=needs_female,
        language_preference=language)
    return SimpleNamespace(
        id=case_id, customer=customer,
        loan=SimpleNamespace(loan_type=loan_type, dpd_bucket=bucket),
        agent_id=agent_id, target_amount=target, collected_amount=collected)


def allocate(cases, agents, *, ptp_fatigue=None, radius_km=16.0):
    return GlobalAllocator(territory_radius_km=radius_km).allocate(
        cases=cases, agents=agents, history_matrix={},
        prio_scores={c.id: 50.0 for c in cases}, ptp_fatigue=ptp_fatigue)


def outcomes(decisions):
    return {d.case_id: str(getattr(d.outcome, "value", d.outcome))
            for d in decisions}


def assigned(decisions):
    return {d.case_id: d.allocated_agent_id
            for d in decisions if d.allocated_agent_id}


# ── spec_match: the term that had never fired ───────────────────────────────

def test_the_two_enums_it_used_to_compare_share_no_member():
    """THE DEFECT, pinned as the reason the fix exists.

    Until 2026-09-07 the term read

        spec_match = 1.0 if str(ag.specialization.value).upper()
                            == loan_type_str.upper() else 0.5

    comparing an AgentSpecialization with a LoanType. If these two vocabularies
    ever DO intersect, that expression starts working by accident and this test
    should be read before anyone concludes the old code was fine.
    """
    assert not ({s.value for s in AgentSpecialization} & {t.value for t in LoanType})


def test_a_specialist_now_scores_above_a_non_specialist():
    """The behaviour the old expression could never produce. Both agents sit on
    the borrower, so proximity, workload, continuity and language are identical
    and only the skills term can decide it."""
    secured = make_agent("sec", spec=AgentSpecialization.SECURED, cap=1)
    unsecured = make_agent("uns", spec=AgentSpecialization.UNSECURED, cap=1)
    # HOME is a secured product; the secured specialist should win it.
    home = allocate([make_case("c1", loan_type=LoanType.HOME)],
                    [secured, unsecured])[1]
    assert assigned(home) == {"c1": "sec"}
    # PERSONAL is unsecured; the other way round.
    personal = allocate([make_case("c2", loan_type=LoanType.PERSONAL)],
                        [secured, unsecured])[1]
    assert assigned(personal) == {"c2": "uns"}


def test_a_non_specialist_is_a_worse_fit_not_an_ineligible_one():
    """The 0.5 floor is kept deliberately. Specialisation is a PREFERENCE — the
    hard gates are elsewhere — so a mismatched agent must still be able to take
    the case when nobody better is free."""
    only_wrong = make_agent("uns", spec=AgentSpecialization.UNSECURED, cap=1)
    decisions = allocate([make_case("c1", loan_type=LoanType.HOME)], [only_wrong])[1]
    assert assigned(decisions) == {"c1": "uns"}


def test_both_specialisation_matches_everything():
    for lt in (LoanType.HOME, LoanType.PERSONAL, LoanType.BUSINESS):
        both = make_agent("both", spec=AgentSpecialization.BOTH, cap=1)
        wrong = make_agent("uns", spec=AgentSpecialization.UNSECURED, cap=1,
                           lat=BASE_LAT + 0.02)
        decisions = allocate([make_case("c1", loan_type=lt)], [both, wrong])[1]
        assert assigned(decisions)["c1"] == "both", lt


def test_business_loans_mark_neither_specialisation_down():
    """A business loan is secured or unsecured depending on the facility, so
    there is no basis to prefer either. ml/eligibility states the same reason."""
    from app.ml.eligibility import specialisation_fit
    loan = SimpleNamespace(loan_type=LoanType.BUSINESS)
    for spec in (AgentSpecialization.SECURED, AgentSpecialization.UNSECURED,
                 AgentSpecialization.BOTH):
        assert specialisation_fit(make_agent("a", spec=spec), loan)


def test_the_allocator_uses_the_shared_rule_rather_than_its_own_copy():
    """One definition. Two copies of the secured/unsecured split is how the
    scorecards and the allocator would end up disagreeing about what a HOME loan
    is — which is the class of bug this repo's changelogs are mostly about."""
    import inspect
    src = inspect.getsource(GlobalAllocator.allocate)
    assert "specialisation_fit(ag, loan)" in src
    assert "_SECURED" not in src and "_UNSECURED" not in src


# ── The hard gates: these must not move ─────────────────────────────────────
# Measured on the demo book when spec_match was corrected: 2 of 227 assignments
# moved, 4 cases swapped in and out of the day's plan, expected recovery total
# shifted -0.80%, and the BLOCKED set was IDENTICAL (15 of 15). That last part
# is the safety property, and it is what these tests hold in place.

def test_do_not_contact_is_blocked_and_never_assigned():
    decisions = allocate([make_case("c1", dnc=True)], [make_agent("a1")])[1]
    assert outcomes(decisions) == {"c1": AllocationOutcome.BLOCKED.value}
    assert not assigned(decisions)


def test_hostile_borrower_is_blocked_and_never_assigned():
    decisions = allocate([make_case("c1", hostile=True)], [make_agent("a1")])[1]
    assert outcomes(decisions) == {"c1": AllocationOutcome.BLOCKED.value}
    assert not assigned(decisions)


def test_a_female_requirement_excludes_male_and_unknown_gender_agents():
    """Unknown is NOT female. The column is nullable and most rows predate it,
    so a permissive default would defeat the whole check on day one."""
    for gender in ("M", None, ""):
        decisions = allocate([make_case("c1", needs_female=True)],
                             [make_agent("a1", gender=gender)])[1]
        assert not assigned(decisions), gender
    female = allocate([make_case("c1", needs_female=True)],
                      [make_agent("a1", gender="F")])[1]
    assert assigned(female) == {"c1": "a1"}


def test_ptp_fatigue_bars_that_agent_and_the_case_still_allocates():
    """Three broken promises to one agent bar THAT AGENT, never the case — the
    borrower still owes the money and should still be visited, by somebody
    else. Because it only removes one column, the solve picks the next best by
    itself."""
    agents = [make_agent("a1", cap=1), make_agent("a2", cap=1)]
    decisions = allocate([make_case("c1")], agents,
                         ptp_fatigue={"c1": {"a1"}})[1]
    assert assigned(decisions) == {"c1": "a2"}


def test_territory_radius_excludes_a_case_outside_the_zone():
    far = make_case("c1", lat=BASE_LAT + 1.5)      # ~165 km
    decisions = allocate([far], [make_agent("a1")])[1]
    assert not assigned(decisions)


def test_no_agent_is_given_more_than_max_cases_per_day():
    agents = [make_agent("a1", cap=2)]
    cases = [make_case(f"c{i}") for i in range(6)]
    result = assigned(allocate(cases, agents)[1])
    assert len(result) == 2
    assert set(result.values()) == {"a1"}


def test_every_case_appears_in_exactly_one_decision():
    """A case that silently vanishes from the plan is worse than one that is
    deferred with a reason, because nobody can see it happened."""
    cases = [make_case("c1"), make_case("c2", dnc=True),
             make_case("c3", lat=BASE_LAT + 1.5)]
    decisions = allocate(cases, [make_agent("a1", cap=1)])[1]
    assert sorted(d.case_id for d in decisions) == ["c1", "c2", "c3"]
    assert len(decisions) == len({d.case_id for d in decisions})


def test_allocation_is_deterministic():
    agents = [make_agent("a1", cap=2), make_agent("a2", cap=2)]
    cases = [make_case(f"c{i}", target=10_000.0 * (i + 1)) for i in range(4)]
    first = assigned(allocate(cases, agents)[1])
    for _ in range(3):
        assert assigned(allocate(cases, agents)[1]) == first
