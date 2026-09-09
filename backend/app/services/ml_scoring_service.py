# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-08 — NEW. The adapter between the live schema and the trained models:
#   turns a Loan (plus its Customer and history) into the feature dict
#   ml/pipeline/engine.py expects.
#
#   THE FEATURE NAMES HERE MUST MATCH ml/pipeline/config.py EXACTLY, and the
#   arithmetic must match app/ml/simulation/book_simulator.py exactly. That is
#   the training/serving contract, and it is the one place this whole exercise
#   can go silently wrong: a model trained on `arrears_ratio = overdue / emi`
#   and served `overdue / total_outstanding` will still return a plausible
#   probability for every borrower and be wrong about all of them. There is no
#   error, no exception, no log line. tests/test_ml_pipeline.py asserts the two
#   definitions agree rather than trusting this paragraph.
#
#   ON. `ML_SCORING_ENABLED` is True — the nightly allocator uses these scores
#   for real decisions, and `ML_LOG_PREDICTIONS` is True, so every score is
#   written to `model_predictions`.
#
#   *(This paragraph used to read "OFF BY DEFAULT. ML_SCORING_ENABLED is False,
#   matching the discipline already used for REPAYMENT_WRITE_RISK_SCORE and
#   RECOVERY_WRITE_LABEL". That was true when this file was written and stopped
#   being true when the model was promoted on 2026-09-08 — verified 2026-09-09
#   against core/config.py:335. Corrected rather than deleted, because a header
#   that says a gate is closed when it is open is the most misleading thing a
#   reader of this file could be handed. The two flags it names ARE still False;
#   this one is not.)*
#
#   The gate still exists and still works: setting `ML_SCORING_ENABLED=False`
#   returns the allocator to its pre-promotion behaviour in one act.
# ───────────────────────────────────────────────────────────────────────────
"""
Live model scoring.

    from app.services.ml_scoring_service import MLScoringService

    svc = MLScoringService(db)
    result = svc.score_loan(loan_id)      # ScoreResult, or a declined one

Nothing here raises on a missing model. A DecisionEngine that cannot load
returns None, and this service returns a ScoreResult with is_modelled=False and
a fallback_reason — so a caller that renders the result honestly shows the
existing hand-weighted scorecard instead, and a caller that forgets cannot
accidentally present a scorecard as a model.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import settings

from app.ml.eligibility import _SECURED as SECURED_LOAN_TYPES  # noqa: F401
from app.ml.pipeline.engine import DecisionEngine, ScoreResult
from app.ml.pipeline.outcomes import MATERIAL_PAYMENT_RATIO
from app.models.case import Case
from app.models.customer import Customer
from app.models.loan import Loan
from app.models.payment import Payment, PaymentStatus
from app.models.ptp import PTP, PTPStatus
from app.models.model_prediction import ModelPrediction
from app.models.visit import Visit

logger = logging.getLogger(__name__)

# IMPORTED, NOT RESTATED. ml/eligibility._SECURED is the existing definition and
# both scorecards already read it; writing {HOME, AUTO, GOLD} out again here
# would have been a third copy of a rule this repo has been bitten by twice
# (two risk_score formulas, two recovery_potential writers, seven DPD-bucket
# spellings). The simulator keeps its own copy because it must import nothing
# from `app` to run without a database — that one exception is covered by a
# test asserting the two sets are equal.

# PTP outcomes that count as kept. PARTIALLY_HONORED counts: the borrower paid
# something against the promise, which is materially different from BROKEN, and
# the simulator's ptp_kept flag is likewise "a payment happened".
PTP_KEPT = {PTPStatus.HONORED, PTPStatus.PARTIALLY_HONORED}



def _as_date(value) -> date | None:
    """Coerce this schema's date-ish columns to a real `date`.

    THIS IS NOT DEFENSIVE PADDING, IT IS THE SCHEMA. `Loan.disbursement_date`,
    `Loan.maturity_date`, `Loan.last_payment_date` and `Customer.date_of_birth`
    are all declared `String(10)` — ISO text, not Date columns. Assuming they
    were dates is exactly the bug that kept the trained model out of production:
    `build_features` raised

        TypeError: unsupported operand type(s) for -: 'datetime.date' and 'str'

    on the first loan it touched, `_ml_recovery_probabilities` caught it, logged
    a warning and returned {} — so the allocator ran with no model
    probabilities at all and fell back to `log_current` every night, silently
    and exactly as designed. The graceful degradation worked; it just hid a
    one-line type error for the whole time the model was "promoted".

    Accepts date, datetime or ISO string; returns None for anything unparseable
    rather than raising, because one malformed row must not cost the whole book
    its scores.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    """Postgres columns are timezone-aware; SQLite in tests gives naive ones."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class MLScoringService:
    def __init__(self, db: Session):
        self.db = db

    # ── feature construction ────────────────────────────────────────────────
    def build_features(self, loan: Loan, *, as_of: date | None = None) -> dict:
        """The feature dict for one loan, as of `as_of` (default: now).

        POINT-IN-TIME, WITH ONE HONEST CAVEAT. Every history aggregate below is
        filtered to strictly before `as_of`, so a past date can be scored
        correctly. `Loan.dpd`, `Loan.overdue_amount` and the rest of the current
        state cannot be — they are overwritten in place with no history, which
        models/repayment_snapshot.py documents at length. So scoring a PAST date
        with this method reads today's balances against yesterday's history and
        is NOT honest. It is fine for `as_of = today`, which is the only way the
        product calls it. Backfilling requires RepaymentSnapshot, not this.
        """
        as_of_dt = (_utcnow() if as_of is None else
                    datetime.combine(as_of, datetime.min.time(),
                                     tzinfo=timezone.utc))
        cust: Customer | None = loan.customer

        f: dict = {}

        # ── direct columns ──────────────────────────────────────────────────
        f["dpd"] = float(loan.dpd or 0)
        f["dpd_bucket"] = loan.dpd_bucket.value if loan.dpd_bucket else None
        f["loan_type"] = loan.loan_type.value if loan.loan_type else None
        f["branch_code"] = loan.branch_code
        f["emi_amount"] = float(loan.emi_amount or 0)
        f["sanction_amount"] = float(loan.sanctioned_amount or 0)
        f["tenure_months"] = float(loan.tenure_months or 0)
        f["interest_rate"] = float(loan.interest_rate or 0)
        f["outstanding_principal"] = float(loan.outstanding_principal or 0)
        f["total_outstanding"] = float(loan.total_outstanding or 0)
        f["overdue_amount"] = float(loan.overdue_amount or 0)
        f["penal_charges"] = float(loan.penal_charges or 0)
        f["is_secured"] = 1 if loan.loan_type in SECURED_LOAN_TYPES else 0

        # ── derived ratios — SAME ARITHMETIC AS THE SIMULATOR ───────────────
        emi = max(f["emi_amount"], 1.0)
        f["arrears_ratio"] = round(f["overdue_amount"] / emi, 3)
        f["penal_ratio"] = round(f["penal_charges"] / max(f["total_outstanding"], 1.0), 4)
        f["outstanding_to_sanction"] = round(
            f["outstanding_principal"] / max(f["sanction_amount"], 1.0), 3)

        disbursed = _as_date(loan.disbursement_date)
        if disbursed:
            f["months_on_book"] = float(max(0, (as_of_dt.date() - disbursed).days // 30))
        last_paid = _as_date(loan.last_payment_date)
        if last_paid:
            f["days_since_last_payment"] = float(
                max(0, (as_of_dt.date() - last_paid).days))

        # ── customer ────────────────────────────────────────────────────────
        if cust is not None:
            f["cibil_score"] = float(cust.cibil_score) if cust.cibil_score else None
            f["city"] = cust.city
            # The panel calls this employment_type; the schema calls it
            # customer_segment and carries the same SALARIED / SELF_EMPLOYED /
            # BUSINESS_OWNER vocabulary.
            f["employment_type"] = cust.customer_segment
            dob = _as_date(cust.date_of_birth)
            if dob:
                f["age"] = float((as_of_dt.date() - dob).days // 365)

        f.update(self._history_features(loan, as_of_dt))
        return f

    def _history_features(self, loan: Loan, as_of_dt: datetime) -> dict:
        """Visit, PTP and payment aggregates over the windows the models use."""
        # QUERIED, NOT READ OFF THE RELATIONSHIP. `Loan.cases` is declared
        # lazy="noload" (models/loan.py:154) — a deliberate performance choice
        # that makes it ALWAYS an empty list unless a caller eager-loads it.
        # Reading it here returned {} for every loan, so every history feature
        # was silently absent and `ptp_kept_ratio` — the model's third most
        # important input (IV 0.2798) — was never supplied in production. The
        # engine still scored, at 0.75 coverage, which is above its 0.60 floor:
        # a wrong answer that looked like a working one.
        case_ids = [cid for (cid,) in
                    self.db.query(Case.id).filter(Case.loan_id == loan.id).all()]
        if not case_ids:
            return {}

        w3 = as_of_dt - timedelta(days=90)
        w6 = as_of_dt - timedelta(days=180)
        w12 = as_of_dt - timedelta(days=365)

        visits = (self.db.query(Visit)
                  .filter(Visit.case_id.in_(case_ids), Visit.check_in_time < as_of_dt)
                  .all())
        v3 = [v for v in visits if _aware(v.check_in_time) >= w3]
        v6 = [v for v in visits if _aware(v.check_in_time) >= w6]
        met6 = [v for v in v6 if v.customer_met]

        out: dict = {
            "visits_3m": float(len(v3)),
            "visits_6m": float(len(v6)),
            "distinct_agents_6m": float(len({v.agent_id for v in v6})) or 1.0,
        }
        # Prior 0.45 where there is no evidence — the same abstain-don't-guess
        # rule the scorecards follow, and the same prior the simulator uses.
        out["contact_rate_6m"] = round(len(met6) / len(v6), 3) if v6 else 0.45
        last_contact = max((_aware(v.check_in_time) for v in visits if v.customer_met),
                           default=None)
        if last_contact:
            out["days_since_last_contact"] = float((as_of_dt - last_contact).days)

        ptps = (self.db.query(PTP)
                .filter(PTP.case_id.in_(case_ids), PTP.created_at < as_of_dt)
                .all())
        p6 = [p for p in ptps if _aware(p.created_at) >= w6]
        kept6 = [p for p in p6 if p.status in PTP_KEPT]
        out["ptp_set_6m"] = float(len(p6))
        out["ptp_kept_6m"] = float(len(kept6))
        out["ptp_kept_ratio"] = round(len(kept6) / len(p6), 3) if p6 else 0.5

        pays = (self.db.query(Payment)
                .filter(Payment.case_id.in_(case_ids),
                        Payment.status == PaymentStatus.VERIFIED,
                        Payment.payment_date < as_of_dt)
                .all())
        # THE DENOMINATOR IS WHAT WAS DUE OVER THE WINDOW, one instalment per
        # month. 2026-09-09.
        #
        # This used to be `sum(Case.target_amount)` across every case of the
        # loan — a FIXED figure that does not scale with the window, so all
        # three ratios shared one denominator and differed only by numerator.
        # The model was trained on a denominator summed over the window's months
        # (`book_simulator`: `hist_due[:, lo3:m].sum(1)`), so the served values
        # were 3x, 6x and 12x too large and `paid_ratio_6m`/`_12m` sat pinned at
        # the 1.5 clip. A textbook training/serving skew, and invisible until
        # the ledger simulator made it possible to run the panel and this
        # adapter side by side: measured on 202 matched loans at one as_of,
        # 119/202 disagreed on `paid_ratio_3m` and 138/202 on `_6m` and `_12m`,
        # by exactly the window factor.
        #
        # Safe to correct now: none of `paid_ratio_*` is in `recovery_risk`
        # 1.1.0's four selected features, so no served score changes today.
        # Leaving it would have corrupted the first model that did select one.
        #
        # (The removed line also carried a note about `Loan.cases` being
        # lazy="noload" — that defect was fixed separately on 2026-09-08 and the
        # query above is the fix. It is unrelated to this denominator.)
        emi = float(loan.emi_amount or 0.0)
        for label, since, months in (("3m", w3, 3), ("6m", w6, 6), ("12m", w12, 12)):
            paid = sum(float(p.amount or 0) for p in pays
                       if _aware(p.payment_date) >= since)
            due = emi * months
            out[f"paid_ratio_{label}"] = round(min(paid / due, 1.5), 3) if due else 0.5
        return out

    # ── scoring ─────────────────────────────────────────────────────────────
    def score_loan(self, loan_id: str, *, model: str = "recovery_risk") -> ScoreResult:
        loan = self.db.query(Loan).filter(Loan.id == loan_id).first()
        if loan is None:
            return ScoreResult(probability=None, points=None, band=None,
                               model=model, version="unknown", is_modelled=False,
                               fallback_reason=f"no loan {loan_id}")
        return self.score(loan, model=model)

    def score(self, loan: Loan, *, model: str = "recovery_risk") -> ScoreResult:
        engine = DecisionEngine.get(model)
        if engine is None:
            return ScoreResult(
                probability=None, points=None, band=None, model=model,
                version="unavailable", is_modelled=False,
                fallback_reason=(
                    f"no champion artifact for '{model}'. The product falls back "
                    f"to its hand-weighted scorecard, which is not a model."),
            )
        return engine.score(self.build_features(loan))

    def score_many(self, loans: list[Loan], *,
                   model: str = "recovery_risk") -> dict[str, float | None]:
        """Probabilities keyed by loan id, in one batched call.

        The nightly path scores the whole book inside the fifteen minutes
        between ingest and allocation, which is why RepaymentService._load bulk
        loads rather than querying per loan. Same reasoning here.
        """
        engine = DecisionEngine.get(model)
        if engine is None or not loans:
            return {l.id: None for l in loans}
        feats = [self.build_features(l) for l in loans]
        return dict(zip((l.id for l in loans), engine.score_batch(feats)))

    # ── Scoring a case list, with the prediction recorded ────────────────────
    def score_cases_and_log(self, cases, *, model: str = "recovery_risk",
                            as_of: date | None = None
                            ) -> tuple[dict[str, float], list[ModelPrediction]]:
        """Score every case's loan and write one ModelPrediction row per case.

        WHY THIS EXISTS RATHER THAN score_many(). The planner used score_many,
        which returns probabilities and records NOTHING — so `model_predictions`
        sat at 0 rows while the model drove allocation, and the feedback loop
        that monitor.py depends on had no input at all. A model that scores and
        is never compared against what happened cannot be monitored, cannot be
        retrained on its own errors and cannot be shown to have decayed.

        Returns (case_id -> P(material payment), the prediction rows) so the
        caller can attach the allocated agent afterwards. Does NOT commit — the
        caller owns the transaction, because a scoring call that commits on its
        own behalf would split a planning run into two.

        LOGGING FAILURE MUST NOT COST THE ALLOCATION. It is caught and reported
        at error level, never raised. But it is reported: the whole reason this
        function exists is that a silent gap went unnoticed for a full cycle.
        """
        engine = DecisionEngine.get(model)
        if engine is None:
            # LOUD. This is the one degradation on this path that said nothing
            # at all: no artifact meant an empty dict, the planner fell back to
            # log_current exactly as designed, and the only trace was
            # `scored=0` in a line that also prints on an empty pool. A
            # swallowed failure that degrades correctly still has to be loud —
            # the same fix `_ml_recovery_probabilities` got on 2026-09-08.
            logger.error("ml.no_champion_artifact model=%s cases=%d — the "
                         "allocator will run with no model at all", model,
                         len(cases))
            return {}, []
        if not cases:
            return {}, []

        # ONE SCORE PER LOAN, APPLIED TO EVERY CASE ON IT. The first version
        # kept only the first case per loan, so a loan carrying two cases left
        # the second one unscored: measured on a live pool of 933 cases over 814
        # distinct loans, 119 cases — 57 of the 214 actually allocated, 27% —
        # were silently ML-less while the run reported itself ML-driven. The
        # model is a LOAN-level estimate, so every case on that loan is entitled
        # to the same probability.
        loans, cases_by_loan = [], {}
        for case in cases:
            loan = getattr(case, "loan", None)
            if loan is None:
                continue
            if loan.id not in cases_by_loan:
                loans.append(loan)
                cases_by_loan[loan.id] = []
            cases_by_loan[loan.id].append(case)

        feats = {l.id: self.build_features(l, as_of=as_of) for l in loans}
        # 2026-09-09 — `score_batch_detailed`, not `score_batch`. The bare-
        # probability call skipped the engine's coverage floor and computed no
        # points, band or reason codes, so 11,817 served rows carried NULL in
        # three columns this function was already assigning. See the note on
        # DecisionEngine.score_batch_detailed; the probability is byte-for-byte
        # the one the previous call produced.
        results = engine.score_batch_detailed([feats[l.id] for l in loans])

        out: dict[str, float] = {}
        rows: list[ModelPrediction] = []
        selected = set(engine.selected)
        stamp = as_of or _utcnow().date()

        for loan, res in zip(loans, results):
            f = feats[loan.id]
            coverage = res.feature_coverage
            if res.probability is None:
                # DECLINED, and recorded as such. A row with is_modelled False
                # and a fallback_reason is the evidence that the model was asked
                # and could not answer — the state that is otherwise invisible,
                # and the one the allocator must not be handed a number for.
                logger.warning("ml.declined loan=%s coverage=%s reason=%s",
                               loan.id, coverage, res.fallback_reason)
                if settings.ML_LOG_PREDICTIONS:
                    for case in cases_by_loan[loan.id]:
                        rows.append(ModelPrediction(
                            model_name=engine.model, model_version=engine.version,
                            artifact_sha256=engine.metadata.get("artifact_sha256"),
                            entity_type="case", entity_id=case.id, loan_id=loan.id,
                            case_id=case.id, as_of_date=stamp,
                            probability=None, is_modelled=False,
                            fallback_reason=res.fallback_reason,
                            features={k: v for k, v in f.items() if k in selected},
                            feature_coverage=coverage,
                        ))
                continue
            p_bad = res.probability
            for case in cases_by_loan[loan.id]:
                out[case.id] = 1.0 - float(p_bad)
                if not settings.ML_LOG_PREDICTIONS:
                    continue
                rows.append(ModelPrediction(
                    model_name=engine.model,
                    model_version=engine.version,
                    artifact_sha256=engine.metadata.get("artifact_sha256"),
                    entity_type="case",
                    entity_id=case.id,
                    loan_id=loan.id,
                    case_id=case.id,
                    # agent_id is filled in AFTER the solve — see
                    # PlannerService.plan_next_day. A prediction without the
                    # decision it informed cannot be evaluated per agent later.
                    agent_id=None,
                    as_of_date=stamp,
                    probability=round(float(p_bad), 6),
                    points=res.points,
                    band=res.band,
                    reason_codes=res.reason_codes or [],
                    is_modelled=True,
                    # Only the features the model reads. Storing all 33
                    # candidates would triple the row for no monitoring value;
                    # PSI is computed on the model's own inputs.
                    features={k: v for k, v in f.items() if k in selected},
                    feature_coverage=coverage,
                    # FROZEN FOR THE LABEL, not for the score. Both columns are
                    # overwritten in place on Loan, so reading them when the
                    # outcome is computed would measure a payment window against
                    # a balance those payments already reduced. `emi_amount` is
                    # not a model feature, so this is its only home.
                    outcome_baseline={
                        "overdue_amount": float(loan.overdue_amount or 0.0),
                        "emi_amount": float(loan.emi_amount or 0.0),
                        "threshold_ratio": MATERIAL_PAYMENT_RATIO,
                    },
                ))

        if rows:
            try:
                self.db.add_all(rows)
            except Exception as exc:      # pragma: no cover
                logger.error("ml.prediction_log_failed model=%s n=%d error=%s",
                             model, len(rows), exc)
                rows = []
        logger.info("ml.scored_and_logged model=%s cases=%d scored=%d logged=%d",
                    model, len(cases), len(out), len(rows))
        return out, rows

    # ── feedback loop ───────────────────────────────────────────────────────
    def log_prediction(self, loan: Loan, result: ScoreResult, features: dict,
                       *, as_of: date | None = None,
                       case_id: str | None = None,
                       agent_id: str | None = None) -> ModelPrediction | None:
        """Record a served score so it can later be judged against the outcome.

        DECLINED SCORES ARE LOGGED TOO. A row with is_modelled False and a
        fallback_reason is the evidence that the model was asked and could not
        answer — exactly the state that is invisible otherwise, and the reason
        core/llm.py counts its own fallbacks per purpose rather than only its
        successes.

        Does not commit. The caller owns the transaction; a scoring call that
        commits on its own behalf would break any batch that scores many loans
        inside one unit of work.
        """
        if not settings.ML_LOG_PREDICTIONS:
            return None
        row = ModelPrediction(
            model_name=result.model,
            model_version=result.version,
            entity_type="loan",
            entity_id=loan.id,
            loan_id=loan.id,
            case_id=case_id,
            agent_id=agent_id,
            as_of_date=as_of or _utcnow().date(),
            probability=result.probability,
            points=result.points,
            band=result.band,
            is_modelled=result.is_modelled,
            fallback_reason=result.fallback_reason,
            # Only the features the model actually reads. Storing all 33
            # candidates would triple the row for no monitoring value — PSI is
            # computed on the model's own inputs (see train.py, where gating PSI
            # on unused candidates produced a spurious FAIL).
            features={k: v for k, v in features.items()
                      if k in (result.feature_points or {})
                      or k in _selected_for(result.model)},
            feature_coverage=result.feature_coverage,
            reason_codes=result.reason_codes or [],
        )
        self.db.add(row)
        return row

    def score_and_log(self, loan: Loan, *, model: str = "recovery_risk",
                      case_id: str | None = None) -> ScoreResult:
        engine = DecisionEngine.get(model)
        features = self.build_features(loan)
        result = (engine.score(features) if engine else
                  ScoreResult(probability=None, points=None, band=None,
                              model=model, version="unavailable",
                              is_modelled=False,
                              fallback_reason=f"no champion artifact for '{model}'"))
        self.log_prediction(loan, result, features, case_id=case_id)
        return result


def _selected_for(model: str) -> list[str]:
    engine = DecisionEngine.get(model)
    return engine.selected if engine else []
