# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-08-25 — New file. PROTOTYPE / SIMULATION ONLY. See synthetic_fixture.py
#   for why this exists and why it never writes to Postgres.
#
#   IT DOES NOT SCORE ANYTHING. The predictions in every fixture are the REAL
#   ones, read out of the 2026-08-24 cohort and copied through untouched. This
#   simulates only what happened AFTER the prediction, which is the one thing
#   that cannot be known until 2026-11-22. ml/recovery_scorecard.py is not
#   imported for anything except its version string, and even that is only used
#   to check the cohort is the version the validator expects.
#
#   TWO MODES, and the split is the safety property:
#     --export-population   opens Postgres READ-ONLY (the connection carries
#                           default_transaction_read_only=on, so the server
#                           itself refuses a write) and dumps the cohort's
#                           predictions to a committed population file.
#     (default)             pure. Reads that file, writes fixtures. No database,
#                           no clock, no network. This is the mode a developer
#                           who has cloned the repository runs, and it is why
#                           the demo is reproducible without production data.
# ───────────────────────────────────────────────────────────────────────────
"""Generate synthetic post-prediction outcomes for the recovery cohort."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from typing import Any

import numpy as np

from scripts.synthetic_fixture import (
    BANNER, CENSORING_DRAW, COHORT_DATE, DISCLAIMER,
    FIXTURE_DIR, ID_SALT, POPULATION_FILE, REAL_CHECKPOINTS, SCENARIOS,
    SIMULATION_VERSION, describe, scenario_path,
)

# The scorecard's own base rate, used here ONLY as the centring constant that
# makes `zero_floor` and `base_shift` readable as "the value at a neutral loan".
# Imported rather than written as 0.35 so that if BASE_RATE ever moves, this
# simulation's parameters keep the meaning their documentation claims.
from app.ml.recovery_scorecard import BASE_RATE, RECOVERY_SCORECARD_VERSION

HORIZONS = (30, 60, 90)

# The scorecard's maturity ramp (_SHARE_*_SLOW/_FAST in recovery_scorecard.py),
# restated here as the SIMULATION's timing model. Restated rather than imported
# because these are private to that module and because the simulation applies a
# `lag` on top: realised money is assumed to arrive slower than the ramp
# predicts, so the 30- and 60-day calibration has a real error to find instead
# of being correct by construction.
_SIM_SHARE_30 = (0.10, 0.55)
_SIM_SHARE_60 = (0.35, 0.80)

# Noise on the speed index. The scorecard's speed reasoning is assumed roughly
# right but not exact; without this the horizon split would be deterministic
# given the prediction, and the 30/60 metrics would measure nothing.
_SPEED_NOISE_SD = 0.12


# ── Read-only population export ──────────────────────────────────────────────
def hash_loan_id(loan_id: str) -> str:
    """A stable, meaningless-without-the-database identifier."""
    return hashlib.sha256((ID_SALT + loan_id).encode("utf-8")).hexdigest()[:16]


def export_population(database_url: str | None, cohort: str) -> dict[str, Any]:
    """Dump the cohort's REAL predictions. Opens a read-only transaction.

    Only the fields the validation framework consumes are carried across:
    the identifier, the band, the three rates, the speed index, the coverage,
    the version, total_outstanding, whether a case exists, and the factor
    contributions reduced to (code, abstained, direction, points).

    `summary` and `evidence` are deliberately DROPPED from the contributions.
    The validation framework never reads them, they carry rupee figures and
    free text, and this file is committed to git — the smallest thing that
    works is the right thing to store.
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    if not database_url:
        # Only reached when no URL was given. app.core.config refuses to import
        # without SECRET_KEY and the MinIO keys, which a developer running this
        # from a host shell will not have set — so --database-url stays usable
        # on its own rather than dragging the whole app config in.
        from app.core.config import settings
        database_url = settings.DATABASE_URL

    engine = create_engine(
        database_url,
        # The hard guarantee, copied from validate_recovery.py: any write
        # attempted through this connection errors rather than quietly working.
        connect_args={"options": "-c default_transaction_read_only=on"},
    )
    db = sessionmaker(bind=engine)()
    try:
        rows = db.execute(text("""
            SELECT s.loan_id,
                   s.recovery_potential,
                   s.recovery_rate_30, s.recovery_rate_60, s.recovery_rate_90,
                   s.recovery_speed_index, s.recovery_evidence_coverage,
                   s.recovery_model_version,
                   s.is_backfill,
                   (s.features ->> 'total_outstanding')::float AS total_outstanding,
                   s.recovery_contributions,
                   EXISTS (SELECT 1 FROM cases c WHERE c.loan_id = s.loan_id) AS has_case
            FROM repayment_score_snapshots s
            WHERE s.as_of_date = :d AND s.recovery_rate_90 IS NOT NULL
            ORDER BY s.loan_id
        """), {"d": cohort}).mappings().all()

        population = []
        for r in rows:
            factors = []
            for f in ((r["recovery_contributions"] or {}).get("factors") or []):
                kept = {"code": f.get("code"), "abstained": bool(f.get("abstained"))}
                if not kept["abstained"]:
                    kept["direction"] = f.get("direction")
                    kept["points"] = float(f.get("points") or 0.0)
                else:
                    kept["reason"] = f.get("reason")
                factors.append(kept)
            population.append({
                "loan_ref": hash_loan_id(r["loan_id"]),
                "recovery_potential": r["recovery_potential"],
                "recovery_rate_30": r["recovery_rate_30"],
                "recovery_rate_60": r["recovery_rate_60"],
                "recovery_rate_90": r["recovery_rate_90"],
                "recovery_speed_index": r["recovery_speed_index"],
                "recovery_evidence_coverage": r["recovery_evidence_coverage"],
                "recovery_model_version": r["recovery_model_version"],
                "is_backfill": bool(r["is_backfill"]),
                "total_outstanding": r["total_outstanding"],
                "has_case": bool(r["has_case"]),
                "factors": factors,
            })
    finally:
        db.rollback()
        db.close()

    # Canonical order, so a fixture never depends on the order Postgres happened
    # to return rows in. The seeded draws below consume the RNG row by row, so
    # without this the "fixed seed" promise would be false.
    population.sort(key=lambda p: p["loan_ref"])
    return {
        "meta": {
            "banner": BANNER,
            "disclaimer": DISCLAIMER,
            "what_this_is": "REAL predictions from the production cohort. No outcomes.",
            "cohort": cohort,
            "scorecard_version": RECOVERY_SCORECARD_VERSION,
            "loan_ids": "sha256(salt + uuid)[:16] — stable, not reversible without the database",
            "read_only": "exported inside default_transaction_read_only=on",
            "n": len(population),
        },
        "population": population,
    }


