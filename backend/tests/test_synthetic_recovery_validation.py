# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-08-25 — New file. Tests for the SYNTHETIC validation prototype.
#
#   Two jobs, and the second matters more than the first.
#
#   (a) The generator is correct: deterministic under its seed, monotone across
#       horizons, bounded by the balance, and faithful to the real predictions it
#       claims to preserve.
#
#   (b) THE PROTOTYPE CANNOT REACH PRODUCTION. A synthetic outcome written into
#       repayment_score_snapshots would be indistinguishable from a real one the
#       moment it landed, and the rollback artefact in docs/rollback/ covers the
#       score columns, not the outcome columns — so there would be nothing to
#       undo it with. Several tests below therefore assert on the SOURCE of the
#       synthetic scripts rather than on their behaviour: a test that only checks
#       what the code does today cannot stop someone adding a commit() tomorrow.
#       Same technique as the write-site guard in test_repayment_service.py.
#
#   The frozen-constants tests are here rather than in test_recovery_scorecard.py
#   on purpose. That file tests the scorecard; these assert that a SIMULATION
#   sitting next to it did not move any of it, which is a different claim and
#   belongs with the thing that could have broken it.
# ───────────────────────────────────────────────────────────────────────────
"""The synthetic prototype: correct, deterministic, and unable to touch production."""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app.ml import recovery_validation as rv
from scripts import synthetic_fixture as sf
from scripts.generate_synthetic_recovery_validation import (
    _security_sign, export_population, generate, hash_loan_id, simulate_row,
)

SCENARIOS = tuple(sorted(sf.SCENARIOS))
BASELINE = sf.DEFAULT_SCENARIO
HORIZONS = (30, 60, 90)

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


# ── Fixtures ─────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def population() -> list[dict]:
    """The committed real-prediction population. Skips if it has not been exported.

    Skips rather than fails: a developer who has cloned the repository without the
    fixtures should get a clear reason, not a red suite from a missing artefact.
    """
    if not sf.POPULATION_FILE.exists():
        pytest.skip(f"no population file at {sf.POPULATION_FILE} — "
                    "run generate_synthetic_recovery_validation --export-population")
    return json.loads(sf.POPULATION_FILE.read_text(encoding="utf-8"))["population"]


@pytest.fixture(scope="module")
def baseline(population):
    return generate(population, BASELINE)


def _rows(doc):
    return [SimpleNamespace(**r) for r in doc["rows"]]


# ── (1) Determinism ──────────────────────────────────────────────────────────
def test_generation_is_deterministic_under_its_seed(population):
    """Same population, same seed, byte-identical output.

    The seeded stream is consumed row by row, so this also pins the CANONICAL
    ORDER: the generator sorts the population by hashed loan id precisely so a
    fixture cannot depend on the order Postgres happened to return rows in. Drop
    that sort and this test fails.
    """
    first = json.dumps(generate(population, BASELINE), sort_keys=True)
    second = json.dumps(generate(population, BASELINE), sort_keys=True)
    assert first == second


def test_different_scenarios_produce_different_data(population):
    """Otherwise the three 'scenarios' are one dataset with three labels."""
    docs = {s: generate(population, s) for s in SCENARIOS}
    signatures = {
        s: tuple(r["recovered_amount_90"] for r in d["rows"])
        for s, d in docs.items()
    }
    assert len(set(signatures.values())) == len(SCENARIOS)


def test_hashed_loan_ids_are_stable_and_not_the_uuid():
    """Stable, unique, and useless without the database — see README on PII."""
    assert hash_loan_id("abc-123") == hash_loan_id("abc-123")
    assert hash_loan_id("abc-123") != hash_loan_id("abc-124")
    assert "abc-123" not in hash_loan_id("abc-123")
    assert len(hash_loan_id("abc-123")) == 16


# ── (2, 3, 4, 5) The predictions are preserved, not regenerated ──────────────
def test_population_size_is_preserved(population, baseline):
    assert len(baseline["rows"]) == len(population) == baseline["meta"]["n"]


def test_loan_ids_are_preserved(population, baseline):
    assert ([r["loan_id"] for r in baseline["rows"]]
            == [p["loan_ref"] for p in population])


