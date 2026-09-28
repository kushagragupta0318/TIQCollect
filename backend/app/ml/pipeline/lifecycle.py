# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-09 — NEW. The chain the 2026-09-09 audit found broken:
#
#     monitoring -> retrain_recommended -> [NOTHING]
#
#   `retrain_recommended` was a boolean returned into a log line. This module is
#   what reads it, and it is the only thing that does.
#
#   WHAT IT DELIBERATELY DOES NOT DO. It never promotes. `PENDING_APPROVAL` is
#   the furthest an automated path can move a candidate; `approve` and `promote`
#   take a user id and are reachable only from an authenticated manager route.
#   That mirrors every other rollout gate in this repo, and it is a larger
#   commitment than any of them: a system that can replace its own decision
#   model without a person is not what this change is making.
# ───────────────────────────────────────────────────────────────────────────
"""Trigger, train, validate, compare, and wait for a human.

    candidate = start_retraining(db, "recovery_risk", report)   # from monitoring
    run_candidate(db, candidate.id)                             # the worker
    approve(db, candidate.id, user_id=...)                      # a person
    promote(db, candidate.id, user_id=...)                      # a person

Every step writes its result to `model_candidates` before moving on, so a worker
that dies mid-run leaves a row saying where it got to rather than nothing.
"""
from __future__ import annotations

import dataclasses
import hashlib
import logging
import platform
import sys
import uuid
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.model_candidate import (
    ACTIVE_STATES, CandidateState, ModelCandidate,
)

logger = logging.getLogger(__name__)

#: Verdicts that may start a retrain. Everything else — `not_ready`,
#: `insufficient_data`, `insufficient_outcome_variation`, `healthy` — must not,
#: and each for its own reason: three of them mean the monitor could not judge,
#: and judging nothing is not evidence of decay.
TRIGGERING_VERDICTS = {"retrain_recommended"}


def monitoring_event_id(report: dict) -> str:
    """A deterministic id for the MONITORING EVENT, not for the run that saw it.

    Two nightly runs over the same matured cohort describe the same event and
    must produce the same id, or the unique constraint on `model_candidates`
    cannot stop the second one starting a duplicate training job. So the id is a
    digest of what defines the observation — model, serving version, outcome
    definition, how many rows matured and what was concluded — and NOT of the
    wall clock.
    """
    parts = [
        str(report.get("model")), str(report.get("version")),
        str(report.get("outcome_definition_version")),
        str(report.get("n_matured")), str(report.get("verdict")),
        "|".join(sorted(report.get("reasons") or [])),
    ]
    return "mon-" + hashlib.sha256("::".join(parts).encode()).hexdigest()[:20]


def _code_versions() -> dict:
    import sklearn

    return {
        "python": sys.version.split()[0],
        "sklearn": sklearn.__version__,
        "pandas": pd.__version__,
        "numpy": np.__version__,
        "platform": platform.platform(),
    }


# ---------------------------------------------------------------------------
# 1. Trigger
# ---------------------------------------------------------------------------

def start_retraining(db: Session, model_name: str, report: dict,
                     *, commit: bool = True) -> ModelCandidate | None:
    """Create a candidate for this monitoring event, or return None.

    Returns None — never raises — for every legitimate reason not to retrain, so
    the nightly task treats "no retrain today" as the ordinary case it is.

    IDEMPOTENT TWICE OVER. In code, because an active candidate for the same
    model blocks a new one; and in the schema, because
    `uq_candidate_monitoring_event` makes a race between two workers end in an
    IntegrityError that is caught here and resolved by returning the row the
    other worker created.
    """
    from app.core.config import settings

    verdict = report.get("verdict")
    if not report.get("retrain_recommended") or verdict not in TRIGGERING_VERDICTS:
        return None
    if not getattr(settings, "ML_AUTO_RETRAIN_ENABLED", True):
        logger.info("ml.lifecycle.trigger_disabled model=%s verdict=%s",
                    model_name, verdict)
        return None

    event = monitoring_event_id(report)

    existing = (db.query(ModelCandidate)
                .filter(ModelCandidate.model_name == model_name,
                        ModelCandidate.monitoring_run_id == event)
                .first())
    if existing is not None:
        logger.info("ml.lifecycle.duplicate_trigger model=%s event=%s state=%s",
                    model_name, event, existing.state)
        return None

    active = (db.query(ModelCandidate)
              .filter(ModelCandidate.model_name == model_name,
                      ModelCandidate.state.in_(list(ACTIVE_STATES)))
              .first())
    if active is not None:
        # A candidate awaiting a person is not a reason to train another one.
        # Without this a nightly monitor on a decayed model would open a new
        # candidate every night until somebody looked.
        logger.warning(
            "ml.lifecycle.trigger_suppressed model=%s event=%s reason=active "
            "candidate %s in %s", model_name, event, active.id, active.state)
        return None

    cand = ModelCandidate(
        id=str(uuid.uuid4()),
        model_name=model_name,
        incumbent_version=report.get("version"),
        state=CandidateState.TRAINING,
        monitoring_run_id=event,
        trigger_reasons=list(report.get("reasons") or []),
        monitoring_report={k: v for k, v in report.items()
                           if k not in ("decile_table", "calibration_table")},
        outcome_definition_version=report.get("outcome_definition_version"),
        code_versions=_code_versions(),
        state_history=[{"at": datetime.now(timezone.utc).isoformat(),
                        "from": None, "to": CandidateState.TRAINING.value,
                        "reason": f"monitoring verdict {verdict}"}],
    )
    db.add(cand)
    try:
        if commit:
            db.commit()
        else:
            db.flush()
    except IntegrityError:
        db.rollback()
        logger.info("ml.lifecycle.trigger_raced model=%s event=%s", model_name, event)
        return None
    logger.warning("ml.lifecycle.retrain_triggered model=%s candidate=%s event=%s "
                   "reasons=%s", model_name, cand.id, event, cand.trigger_reasons)
    return cand