# ── The simulation ───────────────────────────────────────────────────────────
def _security_sign(factors: list[dict]) -> int:
    """+1 secured, -1 unsecured, 0 undetermined — the same rule the validator uses."""
    for f in factors:
        if f.get("code") == "SECURITY" and not f.get("abstained"):
            return 1 if float(f.get("points") or 0.0) > 0 else -1
    return 0


def _clip(x: float, lo: float, hi: float) -> float:
    return float(min(max(x, lo), hi))


def simulate_row(row: dict[str, Any], params: dict[str, Any],
                 rng: np.random.Generator) -> dict[str, Any]:
    """One loan: the real prediction in, a synthetic snapshot-shaped row out.

    The assumption model, in order — each step is a documented choice, and
    assumptions.md carries the same list in prose:

      1. CASELESS LOANS ARE NEVER GIVEN AN AMOUNT. A loan with no case cannot
         show a recovery: Payment.case_id is NOT NULL, so money reaches the
         ledger only through a case. Writing 0.0 there would manufacture exactly
         the correlation the validation is testing for — no case depresses the
         SCORE too, because PAYMENT_MOMENTUM abstains without a payment path —
         and it would bias LOW hardest (50 of the 115 caseless loans are LOW).
         They stay NULL with the marker at 90, which classify() reads as
         UNOBSERVABLE. This mirrors attach_recovery_outcomes exactly.
      2. A LARGE MASS AT EXACTLY ZERO, more likely on weaker loans. Delinquent
         books do not recover a little from everyone; most accounts return
         nothing in a quarter.
      3. THE TRUE EVENTUAL RATE tracks the prediction with slope `beta`, shifted
         by `base_shift` (a level error, so calibration has something to find)
         and by a planted `security_effect` (so the factor diagnostic has a known
         signal to surface), plus loan-level noise `sigma` (so the bands overlap).
      4. TIMING uses the scorecard's own speed index, with noise, times `lag` —
         money arrives slower than the ramp predicts.
      5. CENSORING is drawn after the amounts and does not erase them, because
         the production labeller also measures censored rows and excludes them
         through `outcome` rather than by refusing to look.
      6. A LABELLING BACKLOG leaves a documented share of rows with the 90-day
         figure not yet written, which is the real state of the production cohort
         between two manual checkpoints.
    """
    p = float(row["recovery_rate_90"])
    outstanding = float(row["total_outstanding"])
    sim: dict[str, Any] = {"prediction_used": p}

    out: dict[str, Any] = {
        "loan_id": row["loan_ref"],
        "as_of_date": COHORT_DATE,
        "recovery_model_version": row["recovery_model_version"],
        "recovery_potential": row["recovery_potential"],
        "recovery_rate_30": row["recovery_rate_30"],
        "recovery_rate_60": row["recovery_rate_60"],
        "recovery_rate_90": row["recovery_rate_90"],
        "recovery_speed_index": row["recovery_speed_index"],
        "recovery_evidence_coverage": row["recovery_evidence_coverage"],
        "recovery_contributions": {"factors": row["factors"]},
        "features": {"total_outstanding": outstanding},
        "is_backfill": row["is_backfill"],
        "outcome": None,
        "recovered_amount_30": None,
        "recovered_amount_60": None,
        "recovered_amount_90": None,
        "recovery_labelled_through_days": 0,
    }

    # (1) Unobservable. Draws are still consumed below for observable rows only,
    # so a caseless row costs no randomness and the stream stays stable.
    if not row["has_case"]:
        out["recovery_labelled_through_days"] = 90
        out["_simulation"] = {**sim, "class": "UNOBSERVABLE",
                              "why": "no case for the whole window — the ledger cannot see it"}
        return out

    # (2) Does anything come back at all?
    p_zero = _clip(params["zero_floor"] + params["zero_slope"] * (p - BASE_RATE), 0.02, 0.95)
    recovers_nothing = bool(rng.random() < p_zero)

    # (3) The eventual true rate.
    #
    # Drawn from a BETA, not from a normal clipped to [0, 1]. The first draft did
    # clip a normal, and it put mass on both boundaries where the mean is near
    # them: 27% of HIGH loans in synthetic_strong_signal recovered their entire
    # balance inside 90 days, which no delinquent book does, and a second spike
    # of exact zeros landed on top of the Bernoulli in (2), double-counting the
    # thing that step already models. A Beta is supported on the open interval,
    # so both artefacts disappear and the mean still means what the parameter
    # says. It also squeezes the variance automatically as the mean approaches a
    # boundary, which is the right behaviour rather than a compromise.
    sec = _security_sign(row["factors"])
    mu = (BASE_RATE
          + params["beta"] * (p - BASE_RATE)
          + params["base_shift"]
          + params["security_effect"] * sec)
    if recovers_nothing:
        true_rate = 0.0
    else:
        m = _clip(mu, 0.02, 0.98)
        # Concentration from the requested spread: Var = m(1-m)/(k+1).
        #
        # The floor keeps BOTH shape parameters at or above 1, which is what
        # makes the density unimodal. Without it, a mean pushed to the 0.98 clip
        # (beta=1.8 does that to the strongest predictions, because a linear map
        # of a bounded quantity can leave its interval) gave Beta(0.49, 0.01) — a
        # U-shape piling 31% of HIGH loans at exactly full recovery, which is the
        # boundary artefact this distribution was chosen to avoid. sigma is then
        # not honoured near the boundary; that is the correct trade, since a mean
        # of 0.98 cannot have a spread of 0.15 and stay inside [0, 1].
        k = max(m * (1.0 - m) / (params["sigma"] ** 2) - 1.0, 1.0 / min(m, 1.0 - m))
        true_rate = float(rng.beta(m * k, (1.0 - m) * k))

    # (4) Timing.
    speed = _clip(float(row["recovery_speed_index"] or 0.5)
                  + rng.normal(0.0, _SPEED_NOISE_SD), 0.0, 1.0)
    lag = params["lag"]
    f30 = _clip((_SIM_SHARE_30[0] + (_SIM_SHARE_30[1] - _SIM_SHARE_30[0]) * speed) * lag, 0.0, 1.0)
    f60 = _clip((_SIM_SHARE_60[0] + (_SIM_SHARE_60[1] - _SIM_SHARE_60[0]) * speed) * lag, 0.0, 1.0)

    amt_90 = round(true_rate * outstanding, 2)
    amt_60 = round(amt_90 * f60, 2)
    amt_30 = round(amt_90 * f30, 2)
    # Monotonicity is guaranteed by f30 < f60 <= 1, but rounding at the paisa is
    # cheap to make exact rather than argue about.
    amt_60 = min(amt_60, amt_90)
    amt_30 = min(amt_30, amt_60)

    out["recovered_amount_30"] = amt_30
    out["recovered_amount_60"] = amt_60
    out["recovered_amount_90"] = amt_90
    out["recovery_labelled_through_days"] = 90

    # (5) Censoring — the amounts stay, `outcome` is what excludes the row.
    p_cens = _clip(params["censor_base"] + params["censor_slope"] * (p - BASE_RATE), 0.0, 0.5)
    censored = bool(rng.random() < p_cens)
    if censored:
        names = [n for n, _ in CENSORING_DRAW]
        weights = np.array([w for _, w in CENSORING_DRAW], dtype=float)
        out["outcome"] = str(rng.choice(names, p=weights / weights.sum()))

    # (6) Labelling backlog — the 90-day figure not yet written.
    backlog = bool(rng.random() < params["backlog"])
    if backlog:
        out["recovered_amount_90"] = None
        out["recovery_labelled_through_days"] = 60

    out["_simulation"] = {
        **sim,
        "class": "CENSORED" if censored else ("BACKLOG" if backlog else "OBSERVED"),
        "recovers_nothing": recovers_nothing,
        "p_zero": round(p_zero, 4),
        "true_rate_90": round(true_rate, 4),
        "security_sign": sec,
        "speed_used": round(speed, 4),
        "share_30": round(f30, 4),
        "share_60": round(f60, 4),
    }
    return out