def test_predicted_rates_are_preserved_exactly(population, baseline):
    """THE POINT OF THE WHOLE DESIGN. If the simulation re-scored anything, the
    demo would be validating a scorecard nobody shipped."""
    for row, pop in zip(baseline["rows"], population):
        for horizon in HORIZONS:
            assert row[f"recovery_rate_{horizon}"] == pop[f"recovery_rate_{horizon}"]
        assert row["recovery_speed_index"] == pop["recovery_speed_index"]
        assert row["recovery_evidence_coverage"] == pop["recovery_evidence_coverage"]
        assert row["features"]["total_outstanding"] == pop["total_outstanding"]


def test_bands_are_preserved(population, baseline):
    assert ([r["recovery_potential"] for r in baseline["rows"]]
            == [p["recovery_potential"] for p in population])


def test_the_real_band_structure_survives(baseline):
    """The cohort as scored on 2026-08-24. A simulation that quietly changed the
    band mix would be answering a question about a different book."""
    assert baseline["meta"]["bands"] == {"HIGH": 162, "MEDIUM": 222, "LOW": 141}


def test_model_version_is_carried_through_unchanged(baseline):
    versions = {r["recovery_model_version"] for r in baseline["rows"]}
    assert versions == {rv.EXPECTED_VERSION}


# ── (6, 7, 8, 9) The outcome amounts are well-formed ─────────────────────────
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_amounts_are_monotone_and_bounded(population, scenario):
    """30 <= 60 <= 90 <= outstanding, and never negative — for every row of every
    scenario, not a sampled few. Guaranteed by construction (f30 < f60 <= 1) and
    clamped at the paisa so rounding cannot break it; asserted because
    'guaranteed by construction' is how the repayment scorecard's monotonicity
    bug would have been missed."""
    for row in generate(population, scenario)["rows"]:
        outstanding = row["features"]["total_outstanding"]
        amounts = [row[f"recovered_amount_{h}"] for h in HORIZONS]
        if all(a is None for a in amounts):
            continue                                  # unobservable — see below
        present = [a for a in amounts if a is not None]
        assert all(a >= 0 for a in present), row["loan_id"]
        assert all(a <= outstanding + 0.01 for a in present), row["loan_id"]
        a30, a60, a90 = amounts
        if a30 is not None and a60 is not None:
            assert a30 <= a60, row["loan_id"]
        if a60 is not None and a90 is not None:
            assert a60 <= a90, row["loan_id"]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_the_result_is_not_artificially_perfect(population, scenario):
    """Guards against the failure mode the brief warned about: a fabricated
    separation. Every band must contain both a zero and a non-zero recovery, and
    no band may be uniform — a scorecard demo where every LOW loan recovers
    exactly nothing and every HIGH loan recovers everything demonstrates only
    that the generator was told the answer."""
    rows = [r for r in generate(population, scenario)["rows"]
            if r["recovered_amount_90"] is not None]
    by_band: dict[str, list[float]] = {}
    for r in rows:
        rate = r["recovered_amount_90"] / r["features"]["total_outstanding"]
        by_band.setdefault(r["recovery_potential"], []).append(rate)

    for band, rates in by_band.items():
        assert min(rates) == 0.0, f"{band}: no loan recovered nothing"
        assert max(rates) > 0.0, f"{band}: no loan recovered anything"
        # The bar is on the BAND, not the loan. An individual account recovering
        # its whole balance is what a fully-recovered account is, and forbidding
        # it would be less realistic rather than more; what must not happen is a
        # band that is uniformly everything or uniformly nothing.
        mean = sum(rates) / len(rates)
        assert 0.0 < mean < 1.0, f"{band}: mean rate is at an extreme"
        at_full = sum(1 for r in rates if r >= 1.0) / len(rates)
        assert at_full < 0.25, f"{band}: {at_full:.0%} of loans recovered in full"
        # Individuality is asserted on the NON-ZERO rates. A large share of
        # exact zeros is the intended mass-at-zero, not a lack of variance, and
        # counting them as duplicates would fail a correct dataset.
        nonzero = [r for r in rates if r > 0.0]
        assert len(set(nonzero)) == len(nonzero), f"{band}: two loans share a rate"
        assert len(nonzero) >= 0.2 * len(rates), f"{band}: almost nothing recovered"


