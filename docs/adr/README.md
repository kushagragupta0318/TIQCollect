# Architecture decision records

Each file records one decision: its context, the decision itself, the evidence it rests on, and
the consequences. Records are append-only. If a decision changes, a new ADR supersedes the old
one, and the old one gets a "Superseded by" line and keeps the rest of its text.

These replace the dated "this used to say…" history that used to live in CLAUDE.md and in
source-file CHANGELOG headers (ADR 0006). Commit messages carry the per-change measurements.

| # | Decision | Status |
|---|---|---|
| [0001](0001-one-definition-per-rule.md) | One definition per business rule, guarded by a tripwire | Accepted, 2026-09-07 |
| [0002](0002-allocator-expected-recovery-term.md) | The allocator's expected-recovery term uses the model probability with a rescaled value transform, shipped as one change | Accepted, 2026-09-08 |
| [0003](0003-epsilon-greedy-exploration.md) | ε-greedy exploration at 10% of nightly assignments | Accepted, 2026-09-08 |
| [0004](0004-model-outcome-definition.md) | The recovery_risk outcome label: one rule, one writer, versioned | Accepted, 2026-09-08 |
| [0005](0005-scoring-invariants.md) | Scoring invariants: point-in-time features, no outputs as inputs, abstain rather than impute, `is_modelled` | Accepted, 2026-09-07/09 |
| [0006](0006-comments-and-documentation.md) | Comments say why; history lives in git and ADRs | Accepted, 2026-09-24 |
| [0007](0007-retraining-and-promotion.md) | Retraining is automatic up to a human approval; promotion needs four eyes | Accepted, 2026-09-09/10 |
| [0008](0008-recovery-risk-2-2-0-champion.md) | recovery_risk 2.2.0 (a GAM) is champion; its known limits | Accepted, 2026-09-16 |
| [0009](0009-standalone-product.md) | TIQCollect is a standalone product; `PRODUCT_MODE` is removed | Accepted, 2026-09-28 |
| [0010](0010-placement-engine.md) | The placement engine v1: gates shared with manual placement, value = E × m × (1 − c), headroom as capacity, min-cost flow, exploration off by default, four-eyes apply | Accepted, 2026-09-29 |
| [0011](0011-offline-outbox.md) | Offline outbox: capture time, replay and its bounds | Accepted, 2026-09-29 |
| [0012](0012-object-storage-image.md) | Object storage after `minio/minio` stopped being published: keep the pinned upstream digest, mirror it to ghcr (mirror not yet pushed) | Accepted, 2026-09-30 |
| [0013](0013-monte-carlo-strategy-simulator.md) | The Monte Carlo strategy simulator is a showcase, not a platform: E01/E02 in, E03/E04 and the agent studio deferred; state space and DPD ladder read from `models/loan`; forbidden transitions masked out of the posterior and reported | Accepted, 2026-09-30 |
