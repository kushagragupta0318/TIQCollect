"""Bank Alerts (plan §5.4 / Command Center "Alerts", task C06): a small set
of SQL rules over the bank's own book, shaped as DecisionAlerts.tsx's own
`DecisionAlert` (frontend/src/bank/components/DecisionAlerts.tsx) — this repo
does not let a rule present itself as AI (CLAUDE.md), so these are plain,
named rules, not a model.

Each rule reuses an EXISTING definition rather than restating one:
  - GNPA breach           -> kpi_catalog's own gnpa_pct KPI (compute_overview)
  - Collection-efficiency  -> agency_scorecard.agency_scorecard(), this month
    drop, by agency          against last, per agency (the Agencies tab's own metric)
  - Agency SLA miss        -> agency_scorecard_monthly_scoped's own
                              placements_due_first_visit / first_visits_within_sla
  - Fraud / off-hours      -> collections.fraud_reviews CONFIRMED this month,
                              plus mv_field_activity_daily's out_of_hours_attempts
                              and geo_unverified_visits (the same columns
                              _compliance()'s breaches_over_time panel reads)
  - Pending placement      -> the Exposure tab's own funnel (delinquent minus
                              placed): the one place "placed" is already defined
  - Roll-forward spike     -> kpi_catalog's own roll_forward_rate KPI's SQL,
                              re-read for the latest two month-ends and
                              compared against a real spike threshold (not
                              _trend's 0.05pp "is there a direction" floor)

New, named settings with no existing definition to reuse (core/config.py):
ALERT_GNPA_PCT, ALERT_EFFICIENCY_DROP_PCT, ALERT_ROLL_FORWARD_SPIKE_PP.

Every rule returns None when it does not fire, or when the book does not
have what it needs to answer (ADR 0005: abstain, never invent a reading).
A rule that raises is caught, logged and counted as failed, not silently
dropped -- compute_alerts() reports how many of the total it could not
evaluate, so the page can say so rather than reading a crash as "nothing
is wrong".
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.bank.agency_scorecard import agency_scorecard, latest_month
from app.services.bank.analytics_catalog import compute_tab
from app.services.bank.kpi_catalog import FIELD, KPIS, TRANSITIONS, compute_overview, latest_reading
from app.services.bank.kpi_filter import KpiFilter


def _month_back(m: date, n: int) -> date:
    """Same 3-line arithmetic agency_scorecard.py keeps local rather than
    import a private helper for (that module's own precedent)."""
    y, mo = divmod(m.year * 12 + (m.month - 1) - n, 12)
    return date(y, mo + 1, 1)


def _kpi(overview, kpi_id: str) -> dict | None:
    return next((k for k in overview.kpis if k["id"] == kpi_id), None)


# ── 1. GNPA breach ───────────────────────────────────────────────────────────
def _gnpa_breach(adb: Session, bank_id: str) -> dict | None:
    ov = compute_overview(adb, bank_id)
    gnpa = _kpi(ov, "gnpa_pct")
    delinquent = _kpi(ov, "delinquent_exposure")
    if gnpa is None or not gnpa["available"] or gnpa["raw"] is None:
        return None
    if gnpa["raw"] < settings.ALERT_GNPA_PCT:
        return None
    pct_text = f"{gnpa['raw'] * 100:.1f}%"
    return {
        "id": "gnpa_breach", "severity": "critical",
        "title": f"GNPA at {pct_text}, past the {settings.ALERT_GNPA_PCT * 100:.0f}% alert line",
        "summary": f"NPA exposure is {gnpa['sub']} of the book, read as of {ov.as_of.isoformat() if ov.as_of else 'the latest reading'}.",
        "metrics": [
            {"label": "GNPA %", "value": gnpa["value"]},
            {"label": "NPA accounts", "value": gnpa["sub"]},
        ] + ([{"label": "Delinquent exposure", "value": delinquent["value"]}] if delinquent and delinquent["available"] else []),
        "actions": [{"label": "Open Exposure", "target": "exposure"}],
        "basis": f"{gnpa['basis']} Fires past {settings.ALERT_GNPA_PCT * 100:.0f}%.",
    }


# ── 2. Collection-efficiency drop, by agency ────────────────────────────────
def _efficiency_drop(db: Session, adb: Session, bank_id: str) -> dict | None:
    from app.models.tenancy import Agency

    last = latest_month(adb, bank_id=bank_id)
    if last is None:
        return None
    prior = _month_back(last, 1)
    worst = None
    for agency in db.query(Agency).filter(Agency.bank_id == bank_id):
        now = agency_scorecard(adb, bank_id=bank_id, agency_id=agency.id, month_start=last)
        then = agency_scorecard(adb, bank_id=bank_id, agency_id=agency.id, month_start=prior)
        ce_now, ce_then = now.get("collection_efficiency"), then.get("collection_efficiency")
        if ce_now is None or ce_then is None or ce_then <= 0:
            continue
        drop = (ce_then - ce_now) / ce_then
        if drop >= settings.ALERT_EFFICIENCY_DROP_PCT and (worst is None or drop > worst["drop"]):
            worst = {"agency": agency, "ce_now": ce_now, "ce_then": ce_then, "drop": drop, "row": now}
    if worst is None:
        return None
    a, row = worst["agency"], worst["row"]
    name = a.trade_name or a.legal_name
    return {
        "id": "agency_efficiency_drop", "severity": "warning",
        "title": f"{name}: collection efficiency down {worst['drop'] * 100:.0f}% month on month",
        "summary": f"{worst['ce_now'] * 100:.1f}% against {worst['ce_then'] * 100:.1f}% last month.",
        "metrics": [
            {"label": "Efficiency", "value": f"{worst['ce_now'] * 100:.1f}%"},
            {"label": "Last month", "value": f"{worst['ce_then'] * 100:.1f}%"},
            {"label": "Agency", "value": a.code},
        ],
        "actions": [{"label": "Open agency scorecard", "target": "agency"}],
        "basis": f"Verified collections ÷ collectible due, this month against last, per agency "
                f"(agency_scorecard.py). Fires past a {settings.ALERT_EFFICIENCY_DROP_PCT * 100:.0f}% relative drop.",
    }


# ── 3. Agency SLA miss ───────────────────────────────────────────────────────
def _sla_miss(db: Session, adb: Session, bank_id: str) -> dict | None:
    from app.models.tenancy import Agency
    from app.services.bank.kpi_catalog import SCORECARD

    last = latest_month(adb, bank_id=bank_id)
    if last is None:
        return None
    rows = adb.execute(text(f"""
        SELECT agency_id, SUM(placements_due_first_visit) AS due, SUM(first_visits_within_sla) AS within
        FROM analytics.{SCORECARD} WHERE bank_id = :bank AND month_start = :m GROUP BY agency_id"""),
        {"bank": bank_id, "m": last}).mappings().all()
    # SUM() over this driver path can come back Decimal even for an integer
    # column (measured on accounts/exposure in this same view) -- float()
    # once, here, rather than let a Decimal ever reach an f-string.
    counted = [(str(r["agency_id"]), int(float(r["due"] or 0)), int(float(r["within"] or 0))) for r in rows]
    worst = max(((a, due, due - within) for a, due, within in counted), key=lambda t: t[2], default=(None, 0, 0))
    agency_id, due, missed = worst
    if agency_id is None or missed <= 0:
        return None
    agency = db.get(Agency, agency_id)
    if agency is None:
        return None
    name = agency.trade_name or agency.legal_name
    return {
        "id": "agency_sla_miss", "severity": "warning",
        "title": f"{name}: {missed} first visits missed their SLA this month",
        "summary": f"{missed} of {due} placements due a first visit were not visited inside the agency's SLA window.",
        "metrics": [
            {"label": "Missed", "value": str(missed)},
            {"label": "Due", "value": str(due)},
            {"label": "Agency", "value": agency.code},
        ],
        "actions": [{"label": "Open agency scorecard", "target": "agency"}],
        "basis": "Placements due a first visit this month, against first visits made inside the agency's own "
                "contracted SLA (tenancy.agency_contracts.sla_first_visit_days). Fires on any miss.",
    }


# ── 4. Fraud / off-hours / out-of-fence ──────────────────────────────────────
def _fraud_and_fence(adb: Session, bank_id: str) -> dict | None:
    """"This month" is the book's own latest reading's month, never the
    wall clock (the same bug agency_effect.latest_month() had: a demo book
    anchored months before today's real date would otherwise always read
    as zero confirmed findings). Breach counts reuse mv_field_activity_daily's
    own columns -- the same ones _compliance()'s breaches_over_time panel
    reads, not a second definition of a breach."""
    as_of = latest_reading(adb, bank_id)
    if as_of is None:
        return None
    row = adb.execute(text(f"""
        SELECT
            (SELECT count(*) FROM collections.fraud_reviews
             WHERE bank_id = :bank AND verdict = 'CONFIRMED' AND reviewed_at >= date_trunc('month', :as_of)) AS confirmed,
            (SELECT coalesce(sum(out_of_hours_attempts), 0) FROM analytics.{FIELD}
             WHERE bank_id = :bank AND activity_date >= date_trunc('month', :as_of)) AS out_of_hours,
            (SELECT coalesce(sum(geo_unverified_visits), 0) FROM analytics.{FIELD}
             WHERE bank_id = :bank AND activity_date >= date_trunc('month', :as_of)) AS out_of_fence"""),
        {"bank": bank_id, "as_of": as_of}).mappings().first()
    confirmed = int(float((row or {}).get("confirmed") or 0))
    out_of_hours = int(float((row or {}).get("out_of_hours") or 0))
    out_of_fence = int(float((row or {}).get("out_of_fence") or 0))
    if confirmed <= 0 and out_of_hours <= 0 and out_of_fence <= 0:
        return None
    parts = []
    if confirmed:
        parts.append(f"{confirmed} confirmed fraud finding{'s' if confirmed != 1 else ''}")
    if out_of_hours:
        parts.append(f"{out_of_hours} contact-hour breach{'es' if out_of_hours != 1 else ''}")
    if out_of_fence:
        parts.append(f"{out_of_fence} visit{'s' if out_of_fence != 1 else ''} outside the 100m geofence")
    return {
        "id": "fraud_confirmed", "severity": "critical",
        "title": f"{', '.join(parts)} this month".capitalize(),
        "summary": f"As of {as_of.isoformat()}, this calendar month (mv_field_activity_daily + collections.fraud_reviews).",
        "metrics": [
            {"label": "Confirmed fraud", "value": str(confirmed)},
            {"label": "Contact-hour breaches", "value": str(out_of_hours)},
            {"label": "Out-of-fence visits", "value": str(out_of_fence)},
        ],
        "actions": [{"label": "Open Compliance", "target": "compliance"}],
        "basis": "collections.fraud_reviews (CONFIRMED) plus mv_field_activity_daily's out_of_hours_attempts and "
                "geo_unverified_visits, in the book's latest-reading month. Fires on any.",
    }


# ── 5. Pending placement (delinquent, never placed) ─────────────────────────
def _pending_placement(db: Session, adb: Session, bank_id: str) -> dict | None:
    out = compute_tab(db, adb, "exposure", bank_id, KpiFilter())
    if not out["available"]:
        return None
    funnel = out["panels"]["funnel"]
    delinquent, placed = float(funnel.get("delinquent") or 0), float(funnel.get("placed") or 0)
    pending = delinquent - placed
    if pending <= 0:
        return None
    return {
        "id": "pending_placement", "severity": "info",
        "title": f"₹{pending / 1e7:.2f} Cr delinquent and never placed with an agency",
        "summary": f"₹{delinquent / 1e7:.2f} Cr delinquent, ₹{placed / 1e7:.2f} Cr of it placed — the rest has no agency working it.",
        "metrics": [
            {"label": "Delinquent", "value": f"₹{delinquent / 1e7:.2f} Cr"},
            {"label": "Placed", "value": f"₹{placed / 1e7:.2f} Cr"},
            {"label": "Never placed", "value": f"₹{pending / 1e7:.2f} Cr"},
        ],
        "actions": [{"label": "Open Placement", "target": "placement"}],
        "basis": "Delinquent exposure minus placed exposure, at the latest reading (the Exposure tab's own funnel).",
    }


# ── 6. Roll-forward spike ────────────────────────────────────────────────────
def _roll_forward_rate_kpi():
    return next(k for k in KPIS if k.id == "roll_forward_rate")


def _read_value(adb: Session, sql: str, params: dict) -> float | None:
    row = adb.execute(text(sql), params).mappings().first()
    return None if row is None or row["value"] is None else float(row["value"])


def _roll_forward_spike(adb: Session, bank_id: str) -> dict | None:
    """kpi_catalog._trend's own floor is 0.05 percentage points -- "is there
    a direction at all", not a spike; that floor fired this alert on any
    wobble in the unfavourable direction. This rule re-reads the KPI's own
    SQL (not a second formula) for the latest two month-ends and applies a
    real spike threshold, ALERT_ROLL_FORWARD_SPIKE_PP, before firing."""
    as_of = latest_reading(adb, bank_id)
    if as_of is None:
        return None
    me = adb.execute(text(f"SELECT MAX(month_end) FROM analytics.{TRANSITIONS} WHERE bank_id = :bank "
                          f"AND month_end <= :d"), {"bank": bank_id, "d": as_of}).scalar()
    if me is None:
        return None
    prev_me = me.replace(day=1) - timedelta(days=1)
    k = _roll_forward_rate_kpi()
    now = _read_value(adb, k.sql, {"bank": bank_id, "d": me})
    prev = _read_value(adb, k.sql, {"bank": bank_id, "d": prev_me})
    if now is None or prev is None:
        return None
    delta_pp = (now - prev) * 100
    if delta_pp < settings.ALERT_ROLL_FORWARD_SPIKE_PP:
        return None
    return {
        "id": "roll_forward_spike", "severity": "warning",
        "title": f"Roll-forward rate at {now * 100:.1f}%, up {delta_pp:.1f} pp from {prev * 100:.1f}% last month",
        "summary": "More exposure moved to a worse portfolio state than usual between the last two month-ends.",
        "metrics": [
            {"label": "Roll-forward rate", "value": f"{now * 100:.1f}%"},
            {"label": "Last month", "value": f"{prev * 100:.1f}%"},
            {"label": "Change", "value": f"{delta_pp:+.1f} pp"},
        ],
        "actions": [{"label": "Open Migration", "target": "migration"}],
        "basis": f"{k.basis} Fires past a {settings.ALERT_ROLL_FORWARD_SPIKE_PP:.0f} pp month-on-month increase.",
    }


RULES = (_gnpa_breach, _efficiency_drop, _sla_miss, _fraud_and_fence, _pending_placement, _roll_forward_spike)

RULE_LABELS = {
    "_gnpa_breach": "GNPA breach",
    "_efficiency_drop": "Collection-efficiency drop",
    "_sla_miss": "Agency SLA miss",
    "_fraud_and_fence": "Fraud / off-hours / out-of-fence",
    "_pending_placement": "Pending placement",
    "_roll_forward_spike": "Roll-forward spike",
}


def compute_alerts(db: Session, adb: Session, bank_id: str) -> dict:
    """{"alerts": [...], "rules_total": N, "rules_failed": [label, ...]}.

    A rule that raises is logged and counted as FAILED, not silently
    dropped: the one thing worse than a rule not firing is the page
    claiming "no rule is firing" when a rule actually errored and nobody
    can tell the difference. Both sessions are savepointed: `db` and `adb`
    are two separate connections in production (bank.py's DbSession/
    AnalyticsDb), and a failed query aborts only the connection it ran on —
    rolling back just one would leave the other's transaction poisoned for
    every rule after it."""
    import structlog

    logger = structlog.get_logger()
    order = {"critical": 0, "warning": 1, "info": 2}
    alerts: list[dict] = []
    failed: list[str] = []
    for rule in RULES:
        sp_db, sp_adb = db.begin_nested(), (None if adb is db else adb.begin_nested())
        try:
            needs_db = rule in (_efficiency_drop, _sla_miss, _pending_placement)
            alert = rule(db, adb, bank_id) if needs_db else rule(adb, bank_id)
            sp_db.commit()
            if sp_adb is not None:
                sp_adb.commit()
            if alert is not None:
                alerts.append(alert)
        except Exception:
            sp_db.rollback()
            if sp_adb is not None:
                sp_adb.rollback()
            logger.exception("bank_alerts.rule_failed", rule=rule.__name__, bank_id=bank_id)
            failed.append(RULE_LABELS.get(rule.__name__, rule.__name__))
    alerts.sort(key=lambda a: order.get(a["severity"], 3))
    return {"alerts": alerts, "rules_total": len(RULES), "rules_failed": failed}
