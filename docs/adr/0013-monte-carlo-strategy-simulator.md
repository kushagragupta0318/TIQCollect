# 0013. The Monte Carlo strategy simulator: a showcase, not a platform

**Status:** Accepted, 2026-09-30 (owner's ruling, relayed by coordinator tiqcollect-06; tasks E01,
E02). Narrows and partially supersedes the pause recorded in `docs/business/PRIORITIES.md` and
`docs/PILOT-PLAN.md` §2 — see Context.
**Switch:** none. The endpoint is synchronous and `BANK_ADMIN`-only; there is no feature flag,
because there is no background work or shared state to turn off.
**Engine version:** `mc-1.2.0` (from `mc-1.1.0`; the sampler replaces fixed matrices).

## Context

`docs/business/PRIORITIES.md` (business lead, 2026-09-24) classes E01–E04 as **Defer**, noting
"E02/E04 are being built now… Recommend pausing them", and `PILOT-PLAN.md` §2 records the same
pause. The stated reason is sound and still true: transition matrices, Monte Carlo and backtests
all want months of real DPD history, and the product has only just started collecting it. The
verdict on the category was "Nobody buys it without their own history, and banks already own it."

**The review documents were right and were not read.** E01/E02 were briefed on 2026-09-30 against a
plan nobody had checked, and halted the same day once it was. This surfaced only because the owner
asked whether anyone senior had reviewed the plan, and two lanes challenged briefs written without
that check. The lesson for the next person planning a wave is the one that cost time here: read
`docs/business/PRIORITIES.md` and `PILOT-PLAN.md` first.

What followed is worth recording because the resolution is the decision:

- **The owner's position:** the market does not accept a product with no AI.
- **The counter-argument, drawn from the business lead's 2026-09-24 review and `PILOT-PLAN.md` §2:**
  the product already *has* substantial AI — six scoring layers, a trained GAM champion
  (`recovery_risk` 2.2.0), a Hungarian allocator, an LLM narrative. What those documents defer is
  not AI but an agent *studio*: infrastructure for a bank to author its own agents. That is the
  specific thing the review found nobody buys.
- **Agreed:** build the AI **showcase**, defer the AI **platform**. The framing as showcase-versus-
  platform is coordinator tiqcollect-06's; the substance is the business lead's review; the ruling
  is the owner's.

The simulator falls on the showcase side. A portfolio a bank can stress-test on screen demonstrates
well even on synthetic data — *provided it is labelled honestly*. That proviso is the load-bearing
part of this decision, not a caveat on it.

`PRIORITIES.md` and `PILOT-PLAN.md` still read "Defer" and have not been rewritten. That divergence
is deliberate and visible here rather than silently resolved in those files: the pause stands for
E03 and E04, and for the studio.

## Decision

- **Scope is the showcase slice, and the boundary is a test, not a preference:** if a thing only
  pays off once there are *many* runs or *many* agents, it waits.
  - **In:** E01 transition estimation (`strategy/transitions.py`), E02 the engine reading a sampled
    posterior, a thin synchronous endpoint, and enough screen to demonstrate it.
  - **Out:** E03's Celery and persistence machinery, E04's backtest harness, and the agent studio.
    `strategy/backtest.py` (316 lines) already exists from the cherry-picked history; it is kept as
    it stands, neither extended nor deleted.
- **One definition, imported — the DPD ladder and the state space are `models/loan`'s.**
  `strategy/states.py` restated the 8-state space, the 30/60/90 ladder and the bucket/status
  mapping. All three are now read from `app.models.loan`: `STATES` is `PORTFOLIO_STATES`,
  `NPA_DOUBTFUL_AFTER_MONTHS` is imported, `DPD_RANGE` is swept off `dpd_bucket_for`, and the
  mapping tables are probed off `portfolio_state` at import so a renamed enum member moves them.
  - The package's former justification for the copy — "it must stay DB-free, importing
    `app.models` needs the database" — was wrong on the part that mattered: `create_engine` builds
    a lazy Engine and opens no connection. It needs the app's settings env, which the API, a Celery
    task and pytest all have. The test that justified the copy was itself importing
    `app.models.loan`.
  - **`strategy.portfolio_state` stays a strict wrapper rather than a re-export.** `models/loan`'s
    returns `"UNKNOWN"` for a bad bucket or status, which is right for an ORM reader surfacing a
    data defect. `"UNKNOWN"` is not in `STATES` and has no index, and this engine indexes arrays by
    state, so the wrapper raises instead. Fail closed.
  - Two tripwires hold the dedupe, each mutation-checked: a behavioural one (shift
    `dpd_bucket_for`'s boundaries, the ladder must follow) and an AST scan (no state-name literal
    outside `states.py`, no 30/60/90 inside it). The behavioural one alone is not sufficient — it
    cannot see a literal that happens to equal the sweep, which is what the scan is for.
