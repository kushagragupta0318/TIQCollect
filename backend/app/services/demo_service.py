"""Rewind the showcase demo case so the same story can be told again.

The demo is one case (settings.DEMO_CONTACT_REF) carrying the full set of
borrower detail. Showing it once records a visit, which moves it to Done — so
the next audience finds a completed case and there is nothing left to
demonstrate. Reseeding to fix that costs minutes and discards everything else
on the box.

Why rewind rather than not write at all
---------------------------------------
Submitting a visit is not one request. The client posts the visit, gets its id
back, then posts the payment carrying that id, then the PTP, then queues
transcription against it, then re-optimises and refreshes the beat. Each is its
own transaction. Rolling the first one back would leave the payment's foreign
key dangling, the transcription 404ing and the beat showing no visit — the
receipt, the Done list and the counters would all be visibly wrong. So the flow
writes exactly as it does in production, and this puts it back afterwards.

The baseline is an explicit snapshot rather than something inferred: "clean"
means whatever the case looked like when someone said it was clean.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import text

from app.core.config import settings
from app.models.agent import Agent
from app.models.audit_log import AuditLog
from app.models.call_log import CallLog
from app.models.case import Case
from app.models.customer import Customer
from app.models.payment import Payment
from app.models.ptp import PTP
from app.models.visit import Visit

# Case columns a visit can move. Everything else on the row is static
# borrower/loan detail that the demo flow never touches.
CASE_FIELDS = [
    "status", "visit_count", "collected_amount", "resolved_at",
    "is_escalated", "escalation_reason", "escalated_at", "escalation_notes",
    "resolution_notes", "allocation_date", "target_amount",
]
CUSTOMER_FIELDS = ["do_not_contact", "tags"]
# Month-to-date tallies the visit/payment services increment. Restored to the
# snapshot value rather than recomputed: the rest of the month's work is real
# and has to survive.
AGENT_COUNTERS = ["current_month_visits", "current_month_collections", "current_month_ptps_set"]
# Rows the demo creates, children before parents.
CHILD_MODELS = [Payment, PTP, AuditLog, CallLog, Visit]


class DemoBaselineMissing(RuntimeError):
    """No snapshot stored for this ref — nothing to rewind to."""


class DemoCaseNotFound(RuntimeError):
    """No case matches the configured customer_ref."""


def _ensure_table(db) -> None:
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS demo_baseline (
            customer_ref TEXT PRIMARY KEY,
            taken_at     TIMESTAMPTZ NOT NULL,
            payload      TEXT NOT NULL
        )
    """))


def _serialise(value):
    if isinstance(value, datetime):
        return {"__dt__": value.isoformat()}
    if hasattr(value, "value"):          # enum
        return {"__enum__": value.value}
    return value


def _deserialise(value):
    if isinstance(value, dict):
        if "__dt__" in value:
            return datetime.fromisoformat(value["__dt__"])
        if "__enum__" in value:
            return value["__enum__"]
    return value


def _load(db, ref: str):
    row = (
        db.query(Case, Customer)
        .join(Customer, Customer.id == Case.customer_id)
        .filter(Customer.customer_ref == ref)
        .first()
    )
    if not row:
        raise DemoCaseNotFound(f"No case found for customer_ref={ref!r}")
    case, customer = row
    agent = db.query(Agent).filter(Agent.id == case.agent_id).first() if case.agent_id else None
    return case, customer, agent


def save_baseline(db, ref: str | None = None) -> dict:
    """Snapshot the current state of the demo case. Commits."""
    ref = ref or settings.DEMO_CONTACT_REF
    _ensure_table(db)
    case, customer, agent = _load(db, ref)
    payload = {
        "case_id": case.id,
        "case": {f: _serialise(getattr(case, f)) for f in CASE_FIELDS},
        "customer": {f: _serialise(getattr(customer, f)) for f in CUSTOMER_FIELDS},
        "agent_id": agent.id if agent else None,
        "agent": {f: _serialise(getattr(agent, f)) for f in AGENT_COUNTERS} if agent else {},
    }
    taken_at = datetime.now(timezone.utc)
    db.execute(
        text("""
            INSERT INTO demo_baseline (customer_ref, taken_at, payload)
            VALUES (:r, :t, :p)
            ON CONFLICT (customer_ref)
            DO UPDATE SET taken_at = EXCLUDED.taken_at, payload = EXCLUDED.payload
        """),
        {"r": ref, "t": taken_at, "p": json.dumps(payload)},
    )
    db.commit()
    return {
        "ref": ref, "case_number": case.case_number, "taken_at": taken_at,
        "status": getattr(case.status, "value", case.status),
        "visit_count": case.visit_count, "collected_amount": case.collected_amount,
    }


def rewind(db, ref: str | None = None, dry_run: bool = False) -> dict:
    """Put the demo case back to its snapshot. Commits unless dry_run."""
    ref = ref or settings.DEMO_CONTACT_REF
    _ensure_table(db)
    row = db.execute(
        text("SELECT taken_at, payload FROM demo_baseline WHERE customer_ref = :r"), {"r": ref}
    ).first()
    if not row:
        raise DemoBaselineMissing(
            f"No baseline stored for {ref!r}. Take one while the case is clean:\n"
            f"    python -m scripts.demo_reset --save"
        )
    taken_at, payload = row[0], json.loads(row[1])
    case, customer, agent = _load(db, ref)

    if case.id != payload["case_id"]:
        raise DemoBaselineMissing(
            "The baseline points at a different case row — the database has been "
            "reseeded since. Take a fresh one: python -m scripts.demo_reset --save"
        )

    # Anything attached to this case that did not exist when the snapshot was
    # taken is demo residue.
    deleted: dict[str, int] = {}
    for model in CHILD_MODELS:
        if not hasattr(model, "case_id"):
            continue
        q = db.query(model).filter(model.case_id == case.id, model.created_at > taken_at)
        n = q.count()
        if n:
            deleted[model.__name__] = n
            if not dry_run:
                q.delete(synchronize_session=False)

    changed: dict[str, tuple] = {}

    def _restore(obj, prefix: str, fields: list[str], snap: dict) -> None:
        for f in fields:
            was = _deserialise(snap.get(f))
            now = getattr(obj, f)
            now_cmp = getattr(now, "value", now)
            if now_cmp != was:
                changed[f"{prefix}.{f}"] = (now_cmp, was)
                if not dry_run:
                    setattr(obj, f, was)

    _restore(case, "case", CASE_FIELDS, payload["case"])
    _restore(customer, "customer", CUSTOMER_FIELDS, payload["customer"])
    if agent and payload.get("agent"):
        _restore(agent, "agent", AGENT_COUNTERS, payload["agent"])

    if not dry_run:
        db.commit()

    return {
        "ref": ref, "case_number": case.case_number, "taken_at": taken_at,
        "deleted": deleted, "changed": changed, "dry_run": dry_run,
        "clean": not (deleted or changed),
    }
