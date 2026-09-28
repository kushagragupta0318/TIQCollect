# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-28 (B15) — NEW. The v1 → v2 transform (docs/DATA-MODEL-V2.md §9.2).
#   Reads a v1 database restored from fixtures/fieldops-demo.dump and writes an
#   EMPTY v2 database at the v2 head, under the final demo roster (Appendix C):
#   the v1 lender becomes Girivan Finance Ltd, the v1 agency becomes Aravalli
#   Field Services Pvt. Ltd. with every v1 row it owned, and three bank users
#   are added. The owner's target: a v2 fixture the dev stack can boot, with
#   a bank user able to log in.
#
#   What it does NOT yet do (recorded, not hidden; each is its own task):
#     - the other eight Girivan agencies, Kumaon Finance and Almora (B16-B18,
#       the generator: d4);
#     - beat_stops, attendance, case_assignments, escalations, bank_actions,
#       loan_dpd_history and loan_instalments (design §9.2 steps 5-13). The
#       app still reads beats.ordered_case_ids, and every one of those tables
#       starts empty, as it would on a fresh v2 install;
#     - agent_devices: v1's users.registered_device_fingerprint is dropped, so
#       agents bind their device again on first login (DEMO_DEVICE_REBIND in
#       dev);
#     - the analytics refresh and the §9.6 gates (B19-B21).
# ────────────────────────────────────────────────────────────────────────────
"""v1 → v2 transform (B15).

    V1_DATABASE_URL=postgresql+psycopg2://…/fieldops_v1src \\
    DATABASE_URL=postgresql+psycopg2://…/<empty v2 database at head> \\
    python -m scripts.migrate_v1_to_v2

Rules (design §9.2): refuses a non-empty target; every v1 id is kept as the
same UUID; new rows get deterministic uuid5 ids; historical timestamps are
copied verbatim; one transaction for the whole book, with a row count checked
after every table; aborts on any value it cannot convert exactly (no
truncation, no silent default).

Every password is UNUSABLE (core.security.disabled_password_hash): the
master login (scripts.apply_demo_logins) sets the three demo accounts' at
boot. The fixture holds no usable hash.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal

import sqlalchemy as sa
from sqlalchemy import create_engine, text

import app.models  # noqa: F401 — registers every model on the metadata
from app.core.database import Base
from app.core.security import disabled_password_hash
from app.models.lookups import LOOKUP_MODELS, LOOKUP_SEEDS
from app.models.loan import DPDBucket, LoanType

# ── The roster (Appendix C) ─────────────────────────────────────────────────
# 2026-09-28 (B16, d4): every roster constant moved VERBATIM to
# app/demo/roster.py, the one roster file (owner: all names in one place).
# Same keys, same uuid5 namespace, so every id this transform writes is
# unchanged (tests/test_demo_roster.py pins them). Only the v1 mapping stays.
from app.demo import roster
from app.demo.roster import (  # noqa: E402,F401 — re-exported for the tests and callers
    AGENCY, AGENCY_DOMAIN, BANK, BANK_DOMAIN, BANK_USERS, COMMISSION, CONTRACT, MASTER_ACCOUNTS,
    NAMESPACE_TIQ_V2, REGIONS, V1_STAFF, new_id,
)


class TransformError(Exception):
    pass


V1_BANK_NAMES = {"ABC Bank"}            # the only lender v1 knows; anything else aborts
V1_AGENCY_CODES = {"AGENCY-TIQ-001"}    # the only agency v1 knows; anything else aborts


def email_for(full_name: str, domain: str) -> str:
    """The roster's rule (roster.email_for); an underivable name aborts the transform."""
    try:
        return roster.email_for(full_name, domain)
    except roster.RosterError as exc:
        raise TransformError(str(exc)) from exc


# ── Value conversion (design §9.4) ──────────────────────────────────────────
@dataclass
class Report:
    rows: Counter = field(default_factory=Counter)
    money_changed: Counter = field(default_factory=Counter)


def _uuid(v):
    if v is None:
        return None
    try:
        return str(uuid.UUID(str(v)))
    except ValueError as exc:
        raise TransformError(f"not a UUID: {v!r}") from exc