# ── (10) Caseless loans stay unobservable ────────────────────────────────────
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_caseless_loans_are_unobservable_never_zero(population, scenario):
    """The finding this prototype must not destroy.

    Writing 0.0 for a caseless loan would manufacture the correlation the
    validation is testing for: caselessness depresses the SCORE too, because
    PAYMENT_MOMENTUM abstains without a payment path. And it would bias LOW
    hardest — 50 of the 115 caseless loans are LOW.
    """
    caseless_refs = {p["loan_ref"] for p in population if not p["has_case"]}
    assert len(caseless_refs) == 115

    rows = {r["loan_id"]: r for r in generate(population, scenario)["rows"]}
    for ref in caseless_refs:
        row = rows[ref]
        for horizon in HORIZONS:
            assert row[f"recovered_amount_{horizon}"] is None
            assert rv.classify(SimpleNamespace(**row), horizon=horizon) == rv.UNOBSERVABLE


def test_the_low_band_caseless_skew_is_preserved(population):
    """26 HIGH / 39 MEDIUM / 50 LOW. The skew is the whole reason exclusion is a
    confound removal rather than tidiness, so it is pinned."""
    skew = Counter(p["recovery_potential"] for p in population if not p["has_case"])
    assert dict(skew) == {"HIGH": 26, "MEDIUM": 39, "LOW": 50}


# ── (11) Censoring, and the other exclusion branches ─────────────────────────
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_censored_rows_are_excluded_but_still_measured(population, scenario):
    """Mirrors the production labeller exactly: the amount that arrived is a fact
    and is recorded; `outcome` is what keeps a bank recall out of the analysis. A
    row must not become uncensored because money happened to arrive afterwards."""
    rows = generate(population, scenario)["rows"]
    censored = [r for r in rows if (r["outcome"] or "") in rv.CENSORING_OUTCOMES]
    assert censored, "no censored rows — the CENSORED branch is untested"
    for row in censored:
        assert row["recovered_amount_30"] is not None      # measured
        assert rv.classify(SimpleNamespace(**row), horizon=30) == rv.CENSORED


def test_the_labelling_backlog_reads_as_immature(baseline):
    """A row the manual checkpoint has not reached is IMMATURE at 90 and
    ADMISSIBLE at 30 — the real state of the production cohort between two
    checkpoints, and the only way this fixture exercises that branch."""
    rows = [r for r in baseline["rows"]
            if r["_simulation"].get("class") == "BACKLOG"]
    assert rows, "no backlog rows — the IMMATURE branch is untested"
    for row in rows:
        row_obj = SimpleNamespace(**row)
        assert rv.classify(row_obj, horizon=90) == rv.IMMATURE
        assert rv.classify(row_obj, horizon=30) == rv.ADMISSIBLE


def test_every_exclusion_branch_is_reachable_from_a_fixture_shaped_row():
    """The branches the fixtures report as zero. A count of 0 in the report must
    mean "did not happen", never "cannot happen" — so each is proved reachable
    here with a row shaped exactly like a fixture row."""
    base = {
        "recovery_model_version": rv.EXPECTED_VERSION, "is_backfill": False,
        "recovery_labelled_through_days": 90, "recovered_amount_30": 100.0,
        "outcome": None, "features": {"total_outstanding": 1000.0},
    }
    at30 = lambda **kw: rv.classify(SimpleNamespace(**{**base, **kw}), horizon=30)

    assert at30() == rv.ADMISSIBLE
    assert at30(recovery_model_version="recovery-scorecard-1.0.0") == rv.VERSION_MISMATCH
    assert at30(is_backfill=True) == rv.BACKFILL
    assert at30(features={"total_outstanding": 0.0}) == rv.NO_DENOMINATOR
    assert at30(outcome="WRITTEN_OFF") == rv.CENSORED
    assert at30(recovered_amount_30=None, recovery_labelled_through_days=0) == rv.IMMATURE
    assert at30(recovered_amount_30=None) == rv.UNOBSERVABLE


# ── (12) The prototype cannot write to production ────────────────────────────
def _source(name: str) -> str:
    return (_SCRIPTS / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", [
    "generate_synthetic_recovery_validation.py",
    "report_synthetic_validation.py",
    "synthetic_fixture.py",
])
def test_synthetic_scripts_contain_no_write_path(name):
    """Asserted on the SOURCE, not on behaviour. A behavioural test proves what
    the code does today; this one stops a commit() being added tomorrow."""
    src = _source(name)
    forbidden = (
        r"\.commit\s*\(", r"\.add\s*\(", r"\.add_all\s*\(", r"\.merge\s*\(",
        r"\bINSERT\s+INTO\b", r"\bUPDATE\s+\w+\s+SET\b", r"\bDELETE\s+FROM\b",
        r"\bTRUNCATE\b", r"\bDROP\b",
    )
    for pattern in forbidden:
        assert not re.search(pattern, src, re.IGNORECASE), f"{name} matches {pattern}"


