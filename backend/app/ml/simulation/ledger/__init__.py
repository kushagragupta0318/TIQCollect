# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-09 — NEW. Phase 1 of the event-sourced synthetic book.
#
#   WHY A SECOND SIMULATOR. `book_simulator.py` carries account state in a dict
#   and EMITS AGGREGATED FEATURES DIRECTLY. `dpd` and `overdue_amount` are state
#   variables it updates; `ptp_kept_ratio` is computed inside its own loop. So
#   the simulator is a second feature-engineering implementation, and nothing it
#   produces can test the one that serves. Training/serving skew is undetectable
#   by construction — which is how four silent ML failures survived a full cycle
#   in September.
#
#   This package inverts that: generate EVENTS, derive everything else. `dpd`
#   and `overdue_amount` become pure functions of (installment schedule, payment
#   ledger, as_of) rather than mutable state, so point-in-time correctness is
#   structural instead of a discipline someone has to remember.
#
#   `book_simulator.py` IS NOT TOUCHED. It produced the committed
#   `recovery_risk` 1.1.0 artifact (OOT Gini 0.5136) and remains the baseline
#   every result from here is compared against.
# ───────────────────────────────────────────────────────────────────────────
"""Event-sourced synthetic collections book.

    from app.ml.simulation.ledger import LedgerConfig, LedgerSimulator

    sim = LedgerSimulator(LedgerConfig(n_borrowers=5000, months=24, seed=42))
    ledger = sim.run()          # event tables
    panel = build_panel(ledger) # the modelling frame train.py already consumes
"""
from app.ml.simulation.ledger.config import (  # noqa: F401
    BANDS, LedgerConfig, Provenance,
)
from app.ml.simulation.ledger.simulator import Ledger, LedgerSimulator  # noqa: F401
