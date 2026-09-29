# 0010. The placement engine (bank → agency), v1

**Status:** Accepted, 2026-09-29 (coordinator tiqcollect-0c; task D09).
**Switch:** `PLACEMENT_EXPLORATION_RATE` (default 0.0; a run may set up to 0.20).

## Context

The bank places delinquent loans with collection agencies (plan §6.3). By hand (D08), one BANK_ADMIN
picks loans and an agency. The engine does it for the whole unplaced book: today's allocator one
level up. Its inputs are thinner than the allocator's. There is one model score per loan and a
monthly scorecard per agency and region. Its decisions move work, and commission, between
independent firms.

## Decision

- **The hard gates are placement_service's, judged by one pure function.** The gates are: loan
  open; not placed elsewhere; agency active; contract in force; product and DPD bucket authorised;
  territory covered; room under the cap. `evaluate()` (manual placement, the feed) and the engine
  both call it over facts loaded in batch, so a gate is never restated.
- **The score is `value = E × m × (1 − c)`** for every eligible (loan, agency) pair.
  - `E` is `expected_recovery_inr(P(pay))`. It is ce's one definition, from the loan's newest
    recovery_risk prediction on or before the plan date. With no prediction, `E` is the overdue
    amount and the breakdown says `is_modelled: false` (ADR 0005: abstain from the model, not
    from the decision). `E` is the same for every agency, so it decides which loans get scarce
    room, never which agency gets a loan.
  - `m` is `agency_effect`'s multiplier for the agency in the loan's region. It is collection
    efficiency, EB-shrunk toward the same-region peers and bounded to [0.75, 1.25]. With no
    evidence, `m` is 1.0.
  - `c` is the contract's commission for the loan's product and bucket.
- **Headroom is a hard capacity in the solve, not a factor in the score.** The plan's wording
  ("× capacity headroom") would count capacity twice. This is a deliberate deviation.
- **The solve is a min-cost flow (OR-Tools).** The flow runs source → loan (1) → agency (arc
  cost −value) → sink (capacity = headroom), plus an UNPLACED arc at cost 0.
  - PLACED: the loan is assigned.
  - DEFERRED: eligible agencies exist, but none had room, or the loan lost on value.
  - BLOCKED: no agency passes the gates. `gate_results` keeps each agency's first failure.
- **Recall rules come from the placement's own contract.** NO_ACTIVITY: no visit, call or
  payment for N days. SLA_BREACH: no first visit by `sla_first_visit_due`. CONTRACT_END. A
  recalled loan re-enters the same run's pool with its previous agency excluded. It is re-placed
  as a new placement and case (source RE_PLACEMENT, DATA-MODEL-V2 §2.5).
- **Exploration follows ADR 0003,** at a default of 0. Randomising agencies moves commission
  between real firms, so the owner opts in per run (maximum 0.20), and the rate is recorded on
  the run. The mechanics are the allocator's:
  - swaps, which keep capacity exact;
  - halved initiators;
  - candidates only from the eligible set;
  - the seed is the plan date;
  - keys `exploration`, `exploration_propensity`, `exploration_from_agency`,
    `exploration_n_eligible`, `exploration_seed`, `exploration_rate`;
  - the forecast is re-priced after the swaps.
- **Modes.**
  - `simulate` writes decisions under status SIMULATED and can never be applied.
  - `plan` writes a PLANNED run.
  - `apply` is four-eyes: `applied_by ≠ created_by`, the same rule as model promotion
    (ADR 0007). It works only on the plan date (409 when stale). It re-judges every PLACED
    decision's gates and skips any that now fail, listing them in the summary. It runs in one
    transaction.
- **KEPT placements get no decision row.** They are counted in `total_kept`. The answer to "why
  kept" is the same for every one of them: no recall rule fired and the contract is still in
  force.
- **Runs are synchronous, with a 30-second limit.** A run that would exceed it is refused and
  asked to narrow. Moving runs to Celery needs a RUNNING status (a later ask to 43).

## Known limits

- **The agency effect is not mix-adjusted by product or bucket.** The scorecard view's grain is
  (month, agency, region). Mix enters only through `E`. The v1.1 fix is an `expected_collections`
  column in the view, which lets `m` become Recovery vs Expected over one aligned cohort. Today's
  `expected_recovery_inr` column covers only the month's new placements, so it is not used for
  scoring.
- **Every number is synthetic.** Everything is trained or measured on synthetic borrowers and a
  demo book. The run summary carries the model artifact's SYNTHETIC_WARNING, and states that the
  agency history is a demo book whenever the bank is a demo tenant.
- **Four-eyes apply cannot be shown on the demo** while it has a single BANK_ADMIN login. Adding
  a second login is the owner's decision.

## Consequences

- The feed, manual placement and the engine can never disagree about eligibility.
- A future scoring change bumps `agency-effect-*`, `expected-recovery-*` or the engine version.
  These versions are stamped into `score_breakdown`, so old decisions stay readable.
