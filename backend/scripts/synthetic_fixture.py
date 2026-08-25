# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-08-25 — New file. PROTOTYPE / SIMULATION SUPPORT ONLY.
#
#   The recovery cohort was scored on 2026-08-24, so its real outcomes mature on
#   2026-09-23 (30d), 2026-10-23 (60d) and 2026-11-22 (90d). A demo was needed
#   before any of those dates. This module and its generator produce SYNTHETIC
#   outcomes so that the validation FRAMEWORK can be exercised end to end, and
#   nothing else.
#
#   THE ONE RULE: synthetic outcomes are never presented as production outcomes.
#   Every artefact this produces carries the banner below, every report leads
#   with it, and the fixture stores it as data so the numbers cannot be
#   separated from the warning by a copy-paste.
#
#   WHY A FILE FIXTURE AND NOT THE DATABASE. The obvious route — insert
#   synthetic recovered_amount_* into repayment_score_snapshots and run the
#   existing validator — would contaminate the real 2026-08-24 cohort with
#   fabricated outcomes that are indistinguishable from real ones once written,
#   and the rollback artefact in docs/rollback/ covers the score columns, not
#   the outcome columns. So the synthetic layer never writes to Postgres. The
#   generator READS the cohort once, read-only, into a committed population
#   file; every scenario is then produced offline from that file plus a seed.
#
#   WHY THE ROWS ARE DUCK-TYPED. recovery_validation.classify() and
#   observation_from() were written against "anything with the snapshot's
#   attributes" precisely so the source of the data could be the caller's
#   choice. That is now being used: a SimpleNamespace off a JSON row reaches the
#   same code path an ORM row does, so the synthetic run exercises the REAL
#   validation framework rather than a copy of it. If the framework is wrong,
#   this demo is wrong in the same way — which is the point of reusing it.
# ───────────────────────────────────────────────────────────────────────────
"""Load and describe synthetic recovery-validation fixtures. No DB, no numpy."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# docs/validation/synthetic/ — deliberately outside backend/, beside the other
# recovery documentation, so nobody mistakes a fixture for application data.
FIXTURE_DIR = Path(__file__).resolve().parents[2] / "docs" / "validation" / "synthetic"
POPULATION_FILE = FIXTURE_DIR / "cohort_population.json"

# Stamped into every fixture, every markdown report and every stdout banner.
BANNER = "SYNTHETIC / SIMULATED VALIDATION — NOT PRODUCTION OUTCOMES"
DISCLAIMER = (
    "These results demonstrate that the validation pipeline operates correctly. "
    "They do not establish real-world scorecard performance."
)

# The cohort this simulation is built on. The predictions are the REAL ones;
# only what happened after the prediction is simulated.
COHORT_DATE = "2026-08-24"
REAL_CHECKPOINTS = {30: "2026-09-23", 60: "2026-10-23", 90: "2026-11-22"}

# Bumped when the simulation's assumption model changes, so a fixture can always
# be traced to the rules that made it. Independent of, and irrelevant to,
# RECOVERY_SCORECARD_VERSION — this versions the SIMULATION, not the scorecard.
SIMULATION_VERSION = "recovery-simulation-1.0.0"

# Loan ids are hashed before they are written to a committed fixture. They are
# internal UUIDs rather than personal data, but a file that lives in git for the
# life of the repository is the wrong place for production keys, and the
# validation framework only ever needs an identifier to be STABLE and UNIQUE.
# Fixed salt, so the mapping is reproducible for anyone with the database and
# meaningless to anyone without it.
ID_SALT = "tiqcollect-synthetic-validation-2026-08-25"


# ── Scenarios ────────────────────────────────────────────────────────────────
# Each scenario is a FIXED, DOCUMENTED set of simulation assumptions and one
# seed. They were written down before any of them was run, and none has been
# altered after seeing its validation result — the demonstration is worthless if
# a dataset is tuned until a metric passes. If a scenario fails a validation
# criterion, that failure IS the finding and is reported as one.
#
# The parameters are the assumption model in numbers; assumptions.md is the same
# model in prose.
#
#   beta        how strongly the TRUE eventual recovery rate tracks the
#               scorecard's prediction. 1.0 means the scorecard's spread is
#               exactly right; below 1 it over-spreads, above 1 it under-spreads.
#   sigma       loan-level idiosyncratic variation in the true rate. This is what
#               creates overlap between bands. Too small and the demo shows a
#               separation no real book would produce.
#   base_shift  a deliberate LEVEL error, so the calibration section has
#               something to find. Ranking and calibration fail independently,
#               and a demo where both are perfect exercises neither.
#   security_effect  an extra effect of collateral on the true rate, ON TOP of
#               the ±0.16 the scorecard already scores. Planted so the
#               factor-vs-residual diagnostic has a known signal to surface. The
#               report says it is planted, so no reader is told it is a discovery.
#   zero_floor / zero_slope  the probability that a loan recovers NOTHING AT ALL,
#               as a function of its prediction. Real delinquent books have a
#               large mass at exactly zero; without it every LOW loan would
#               recover something and the demo would flatter the scorecard.
#   lag         how much slower money actually arrives than the scorecard's speed
#               ramp expects. Applied to the 30/60 shares only, so horizon
#               progression is exercised rather than assumed correct.
#   censor_base / censor_slope  probability of a bank action (write-off,
#               settlement, recall, death) inside the window. Higher on weaker
#               loans, which is what makes censoring worth excluding rather than
#               ignoring: it is correlated with the thing being measured.
#   backlog     share of rows the manual labelling checkpoint has not reached, so
#               the IMMATURE branch of classify() is exercised. This is a real
#               operational state, not an invention: labelling is manual per the
#               runbook, and between 2026-10-23 and 2026-11-22 the production
#               cohort will look exactly like this.
SCENARIOS: dict[str, dict[str, Any]] = {
    "synthetic_baseline": {
        "seed": 20260824,
        "headline": "A scorecard with real but ordinary ranking power",
        "beta": 1.00, "sigma": 0.22, "base_shift": -0.06,
        "security_effect": 0.05,
        "zero_floor": 0.28, "zero_slope": -0.85,
        "lag": 0.80,
        "censor_base": 0.05, "censor_slope": -0.05,
        "backlog": 0.06,
    },
    "synthetic_strong_signal": {
        "seed": 20260825,
        "headline": "A scorecard that separates well — the good case",
        "beta": 1.80, "sigma": 0.15, "base_shift": -0.02,
        "security_effect": 0.05,
        "zero_floor": 0.30, "zero_slope": -1.40,
        "lag": 0.90,
        "censor_base": 0.05, "censor_slope": -0.05,
        "backlog": 0.06,
    },
    "synthetic_weak_signal": {
        "seed": 20260826,
        "headline": "A scorecard with almost no ranking power — the failure case",
        "beta": 0.20, "sigma": 0.30, "base_shift": -0.10,
        "security_effect": 0.05,
        "zero_floor": 0.30, "zero_slope": -0.20,
        "lag": 0.70,
        "censor_base": 0.05, "censor_slope": -0.05,
        "backlog": 0.06,
    },
}
DEFAULT_SCENARIO = "synthetic_baseline"

# Bank actions drawn when a row is censored, with their relative weights. The
# vocabulary is taken from models/repayment_snapshot.CENSORED_OUTCOMES rather
# than invented, so classify() excludes them through the production rule instead
# of a synthetic-only special case.
CENSORING_DRAW = (
    ("WRITTEN_OFF", 0.40),
    ("SETTLED", 0.30),
    ("RECALLED", 0.20),
    ("DECEASED", 0.10),
)


def scenario_path(name: str) -> Path:
    return FIXTURE_DIR / f"{name}.json"


def describe(name: str) -> dict[str, Any]:
    """A scenario's assumptions, or a clear error naming the ones that exist."""
    try:
        return SCENARIOS[name]
    except KeyError:
        raise SystemExit(
            f"unknown scenario {name!r} — available: {', '.join(sorted(SCENARIOS))}"
        ) from None


def load_fixture(name: str) -> tuple[dict[str, Any], list[SimpleNamespace]]:
    """(meta, rows) for a generated scenario. The rows quack like snapshot rows.

    Refuses a fixture that does not carry the banner. A file claiming to be a
    synthetic fixture without the disclaimer stored inside it is either
    hand-edited or produced by something else, and either way must not be fed to
    a validator that will print its numbers under a synthetic heading.
    """
    path = scenario_path(name)
    if not path.exists():
        raise SystemExit(
            f"no fixture at {path}\n"
            "  generate it first:  python -m scripts.generate_synthetic_recovery_validation"
        )
    doc = json.loads(path.read_text(encoding="utf-8"))
    meta = doc.get("meta") or {}
    if meta.get("banner") != BANNER:
        raise SystemExit(f"{path} does not carry the synthetic banner — refusing to use it")
    return meta, [SimpleNamespace(**row) for row in doc["rows"]]
