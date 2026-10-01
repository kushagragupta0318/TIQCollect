# 0014. Show synthetic performance, name its basis; E01–E04 are in scope

**Status:** Accepted, 2026-09-30 (owner's ruling, relayed by coordinator tiqcollect-06).
**Supersedes the scope section of [0013](0013-monte-carlo-strategy-simulator.md)**, which deferred
E03 and E04 earlier the same day. 0013's engineering decisions stand unchanged; only what is built
now changes.

## Context

0013 narrowed P4 to a "showcase" — E01 and E02 plus a thin endpoint — and deferred E03 (persistence)
and E04 (the backtest harness) on the strength of `docs/business/PRIORITIES.md`, which classes
E01–E04 as Defer because they "need months of real DPD history".

That deferral was over-applied, and the correction matters more than the reversal:

- **The business lead's reason is an argument about VALIDATION, not about construction.** "Needs
  months of real DPD history" is a reason we cannot yet claim a model is validated against a
  particular bank's book. It is not a reason the engine cannot be built, and not a reason it cannot
  be demonstrated. The demo book carries **761,000 `loan_dpd_history` rows**; the simulator runs on
  them today.
- What is genuinely not worth building is **AI agents operating inside a real bank's environment** —
  the agent runtime, the registry, and the studio in which a bank authors its own agents. That is
  the thing the review found nobody buys, and it stays out.
- Monte Carlo, the core ML modelling, and an intuitive ML environment for banks and NBFCs to use
  are the product, not an adjunct to it.

Recorded twice, deliberately: the deferral was reasoned about, then reasoned about again with the
distinction between building and validating made explicit. Keeping both decisions visible is more
useful than a record that reads as though the second one were the first.

## Decision

- **We do not claim real-world performance. We do show performance on the synthetic data we
  generated, and we name what it was measured on.**
  - False: "this model scores 0.48 Gini".
  - True, demonstrable, and more credible to a bank's model-risk team: "on N synthetic borrowers we
    generated, this model scores 0.48 Gini; here is the generator and the methodology; real-book
    validation begins when labels mature."
  - The SYNTHETIC and UNCALIBRATED stamps are therefore what makes showing the numbers *legitimate*.
    They are not an apology attached to a weak figure.
- **E01–E04 are in scope, plus the synchronous endpoint.** E03 and E04 come off the defer list
  because they are how calibration is *shown* rather than asserted: a p10–p90 coverage figure
  measured on synthetic data is what distinguishes a simulator from a random number generator with a
  chart on top. The agent runtime, registry and studio remain out.
- **Sequence: `transitions.py` → E04 (backtest) → the endpoint → E03 (persistence).**
  - E04 before the endpoint, because it converts UNCALIBRATED from an apology into a measured
    statement, and demonstrating a band before we can say whether the band means anything would
    undercut the claim being made.
  - The endpoint next: a synchronous run returns results inline and needs no persistence to be
    demonstrable.
  - E03 last, shaped by what the UI and the report engine actually need to re-read, rather than
    guessed at in advance.
- **Every derived figure is computed, never asserted. The label-maturity date in particular is
  derived** from the outcome definition (ADR 0004) plus the earliest `model_prediction` row, at read
  time, and reaches a screen only through `/ml/health`. No lane hardcodes it — not in a UI, not in a
  report, not in an ADR. This rule exists because an asserted maturity date was found not to
  reconcile with ADR 0004's own arithmetic while this decision was being made: 30-day labels
  maturing 2026-10-08 implies predictions from 2026-09-08, and 90 days from there is 2026-12-07.
  CLAUDE.md already says "measure counts, don't copy them"; a date inside an honesty claim is
  exactly where copying is least affordable.
- **One honesty renderer, built before its consumers exist.** The engine owns the structural fields
  — `synthetic`, `calibrated`, `engine_version`, `data_version`, `basis` (what the figure was
  measured on) — and exposes a single canonical renderer. **No consumer writes caption text.** Four
  consumers formatting their own caption gives four spellings and eventually one omission, and the
  omission lands in the board PDF, because that is the surface someone reformats for space. A
  tripwire enforces it: a surface that emits a figure without its stamp fails rather than ships.
- **One metric registry in the engine** (`ECL`, `GNPA_PCT`, `NET_RECOVERY`, …), imported by
  persistence, the API, the UI and the report engine. `metric` is a column in v2_0018, so the
  spelling is shared state across four consumers; a mismatch does not error, it silently returns
  nothing and the chart looks empty but plausible.
- **A weekly figure is never a monthly figure divided by 4.33.** The engine is monthly. E06's
  13-week cash forecast must either carry an explicitly labelled disaggregation ("monthly cash
  spread by a stated rule, not weekly-estimated") or be a separate estimator with its own evidence.
  A smooth disaggregation wearing the engine's credibility is a fabricated number, and worse than an
  absent one because it looks authoritative.
- **Scenarios round-trip as data.** If a bank authors scenarios (E08), `MacroScenario` / `PRESETS`
  cannot stay code constants; `compare_scenarios` is the seam.

## Known limits

- **Synthetic remains synthetic.** Nothing here makes a number evidence about a real borrower. The
  claim being enabled is narrow and stated: measured performance on a named generated population.
- **UNCALIBRATED does not clear on synthetic data.** E04's coverage figure demonstrates that the
  bands behave as advertised on a book whose truth we generated. Clearing the stamp for a bank needs
  a backtest on that bank's own history, which is the thing `PRIORITIES.md` was right about.
- **A pooled segment's certainty is overstated.** A segment below `MIN_MONTHS` or
  `MIN_FROM_ACCOUNTS` takes the bank-wide row, and with it the bank's sample size rather than its
  own. It is flagged `POOLED` with a reason; a variance-correct shrinkage is not attempted.

## Consequences

- The simulator can be demonstrated on the demo book now, with its basis named, rather than waiting
  on history that does not exist yet.
- Because the honesty renderer and the metric registry are built before E05/E06/E08/E09/E10, those
  lanes inherit one spelling and one caption instead of negotiating five.
- ADR 0013 is not edited. A reader sees the narrowing, then this, and can tell which reasoning
  produced which scope.
