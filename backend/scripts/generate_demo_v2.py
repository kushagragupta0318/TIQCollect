# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-28 (B16-B18, d4) — NEW. The demo generator's driver. Runs AFTER
#   scripts/migrate_v1_to_v2.py on the same v2 database:
#       restore v1 → alembic upgrade head → migrate_v1_to_v2 → THIS → refresh
#       the analytics views → pg_dump  (backend/fixtures/README.md has the recipe)
#   Profiles (plan §4.7, sizes agreed with the coordinator 2026-09-28):
#       dev     3 agencies, <= 10 agents each — local runs and tests
#       demo    every roster agency at Aravalli's density — the committed fixture
#       stress  every agency, 6x the agents — scalability only, NEVER committed
#   The ground-truth manifest (latent quality, every agent's skill and gender,
#   every injected breach) is written to --manifest, never to a product table.
# ────────────────────────────────────────────────────────────────────────────
"""Generate the demo tenants and their books into a v2 database that already
holds B15's transformed Aravalli book.

    DATABASE_URL=… CONTACT_HOUR_START=8 CONTACT_HOUR_END=19 \\
    python -m scripts.generate_demo_v2 --profile demo --manifest fixtures/fieldops-demo-v2.truth.json
"""
from __future__ import annotations

import argparse
import calendar
import json
import sys
import time
from dataclasses import dataclass
from datetime import date

import sqlalchemy as sa
from sqlalchemy import create_engine

from app.core.config import settings
from app.demo import roster as R
from app.demo.books import generate_book, require_rbi_window
from app.demo.latent import AGENCY_LATENT, CONTACT_HOUR
from app.demo.world import T, build_world, insert


@dataclass(frozen=True)
class Profile:
    agencies: tuple
    slots_per_agent: float        # ledger borrower slots per agent (~2.1 placed loans each)
    agents_cap: int | None = None
    agents_scale: float = 1.0


PROFILES = {
    "dev": Profile(agencies=("ARAVALLI", "SAHYADRI", "DECCAN", "AWADH"), slots_per_agent=30, agents_cap=10),
    "demo": Profile(agencies=tuple(a.key for a in R.AGENCIES), slots_per_agent=38),
    "stress": Profile(agencies=tuple(a.key for a in R.AGENCIES), slots_per_agent=100, agents_scale=6.0),
}


def _add_months(d: date, k: int) -> date:
    m = d.month - 1 + k
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def aravalli_instalments(conn) -> int:
    """Coordinator Q4: Aravalli's schedules, derived deterministically from the
    loan terms (EMI x tenure from the disbursement date), marked GENERATED so
    no reader mistakes them for a bank feed. No history is invented."""
    lo = T["loans"]
    # Called before any generated book is written, so every Girivan loan in
    # the database is B15's, i.e. Aravalli's book.
    rows = conn.execute(sa.select(lo.c.id, lo.c.emi_amount, lo.c.tenure_months, lo.c.disbursement_date,
                                  lo.c.maturity_date).where(lo.c.bank_id == R.BANK["id"])).all()
    out = []
    for r in rows:
        tenure = r.tenure_months
        if not tenure:
            tenure = max(1, (r.maturity_date.year - r.disbursement_date.year) * 12
                         + r.maturity_date.month - r.disbursement_date.month)
        for i in range(1, int(tenure) + 1):
            out.append(dict(id=R.new_id("instalment", f"ARAVALLI:{r.id}:{i}"), bank_id=R.BANK["id"],
                            loan_id=r.id, instalment_no=i, due_date=_add_months(r.disbursement_date, i),
                            amount_due=r.emi_amount, source="GENERATED"))
    return insert(conn, "loan_instalments", out)


def rederive_aravalli_visit_flags(conn) -> dict:
    """DATA-R (coordinator Q2): v1 wrote within_contact_hours and geo_verified
    as TRUE on every visit — 792 of 2,400 were checked in outside 08-19 IST and
    400 more than 100 m from the borrower. Both flags are DERIVED columns, so
    they are re-derived here through core.geo, the two calls visit_service
    makes, from the event's own time and coordinates. The distance is
    recomputed from the same coordinates. Nothing else about a visit changes."""
    from app.core.geo import is_within_contact_hours, within_geo_fence
    vi, cu, ca = T["visits"], T["customers"], T["cases"]
    rows = conn.execute(sa.select(vi.c.id, vi.c.check_in_time, vi.c.check_in_latitude, vi.c.check_in_longitude,
                                  vi.c.within_contact_hours, vi.c.geo_verified, vi.c.distance_from_customer_metres,
                                  cu.c.latitude, cu.c.longitude)
                        .join(ca, ca.c.id == vi.c.case_id).join(cu, cu.c.id == ca.c.customer_id)
                        .where(vi.c.agency_id == R.AGENCY["id"])).all()
    before = {"visits": len(rows), "within_contact_hours_true": sum(bool(r.within_contact_hours) for r in rows),
              "geo_verified_true": sum(bool(r.geo_verified) for r in rows)}
    moved = 0
    after_hours = after_geo = 0
    for r in rows:
        hours_ok = is_within_contact_hours(r.check_in_time)
        dist, geo_ok = within_geo_fence(r.check_in_latitude, r.check_in_longitude, r.latitude, r.longitude)
        dist = round(dist, 1)
        if abs(float(r.distance_from_customer_metres) - dist) > 1.0:
            moved += 1
        after_hours += hours_ok
        after_geo += geo_ok
        if (hours_ok, geo_ok, dist) != (r.within_contact_hours, r.geo_verified, r.distance_from_customer_metres):
            conn.execute(vi.update().where(vi.c.id == r.id).values(
                within_contact_hours=hours_ok, geo_verified=geo_ok, distance_from_customer_metres=dist))
    return {"before": before, "after": {"within_contact_hours_true": after_hours, "geo_verified_true": after_geo},
            "distance_changed_over_1m": moved}


