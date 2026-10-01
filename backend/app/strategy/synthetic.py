# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-24 — New. SYNTHETIC inputs for the Monte Carlo engine: a benchmark
#   book, test fixtures, and a demo until task E01 reads real transition
#   counts from `mv_bucket_transitions_monthly`.
#
#   The reference rows reuse CC's hand-authored "Near-prime" rates
#   (models/stress_simulator_data.py:21-116) with one deliberate change:
#   CC rolls 30% of Sub-standard into Doubtful EVERY MONTH (:91, "a rough
#   proxy"), which empties Sub-standard in ~3 months. Doubtful is a 12-month
#   age rule (states.NPA_DOUBTFUL_AFTER_MONTHS), so the monthly hazard here is
#   1/12. And unlike CC, what comes out is COUNTS drawn from those rows —
#   the engine's input is evidence, and a thin segment has to look thin.
#
#   Everything built here carries `synthetic=True`, which the result
#   propagates, per the repo's SYNTHETIC_WARNING convention.
#
# 2026-09-24 (later) — The 1/12 Sub -> Doubtful cell stays in the reference
#   rows (it is what an observed matrix would contain), but since mc-1.1.0
#   the engine folds it into staying put and applies the 12-month rule
#   itself, so it no longer moves anyone in a simulation.
# ───────────────────────────────────────────────────────────────────────────
"""Synthetic transition counts and books. Never evidence about real borrowers."""
from __future__ import annotations

from typing import Sequence

import numpy as np

from app.strategy.monte_carlo import Portfolio, SegmentMatrices
from app.strategy.states import (
    CURRENT, N_STATES, NPA_DOUBTFUL, NPA_SUB, RESOLVED, SMA_0, SMA_1, SMA_2, WRITTEN_OFF,
)

SYNTHETIC_WARNING = "SYNTHETIC: generated inputs, not evidence about any real book."

# (monthly Current->SMA-0 roll, SMA-0 cure, NPA resolution) — CC's Near-prime rates.
_RATES: dict[str, tuple[float, float, float]] = {
    "PERSONAL": (0.035, 0.42, 0.07),
    "CREDIT_CARD": (0.040, 0.40, 0.08),
    "HOME": (0.010, 0.55, 0.03),
    "AUTO": (0.025, 0.48, 0.12),
    "BUSINESS": (0.038, 0.41, 0.06),     # CC "SME"
    "GOLD": (0.050, 0.45, 0.05),         # CC "Agri"
    "MICROFINANCE": (0.050, 0.45, 0.05),  # CC "Agri"
    "EDUCATION": (0.035, 0.42, 0.07),    # CC "Personal Loan"
}
# Median total_outstanding, INR. ASSUMPTION, for plausible magnitudes only.
_MEDIAN_BALANCE: dict[str, float] = {
    "PERSONAL": 200_000, "CREDIT_CARD": 80_000, "HOME": 2_500_000, "AUTO": 500_000,
    "BUSINESS": 1_000_000, "GOLD": 100_000, "MICROFINANCE": 40_000, "EDUCATION": 400_000,
}
DEFAULT_STATE_MIX = (0.80, 0.08, 0.04, 0.03, 0.03, 0.02, 0.0, 0.0)


def reference_matrix(loan_type: str = "PERSONAL") -> np.ndarray:
    """An 8x8 row-stochastic matrix shaped like CC's (see CHANGELOG)."""
    c0, cure, npa_res = _RATES.get(loan_type, _RATES["PERSONAL"])
    m = np.zeros((N_STATES, N_STATES))
    m[CURRENT, SMA_0], m[CURRENT, RESOLVED] = c0, 0.02
    m[SMA_0, CURRENT], m[SMA_0, SMA_1], m[SMA_0, RESOLVED] = cure, 3.5 * c0, 0.01
    f2 = min(0.9, 3.5 * c0 * 1.5)
    m[SMA_1, CURRENT], m[SMA_1, SMA_0], m[SMA_1, SMA_2], m[SMA_1, RESOLVED] = 0.4 * cure, 0.3 * cure, f2, 0.005
    m[SMA_2, CURRENT], m[SMA_2, SMA_1], m[SMA_2, NPA_SUB] = 0.1 * cure, 0.2 * cure, min(0.95, f2 * 1.2)
    m[NPA_SUB, CURRENT], m[NPA_SUB, NPA_DOUBTFUL], m[NPA_SUB, RESOLVED] = 0.01, 1.0 / 12.0, 0.8 * npa_res
    m[NPA_DOUBTFUL, WRITTEN_OFF], m[NPA_DOUBTFUL, RESOLVED] = 0.05, npa_res
    for i in range(N_STATES):
        m[i, i] = 0.0
        m[i, i] = 1.0 - m[i].sum()
    m[WRITTEN_OFF] = np.eye(N_STATES)[WRITTEN_OFF]
    m[RESOLVED] = np.eye(N_STATES)[RESOLVED]
    return m


def counts_from_matrix(matrix: np.ndarray, transitions_per_row: int | Sequence[int],
                       rng: np.random.Generator) -> np.ndarray:
    """Multinomial transition counts, `transitions_per_row` observed per row."""
    n = np.broadcast_to(np.asarray(transitions_per_row), (N_STATES,))
    return np.stack([rng.multinomial(int(n[i]), matrix[i]) for i in range(N_STATES)]).astype(float)


def synthetic_book(n_accounts: int = 50_000, seed: int = 0,
                   loan_types: Sequence[str] = ("PERSONAL", "CREDIT_CARD", "HOME", "AUTO", "BUSINESS", "GOLD"),
                   regions: Sequence[str] = ("NORTH", "SOUTH", "EAST", "WEST"),
                   transitions_per_row: int = 3_000,
                   state_mix: Sequence[float] = DEFAULT_STATE_MIX) -> tuple[Portfolio, SegmentMatrices]:
    """A SYNTHETIC book: segments = loan type x region, each with counts drawn
    from its product's reference matrix, lightly perturbed per region."""
    rng = np.random.default_rng(np.random.SeedSequence([seed, 0x5E6]))
    keys, lts, counts = [], [], []
    for lt in loan_types:
        ref = reference_matrix(lt)
        for rg in regions:
            m = ref * rng.lognormal(0.0, 0.15, size=ref.shape)  # regional variation
            m[np.eye(N_STATES, dtype=bool)] = 0.0
            m[ref == 0] = 0.0
            np.fill_diagonal(m, np.maximum(0.0, 1.0 - m.sum(axis=1)))
            m /= m.sum(axis=1, keepdims=True)
            keys.append(f"lt={lt}|r={rg}")
            lts.append(lt)
            counts.append(counts_from_matrix(m, transitions_per_row, rng))
    matrices = SegmentMatrices(tuple(keys), np.stack(counts), loan_types=tuple(lts),
                               synthetic=True, source=SYNTHETIC_WARNING)
    S = len(keys)
    segment = rng.integers(0, S, n_accounts).astype(np.int32)
    mix = np.asarray(state_mix, dtype=float)
    state = rng.choice(N_STATES, size=n_accounts, p=mix / mix.sum()).astype(np.int8)
    lt_arr = np.asarray(lts, dtype=object)[segment]
    med = np.array([_MEDIAN_BALANCE.get(x, 200_000) for x in lt_arr])
    balance = np.round(med * rng.lognormal(0.0, 0.8, n_accounts), 2)
    region = np.asarray([k.split("|r=")[1] for k in keys], dtype=object)[segment]
    portfolio = Portfolio(state=state, balance=balance, segment=segment, loan_type=lt_arr,
                          region=region, synthetic=True)
    return portfolio, matrices