# ---------------------------------------------------------------------------
# 2. Train + validate + compare
# ---------------------------------------------------------------------------

def _mint_version(base: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{base}-candidate-{stamp}"


def _derive_spec(base_spec, *, version: str, frame: pd.DataFrame):
    """The champion's spec, restricted to what the prediction log actually holds.

    NOT a re-specification. Target, expected signs, gates, split fractions, PDO
    scaling and the forbidden list are all carried across untouched; only the
    feature list narrows to the columns that exist, and the split column moves
    from month to day.

    THE SPLIT COLUMN HAS TO MOVE. The synthetic panel spans 24 months, so a
    60/15/25 split by `month_index` gives 14/4/6 months. A production cohort
    spans weeks: by month it is one period and the split degenerates to a single
    empty slice. `as_of_period` is the day, which keeps the split strictly
    chronological and gives it something to cut.
    """
    have = set(frame.columns)
    numeric = tuple(f for f in base_spec.numeric_features if f in have)
    categorical = tuple(f for f in base_spec.categorical_features if f in have)
    return dataclasses.replace(
        base_spec, version=version,
        numeric_features=numeric, categorical_features=categorical,
        split_col="as_of_period", time_col="as_of_date",
        id_cols=tuple(c for c in ("loan_id", "borrower_id") if c in have),
    )


def _engine_scores(model: str, version: str, frame: pd.DataFrame,
                   features: list[str]) -> np.ndarray:
    """Score a frame THROUGH THE PRODUCTION SERVING PATH.

    Deliberately `DecisionEngine.score_batch_detailed` rather than calling the
    pickle directly: it applies the calibrator, the coverage floor and the same
    frame construction the allocator gets, so the comparison measures what would
    actually be served. A candidate that scores well only when bypassing the
    engine is not a candidate.
    """
    from app.ml.pipeline.engine import DecisionEngine

    eng = DecisionEngine.get(model, version)
    if eng is None:
        raise RuntimeError(f"could not load {model} {version} for comparison")
    rows = frame[features].to_dict(orient="records")
    out = eng.score_batch_detailed(rows)
    return np.array([r.probability if r.probability is not None else np.nan
                     for r in out], dtype=float)


def run_candidate(db: Session, candidate_id: str) -> ModelCandidate:
    """Train, validate and compare. Ends at PENDING_APPROVAL or a rejection.

    Never raises past its own bookkeeping: every failure lands in a persisted
    terminal state with the error recorded, because a candidate row that stops
    updating is indistinguishable from a worker that is still working.

    THE CHAMPION IS NOT TOUCHED ON ANY PATH THROUGH THIS FUNCTION. The trainer
    is called with `make_champion=False`, and `champion.txt` is written by
    `registry.promote` alone.
    """
    from app.ml.pipeline import comparison as cmp_mod
    from app.ml.pipeline import config as cfg
    from app.ml.pipeline import registry
    from app.ml.pipeline.production_dataset import (
        InsufficientProductionData, build_training_frame,
    )
    from app.ml.pipeline.train import ModelTrainer

    cand = db.query(ModelCandidate).filter(ModelCandidate.id == candidate_id).one()
    if cand.state != CandidateState.TRAINING:
        # Re-delivery of the same Celery message, or a manual re-run. The work
        # is already done or already failed; doing it again would mint a second
        # artifact for one candidate.
        logger.info("ml.lifecycle.run_skipped candidate=%s state=%s",
                    candidate_id, cand.state)
        return cand

    def _terminal(state: CandidateState, reason: str, **fields) -> ModelCandidate:
        for k, v in fields.items():
            setattr(cand, k, v)
        cand.transition(state, reason)
        db.commit()
        logger.warning("ml.lifecycle.candidate_%s candidate=%s reason=%s",
                       state.value.lower(), candidate_id, reason)
        return cand

    # ── data ────────────────────────────────────────────────────────────────
    try:
        frame, cohort = build_training_frame(
            db, cand.model_name,
            outcome_definition_version=cand.outcome_definition_version)
    except InsufficientProductionData as exc:
        # THE ONE THING THAT MUST NOT HAPPEN HERE IS A FALLBACK. The synthetic
        # panel is two seconds away and would produce a plausible model with a
        # plausible Gini that describes simulated borrowers.
        return _terminal(CandidateState.INSUFFICIENT_DATA, str(exc),
                         error=str(exc), training_cohort=exc.detail,
                         cohort_rows=exc.detail.get("n_rows", 0))
    except Exception as exc:                                   # pragma: no cover
        return _terminal(CandidateState.FAILED,
                         f"dataset build raised {type(exc).__name__}",
                         error=f"{type(exc).__name__}: {exc}")

    incumbent = registry.pointer_version(cand.model_name)
    if not incumbent:
        return _terminal(CandidateState.FAILED, "no incumbent champion to compare against",
                         error="champion.txt absent")
    cand.incumbent_version = incumbent
    cand.training_cohort = cohort.to_dict()
    cand.cohort_rows = cohort.n_rows
    cand.candidate_version = _mint_version(incumbent)
    cand.transition(CandidateState.VALIDATING,
                    f"training on {cohort.n_rows} production rows")
    db.commit()

    # ── train ───────────────────────────────────────────────────────────────
    # The WIDEST spec for the model, not the incumbent's. 2026-09-15: since the
    # prediction log now carries every candidate (`LOGGED_FEATURES`), a
    # challenger may select a feature the incumbent never used; `_derive_spec`
    # still narrows to what the frame actually holds, so rows logged before
    # the widening simply put the new columns in the Missing bin.
    base = {"recovery_risk": cfg.RECOVERY_RISK_V2,
            "contact_risk": cfg.CONTACT_RISK}.get(cand.model_name)
    if base is None:
        return _terminal(CandidateState.FAILED,
                         f"no ModelSpec registered for {cand.model_name}",
                         error="unknown model")
    spec = _derive_spec(base, version=cand.candidate_version, frame=frame)
    cand.feature_set = list(spec.all_features)

    try:
        trainer = ModelTrainer(spec, frame, dataset_meta=cohort.to_dict())
        # The same deterministic split the run will use, taken first so the
        # comparison below scores BOTH models on the identical out-of-time rows.
        _, _, oot = trainer._split()
        result = trainer.run(make_champion=False)
    except Exception as exc:
        logger.exception("ml.lifecycle.training_failed candidate=%s", candidate_id)
        return _terminal(CandidateState.FAILED,
                         f"training raised {type(exc).__name__}",
                         error=f"{type(exc).__name__}: {exc}")

    gates = (result.gates.to_dict(orient="records")
             if result.gates is not None else [])
    cand.gate_results = gates
    cand.gates_passed = bool(result.passed)
    cand.validation_run_id = f"val-{uuid.uuid4().hex[:12]}"
    cand.training_metrics = result.metrics
    db.commit()

    if not result.passed:
        failed = [g["gate"] for g in gates if g.get("result") == "FAIL"]
        return _terminal(
            CandidateState.REJECTED_VALIDATION,
            f"failed validation gates: {', '.join(failed) or 'unknown'}",
            rejection_reason=f"failed gates: {', '.join(failed)}")

    # ── compare against the model that is actually deployed ─────────────────
    cand.transition(CandidateState.COMPARING,
                    f"gates passed; comparing against incumbent {incumbent}")
    db.commit()
    try:
        if len(oot) < 1 or oot[spec.target].nunique() < 2:
            return _terminal(
                CandidateState.REJECTED_COMPARISON,
                "out-of-time slice has fewer than two outcome classes; no "
                "comparison is possible",
                rejection_reason="degenerate out-of-time slice")
        feats = list(spec.all_features)
        y = oot[spec.target].astype(int).to_numpy()
        ch_scores = _engine_scores(cand.model_name, cand.candidate_version, oot, feats)
        in_scores = _engine_scores(cand.model_name, incumbent, oot, feats)
        if np.isnan(ch_scores).any() or np.isnan(in_scores).any():
            return _terminal(
                CandidateState.REJECTED_COMPARISON,
                "a model declined to score part of the out-of-time slice, so "
                "the two sides do not describe the same rows",
                rejection_reason="incomplete scoring on the shared frame")
        segment = oot["dpd_bucket"] if "dpd_bucket" in oot.columns else None
        comp = cmp_mod.compare_to_incumbent(
            cand.model_name,
            challenger_version=cand.candidate_version, challenger_scores=ch_scores,
            incumbent_version=incumbent, incumbent_scores=in_scores,
            y=y, segment_series=segment)
    except Exception as exc:
        logger.exception("ml.lifecycle.comparison_failed candidate=%s", candidate_id)
        return _terminal(CandidateState.FAILED,
                         f"comparison raised {type(exc).__name__}",
                         error=f"{type(exc).__name__}: {exc}")

    cand.comparison_results = comp.to_dict()
    cand.comparison_run_id = comp.comparison_run_id
    cand.comparison_passed = comp.passed
    cand.gini_uplift = comp.gini_uplift
    db.commit()

    if not comp.passed:
        return _terminal(
            CandidateState.REJECTED_COMPARISON,
            f"{comp.verdict}: Gini {comp.challenger['gini']:.4f} against "
            f"incumbent {comp.incumbent['gini']:.4f} "
            f"(uplift {comp.gini_uplift:+.4f}, needs > {comp.uplift_tolerance:.4f})",
            rejection_reason="; ".join(comp.reasons) or comp.verdict)

    cand.transition(
        CandidateState.PENDING_APPROVAL,
        f"beat incumbent {incumbent} by {comp.gini_uplift:+.4f} Gini "
        f"(tolerance {comp.uplift_tolerance:.4f}); awaiting human approval")
    db.commit()
    logger.warning("ml.lifecycle.pending_approval candidate=%s version=%s "
                   "uplift=%+.4f", candidate_id, cand.candidate_version,
                   comp.gini_uplift)
    return cand


# ---------------------------------------------------------------------------
# 3. The human decisions
# ---------------------------------------------------------------------------

class ApprovalRefused(RuntimeError):
    """The decision could not be applied. The champion is unchanged."""


def _refuse_while_demo_master_login(db: Session, *, user_id: str, candidate_id: str, step: str) -> None:
    """2026-09-24 (hotfix DEMO-LOGIN) — one shared password for an admin AND a
    manager lets one person approve as one and promote as the other, which
    satisfies the four-eyes rule below while being one pair of eyes, and
    rewrites champion.txt for the whole deployment. A demo box does not
    promote models: while the master login is on, approve and promote refuse
    (the endpoints answer 409) and the attempt is audited."""
    from app.core.audit import write_audit
    from app.core.security import demo_master_login_active
    from app.models.audit_log import AuditAction
    if not demo_master_login_active():
        return
    write_audit(db, action=AuditAction.ROLE_VIOLATION_ATTEMPT, user_id=user_id,
                entity_type="ModelCandidate", entity_id=candidate_id, success=False,
                failure_reason=f"{step} refused: the demo master login is active on this box")
    raise ApprovalRefused(f"model {step} is disabled while the shared demo login is active")


def approve(db: Session, candidate_id: str, *, user_id: str,
            note: str | None = None) -> ModelCandidate:
    """Move PENDING_APPROVAL -> APPROVED. Does NOT promote.

    Approval and promotion are separate on purpose: approving records a person's
    judgement, promoting changes what production serves, and the second re-checks
    the incumbent because time passes between them.

    IDEMPOTENT: approving an already-approved candidate returns it unchanged
    rather than raising, so a double-clicked button is not an error.
    """
    _refuse_while_demo_master_login(db, user_id=user_id, candidate_id=candidate_id, step="approval")
    cand = db.query(ModelCandidate).filter(ModelCandidate.id == candidate_id).one()
    if cand.state == CandidateState.APPROVED:
        return cand
    if cand.state != CandidateState.PENDING_APPROVAL:
        raise ApprovalRefused(
            f"candidate {candidate_id} is {cand.state.value}, not "
            f"PENDING_APPROVAL; only a candidate that trained, passed every "
            f"validation gate and beat the incumbent can be approved")
    if not (cand.gates_passed and cand.comparison_passed):
        raise ApprovalRefused(
            "candidate did not pass both validation and the incumbent "
            "comparison; its state is inconsistent and it will not be approved")

    from app.ml.pipeline import registry
    current = registry.pointer_version(cand.model_name)
    if current != cand.incumbent_version:
        raise ApprovalRefused(
            f"stale: this candidate was compared against "
            f"{cand.incumbent_version!r} but the champion is now {current!r}. "
            f"Re-run validation and comparison against the current champion "
            f"rather than approving a decision about a model nobody is running")

    cand.decided_by_id = user_id
    cand.decided_at = datetime.now(timezone.utc)
    cand.decision_note = note
    cand.transition(CandidateState.APPROVED, f"approved by {user_id}")
    db.commit()
    return cand


def reject(db: Session, candidate_id: str, *, user_id: str,
           note: str | None = None) -> ModelCandidate:
    """A person declines the candidate. The champion is untouched by definition."""
    cand = db.query(ModelCandidate).filter(ModelCandidate.id == candidate_id).one()
    if cand.state == CandidateState.REJECTED_BY_HUMAN:
        return cand
    if cand.state not in (CandidateState.PENDING_APPROVAL, CandidateState.APPROVED):
        raise ApprovalRefused(
            f"candidate {candidate_id} is {cand.state.value}; only a pending or "
            f"approved candidate can be rejected by a person")
    cand.decided_by_id = user_id
    cand.decided_at = datetime.now(timezone.utc)
    cand.decision_note = note
    cand.rejection_reason = note or "rejected by reviewer"
    cand.transition(CandidateState.REJECTED_BY_HUMAN, f"rejected by {user_id}")
    db.commit()
    return cand


def promote(db: Session, candidate_id: str, *, user_id: str) -> ModelCandidate:
    """APPROVED -> PROMOTED, and the pointer moves. The last gate in the system.

    Six checks before the pointer is touched, and `registry.promote` repeats
    the incumbent one against the file itself — belt and brace, because this is
    the single write in the codebase that changes what borrowers are scored by.

    *(This said "Five checks" until 2026-09-10, when the four-eyes rule was
    added. Corrected rather than left to drift.)*
    """
    from app.ml.pipeline import registry
    from app.ml.pipeline.engine import DecisionEngine

    _refuse_while_demo_master_login(db, user_id=user_id, candidate_id=candidate_id, step="promotion")
    cand = db.query(ModelCandidate).filter(ModelCandidate.id == candidate_id).one()
    if cand.state == CandidateState.PROMOTED:
        return cand
    if cand.state != CandidateState.APPROVED:
        raise ApprovalRefused(
            f"candidate {candidate_id} is {cand.state.value}; only an APPROVED "
            f"candidate can be promoted")
    if not cand.is_promotable:
        raise ApprovalRefused(
            "candidate is APPROVED but its recorded validation or comparison "
            "result is not a pass; refusing to promote")
    # FOUR EYES, added 2026-09-10 by the repo-wide audit.
    #
    # Every other gate on this path asks whether the MODEL is good enough. None
    # asked how many PEOPLE agreed, and one person could approve and then
    # promote in two clicks — so "approval and promotion are separate on
    # purpose" (see approve() above) bought a second checkpoint that the same
    # judgement passed twice.
    #
    # The audit also found the role gate does not help here: `ManagerOnly`
    # resolves to AGENCY_MANAGER + AGENCY_ADMIN, and AGENCY_ADMIN is never used
    # to distinguish anything anywhere in the codebase, so every manager can
    # already reach this function. Separation of DUTY is available where
    # separation of PRIVILEGE is not.
    #
    # Enforced here rather than in the endpoint because the endpoint is not the
    # only caller a future script could have, and this is the function that
    # moves the pointer.
    if cand.decided_by_id and cand.decided_by_id == user_id:
        raise ApprovalRefused(
            f"four-eyes: this candidate was approved by {user_id} and cannot be "
            f"promoted by the same person. Promotion changes what every "
            f"borrower is scored by; it needs a second reviewer")

    try:
        previous = registry.promote(
            cand.model_name, cand.candidate_version,
            expected_incumbent=cand.incumbent_version,
            approved_by=user_id, candidate_id=cand.id)
    except registry.PromotionRefused as exc:
        cand.error = str(exc)
        db.commit()
        raise ApprovalRefused(str(exc)) from exc

    cand.promoted_from_version = previous
    cand.promoted_at = datetime.now(timezone.utc)
    cand.transition(CandidateState.PROMOTED,
                    f"promoted by {user_id}; replaced {previous}")
    db.commit()
    # This process must not be the one still serving the old artifact.
    state = DecisionEngine.reload(cand.model_name)
    logger.warning("ml.lifecycle.promoted candidate=%s version=%s replaced=%s "
                   "serving=%s", cand.id, cand.candidate_version, previous, state)
    return cand