def _date(v, col):
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v)
    try:
        if len(s) != 10:
            raise ValueError
        return date.fromisoformat(s)
    except ValueError:
        raise TransformError(f"{col}: not an ISO date: {v!r}") from None


def _datetime(v):
    if v is None:
        return None
    if isinstance(v, str):
        v = datetime.fromisoformat(v)
    return v if v.tzinfo is not None else v.replace(tzinfo=timezone.utc)


def _money(v, col, report):
    if v is None:
        return None
    d = Decimal(repr(float(v)))
    q = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    if abs(q - d) >= Decimal("0.005"):
        raise TransformError(f"{col}: {v!r} is not a paise amount")
    if q != d:
        report.money_changed[col] += 1
    return q


def _json(v):
    if isinstance(v, str):
        return json.loads(v)
    return v


def convert(v, column: sa.Column, report: Report):
    t = column.type
    name = f"{column.table.name}.{column.name}"
    if isinstance(t, sa.Uuid):
        return _uuid(v)
    if isinstance(t, sa.Date) and not isinstance(t, sa.DateTime):
        return _date(v, name)
    if isinstance(t, sa.DateTime):
        return _datetime(v)
    if isinstance(t, sa.Numeric) and not isinstance(t, sa.Float):
        return _money(v, name, report)
    if isinstance(t, sa.JSON) or t.__class__.__name__ in ("JSONB", "JSON"):
        return _json(v)
    if isinstance(t, sa.Enum) and v is not None:
        s = getattr(v, "value", v)
        if t.enums and s not in t.enums:
            raise TransformError(f"{name}: {s!r} is not a v2 value ({t.enums})")
        return s
    return v


# ── The copier ──────────────────────────────────────────────────────────────
class Transform:
    def __init__(self, src, dst):
        self.src, self.dst = src, dst
        self.report = Report()
        self.tables = {t.name: t for t in Base.metadata.sorted_tables}

    def v1(self, sql: str, **params):
        return [dict(r._mapping) for r in self.src.execute(text(sql), params)]

    def write(self, table_name: str, rows: list[dict]):
        table = self.tables[table_name]
        if rows:
            for i in range(0, len(rows), 2000):
                self.dst.execute(table.insert(), rows[i:i + 2000])
        got = self.dst.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()
        self.report.rows[table_name] = got
        return got

    def copy(self, v1_table: str, *, v2_table: str | None = None, rule=None, where: str = "",
             expect: int | None = None, tenant: dict | None = None):
        """Every v2 column from the same-named v1 column (converted), then
        the tenant columns, then `rule(row) -> {col: value}` overrides."""
        table = self.tables[v2_table or v1_table]
        tenant = tenant if tenant is not None else {"bank_id": BANK["id"], "agency_id": AGENCY["id"]}
        src_rows = self.v1(f"SELECT * FROM public.{v1_table} {where}")
        out = []
        for r in src_rows:
            row = {}
            overrides = rule(r) if rule is not None else {}
            for col in table.columns:
                if col.name in overrides:
                    continue                      # the rule owns this column
                if col.name in tenant:
                    # The tenant wins over any v1 value: v1's agency_id was a
                    # text code ("AGENCY-TIQ-001"), the one agency there was.
                    if col.name == "agency_id" and r.get("agency_id") not in (None, "", *V1_AGENCY_CODES):
                        raise TransformError(f"{table.name}: unknown v1 agency {r.get('agency_id')!r}")
                    row[col.name] = tenant[col.name]
                elif col.name in r:
                    row[col.name] = convert(r[col.name], col, self.report)
            for k, v in overrides.items():
                row[k] = convert(v, table.c[k], self.report) if k in table.c else v
            out.append({k: v for k, v in row.items() if k in table.c})
        got = self.write(table.name, out)
        want = len(src_rows) if expect is None else expect
        if got != want:
            raise TransformError(f"{table.name}: wrote {got}, expected {want}")
        return src_rows


def target_is_empty(dst) -> bool:
    return dst.execute(text("SELECT count(*) FROM tenancy.banks")).scalar_one() == 0