def generate(population: list[dict[str, Any]], scenario: str) -> dict[str, Any]:
    """A whole scenario. Deterministic: same population + same seed, same file."""
    params = describe(scenario)
    rng = np.random.default_rng(params["seed"])
    rows = [simulate_row(r, params, rng) for r in population]

    classes: dict[str, int] = {}
    bands: dict[str, int] = {}
    for r in rows:
        classes[r["_simulation"]["class"]] = classes.get(r["_simulation"]["class"], 0) + 1
        bands[r["recovery_potential"]] = bands.get(r["recovery_potential"], 0) + 1

    return {
        "meta": {
            "banner": BANNER,
            "disclaimer": DISCLAIMER,
            "scenario": scenario,
            "headline": params["headline"],
            "seed": params["seed"],
            "simulation_version": SIMULATION_VERSION,
            "cohort": COHORT_DATE,
            "scorecard_version": RECOVERY_SCORECARD_VERSION,
            "real_checkpoints": REAL_CHECKPOINTS,
            "n": len(rows),
            "bands": bands,
            "simulated_classes": classes,
            "assumptions": {k: v for k, v in params.items() if k != "headline"},
            "predictions": "REAL — copied from the production cohort, not re-scored",
            "outcomes": "SYNTHETIC — generated by scripts/generate_synthetic_recovery_validation.py",
        },
        "rows": rows,
    }