- **Transitions are a posterior, not a point estimate.** Counts come from
  `mv_bucket_transitions_monthly` through its `*_scoped` view on the tenant-bound analytics
  session, over a default 12 complete months, at bank grain with agency aggregated out. Segment is
  (`loan_type`, `region_id`).
  - Each from-row is `Dirichlet(counts + α · pooled_row)`, where the pooled row is the bank-wide
    row for that from-state and `POOLED_PRIOR_ALPHA` is 1 pseudo-account per cell.
  - **Absorbing rows are fixed identities, never sampled.** `WRITTEN_OFF` and `RESOLVED` stay put.
  - **Structural zeros stay zero in every draw** — the age-driven SUB↔DOUBTFUL fold, and the rule
    that `RESOLVED`/`WRITTEN_OFF` cannot return. Enforced by masking the prior and the counts
    together, so no draw can leak mass into a cell the state space forbids.
  - `to_state = NO_READING` is excluded. `excluded_stale` and `missing_pairs` are reported, never
    imputed.
- **Abstention is a first-class outcome, and a feature to show.** Per ADR 0005's spirit: a segment
  under `MIN_MONTHS` (6) or `MIN_FROM_ACCOUNTS` (30) falls back to the pooled bank-row posterior,
  flagged `segment_status="POOLED"` with a reason. **A bank with under 6 months of history overall
  refuses the run** with a typed reason and fabricates no matrix. Both are counted and shown.
  Structurally this is distinct from a crash: `strategy.simulation_runs` (v2_0018) carries an
  `ABSTAINED` status whose `abstain_reason` is CHECK-constrained to be set exactly then.
- **Honest labelling is non-negotiable, on every surface.** `synthetic_warning` wherever inputs
  derive from a demo tenant or the synthetic generator, and `UNCALIBRATED` until a backtest reaches
  nominal coverage on that bank — both stamped with `engine_version` and `data_version`, on the
  headline, the records, the API and the screen. The demo's whole value rests on a bank believing
  the numbers are honest; a simulator that overstates its own basis is worse than none.
- **The endpoint is thin and synchronous.** `BANK_ADMIN` only, `AGENCY_*` never. `n_paths` capped
  at 1,000, and the time budget is the placement engine's constant, imported rather than restated.
  `strategy/service.run_simulation(db, adb, bank_id, inputs, progress)` is pure orchestration, so
  the Celery seam exists without the Celery machinery being built. A new `strategy.simulate`
  capability is a permissions change and goes to the owner before it is wired.

## Known limits

- **Nothing here is evidence about a real book.** Every input is synthetic or a demo tenant, and
  every output says so. The reason E01–E04 were deferred has not gone away: the history the
  estimator wants does not exist yet. This ships as a demonstration of method, not a measurement.
- **`UNCALIBRATED` is the honest default and will stay set for a while.** Clearing it needs a
  backtest reaching nominal coverage on that bank's own history, and E04's harness is out of scope,
  so no path clears it in this slice. The label is not a placeholder to be removed on a whim.
- **The posterior is only as good as the view's grain.** (`loan_type`, `region_id`) at bank grain
  means an agency's own effect on transitions is aggregated away; a segment that is thin for
  structural reasons rather than data reasons is pooled with no way to tell the difference.
- **No background execution.** A run that would exceed the time budget is refused and asked to
  narrow, exactly as the placement engine does.

## Consequences

- The DPD ladder and the 8-state space exist once, in `models/loan`, and two tripwires fail if a
  second copy appears — including a copy that is merely written and not yet used.
- A bank with too little history gets a refusal with a reason rather than a plausible matrix. That
  is the intended demonstration, not a gap in it.
- `strategy.simulation_runs` / `simulation_results` (v2_0018) land ahead of most of the engine that
  fills them. That was chosen rather than inherited: `strategy.cost_rates` already landed the same
  way in v2_0013, and keeping a linear `down_revision` chain is worth more than avoiding two empty
  tables.
- If the studio is ever revisited, this ADR is the record that it was separated from the showcase
  deliberately, and why.
