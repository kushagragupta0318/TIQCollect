"""Enrich the demo book with the data categories the generator never produced,
so the Tech Ops and Cost surfaces show real content instead of empty states.

Additive and IDEMPOTENT — each section is skipped if its data already exists, so
this is safe to run on every DEMO boot (the entrypoint does) and by hand. It
touches only demo tenants (banks.is_demo) and writes nothing a real deployment
would not also hold.

What it adds, and why each surface was empty without it:
  - strategy.cost_rates (FIELD_VISIT / PER_ATTEMPT): the agency scorecard's
    field_cost is the FIELD_VISIT rate applied per visit (mv_agency_scorecard_
    monthly). With no rate, field_cost is 0 and cost-per-₹100 is null, so the
    Cost tab showed only commission. One realistic rate per bank fills it.
  - lending.bank_feed_batches/rows: the Data Quality page reads the bank-feed
    staging + quarantine queue. The generated book was never fed by the file
    ingest, so every panel was empty. A handful of recent LOADED batches, most
    rows ACCEPTED and a few QUARANTINED with real reasons, fills Feed Freshness
    and the quarantine panels.
  - one duplicate customer phone and one out-of-range loan: the two structural
    checks the Data Quality page runs over the book — zero hits read as "the
    checks don't work", so seed exactly one of each.

Run:  python -m scripts.demo_enrich            # report what it would add
      python -m scripts.demo_enrich --apply
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text

QUARANTINE_REASONS = [
    ("MISSING_CUSTOMER_PHONE", "No contact number on the row"),
    ("INVALID_DPD", "DPD is negative"),
    ("UNKNOWN_BRANCH", "Branch code not in the bank's branch master"),
    ("NEGATIVE_OUTSTANDING", "Total outstanding is below zero"),
    ("DUPLICATE_LOAN_ACCOUNT", "Loan account already present earlier in the file"),
]


def _business_days(end: date, n: int) -> list[date]:
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def _demo_banks(conn) -> list[tuple[str, str]]:
    return [(str(r[0]), r[1]) for r in conn.execute(text(
        "SELECT id, display_name FROM tenancy.banks WHERE is_demo = true ORDER BY code")).all()]


def _bank_admin(conn, bank_id: str) -> str | None:
    # Prefer a bank admin for authorship; fall back to ANY user of the bank so
    # uploaded_by / created_by are always a valid FK (a demo bank may carry its
    # staff under other roles).
    r = conn.execute(text(
        "SELECT id FROM tenancy.users WHERE bank_id = :b "
        "ORDER BY (role IN ('BANK_ADMIN', 'AGENCY_ADMIN')) DESC, role LIMIT 1"), {"b": bank_id}).scalar()
    return str(r) if r else None


def _cost_rates(conn, bank_id: str, apply: bool) -> int:
    exists = conn.execute(text(
        "SELECT 1 FROM strategy.cost_rates WHERE bank_id = :b AND channel = 'FIELD_VISIT' AND unit = 'PER_ATTEMPT' LIMIT 1"),
        {"b": bank_id}).scalar()
    if exists:
        return 0
    if apply:
        conn.execute(text("""
            INSERT INTO strategy.cost_rates (id, bank_id, channel, unit, rate_inr, valid_from, valid_to, source, created_by, created_at)
            VALUES (gen_random_uuid(), :b, 'FIELD_VISIT', 'PER_ATTEMPT', :rate, DATE '2025-01-01', NULL, 'DEMO',
                    (SELECT id FROM tenancy.users WHERE bank_id = :b
                     ORDER BY (role IN ('BANK_ADMIN', 'AGENCY_ADMIN')) DESC LIMIT 1), now())
        """), {"b": bank_id, "rate": 165})
    return 1


def _feed(conn, bank_id: str, today: date, apply: bool) -> int:
    has = conn.execute(text("SELECT 1 FROM lending.bank_feed_batches WHERE bank_id = :b LIMIT 1"), {"b": bank_id}).scalar()
    if has:
        return 0
    if not apply:
        return 5
    rng = random.Random(f"feed-{bank_id}")
    accounts = [r[0] for r in conn.execute(text(
        "SELECT loan_account_number FROM lending.loans WHERE bank_id = :b ORDER BY id LIMIT 200"), {"b": bank_id}).all()]
    made = 0
    for i, bd in enumerate(_business_days(today, 5)):
        quarantined = rng.randint(2, 5) if i >= 3 else 0     # the two most recent days have a few bad rows
        accepted = rng.randint(40, 80)
        total = accepted + quarantined
        status = "PARTIAL" if quarantined else "LOADED"
        recv = datetime(bd.year, bd.month, bd.day, 6, 30, tzinfo=timezone.utc)
        batch_id = conn.execute(text("""
            INSERT INTO lending.bank_feed_batches
                (id, bank_id, feed_type, business_date, file_name, file_sha256, storage_key, received_via,
                 uploaded_by, status, rows_total, rows_accepted, rows_quarantined, rows_skipped, dq_report, received_at, loaded_at, created_at)
            VALUES (gen_random_uuid(), :b, 'DAILY_BOOK', :bd, :fn, :sha, :key, 'DEMO',
                 (SELECT id FROM tenancy.users WHERE bank_id = :b
                  ORDER BY (role IN ('BANK_ADMIN', 'AGENCY_ADMIN')) DESC LIMIT 1),
                 :st, :tot, :acc, :q, 0, :rep, :recv, :recv, now())
            RETURNING id
        """), {"b": bank_id, "bd": bd, "fn": f"daily_book_{bd:%Y%m%d}.csv",
               "sha": rng.getrandbits(256).to_bytes(32, "big").hex(), "key": f"feeds/{bank_id}/{bd:%Y%m%d}.csv",
               "st": status, "tot": total, "acc": accepted, "q": quarantined,
               "rep": json.dumps({"accepted": accepted, "quarantined": quarantined}), "recv": recv}).scalar()
        for n in range(quarantined):
            reason, detail = rng.choice(QUARANTINE_REASONS)
            acct = rng.choice(accounts) if accounts else f"DEMO{rng.randint(10000, 99999)}"
            conn.execute(text("""
                INSERT INTO lending.bank_feed_rows
                    (id, bank_id, batch_id, row_no, loan_account_number, customer_ref, case_number, raw, status, dq_errors, created_at)
                VALUES (gen_random_uuid(), :b, :bt, :rn, :acct, :cref, NULL, :raw, 'QUARANTINED', :errs, :cat)
            """), {"b": bank_id, "bt": batch_id, "rn": accepted + n + 1, "acct": acct,
                   "cref": f"CUST{rng.randint(10000, 99999)}", "raw": json.dumps({"loan_account_number": acct}),
                   "errs": json.dumps([{"reason": reason, "detail": detail}]), "cat": recv})
        made += 1
    return made


def _dup_phone(conn, bank_id: str, apply: bool) -> int:
    dup = conn.execute(text("""
        SELECT 1 FROM (SELECT phone_primary FROM lending.customers WHERE bank_id = :b AND phone_primary IS NOT NULL
                       GROUP BY phone_primary HAVING count(*) > 1 LIMIT 1) d"""), {"b": bank_id}).scalar()
    if dup:
        return 0
    pair = conn.execute(text(
        "SELECT id, phone_primary FROM lending.customers WHERE bank_id = :b AND phone_primary IS NOT NULL ORDER BY id LIMIT 2"),
        {"b": bank_id}).all()
    if len(pair) < 2:
        return 0
    if apply:
        conn.execute(text("UPDATE lending.customers SET phone_primary = :p WHERE id = :id"),
                     {"p": pair[0][1], "id": pair[1][0]})
    return 1


def _out_of_range(conn, bank_id: str, apply: bool) -> int:
    has = conn.execute(text(
        "SELECT 1 FROM lending.loans WHERE bank_id = :b AND overdue_amount > total_outstanding LIMIT 1"), {"b": bank_id}).scalar()
    if has:
        return 0
    loan = conn.execute(text(
        "SELECT id, total_outstanding FROM lending.loans WHERE bank_id = :b AND total_outstanding > 0 ORDER BY id LIMIT 1"),
        {"b": bank_id}).first()
    if not loan:
        return 0
    if apply:
        conn.execute(text("UPDATE lending.loans SET overdue_amount = :o WHERE id = :id"),
                     {"o": float(loan[1]) * 1.1 + 1, "id": loan[0]})
    return 1


# The LLM features the product actually calls (ai.llm_calls.feature), with a
# rough calls-per-day weight so the mix looks real: a daily briefing, a few
# agent insights, board/monthly commentary now and then.
USAGE_FEATURES = [("briefing", 1.0), ("agent_insight", 2.5), ("board_report_commentary", 0.3),
                  ("monthly_report", 0.1)]
# Sonnet-class per-1k-token prices (USD), so the cost totals are plausible.
_PRICE_IN, _PRICE_OUT = 0.003, 0.015


def _usage(conn, bank_id: str, today: date, apply: bool) -> int:
    recent = conn.execute(text(
        "SELECT count(*) FROM ai.llm_calls WHERE bank_id = :b AND created_at >= :since"),
        {"b": bank_id, "since": datetime.now(timezone.utc) - timedelta(days=30)}).scalar()
    if recent and recent >= 20:
        return 0
    if not apply:
        return 30 * 3        # rough estimate for the dry-run count
    rng = random.Random(f"usage-{bank_id}")
    made = 0
    for i in range(30):                                   # the last 30 days
        day = today - timedelta(days=i)
        for feature, weight in USAGE_FEATURES:
            n = 1 if rng.random() < weight else (2 if rng.random() < weight - 1 else 0)
            for _ in range(max(0, n)):
                inp = rng.randint(600, 3200)
                out = rng.randint(200, 1500)
                cost = round(inp / 1000 * _PRICE_IN + out / 1000 * _PRICE_OUT, 6)
                ts = datetime(day.year, day.month, day.day,
                              rng.randint(7, 20), rng.randint(0, 59), tzinfo=timezone.utc)
                conn.execute(text("""
                    INSERT INTO ai.llm_calls (id, bank_id, provider, model, feature,
                        input_tokens, output_tokens, cache_tokens, cost, created_at)
                    VALUES (gen_random_uuid(), :b, 'anthropic', 'claude-sonnet-4-5', :f, :i, :o, 0, :c, :ts)
                """), {"b": bank_id, "f": feature, "i": inp, "o": out, "c": cost, "ts": ts})
                made += 1
    return made


def run(engine, apply: bool) -> dict:
    today = datetime.now(timezone.utc).date()
    summary: dict = {"apply": apply, "banks": [], "cost_rates": 0, "feed_batches": 0,
                     "dup_phones": 0, "out_of_range": 0, "llm_calls": 0}
    with engine.begin() as conn:
        for bank_id, name in _demo_banks(conn):
            summary["cost_rates"] += _cost_rates(conn, bank_id, apply)
            summary["feed_batches"] += _feed(conn, bank_id, today, apply)
            summary["dup_phones"] += _dup_phone(conn, bank_id, apply)
            summary["out_of_range"] += _out_of_range(conn, bank_id, apply)
            summary["llm_calls"] += _usage(conn, bank_id, today, apply)
            summary["banks"].append(name)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write. Default is a dry run that reports what it would add.")
    parser.add_argument("--no-refresh", action="store_true", help="Skip refreshing the scorecard MV after an apply.")
    args = parser.parse_args(argv)

    from app.core.database import engine

    result = run(engine, args.apply)
    if args.apply and not args.no_refresh and result["cost_rates"]:
        from app.workers.tasks import analytics_refresh
        result["refresh"] = analytics_refresh.refresh_all(engine, wait_seconds=0)
    print(json.dumps(result, indent=2, default=str))
    if not args.apply:
        print("\n(dry run — nothing written; pass --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
