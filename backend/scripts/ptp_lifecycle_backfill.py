"""One-off backfill: resolve the promises that were ACTIVE past their grace day
before the nightly lifecycle job existed (2026-09-17).

Runs THE SAME CODE as the nightly task — `workers.tasks.ptp_lifecycle
.run_for_all_managers`, which calls `services.ptp_lifecycle_service
.process_scope` once per manager — with `source="ptp_lifecycle_backfill"` so
the audit rows say which run wrote them. There is no rule in this file.

    python -m scripts.ptp_lifecycle_backfill                 # dry run (default): report + manifest
    python -m scripts.ptp_lifecycle_backfill --execute       # apply, write the rollback manifest
    python -m scripts.ptp_lifecycle_backfill --undo /tmp/manifest.json

    --effective-date YYYY-MM-DD   business date to judge against (default: today)

UNDO restores each promise's status and actual_paid_amount to the values the
manifest recorded — but ONLY a row that is provably still as the backfill
left it. Four checks, all of which must hold (`undo_manifest`):
  1. status  == the status the backfill wrote
  2. actual_paid_amount == the amount the backfill wrote
  3. updated_at has not moved past the backfill's processing time
  4. no PTP_UPDATED audit row for the promise is dated after the backfill's own
A row failing any check has legitimately moved on (a later payment honoured
it, an operator touched it) and is left alone and listed. Undo NEVER deletes
the audit rows the backfill wrote — the trail is append-only by policy
(tests/test_compliance_hardening.py) — and writes one compensating
PTP_UPDATED row per restored promise with reason BACKFILL_ROLLBACK, so the
trail reads: resolved by the calendar, then reversed by an operator, both
dated.

The api container does not see the repo's docs/, so the manifest is written
under the container's rollback dir and copied out:
    docker compose cp api:/app/docs/rollback/<file> docs/rollback/2026-09-17-ptp-lifecycle-backfill/
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import uuid
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone

ROLLBACK_DIR = pathlib.Path(__file__).resolve().parents[1] / "docs" / "rollback"

# updated_at is set by the ORM in Python and the audit row's created_at by the
# service, in the same commit; allow them to differ by this much.
_CLOCK_SLACK = timedelta(seconds=5)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def undo_manifest(db, manifest: dict) -> tuple[int, list[tuple[str, str]]]:
    """Reverse an --execute manifest. Returns (restored, [(ptp_id, why_left)]).

    Importable so the guard is tested, not just described.
    """
    from app.models.audit_log import AuditAction, AuditLog
    from app.models.ptp import PTP, PTPStatus
    from app.services.ptp_lifecycle_service import SOURCE_BACKFILL

    now = datetime.now(timezone.utc)
    restored, left = 0, []
    for row in manifest["rows"]:
        ptp = db.get(PTP, row["ptp_id"])
        if ptp is None:
            left.append((row["ptp_id"], "row no longer exists")); continue
        if ptp.status.value != row["new_status"]:
            left.append((ptp.id, f"status is now {ptp.status.value}, not {row['new_status']}")); continue
        if abs(float(ptp.actual_paid_amount or 0.0) - float(row["paid_through_grace"])) > 0.005:
            left.append((ptp.id, f"actual_paid_amount is now {ptp.actual_paid_amount}, not {row['paid_through_grace']}")); continue
        processed_at = _aware(datetime.fromisoformat(row["processed_at"])) if row.get("processed_at") else None
        if processed_at is not None and _aware(ptp.updated_at) is not None \
                and _aware(ptp.updated_at) > processed_at + _CLOCK_SLACK:
            left.append((ptp.id, f"updated_at {ptp.updated_at.isoformat()} is after the backfill")); continue
        later = (
            db.query(AuditLog.id)
            .filter(AuditLog.entity_type == "PTP", AuditLog.entity_id == ptp.id,
                    AuditLog.action == AuditAction.PTP_UPDATED, AuditLog.id != row.get("audit_log_id"))
            .all()
        )
        later_ids = [r[0] for r in later]
        if processed_at is not None and later_ids:
            newer = (
                db.query(AuditLog.id).filter(AuditLog.id.in_(later_ids), AuditLog.created_at > processed_at).count()
            )
            if newer:
                left.append((ptp.id, f"{newer} later PTP_UPDATED audit row(s)")); continue
        ptp.status = PTPStatus(row["previous_status"])
        ptp.actual_paid_amount = float(row["previous_actual_paid_amount"])
        db.add(AuditLog(
            id=str(uuid.uuid4()), created_at=now, user_id=None,
            action=AuditAction.PTP_UPDATED, entity_type="PTP", entity_id=ptp.id,
            details={"from": row["new_status"], "to": row["previous_status"],
                     "reason": "BACKFILL_ROLLBACK", "actor": "SYSTEM",
                     "source": SOURCE_BACKFILL, "reverses_audit_log_id": row.get("audit_log_id"),
                     "manifest": manifest.get("written_at"), "processed_at": now.isoformat()},
            success=True,
        ))
        db.commit()
        restored += 1
    return restored, left


def _print_report(result: dict, *, applied: bool) -> None:
    mode = "APPLIED" if applied else "DRY RUN"
    print(f"\nPTP lifecycle backfill — {mode}")
    print(f"  effective_date={result['effective_date']}  cutoff(latest eligible due date)={result['cutoff']}  managers={result['managers']}")
    print(f"  eligible={result['eligible']}")
    print(f"  honored={result['honored']}")
    print(f"  partially_honored={result['partially_honored']}")
    print(f"  broken={result['broken']}")
    print(f"  rescheduled_skipped={result['rescheduled_skipped']}")
    print(f"  grace_period_skipped={result['grace_period_skipped']}")
    print(f"  failed={result['failed']}  orphan_agent_overdue_skipped={result['orphan_agent_overdue_skipped']}")
    for pm in result["per_manager"]:
        print(f"    manager {pm['manager_user_id'][:8]}… agents={pm['agents']} eligible={pm['eligible']} "
              f"honored={pm['honored']} partial={pm['partially_honored']} broken={pm['broken']}")
    transitions = [t for pm in result["per_manager"] for t in pm["transitions"]]
    ages = Counter()
    for t in transitions:
        d = (date.fromisoformat(result["effective_date"]) - date.fromisoformat(t.committed_date)).days
        ages["2-7d" if d <= 7 else "8-30d" if d <= 30 else ">30d"] += 1
    print(f"  age of eligible promises: {dict(ages)}")
    print("  representative examples:")
    shown = set()
    for t in transitions:
        if t.new_status in shown:
            continue
        shown.add(t.new_status)
        print(f"    {t.new_status:<18} ptp={t.ptp_id[:8]}… case={t.case_id[:8]}… origin={t.origin[:16]} due={t.committed_date} "
              f"committed=₹{t.committed_amount:,.0f} verified_paid_from_origin_through_grace=₹{t.paid_through_grace:,.0f}")


def _manifest_rows(result: dict) -> list[dict]:
    return [asdict(t) for pm in result["per_manager"] for t in pm["transitions"]]


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill the PTP lifecycle (same code as the nightly job).")
    ap.add_argument("--execute", action="store_true", help="Apply. Default is a dry run.")
    ap.add_argument("--undo", type=str, help="Manifest from an --execute run to reverse.")
    ap.add_argument("--effective-date", type=str, default=None)
    args = ap.parse_args()

    from app.core.database import SessionLocal
    from app.services.ptp_lifecycle_service import SOURCE_BACKFILL
    from app.workers.tasks.ptp_lifecycle import run_for_all_managers

    db = SessionLocal()
    try:
        if args.undo:
            manifest = json.loads(pathlib.Path(args.undo).read_text(encoding="utf-8"))
            if manifest.get("dry_run"):
                sys.exit("That manifest is from a dry run; nothing was applied, nothing to undo.")
            restored, left = undo_manifest(db, manifest)
            print(f"undo: restored {restored} of {len(manifest['rows'])} promises; "
                  f"left alone (moved on since the backfill): {len(left)}")
            for pid, why in left[:40]:
                print(f"   {pid}  {why}")
            return

        eff = date.fromisoformat(args.effective_date) if args.effective_date else date.today()
        result = run_for_all_managers(db, eff, source=SOURCE_BACKFILL, dry_run=not args.execute)
        _print_report(result, applied=args.execute)

        ROLLBACK_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        kind = "applied" if args.execute else "dryrun"
        out = ROLLBACK_DIR / f"{eff.isoformat()}-ptp-lifecycle-backfill-{kind}-{stamp}.json"
        out.write_text(json.dumps({
            "written_at": datetime.now(timezone.utc).isoformat(),
            "dry_run": not args.execute,
            "effective_date": result["effective_date"], "cutoff": result["cutoff"],
            "grace_days": 1, "source": SOURCE_BACKFILL,
            "summary": {k: result[k] for k in ("managers", "eligible", "honored", "partially_honored", "broken",
                                                "rescheduled_skipped", "grace_period_skipped", "failed",
                                                "orphan_agent_overdue_skipped")},
            "rows": _manifest_rows(result),
        }, indent=1), encoding="utf-8")
        print(f"\nmanifest -> {out}")
        if args.execute:
            print(f"undo -> python -m scripts.ptp_lifecycle_backfill --undo {out}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
