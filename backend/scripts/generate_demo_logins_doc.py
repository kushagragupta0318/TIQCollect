# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-30 (lane L6, owner's request via tiqcollect-06) — NEW. The demo's
#   credentials doc: exactly the accounts apply_agency_staff() would give the
#   shared password (its staff_targets(), the one definition — this script
#   never invents its own list), grouped by agency, each row carrying a real
#   performance figure so the doc doubles as "log in as X, see Y" for a
#   client demo. Read-only; never prints or writes the password itself.
#
#   Agency headlines and per-agent figures are computed here with the same
#   "an unread row is excluded from BOTH sides of a ratio, never zeroed"
#   rule as app/services/bank/agency_scorecard.compute_metrics (independent
#   implementation, plain SQL: that module needs an RLS-bound session this
#   script has no reason to set up). A figure that cannot be computed says
#   so in the doc; it is never guessed or invented.
# ────────────────────────────────────────────────────────────────────────────
"""Write docs/DEMO-LOGINS.md from the live database.

    DATABASE_URL=... python -m scripts.generate_demo_logins_doc
"""
from __future__ import annotations

import argparse
import sys
from datetime import date

import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from scripts.apply_demo_logins import DEFAULT_AGENTS_PER_AGENCY, staff_targets

OUT_DEFAULT = "docs/DEMO-LOGINS.md"


def _agency_headline(conn, *, bank_id: str, agency_id: str) -> dict | None:
    """The agency's most recent month, summed across its rows, direct from
    the mv, with collection_efficiency excluding an unread row from both
    sides (agency_scorecard.compute_metrics' rule) rather than the whole
    thing. The mv's grain is (month, bank, agency, REGION): an agency with
    borrowers in several regions has several rows per month, so this must
    SUM them, never pick one — the first cut of this script did LIMIT 1 and
    reported 0 for every generated agency, caught before it shipped."""
    row = conn.execute(sa.text("""
        SELECT month_start,
               sum(verified_collections) FILTER (WHERE collectible_due IS NOT NULL) AS collected_where_due_known,
               sum(collectible_due) AS collectible_due, sum(verified_collections) AS verified_collections,
               sum(visits) AS visits, sum(met_visits) AS met_visits, sum(ptps_matured) AS ptps_matured,
               sum(ptps_honoured) AS ptps_honoured, sum(active_placements_eom) AS active_placements_eom,
               sum(agents_active) AS agents_active
        FROM analytics.mv_agency_scorecard_monthly
        WHERE bank_id = :b AND agency_id = :a
        GROUP BY month_start ORDER BY month_start DESC LIMIT 1
    """), {"b": bank_id, "a": agency_id}).mappings().first()
    if row is None:
        return None
    d = dict(row)
    d["collection_efficiency"] = (None if not d["collectible_due"]
                                  else round(float(d["collected_where_due_known"] or 0) / float(d["collectible_due"]), 4))
    d["met_rate"] = None if not d["visits"] else round(d["met_visits"] / d["visits"], 4)
    d["ptp_honour_rate"] = None if not d["ptps_matured"] else round(d["ptps_honoured"] / d["ptps_matured"], 4)
    return d


def _agent_stats(conn, bank_id: str) -> dict[str, dict]:
    """Every agent's real visit/collection/PTP figures, computed from the
    ledger tables directly (workforce.agents' own summary columns are an
    unfilled placeholder, 0, for every generated agency: fixtures/README).
    Pre-aggregated per table before joining — a straight three-way join on
    agent_id fans out (visits x payments x ptps per agent) before GROUP BY
    can collapse it; caught once already this session, not repeating it."""
    rows = conn.execute(sa.text("""
        WITH vis AS (
            SELECT agent_id, count(*) visits, count(*) FILTER (WHERE customer_met) met_visits
            FROM collections.visits WHERE bank_id = :b GROUP BY agent_id
        ), pay AS (
            SELECT agent_id, sum(amount) collected FROM collections.payments
            WHERE bank_id = :b AND status = 'VERIFIED' AND agent_id IS NOT NULL GROUP BY agent_id
        ), ptp AS (
            SELECT agent_id, count(*) FILTER (WHERE status IN ('ACTIVE', 'HONORED', 'PARTIALLY_HONORED')) ptps_live,
                   count(*) FILTER (WHERE status IN ('HONORED', 'PARTIALLY_HONORED')) ptps_kept
            FROM collections.ptps WHERE bank_id = :b GROUP BY agent_id
        )
        SELECT g.id AS agent_id, g.user_id, coalesce(vis.visits, 0) visits, coalesce(vis.met_visits, 0) met_visits,
               coalesce(pay.collected, 0) collected, coalesce(ptp.ptps_live, 0) ptps_live,
               coalesce(ptp.ptps_kept, 0) ptps_kept
        FROM workforce.agents g
        LEFT JOIN vis ON vis.agent_id = g.id LEFT JOIN pay ON pay.agent_id = g.id LEFT JOIN ptp ON ptp.agent_id = g.id
        WHERE g.bank_id = :b
    """), {"b": bank_id}).mappings().all()
    # str(): psycopg2 hands back a native uuid.UUID for a uuid column, but
    # User.id (looked up against this) is a plain str throughout the ORM —
    # an unstrung key made every lookup miss and every agent show "—",
    # caught before it shipped.
    return {str(r["user_id"]): dict(r) for r in rows}