def run(engine, profile_name: str, manifest_path: str | None, seed: int = 20260922) -> dict:
    require_rbi_window()
    prof = PROFILES[profile_name]
    t0 = time.time()
    with engine.begin() as conn:
        if conn.execute(sa.select(sa.func.count()).select_from(T["banks"])
                        .where(T["banks"].c.id == R.BANK["id"])).scalar_one() != 1:
            raise SystemExit("Girivan Finance is not in this database: run scripts.migrate_v1_to_v2 first")
        if conn.execute(sa.select(sa.func.count()).select_from(T["agencies"])).scalar_one() != 1:
            raise SystemExit("this database already has generated agencies; refusing to run twice")
        world = build_world(conn, agency_keys=prof.agencies, agents_cap=prof.agents_cap,
                            agents_scale=prof.agents_scale, seed=seed)
        counts = {"world": world.counts}
        if "ARAVALLI" in prof.agencies:
            counts["aravalli loan_instalments (GENERATED)"] = aravalli_instalments(conn)
            counts["aravalli visit flags re-derived (DATA-R)"] = rederive_aravalli_visit_flags(conn)
        truths = {}
        for i, (key, w) in enumerate(sorted(world.agencies.items(), key=lambda kv: kv[1].number)):
            if not w.agents:
                continue                      # Hooghly: onboarding, no workforce, no book
            t = generate_book(conn, w, slots_per_agent=prof.slots_per_agent, seed=seed + 101 * w.number,
                              bank_admin_id=world.bank_admin[w.roster.bank_key])
            truths[key] = t
            print(f"[generate_demo_v2] {key:11s} {dict(t.counts)}", flush=True)
    manifest = {
        "SYNTHETIC_WARNING": "Every borrower, loan, agent and event in this book is synthetic; every "
                             "organisation and person is fictional (docs/DATA-MODEL-V2.md Appendix C).",
        "what": "Ground truth for the generated demo book. Generator-only: never loaded into a product table.",
        "profile": profile_name, "profile_params": prof.__dict__, "seed": seed,
        "anchor_date": R.ANCHOR_DATE.isoformat(),
        "contact_window": [settings.CONTACT_HOUR_START, settings.CONTACT_HOUR_END],
        "contact_hour_model": CONTACT_HOUR,
        "agencies": {
            key: {"latent": AGENCY_LATENT[key].to_dict(), "ledger_fingerprint": t.ledger_fingerprint,
                  "ledger_seed": t.seed, "agent_skill": t.agent_skill, "agent_gender": t.agent_gender,
                  "injected": {k: sorted(v) for k, v in t.injected.items()},
                  "injected_counts": {k: len(v) for k, v in t.injected.items()},
                  "counts": dict(t.counts), "measured": t.rates}
            for key, t in truths.items()},
        "not_generated": {"ARAVALLI": "v1's book via the B15 transform; carries no injected truth"},
        "world_and_corrections": counts,
        "seconds": round(time.time() - t0, 1),
    }
    if manifest_path:
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=1, sort_keys=True, default=str)
            f.write("\n")
    counts["seconds"] = manifest["seconds"]
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", choices=sorted(PROFILES), default="demo")
    ap.add_argument("--manifest", default=None, help="where to write the ground-truth manifest (JSON)")
    ap.add_argument("--seed", type=int, default=20260922)
    ap.add_argument("--refresh-analytics", action="store_true",
                    help="refresh the analytics materialized views afterwards (the fixture build does)")
    args = ap.parse_args()
    engine = create_engine(settings.DATABASE_URL)
    counts = run(engine, args.profile, args.manifest, args.seed)
    if args.refresh_analytics:
        # 43: the fixture's materialized views must not be empty. The nightly
        # refresher's own function, so the fixture is refreshed as production is.
        from app.workers.tasks.analytics_refresh import refresh_all
        counts["analytics"] = refresh_all(engine, wait_seconds=0)
        if counts["analytics"]["failed"] or counts["analytics"]["skipped"]:
            print(json.dumps(counts, indent=1, default=str))
            return 1
    print(json.dumps(counts, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
