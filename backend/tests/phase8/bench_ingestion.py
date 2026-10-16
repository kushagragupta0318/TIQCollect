"""§5 — ingestion throughput on real PostgreSQL. A measurement script, not a test.

RUN:
    docker compose exec -T api sh -lc \
      'cd /app && PHASE8_DB=1 python tests/phase8/bench_ingestion.py 100 500 2000'

WHAT IT MEASURES. The SUCCESSFUL path through the real engine
(scripts.ingest_daily.run_ingestion) against the dedicated fieldops_phase8
database: parse + customer/loan/case upserts + batch bookkeeping, end to end,
as the nightly run would do it.

HONESTY NOTES.
  * Synthetic borrowers, generated here. Masked identifier columns carry
    obvious placeholder tokens; no real PII is used or printed.
  * Rows are unique per run, so every run measures the INSERT path (the worst
    case). A real nightly feed is mostly UPDATEs of existing loans, which is a
    different and usually cheaper shape — stated rather than extrapolated.
  * Query counts come from a SQLAlchemy event hook, so "queries per row" is
    counted, not estimated.
  * The algorithm is NOT modified to improve these numbers (Phase 8 forbids
    it). Bottlenecks are reported for a separate decision.
"""
from __future__ import annotations

import gc
import os
import statistics
import sys
import tempfile
import time
import uuid
from datetime import date
from pathlib import Path

HEADER = ("customer_ref,customer_name,loan_account_number,case_number,branch_code,"
          "loan_type,dpd,total_outstanding,overdue_amount,emi_amount,"
          "sanctioned_amount,disbursed_amount,outstanding_principal,"
          "disbursement_date,maturity_date,interest_rate,phone_primary,"
          "address_line1,city,state,pincode,dob,gender,latitude,longitude,"
          "pan_masked,aadhaar_masked\n")


def make_csv(n: int, tag: str, branch_code: str) -> str:
    """n synthetic borrowers. Masked columns are placeholder tokens only."""
    out = [HEADER]
    for i in range(n):
        ref = f"B{tag}{i:06d}"
        out.append(
            f"{ref},Synthetic Borrower {i},LN{tag}{i:06d},CS{tag}{i:06d},{branch_code},"
            f"PERSONAL,{30 + (i % 120)},{100000 + i},{5000 + i},3000,"
            f"200000,200000,150000,2024-02-01,2027-02-01,14.25,"
            f"9{(8000000000 + i) % 1000000000:09d},Addr {i},Gurugram,Haryana,122018,"
            f"1988-06-14,MALE,28.{400 + (i % 500):03d},77.{100 + (i % 500):03d},"
            f"XXXXX0000X,XXXXXXXX0000\n"
        )
    return "".join(out)


def _mem_mb() -> float | None:
    try:
        import resource
        # ru_maxrss is KB on Linux.
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except Exception:
        return None


def run_one(n: int, Session, tenant, *, count_queries: bool = True) -> dict:
    from sqlalchemy import event
    from scripts import ingest_daily as ing
    from scripts.ingest_daily import run_ingestion

    tag = uuid.uuid4().hex[:6]
    csv = make_csv(n, tag, tenant["branch_code"])
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w") as fh:
        fh.write(csv)

    queries = {"n": 0}
    engine = Session.kw["bind"]

    def _count(conn, cursor, statement, params, context, executemany):
        queries["n"] += 1

    if count_queries:
        event.listen(engine, "before_cursor_execute", _count)

    original = ing.SessionLocal
    ing.SessionLocal = Session
    gc.collect()
    mem_before = _mem_mb()
    t0 = time.perf_counter()
    try:
        result = run_ingestion(Path(path), dry_run=False, bank_code=tenant["bank_code"],
                               agency_code=tenant["agency_code"])
    finally:
        elapsed = time.perf_counter() - t0
        ing.SessionLocal = original
        if count_queries:
            event.remove(engine, "before_cursor_execute", _count)
        os.unlink(path)

    mem_after = _mem_mb()
    return {
        "rows": n,
        "seconds": elapsed,
        "rows_per_sec": n / elapsed if elapsed else 0.0,
        "queries": queries["n"],
        "queries_per_row": queries["n"] / n if n else 0.0,
        "rows_read": result.rows_read,
        "rows_skipped": result.rows_skipped,
        "rows_quarantined": result.rows_quarantined,
        "cases_inserted": result.cases_inserted,
        "loans_inserted": getattr(result, "loans_inserted", None),
        "mem_delta_mb": (mem_after - mem_before) if (mem_before and mem_after) else None,
        "mem_peak_mb": mem_after,
    }