def test_the_only_database_access_is_a_read_only_select():
    """One SELECT, inside a transaction Postgres itself will not let write."""
    src = _source("generate_synthetic_recovery_validation.py")
    assert "default_transaction_read_only=on" in src
    assert src.count("db.execute(") == 1
    assert "db.rollback()" in src


def test_synthetic_validation_opens_no_database_at_all():
    """--synthetic must not reach _session(). The fixture is the whole input."""
    src = _source("validate_recovery.py")
    body = src[src.index("def _main_synthetic"):src.index("\ndef main(")]
    assert "_session(" not in body
    assert "create_engine" not in body


def test_the_fixture_directory_is_outside_the_application():
    """docs/validation/synthetic/, not anywhere under backend/app — so a fixture
    can never be mistaken for application data or picked up by a loader."""
    assert sf.FIXTURE_DIR.parts[-3:] == ("docs", "validation", "synthetic")
    assert "app" not in sf.FIXTURE_DIR.parts


def test_export_population_refuses_to_run_without_a_database():
    """It cannot silently fall back to something writable."""
    with pytest.raises(Exception):
        export_population("postgresql+psycopg2://nobody@127.0.0.1:1/none",
                          "2026-08-24")


# ── (13) The production scorecard did not move ────────────────────────────────
def test_recovery_scorecard_constants_are_frozen():
    """Every value the prototype brief pinned. Not one of them may be touched by
    a simulation, and least of all recalibrated to fit synthetic data."""
    from app.ml import recovery_scorecard as rs

    assert rs.RECOVERY_SCORECARD_VERSION == "recovery-scorecard-1.1.0"
    assert rs.BASE_RATE == 0.35
    assert (rs.MIN_RATE, rs.MAX_RATE) == (0.02, 0.95)
    assert rs.RECOVERY_BAND_EDGES == ((0.50, "HIGH"), (0.25, "MEDIUM"))
    assert round(rs.TOTAL_WEIGHT, 10) == 0.90
    assert rs.HORIZONS == (30, 60, 90)
    assert rs.LABEL_HORIZON == 90
    assert (rs._SHARE_30_SLOW, rs._SHARE_30_FAST) == (0.10, 0.55)
    assert (rs._SHARE_60_SLOW, rs._SHARE_60_FAST) == (0.35, 0.80)


def test_repayment_scorecard_version_is_frozen():
    from app.ml.repayment_scorecard import SCORECARD_VERSION

    assert SCORECARD_VERSION == "scorecard-1.1.0"


def test_the_write_gate_is_shut():
    """RECOVERY_WRITE_LABEL is the only thing standing between the scorecard and
    Loan.recovery_potential, which another system may one day read."""
    from app.core.config import Settings

    assert Settings.model_fields["RECOVERY_WRITE_LABEL"].default is False


def test_the_validation_framework_thresholds_are_frozen():
    """The bars the PASS/FAIL table quotes. Moving one to make a synthetic dataset
    pass is the exact failure the brief forbade."""
    assert rv.EXPECTED_VERSION == "recovery-scorecard-1.1.0"
    assert rv.MIN_ADMISSIBLE_PER_BAND == 100
    assert rv.BANDS == ("HIGH", "MEDIUM", "LOW")


# ── (14) The framework accepts the fixture ───────────────────────────────────
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_the_committed_fixture_loads_and_validates(scenario):
    """End to end against the committed artefact, through the REAL framework
    functions — the same ones the 2026-11-22 production run will call."""
    if not sf.scenario_path(scenario).exists():
        pytest.skip(f"{scenario} not generated")
    meta, rows = sf.load_fixture(scenario)
    assert meta["banner"] == sf.BANNER
    assert len(rows) == 525

    admissible = [rv.observation_from(r, horizon=90) for r in rows
                  if rv.classify(r, horizon=90) == rv.ADMISSIBLE]
    assert admissible

    ranking = rv.ranking_metrics(admissible)
    calibration = rv.calibration_metrics(admissible)
    capture = rv.top_k_capture(admissible)
    security = rv.marginal_lift_by_security(admissible)
    factors = rv.factor_residual_correlation(admissible)
    verdict = rv.diagnose(ranking, calibration)

    assert ranking["spearman"] is not None
    assert calibration["bias"] is not None
    assert capture["captured"] is not None
    assert security["SECURED"]["n"] > 0 and security["UNSECURED"]["n"] > 0
    assert "SECURITY" in factors and "AGEING" in factors
    assert verdict["action"].startswith("REPORT ONLY")