# ── Output ───────────────────────────────────────────────────────────────────
_CSV_COLUMNS = (
    "loan_id", "recovery_potential", "recovery_rate_30", "recovery_rate_60",
    "recovery_rate_90", "recovered_amount_30", "recovered_amount_60",
    "recovered_amount_90", "total_outstanding", "outcome",
    "recovery_labelled_through_days", "simulated_class",
)


def dump_json(doc: dict[str, Any], rows_key: str = "rows") -> str:
    """Readable meta, one compact object per row.

    `json.dumps(indent=1)` on 525 rows of nested factor lists is 972 KB of mostly
    whitespace, committed for the life of the repository. One row per line is both
    smaller and easier to work with — `grep` finds a loan, and a diff between two
    scenarios is line-per-loan instead of unreadable.
    """
    meta = json.dumps(doc["meta"], indent=1)
    rows = ",\n  ".join(json.dumps(r, separators=(",", ":")) for r in doc[rows_key])
    return ('{\n "meta": ' + meta + f',\n "{rows_key}": [\n  ' + rows + "\n ]\n}\n")


def write_csv(doc: dict[str, Any], scenario: str) -> None:
    """A flat view for eyeballing in a spreadsheet. The JSON is authoritative."""
    path = FIXTURE_DIR / f"{scenario}.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([f"# {BANNER}"])
        w.writerow([f"# {DISCLAIMER}"])
        w.writerow(_CSV_COLUMNS)
        for r in doc["rows"]:
            w.writerow([
                r["loan_id"], r["recovery_potential"], r["recovery_rate_30"],
                r["recovery_rate_60"], r["recovery_rate_90"],
                r["recovered_amount_30"], r["recovered_amount_60"],
                r["recovered_amount_90"], r["features"]["total_outstanding"],
                r["outcome"] or "", r["recovery_labelled_through_days"],
                r["_simulation"]["class"],
            ])
    print(f"  wrote {path.relative_to(FIXTURE_DIR.parents[2])}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--export-population", action="store_true",
                    help="re-read the real cohort from Postgres (READ-ONLY) into "
                         "cohort_population.json")
    ap.add_argument("--cohort", default=COHORT_DATE)
    ap.add_argument("--scenario", action="append", choices=sorted(SCENARIOS),
                    help="default: all scenarios")
    ap.add_argument("--database-url", default=None)
    args = ap.parse_args()

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    print(BANNER)
    print(DISCLAIMER)
    print()

    if args.export_population:
        doc = export_population(args.database_url, args.cohort)
        POPULATION_FILE.write_text(
            dump_json(doc, rows_key="population"),
            encoding="utf-8")
        print(f"  exported {doc['meta']['n']} real predictions (READ-ONLY) -> "
              f"{POPULATION_FILE.name}")
        print()

    if not POPULATION_FILE.exists():
        raise SystemExit(
            f"no population file at {POPULATION_FILE}\n"
            "  create it once, against the live cohort, with:\n"
            "    python -m scripts.generate_synthetic_recovery_validation "
            "--export-population")

    pop_doc = json.loads(POPULATION_FILE.read_text(encoding="utf-8"))
    population = pop_doc["population"]
    print(f"  population: {len(population)} real predictions, cohort "
          f"{pop_doc['meta']['cohort']}, {pop_doc['meta']['scorecard_version']}")

    for scenario in (args.scenario or sorted(SCENARIOS)):
        doc = generate(population, scenario)
        scenario_path(scenario).write_text(dump_json(doc), encoding="utf-8")
        m = doc["meta"]
        print(f"\n  {scenario}  seed={m['seed']}  n={m['n']}")
        print(f"    bands   : {m['bands']}")
        print(f"    classes : {m['simulated_classes']}")
        print(f"  wrote {scenario_path(scenario).name}")
        write_csv(doc, scenario)

    print("\n  Nothing was written to any database. Predictions are real; "
          "outcomes are synthetic.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