def main(sizes: list[int], repeats: int = 3) -> int:
    os.environ.setdefault("PHASE8_DB", "1")
    # The engine logs a full per-run report at INFO; it would bury the table.
    import logging
    logging.getLogger().setLevel(logging.WARNING)
    for name in ("scripts.ingest_daily", "ingest_daily", "root"):
        logging.getLogger(name).setLevel(logging.WARNING)
    from sqlalchemy import create_engine, func, insert, select, text
    from sqlalchemy.orm import sessionmaker

    from app.core.config import settings
    from app.core.security import hash_password
    import app.models  # noqa: F401
    from app.models.lookups import LOOKUP_MODELS, LOOKUP_SEEDS
    from app.models.tenancy import Agency, Bank, Branch
    from app.models.user import User, UserRole

    dsn = settings.DATABASE_URL.rsplit("/", 1)[0] + "/fieldops_phase8"
    assert dsn.endswith("fieldops_phase8"), f"refusing to benchmark against {dsn}"
    engine = create_engine(dsn, future=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    with engine.begin() as conn:
        for table, rows in LOOKUP_SEEDS.items():
            model = LOOKUP_MODELS[table]
            if conn.execute(select(func.count()).select_from(model.__table__)).scalar():
                continue
            conn.execute(insert(model.__table__), rows)

    tag = uuid.uuid4().hex[:8]
    bank_id, agency_id = str(uuid.uuid4()), str(uuid.uuid4())
    s = Session()
    s.add(Bank(id=bank_id, code=f"BM{tag[:6].upper()}", legal_name=f"Bench Bank {tag}",
               display_name="Bench", timezone="Asia/Kolkata", brand={}, status="ACTIVE",
               is_demo=True))
    s.flush()
    s.add_all([
        Agency(id=agency_id, bank_id=bank_id, code=f"AGY-{tag[:6].upper()}",
               legal_name="Bench Agency", status="ACTIVE", contacts=[], is_demo=True),
        Branch(id=str(uuid.uuid4()), bank_id=bank_id, branch_code=f"BB{tag[:4].upper()}",
               name="Bench Branch", is_active=True),
        User(id=str(uuid.uuid4()), email=f"bench.{tag}@example.test",
             phone=f"9{int(tag[:6], 16) % 1000000000:09d}", full_name="Bench Admin",
             hashed_password=hash_password("Harbour-Lights-2026"), role=UserRole.BANK_ADMIN,
             bank_id=bank_id, agency_id=None, is_active=True),
    ])
    s.commit()

    # CONTRACT + REGION COVERAGE. Without these, placement is refused and every
    # row is QUARANTINED — which measures the quarantine path, not the
    # successful one this benchmark is supposed to time. The agency must be
    # able to take the loans for the numbers below to mean anything.
    from app.models.tenancy import AgencyContract, AgencyRegion
    from tests._placement import region_tree

    regions = region_tree(s, bank_id=bank_id)
    contract = AgencyContract(id=str(uuid.uuid4()), bank_id=bank_id, agency_id=agency_id,
                              contract_no=f"C/{tag}", status="ACTIVE",
                              start_date=date(2026, 4, 1), end_date=date(2027, 3, 31),
                              max_placed_cases=None, sla_first_visit_days=5)
    s.add(contract)
    s.flush()
    s.add(AgencyRegion(id=str(uuid.uuid4()), bank_id=bank_id, agency_id=agency_id,
                       contract_id=contract.id, region_id=regions["NORTH"]))
    s.execute(text("update tenancy.branches set region_id=:r where bank_id=:b"),
              {"r": regions["GGN"], "b": bank_id})
    s.commit()

    branch_code = s.execute(text("select branch_code from tenancy.branches where bank_id=:b"),
                            {"b": bank_id}).scalar()
    s.close()
    tenant = {"bank_id": bank_id, "bank_code": f"BM{tag[:6].upper()}",
              "agency_code": f"AGY-{tag[:6].upper()}", "branch_code": branch_code}

    print("=" * 74)
    print("INGESTION BENCHMARK — real PostgreSQL (fieldops_phase8), synthetic borrowers")
    print(f"repeats per size: {repeats}   (all rows NEW: the INSERT path, worst case)")
    print("=" * 74)
    print(f"{'rows':>7} {'median s':>9} {'min s':>8} {'max s':>8} {'rows/s':>9} "
          f"{'q/row':>7} {'skip':>5} {'quar':>5} {'peakMB':>7}")

    all_results: dict[int, list[dict]] = {}
    for n in sizes:
        runs = [run_one(n, Session, tenant) for _ in range(repeats)]
        all_results[n] = runs
        secs = sorted(r["seconds"] for r in runs)
        med = statistics.median(secs)
        print(f"{n:>7} {med:>9.2f} {secs[0]:>8.2f} {secs[-1]:>8.2f} "
              f"{n / med:>9.1f} {runs[0]['queries_per_row']:>7.1f} "
              f"{runs[0]['rows_skipped']:>5} {runs[0]['rows_quarantined']:>5} "
              f"{runs[-1]['mem_peak_mb'] or 0:>7.0f}")

    # Projection, with the assumption named rather than hidden.
    print("-" * 74)
    biggest = max(sizes)
    med_big = statistics.median([r["seconds"] for r in all_results[biggest]])
    per_row = med_big / biggest
    print(f"Measured cost per row at n={biggest}: {per_row * 1000:.1f} ms/row (median)")
    for volume in (10_000, 50_000, 100_000):
        print(f"  ASSUMING linear scaling (unverified above {biggest} rows): "
              f"{volume:>7} rows -> {volume * per_row / 60:.1f} min")
    print(f"The 19:30->19:45 window is 15 min = 900 s; at {per_row * 1000:.1f} ms/row that is "
          f"~{int(900 / per_row):,} rows.")
    print("-" * 74)

    # Cleanup: this benchmark's own tenant only.
    s = Session()
    for sql in ("delete from collections.cases where bank_id=:b",
                "delete from collections.placements where bank_id=:b",
                "delete from lending.bank_feed_rows where bank_id=:b",
                "delete from lending.bank_feed_batches where bank_id=:b",
                "delete from ml.repayment_score_snapshots where bank_id=:b",
                "delete from lending.loans where bank_id=:b",
                "delete from lending.customers where bank_id=:b",
                "delete from tenancy.agency_regions where bank_id=:b",
                "delete from tenancy.agency_contract_terms where bank_id=:b",
                "delete from tenancy.agency_contracts where bank_id=:b",
                "delete from tenancy.regions where bank_id=:b",
                "delete from tenancy.branches where bank_id=:b",
                "delete from tenancy.agencies where bank_id=:b",
                "delete from audit.audit_logs where bank_id=:b",
                "delete from tenancy.users where bank_id=:b",
                "delete from tenancy.banks where id=:b"):
        try:
            s.execute(text(sql), {"b": bank_id})
            s.commit()
        except Exception:
            s.rollback()
    s.close()
    print("benchmark tenant cleaned up")
    return 0


if __name__ == "__main__":
    sizes = [int(a) for a in sys.argv[1:]] or [100, 500]
    raise SystemExit(main(sizes))