def test_a_fixture_without_the_banner_is_refused(tmp_path, monkeypatch):
    """A file claiming to be a synthetic fixture without the disclaimer inside it
    is either hand-edited or from another tool. Either way its numbers must not
    be printed under a synthetic heading as though they carried the warning."""
    monkeypatch.setattr(sf, "FIXTURE_DIR", tmp_path)
    (tmp_path / "forged.json").write_text(
        json.dumps({"meta": {"scenario": "forged"}, "rows": []}), encoding="utf-8")
    with pytest.raises(SystemExit, match="synthetic banner"):
        sf.load_fixture("forged")


def test_an_unknown_scenario_names_the_real_ones():
    with pytest.raises(SystemExit, match="unknown scenario"):
        sf.describe("no_such_scenario")


# ── (15) The disclaimer is inseparable from the numbers ──────────────────────
@pytest.mark.parametrize("horizon", HORIZONS)
def test_the_rendered_report_carries_the_disclaimer(horizon):
    """Checked on the rendered text, because that is what a stakeholder reads. It
    must appear at the top AND beside the verdict — a reader who scrolls straight
    to Section 7 must not be able to miss it."""
    if not sf.scenario_path(BASELINE).exists():
        pytest.skip("baseline fixture not generated")
    from scripts.report_synthetic_validation import render

    text = render(BASELINE, horizon)
    assert text.startswith(f"# {sf.BANNER}")
    assert sf.DISCLAIMER in text
    assert text.count(sf.BANNER) >= 2
    assert "not evidence of production" in text
    assert "REPORT ONLY" in text
    # Exclusions before findings, always.
    assert text.index("Data quality") < text.index("Section 2 — Ranking")
    # And no claim the brief forbade.
    for forbidden in ("AUC", "Gini", "the model is validated"):
        assert forbidden not in text


def test_every_fixture_stores_the_disclaimer_as_data(baseline):
    """Not just in a report someone might regenerate without it — in the dataset,
    so the numbers cannot be separated from the warning by a copy-paste."""
    assert baseline["meta"]["banner"] == sf.BANNER
    assert baseline["meta"]["disclaimer"] == sf.DISCLAIMER
    assert baseline["meta"]["predictions"].startswith("REAL")
    assert baseline["meta"]["outcomes"].startswith("SYNTHETIC")


# ── The simulation's own arithmetic ──────────────────────────────────────────
def test_security_sign_matches_the_validators_rule():
    """Both read the sign of the SECURITY points. If they disagree, the planted
    security effect lands on the wrong loans and the diagnostic is meaningless."""
    assert _security_sign([{"code": "SECURITY", "points": 0.16}]) == 1
    assert _security_sign([{"code": "SECURITY", "points": -0.16}]) == -1
    assert _security_sign([{"code": "SECURITY", "abstained": True}]) == 0
    assert _security_sign([{"code": "AGEING", "points": -0.18}]) == 0


def test_a_caseless_row_consumes_no_randomness():
    """The seeded stream must not shift depending on how many caseless loans
    precede a row, or 'same seed, same dataset' would be false the moment
    allocation changed."""
    params = sf.describe(BASELINE)
    row = {
        "loan_ref": "deadbeefdeadbeef", "recovery_potential": "LOW",
        "recovery_rate_30": 0.05, "recovery_rate_60": 0.10, "recovery_rate_90": 0.20,
        "recovery_speed_index": 0.5, "recovery_evidence_coverage": 0.8,
        "recovery_model_version": rv.EXPECTED_VERSION, "is_backfill": False,
        "total_outstanding": 100_000.0, "has_case": False, "factors": [],
    }
    rng = np.random.default_rng(1)
    simulate_row(row, params, rng)
    assert rng.random() == np.random.default_rng(1).random()