def run(src, dst) -> Report:
    if not target_is_empty(dst):
        raise TransformError("the target database is not empty (tenancy.banks has rows); refusing")
    T = Transform(src, dst)

    # 1. lookups (no seeding migration exists yet; the rows come from the one
    #    definition in models/lookups), then the tenant.
    for name, rows in LOOKUP_SEEDS.items():
        T.write(LOOKUP_MODELS[name].__tablename__, [dict(r) for r in rows])
    T.write("banks", [BANK])
    region_ids = {}
    rows = []
    for level, code, name, parent, lat, lon in REGIONS:
        rid = new_id("region", code)
        region_ids[code] = rid
        path = code if parent is None else f"{rows[[r['code'] for r in rows].index(parent)]['path']}.{code}"
        rows.append(dict(id=rid, bank_id=BANK["id"], parent_id=region_ids.get(parent), level=level, code=code,
                         name=name, path=path, latitude=lat, longitude=lon, is_active=True))
    T.write("regions", rows)

    loans = T.v1("SELECT DISTINCT bank_name, branch_code FROM public.loans")
    foreign = sorted({l["bank_name"] for l in loans} - V1_BANK_NAMES)
    if foreign:
        raise TransformError(f"loans.bank_name holds lenders the roster does not map: {foreign}")
    city_of = {"GGN": "GURUGRAM", "DEL": "DELHI", "DL": "DELHI", "NOI": "NOIDA", "NDA": "NOIDA"}
    branches = []
    for code in sorted({l["branch_code"] for l in loans if l["branch_code"]}):
        city = next((c for p, c in city_of.items() if code.upper().startswith(p)), "GURUGRAM")
        branches.append(dict(id=new_id("branch", code), bank_id=BANK["id"], region_id=region_ids[city],
                             branch_code=code, name=f"{city.title()} {code}", is_active=True))
    T.write("branches", branches)
    T.write("agencies", [AGENCY])
    T.write("agency_contracts", [CONTRACT])
    T.write("agency_contract_terms", [
        dict(id=new_id("term", f"{lt.value}:{b.value}"), bank_id=BANK["id"], agency_id=AGENCY["id"],
             contract_id=CONTRACT["id"], loan_type=lt.value, dpd_bucket=b.value,
             commission_pct=Decimal(COMMISSION[b.value]), is_authorised=True)
        for lt in LoanType for b in DPDBucket])
    T.write("agency_regions", [dict(id=new_id("agency_region", "ARAVALLI:NCR"), bank_id=BANK["id"],
                                    agency_id=AGENCY["id"], contract_id=CONTRACT["id"],
                                    region_id=region_ids["NCR"])])

    # 2. users: 21 v1 + 3 bank users. Every hash unusable.
    unusable = disabled_password_hash
    emails: set[str] = set()

    def user_rule(r):
        staff = V1_STAFF.get(r["email"])
        if staff:
            local, name, role = staff
            email = f"{local}@{AGENCY_DOMAIN}"
        elif r["role"] == "FIELD_AGENT":
            name, role = r["full_name"], "FIELD_AGENT"
            email = email_for(name, AGENCY_DOMAIN)
        else:
            raise TransformError(f"v1 user {r['email']!r} ({r['role']}) has no roster mapping")
        if email in emails:
            raise TransformError(f"two users map to {email}")
        emails.add(email)
        return dict(email=email, full_name=name, role=role, hashed_password=unusable(),
                    bank_id=BANK["id"], agency_id=AGENCY["id"], must_change_password=False,
                    is_active=r["is_active"], failed_login_attempts=0, locked_until=None)
    T.copy("users", rule=user_rule, tenant={})
    bank_rows = [dict(id=new_id("user", local), email=f"{local}@{BANK_DOMAIN}", phone=phone, full_name=name,
                      role=role, hashed_password=unusable(), bank_id=BANK["id"], agency_id=None,
                      is_active=True, is_verified=True, must_change_password=False, totp_enabled=False,
                      failed_login_attempts=0)
                 for local, name, role, phone in BANK_USERS]
    T.dst.execute(T.tables["users"].insert(), bank_rows)
    T.report.rows["users"] += len(bank_rows)

    # 3-4. lending
    T.copy("customers", tenant={"bank_id": BANK["id"]})
    T.copy("loans", tenant={"bank_id": BANK["id"]})

    # 5. workforce
    T.copy("agents")
    def month_rule(r):
        # v1 stored the month as "YYYY-MM"; v2 is the first day of it (a DATE).
        m = str(r["month"])
        if len(m) != 7 or m[4] != "-":
            raise TransformError(f"agent_performance.month: {m!r} is not YYYY-MM")
        return {"month": date(int(m[:4]), int(m[5:]), 1)}
    T.copy("agent_performance", rule=month_rule)
    T.copy("leave_requests")

    # 6. placements (one per loan that has a case), then cases.
    first_case = {r["loan_id"]: r for r in T.v1(
        "SELECT DISTINCT ON (c.loan_id) c.loan_id, c.allocation_date, c.created_at, l.dpd, l.dpd_bucket, "
        "l.total_outstanding, l.overdue_amount FROM public.cases c JOIN public.loans l ON l.id = c.loan_id "
        "ORDER BY c.loan_id, c.created_at")}
    placements = []
    for loan_id, r in first_case.items():
        placed_on = _date(r["allocation_date"], "cases.allocation_date") or r["created_at"].date()
        placements.append(dict(
            id=new_id("placement", loan_id), bank_id=BANK["id"], agency_id=AGENCY["id"], loan_id=loan_id,
            contract_id=CONTRACT["id"], source="TRANSFORM", status="ACTIVE", placed_on=placed_on,
            dpd_at_placement=int(r["dpd"] or 0), dpd_bucket_at_placement=r["dpd_bucket"],
            exposure_at_placement=_money(r["total_outstanding"] or 0, "placements.exposure", T.report),
            overdue_at_placement=_money(r["overdue_amount"] or 0, "placements.overdue", T.report)))
    T.write("placements", placements)
    T.copy("cases", rule=lambda r: {"placement_id": new_id("placement", r["loan_id"])})

    # 7. planning settings and runs
    T.copy("allocation_settings")
    runs = {r["id"]: r for r in T.copy("allocation_runs")}

    # 8. ml
    preds = {r["id"]: r["as_of_date"] for r in T.v1("SELECT id, as_of_date FROM public.model_predictions")}
    T.copy("model_predictions")
    T.copy("repayment_score_snapshots", tenant={"bank_id": BANK["id"]})
    T.copy("model_candidates", tenant={})

    # 9. decisions (plan_date from the run; the prediction's as-of date)
    def decision_rule(r):
        run_ = runs.get(r["run_id"])
        if run_ is None:
            raise TransformError(f"allocation_decisions {r['id']}: unknown run {r['run_id']}")
        pid = r.get("model_prediction_id")
        return {"plan_date": run_["plan_date"], "model_prediction_as_of": preds.get(pid) if pid else None}
    T.copy("allocation_decisions", rule=decision_rule)

    # 10. beats
    T.copy("beats")

    # 11. field activity
    loan_of_case = {r["id"]: r["loan_id"] for r in T.v1("SELECT id, loan_id FROM public.cases")}
    T.copy("visits")
    T.copy("payments", rule=lambda r: {"loan_id": loan_of_case[r["case_id"]]})
    T.copy("ptps")
    T.copy("call_logs")
    T.copy("fraud_reviews")

    # 12. trails
    T.copy("agent_locations")
    T.copy("audit_logs")
    return T.report


def main() -> int:
    src_url, dst_url = os.environ.get("V1_DATABASE_URL"), os.environ.get("DATABASE_URL")
    if not src_url or not dst_url or src_url == dst_url:
        print("set V1_DATABASE_URL (the v1 source) and DATABASE_URL (an empty v2 target); they must differ",
              file=sys.stderr)
        return 2
    src_engine, dst_engine = create_engine(src_url), create_engine(dst_url)
    try:
        with src_engine.connect() as src, dst_engine.begin() as dst:
            report = run(src, dst)
    except TransformError as exc:
        print(f"[migrate_v1_to_v2] ABORTED, nothing written: {exc}", file=sys.stderr)
        return 1
    for name, n in sorted(report.rows.items()):
        print(f"[migrate_v1_to_v2] {name:32s} {n:>7d}")
    for col, n in sorted(report.money_changed.items()):
        print(f"[migrate_v1_to_v2] money rounded to paise: {col} x{n}")
    print(f"[migrate_v1_to_v2] master login accounts: {','.join(MASTER_ACCOUNTS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
