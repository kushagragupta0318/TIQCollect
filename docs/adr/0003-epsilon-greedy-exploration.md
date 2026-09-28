# 0003. ε-greedy exploration at 10%

**Status:** Accepted, live from 2026-09-08. **Switch:** `ALLOCATOR_EXPLORATION_RATE`
(0.0 turns it off).

## Context

Every historical (agent, case, outcome) row was produced by the allocator itself, so good
agents systematically got good cases. An agent-fit model trained on that history learns the
allocator, not the agents. Propensity weighting cannot remove a confound that was never broken.

## Decision

- **Each night, 10% of assignments go to a uniformly random *eligible* agent** instead of the
  best-scoring one.
- **Exploration SWAPS assignments, it never moves one,** so capacity stays exact.
- **The draw is seeded from the plan date,** so a re-plan explores identically.
- **Every explored decision records** `exploration`, `exploration_propensity`,
  `exploration_from_agent`, `exploration_n_eligible` and `exploration_seed`. Unexplored ones
  carry `exploration: False`.

## Evidence and sizing

At about 233 allocations a day over 30 agents, the arithmetic is:
- **Ranking individual agents is out of reach at any tolerable rate.** It needs about 820
  randomised visits per agent.
- **The variance component is reachable** ("does agent identity matter at all?"): about
  1.3 months at 30 visits per agent.

**Do not read this data as a leaderboard.**

Measured cost at 10%:
- mean base-to-case distance **+14.3%** (3.37 → 3.85 km)
- expected recovery +0.03%
- BLOCKED set identical, caseloads exact

## Consequences

- **The hard gates cannot be bypassed, by construction.** Candidates come only from
  `eligible_agents`, the one place where DNC, hostility, the female-agent rule, territory and
  PTP fatigue (and, from the pilot, the DRA gate) are applied.
  - **Gap, found 2026-09-24:** the test CLAUDE.md cited for this,
    `test_exploration_never_sends_a_case_to_an_ineligible_agent`, does not exist in `tests/`.
  - Existing coverage is `test_sticky_ownership` (exploration never moves an owned case) and
    `test_prediction_lineage` (a swap keeps its prediction).
  - Writing the missing test is RESTRUCTURE-PLAN step 1.15, before N04 adds the DRA gate.
- **Two defects were found while building it, and both are fixed:**
  - The swap made ε mean 2ε. A configured 10% measured 19.1%.
  - The forecast was priced before exploration ran.
- **Travel is the real price.** Restricting candidates to the k nearest eligible agents would
  cut it, at the cost of a narrower randomisation.