def _pct(x: float | None) -> str:
    return "not available" if x is None else f"{x * 100:.1f}%"


def _money(x) -> str:
    x = float(x or 0)
    return f"₹{x / 1e7:,.2f} Cr" if abs(x) >= 1e7 else f"₹{x / 1e5:,.2f} L" if abs(x) >= 1e5 else f"₹{x:,.0f}"


def build_doc(db: Session, *, bank_code: str, agents_per_agency: int) -> str:
    from app.models.tenancy import Agency, Bank

    conn = db.connection()
    bank = db.query(Bank).filter(Bank.code == bank_code).one_or_none()
    if bank is None:
        raise SystemExit(f"no bank with code {bank_code!r}")

    targets, n_agencies = staff_targets(db, bank_code=bank_code, agents_per_agency=agents_per_agency)
    by_id = {u.id: u for u in targets}
    agent_stats = _agent_stats(conn, bank.id)

    bank_users = sorted((u for u in targets if u.agency_id is None), key=lambda u: (u.role.value, u.email.lower()))
    agencies = (db.query(Agency).filter(Agency.bank_id == bank.id, Agency.status == "ACTIVE")
               .order_by(Agency.code).all())

    lines = [
        "# TIQCollect demo logins",
        "",
        f"*Generated {date.today().isoformat()} from the rebuilt demo book "
        f"(`scripts/generate_demo_logins_doc.py`); every name, email and figure below is a real row in it.*",
        "",
        "**Every account below uses the same shared demo password — the email is the unique login id.** "
        "Ask the team for the password; it is never written here, in the repo, the UI or the logs.",
        "",
        f"Covers the bank's own users and {n_agencies} active agencies (their admin, every manager, and up "
        f"to {agents_per_agency} field agents each). A performance figure that cannot be computed says so, "
        "rather than guessing.",
        "",
        "## Bank",
        "",
        "| Role | Name | Email |",
        "|---|---|---|",
    ]
    for u in bank_users:
        lines.append(f"| {u.role.value} | {u.full_name} | `{u.email}` |")

    for a in agencies:
        h = _agency_headline(conn, bank_id=bank.id, agency_id=a.id)
        lines += ["", f"## {a.trade_name or a.legal_name} ({a.code})", ""]
        if h is None:
            lines.append("*No scorecard reading yet for this agency this month.*")
        else:
            lines.append(
                f"As of {h['month_start']}: collection efficiency **{_pct(h['collection_efficiency'])}**"
                + (" (no opening reading before the book's start — this agency's own history is incomplete, "
                   "not the demo's fault)" if h["collection_efficiency"] is None else "")
                + f", {_money(h['verified_collections'])} collected, {h['active_placements_eom']:,} active "
                  f"placements, {h['visits']:,} visits ({_pct(h['met_rate'])} met), "
                  f"{_pct(h['ptp_honour_rate'])} of {h['ptps_matured']:,} matured promises kept, "
                  f"{h['agents_active']:,} agents active this month."
            )
        lines += ["", "| Role | Name | Email | Performance (this book) |", "|---|---|---|---|"]
        staff = sorted((u for u in targets if u.agency_id == a.id),
                       key=lambda u: (u.role.value, u.full_name.lower()))
        # Field agents sorted by collections within the agency, so a reader
        # sees the strong-to-weak range among the accounts that can log in.
        admins_mgrs = [u for u in staff if u.role.value != "FIELD_AGENT"]
        field_agents = [u for u in staff if u.role.value == "FIELD_AGENT"]
        field_agents.sort(key=lambda u: -(agent_stats.get(str(u.id), {}).get("collected") or 0))
        for u in admins_mgrs + field_agents:
            st = agent_stats.get(str(u.id))
            if st is None:
                perf = "—"
            else:
                honour = "not available" if not st["ptps_live"] else f"{st['ptps_kept']}/{st['ptps_live']} PTPs kept"
                perf = f"{st['visits']} visits ({st['met_visits']} met), {_money(st['collected'])} collected, {honour}"
            lines.append(f"| {u.role.value} | {u.full_name} | `{u.email}` | {perf} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bank-code", default=settings.DEMO_STAFF_BANK_CODE or "GIRIVAN")
    ap.add_argument("--agents-per-agency", type=int,
                    default=int(settings.DEMO_STAFF_AGENTS_PER_AGENCY or DEFAULT_AGENTS_PER_AGENCY))
    ap.add_argument("--out", default=OUT_DEFAULT)
    args = ap.parse_args()

    engine = create_engine(settings.DATABASE_URL)
    with Session(engine) as db:
        doc = build_doc(db, bank_code=args.bank_code, agents_per_agency=args.agents_per_agency)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(doc)
    print(f"[demo-logins-doc] wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
