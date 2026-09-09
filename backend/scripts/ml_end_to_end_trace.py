"""Walk the ML path end to end on the RUNNING system and print what it finds.

    docker compose exec api python -m scripts.ml_end_to_end_trace

Twelve stages, Postgres to the browser payload and back to the monitor. Every
line is measured at runtime against the live database; nothing here is read out
of the source. A stage that cannot be proven prints FAIL and the script exits
non-zero, so this is a check rather than a report.

WHAT IT DELIBERATELY DOES NOT DO. It never executes a rollback against the live
plan. Rollback is destructive and proving it by destroying the plan a manager is
looking at is the wrong trade; it is executed for real, against a real database
session, in tests/test_ml_api_and_rollback.py. Here we only assert the route is
mounted and the run is in a rollback-able state.

AND IT PROVES NOTHING ABOUT PREDICTIVE ACCURACY. Stage 11 reports the monitoring
gate, which on this book is `not_ready` and will stay that way until outcomes
mature. The pipeline being wired and the model being good are different claims.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import date, timedelta

FAILURES: list[str] = []
_n = 0


def stage(title: str):
    global _n
    _n += 1
    print(f"\n{'─' * 74}\n{_n:2}. {title}\n{'─' * 74}")


def ok(msg: str):
    print(f"   PASS  {msg}")


def bad(msg: str):
    FAILURES.append(msg)
    print(f"   FAIL  {msg}")


def check(cond, msg: str):
    (ok if cond else bad)(msg)
    return bool(cond)


def main() -> int:  # noqa: C901 — a linear trace reads better than ten helpers
    import logging

    from sqlalchemy import text

    from app.core.config import settings
    from app.core.database import SessionLocal, engine as _db_engine
    from app.core.security import create_access_token
    from app.ml.pipeline.engine import DecisionEngine
    from app.ml.pipeline.monitor import readiness, serving_version
    from app.ml.pipeline.outcomes import OUTCOME_DEFINITION_VERSION
    from app.models.agent import Agent
    from app.models.allocation_decision import AllocationDecision
    from app.models.beat import Beat
    from app.models.case import Case, CaseStatus
    from app.models.model_prediction import ModelPrediction
    from app.models.user import User
    from app.services.ml_scoring_service import MLScoringService
    from app.services.planner_service import PlannerService

    # DEBUG=True puts the app engine in echo mode, which buries the evidence
    # under every statement. `echo` is an InstanceLogger flag and ignores the
    # logger level, so it has to be turned off on the engine itself.
    _db_engine.echo = False
    for _lg in ("sqlalchemy.engine", "sqlalchemy.engine.Engine"):
        logging.getLogger(_lg).setLevel(logging.WARNING)

    db = SessionLocal()

    # ── 1. the database itself ──────────────────────────────────────────────
    stage("Postgres — the real database, not a fixture")
    ver = db.execute(text("select version()")).scalar()
    check("PostgreSQL" in ver, f"connected: {ver.split(',')[0]}")
    head = db.execute(text("select version_num from alembic_version")).scalar()
    print(f"         alembic head {head}")
    for tbl in ("model_predictions", "allocation_decisions", "beats", "cases"):
        n = db.execute(text(f"select count(*) from {tbl}")).scalar()
        print(f"         {tbl:22} {n:>7} rows")
    check(db.execute(text(
        "select count(*) from information_schema.columns where "
        "table_name='allocation_decisions' and column_name='model_prediction_id'"
    )).scalar() == 1, "allocation_decisions.model_prediction_id exists in Postgres")

    # ── 2. the champion artifact ────────────────────────────────────────────
    stage("Engine — the artifact that is actually loaded")
    eng = DecisionEngine.get("recovery_risk")
    if not check(eng is not None, "recovery_risk champion loaded"):
        return 1
    print(f"         version {eng.version}   sha "
          f"{(eng.metadata.get('artifact_sha256') or '?')[:16]}")
    print(f"         features {eng.selected}")
    check(serving_version("recovery_risk") == eng.version,
          f"monitor and scorer agree on the serving version ({eng.version})")
    check(settings.ML_MODEL_VERSION in ("champion", eng.version),
          f"ML_MODEL_VERSION={settings.ML_MODEL_VERSION!r} resolves to {eng.version}")
    check(settings.ML_SCORING_ENABLED is True,
          f"ML_SCORING_ENABLED={settings.ML_SCORING_ENABLED}")

    # ── 3. point-in-time features off a real loan ───────────────────────────
    stage("Features — built from live rows, at a point in time")
    svc = MLScoringService(db)
    case = (db.query(Case).filter(Case.status.in_((CaseStatus.ASSIGNED, CaseStatus.IN_PROGRESS,
                             CaseStatus.UNASSIGNED)))
            .order_by(Case.created_at.desc()).first())
    if not check(case is not None, "a live open case to score"):
        return 1
    feats = svc.build_features(case.loan, as_of=date.today())
    print(f"         case {case.id[:8]}  loan {case.loan_id[:8]}")
    print("         " + json.dumps({k: feats[k] for k in eng.selected}, default=str))
    check(all(f in feats for f in eng.selected),
          "every champion feature is present in the served vector")
    old = svc.build_features(case.loan, as_of=date.today() - timedelta(days=120))
    check(old.get("dpd") != feats.get("dpd") or old != feats,
          "as_of changes the vector — the features are point-in-time, not 'now'")
    from app.services.repayment_service import _FORBIDDEN_FEATURE_KEYS
    leaked = _FORBIDDEN_FEATURE_KEYS & set(feats)
    check(not leaked, f"no forbidden/self-referential key in the vector ({leaked or 'none'})")

    # ── 4. scoring, with the prediction recorded ────────────────────────────
    stage("Scoring — score_cases_and_log writes what it served")
    before = db.query(ModelPrediction).count()
    probs, rows = svc.score_cases_and_log([case])
    check(case.id in probs, f"P(material payment) = {probs.get(case.id)}")
    check(len(rows) == 1 and rows[0].features and rows[0].outcome_baseline,
          "one prediction row, carrying its features AND its outcome baseline")
    db.rollback()      # read-only trace: the planner below writes for real
    check(db.query(ModelPrediction).count() == before,
          "the trace's own scratch score was rolled back, not left behind")

    # ── 5. the planner, for real ────────────────────────────────────────────
    stage("Planner — the production nightly path, run live")
    mgr = (db.query(User).join(Agent, Agent.manager_user_id == User.id)
           .group_by(User.id).order_by(User.id).first())
    if not check(mgr is not None, "a manager with agents"):
        return 1
    run = PlannerService(db, mgr.id).plan_next_day(strategy="SMART",
                                                   force_replan=True)
    db.commit()
    print(f"         run {run.id[:8]}  {run.plan_date}  {run.strategy}")
    print(f"         evaluated {run.total_cases_evaluated}  allocated "
          f"{run.total_cases_allocated}  blocked {run.total_cases_blocked}")
    check(run.total_cases_allocated > 0, "the run allocated cases")

    all_decisions = (db.query(AllocationDecision)
                     .filter(AllocationDecision.run_id == run.id).all())
    decisions = [d for d in all_decisions if d.allocated_agent_id]
    beats = db.query(Beat).filter(Beat.allocation_run_id == run.id).count()
    check(beats > 0, f"{beats} beats persisted for the run")

    # ── 6. the model actually drove it ──────────────────────────────────────
    stage("Allocator — the model's number is the one that was used")
    used = [d for d in decisions if (d.score_breakdown or {}).get("ml_used_for_decision")]
    check(len(used) == len(decisions),
          f"{len(used)}/{len(decisions)} allocated decisions ml_used_for_decision")
    transforms = {(d.score_breakdown or {}).get("value_transform") for d in decisions}
    check(transforms == {"log_rescaled"}, f"value transform {transforms}")

    mismatched = 0
    checked = 0
    for d in used:
        bd = d.score_breakdown or {}
        p, inr = bd.get("prob_recovery_ml"), bd.get("expected_case_inr")
        c = d.case
        collectable = max(0.0, float(c.target_amount or 0) - float(c.collected_amount or 0))
        if not (p and inr and collectable > 0):
            continue
        checked += 1
        if abs(inr / collectable - p) > 0.002:
            mismatched += 1
    check(checked > 0 and mismatched == 0,
          f"expected_case_inr = collectable x prob_recovery_ml on {checked} decisions "
          f"({mismatched} mismatched)")
    stale = [d for d in used
             if abs(float((d.score_breakdown or {}).get("prob_recovery") or 0)
                    - float((d.score_breakdown or {}).get("prob_recovery_ml") or 0)) < 1e-9]
    print(f"         shadow prob_recovery differs from the live one on "
          f"{len(used) - len(stale)}/{len(used)} decisions")

    # ── 7. lineage ──────────────────────────────────────────────────────────
    stage("Lineage — every decision points at the score behind it")
    linked = [d for d in decisions if d.model_prediction_id]
    check(len(linked) == len(decisions),
          f"{len(linked)}/{len(decisions)} decisions carry model_prediction_id")
    orphans = db.execute(text(
        "select count(*) from allocation_decisions d "
        "left join model_predictions p on p.id = d.model_prediction_id "
        "where d.model_prediction_id is not null and p.id is null")).scalar()
    check(orphans == 0, f"{orphans} dangling lineage references anywhere in the table")
    wrong_case = [d for d in linked
                  if db.get(ModelPrediction, d.model_prediction_id).case_id != d.case_id]
    check(not wrong_case,
          f"{len(wrong_case)} decisions linked to a prediction for a DIFFERENT case")
    agent_stamped = db.query(ModelPrediction).filter(
        ModelPrediction.id.in_([d.model_prediction_id for d in linked]),
        ModelPrediction.agent_id.isnot(None)).count()
    check(agent_stamped == len(linked),
          f"{agent_stamped}/{len(linked)} predictions stamped with the allocated agent")

    # ── 8. the API ──────────────────────────────────────────────────────────
    stage("API — over HTTP, the endpoint the page calls")
    import requests
    token = create_access_token(user_id=mgr.id, role=mgr.role.value,
                                device_id="ml-trace")
    base = os.environ.get("TRACE_API", "http://localhost:8000")
    r = requests.get(f"{base}/api/v1/manager/allocation/latest",
                     headers={"Authorization": f"Bearer {token}"}, timeout=120)
    if not check(r.status_code == 200, f"GET /manager/allocation/latest -> {r.status_code}"):
        print(r.text[:400])
        return 1
    body = r.json()
    check(body.get("run_id") == run.id, "the API serves the run just planned")
    api_decisions = body.get("decisions") or []
    with_ml = [d for d in api_decisions if isinstance(d.get("ml"), dict)]
    check(len(with_ml) == len(api_decisions) and api_decisions,
          f"{len(with_ml)}/{len(api_decisions)} decisions carry an `ml` block")
    sample = with_ml[0]["ml"]
    print("         " + json.dumps(sample))
    check(sample["model_version"] == eng.version,
          "the version on the wire is the serving version, off the prediction row")
    by_id = {d.id: d for d in all_decisions}   # blocked and deferred rows too
    drift = [d for d in with_ml
             if d["ml"]["prediction_id"] != by_id[d["decision_id"]].model_prediction_id]
    check(not drift, f"{len(drift)} API rows disagree with the persisted lineage")

    # ── 9. the frontend contract ────────────────────────────────────────────
    stage("Frontend contract — the fields the page actually reads")
    required = {"used_for_decision", "probability_used", "model_name",
                "model_version", "feature_coverage", "expected_recovery_inr",
                "prediction_id"}
    missing = required - set(sample)
    check(not missing, f"every field DecisionMl declares is on the wire ({missing or 'none'})")
    bd = by_id[with_ml[0]["decision_id"]].score_breakdown or {}
    check(with_ml[0]["ml"]["probability_used"] == bd.get("prob_recovery_ml"),
          "the badge's probability is the one the allocator multiplied by")
    check(abs(bd.get("affinity_score", 0) - (bd.get("prob_recovery_ml") or 0)) > 1e-9,
          "affinity_score is NOT the number the panel would show (the 5x defect)")

    # ── 10. the labeller ────────────────────────────────────────────────────
    stage("Outcome labelling — scheduled, versioned, and not yet due")
    from app.ml.pipeline.outcomes import attach_outcomes
    summary = attach_outcomes(db, "recovery_risk", commit=False)
    db.rollback()
    print("         " + json.dumps(summary))
    check(summary["definition_version"] == OUTCOME_DEFINITION_VERSION,
          f"labels stamped {OUTCOME_DEFINITION_VERSION}")
    check(summary["horizon_days"] == settings.REPAYMENT_OUTCOME_HORIZON_DAYS,
          f"horizon {summary['horizon_days']}d, from settings")

    # ── 11. the monitor ─────────────────────────────────────────────────────
    stage("Monitoring — the gate, on real rows")
    gate = readiness(db, "recovery_risk")
    print("         " + json.dumps(gate.to_dict()))
    check(gate.model_version == eng.version,
          "the gate counts the SERVING version, not a pool")
    if gate.ready:
        ok("READY — real matured outcomes exist; metrics are meaningful")
    else:
        earliest = db.execute(text(
            "select min(as_of_date) from model_predictions "
            "where actual_outcome is null")).scalar()
        due = (earliest + timedelta(days=summary["horizon_days"])) if earliest else None
        ok(f"not_ready by design: {gate.n_matured}/{gate.required} matured. "
           f"First outcome matures {due}. PENDING REAL-WORLD DATA, not a defect.")

    # ── 12. rollback ────────────────────────────────────────────────────────
    stage("Rollback — mounted and reachable, NOT executed here")
    from app.main import app as fastapi_app
    routes = {r.path for r in fastapi_app.routes}
    check("/api/v1/manager/allocation/rollback" in routes,
          "POST /manager/allocation/rollback is mounted")
    check(run.status in ("PLANNED", "COMPLETED"),
          f"the live run is in a rollback-able state ({run.status})")
    print("         executed for real against a database session in "
          "tests/test_ml_api_and_rollback.py (5 tests); deliberately not run "
          "against the live plan here.")

    db.close()
    print(f"\n{'═' * 74}")
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print(f"{_n} stages, all checks passed on the live system.")
    print("Wired and executable: yes. Validated on real outcomes: not yet — "
          "see stage 11.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
