"""Attach the Empirical Bayes agent estimate to each snapshot, as it stood then.

WHY THIS IS SAFE TO BACKFILL WHEN THE BORROWER FEATURES ARE NOT
---------------------------------------------------------------
models/repayment_snapshot.py explains why the borrower features had to be
FROZEN at scoring time: Loan.dpd and PTP.status are overwritten in place with
no history, so recomputing them tomorrow answers a different question, and a
row rebuilt from today's loan table leaks the future through both.

EB does not have that problem. It is computed entirely from `payments`, which
are append-only and carry `payment_date`. The window
`[as_of - 180d, as_of]` therefore reconstructs exactly, today or in a year, and
the value it produces is the value that WAS true on that date. This is the one
feature in this system that can honestly be added to a historical row after the
fact.

WHAT IS WRITTEN
---------------
Into the existing `features` JSON — no schema change, matching the storage rule
the rest of the table follows:

    eb_shrunk_win       the Empirical Bayes estimate for (agent, loan_type, dpd
                        bucket): the agent's own observed rate pulled toward the
                        segment prior in proportion to how little evidence backs
                        it. This is the V1 feature.
    eb_evidence_n       how many (agent, case) observations back it. WITHOUT
                        THIS, eb_shrunk_win IS AMBIGUOUS: below the adjuster's
                        min_sample_threshold it hands back the segment prior
                        unchanged, so a model cannot tell an agent's measured
                        rate from the segment average standing in for it. On
                        this book the fallback is the common path.
    eb_segment_prior    the segment average itself, so the two can be compared.
    eb_raw_win          the unshrunk rate, or None below threshold.
    _eb_*               provenance: the as_of, the window, the agent, and the
                        segment the value was looked up under.

THE SEGMENT IS DERIVED FROM THE FROZEN DPD, NOT FROM THE LOAN
--------------------------------------------------------------
Loan.dpd_bucket is mutable and reflects today. features["dpd"] is the DPD that
was true on as_of_date and was frozen for exactly this reason. Reading the loan
would silently re-bucket a row into a segment it was never in, which is the same
leak the snapshot table was built to prevent.

USAGE
    python scripts/backfill_eb_features.py --dry-run
    python scripts/backfill_eb_features.py --apply
    python scripts/backfill_eb_features.py --apply --overwrite   # recompute
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _resolve_database_url() -> str:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    here = pathlib.Path(__file__).resolve().parents[2]

    def envmap(p: pathlib.Path) -> dict[str, str]:
        d: dict[str, str] = {}
        if not p.exists():
            return d
        for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                d[k.strip()] = v.strip().strip('"').strip("'")
        return d

    root, be = envmap(here / ".env"), envmap(here / "backend" / ".env")
    url = re.sub(r"\$\{(\w+)(?::-[^}]*)?\}", lambda m: root.get(m.group(1), m.group(0)),
                 be.get("DATABASE_URL", ""))
    return url.replace("@postgres:", "@localhost:").replace(":5432/", ":15432/")


os.environ["DATABASE_URL"] = _resolve_database_url()
os.environ.setdefault("SECRET_KEY", "backfill-script")
os.environ.setdefault("COMMAND_CENTRE_API_KEY", "backfill-script")

from sqlalchemy.orm.attributes import flag_modified                  # noqa: E402

from app.core.database import SessionLocal                           # noqa: E402
from app.ml.empirical_bayes import EmpiricalBayesAgentAdjuster       # noqa: E402
from app.models.case import Case                                     # noqa: E402
from app.models.loan import DPDBucket, Loan                          # noqa: E402
from app.models.repayment_snapshot import RepaymentSnapshot          # noqa: E402

LOOKBACK_DAYS = EmpiricalBayesAgentAdjuster.DEFAULT_LOOKBACK_DAYS


def dpd_to_bucket(dpd: int) -> str:
    """Same thresholds as scripts/ingest_daily.py and scripts/seed_data.py."""
    if dpd == 0:
        return DPDBucket.CURRENT.value
    if dpd <= 30:
        return DPDBucket.BUCKET_1.value
    if dpd <= 60:
        return DPDBucket.BUCKET_2.value
    if dpd <= 90:
        return DPDBucket.BUCKET_3.value
    return DPDBucket.NPA.value


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--overwrite", action="store_true",
                    help="recompute rows that already carry an EB value")
    ap.add_argument("--lookback-days", type=int, default=LOOKBACK_DAYS)
    args = ap.parse_args()

    db = SessionLocal()
    rows = db.query(RepaymentSnapshot).order_by(RepaymentSnapshot.as_of_date).all()
    if not rows:
        print("no snapshots to backfill")
        db.close()
        return

    # Agent per case. A snapshot is per LOAN; EB is keyed on the AGENT, so the
    # case is the only bridge between them.
    agent_of_case = {
        cid: aid for cid, aid in db.query(Case.id, Case.agent_id).all()
    }
    # Loan type is in the frozen features for most rows; the loan table is the
    # fallback. Unlike dpd, loan_type does not change, so reading it is safe.
    type_of_loan = {
        lid: (lt.value if hasattr(lt, "value") else str(lt))
        for lid, lt in db.query(Loan.id, Loan.loan_type).all()
    }

    by_date: dict[object, list[RepaymentSnapshot]] = defaultdict(list)
    for r in rows:
        by_date[r.as_of_date].append(r)

    skipped: Counter = Counter()
    written = 0
    already = 0
    per_date: list[tuple[object, int, int, float]] = []

    # ONE fit per distinct as_of_date, not one per snapshot — the estimate is
    # identical for every row sharing a date, and each fit is a full aggregate
    # over the payment ledger.
    for as_of in sorted(by_date):
        eb = EmpiricalBayesAgentAdjuster().fit_from_db(
            db, as_of=as_of, lookback_days=args.lookback_days)
        start, cutoff = eb.fitted_window
        date_written = 0

        for row in by_date[as_of]:
            feats = dict(row.features or {})
            if "eb_shrunk_win" in feats and not args.overwrite:
                already += 1
                continue

            agent_id = agent_of_case.get(row.case_id) if row.case_id else None
            if not agent_id:
                # No case, or a case nobody holds. There is no agent to
                # estimate, so there is no EB value — and a placeholder here
                # would be indistinguishable from a real one.
                skipped["no agent on the case" if row.case_id else "snapshot has no case"] += 1
                continue

            loan_type = feats.get("loan_type") or type_of_loan.get(row.loan_id)
            dpd = feats.get("dpd")
            if loan_type is None or dpd is None:
                skipped["frozen features lack loan_type or dpd"] += 1
                continue

            bucket = dpd_to_bucket(int(dpd))
            shrunk, prior, _mult = eb.get_segment_multiplier(agent_id, str(loan_type), bucket)
            n = eb.get_segment_evidence(agent_id, str(loan_type), bucket)
            obs = eb.agent_observations.get((agent_id, str(loan_type), bucket))
            raw = (obs["recovered"] / obs["target"]) if obs and obs["target"] > 0 else None

            feats.update({
                "eb_shrunk_win": round(float(shrunk), 6),
                "eb_segment_prior": round(float(prior), 6),
                "eb_evidence_n": int(n),
                "eb_raw_win": round(float(raw), 6) if raw is not None else None,
                "_eb_as_of": str(as_of),
                "_eb_lookback_days": args.lookback_days,
                "_eb_window": [start.isoformat(), cutoff.isoformat()],
                "_eb_agent_id": agent_id,
                "_eb_segment": [str(loan_type), bucket],
            })
            row.features = feats
            # The JSON column is a plain dict as far as SQLAlchemy is
            # concerned; without this the reassignment above is still not
            # enough on some backends and the UPDATE is never emitted.
            flag_modified(row, "features")
            written += 1
            date_written += 1

        per_date.append((as_of, len(by_date[as_of]), date_written, eb.global_prior))

    print(f"snapshots            : {len(rows)}")
    print(f"EB values to write   : {written}")
    if already:
        print(f"already carried one  : {already}   (pass --overwrite to recompute)")
    for why, n in skipped.most_common():
        print(f"   skipped {n:>4}  {why}")

    print("\nper snapshot date:")
    print(f"   {'as_of':<12} {'rows':>6} {'written':>8} {'EB global prior':>16}")
    for as_of, total, w, gp in per_date:
        print(f"   {str(as_of):<12} {total:>6} {w:>8} {gp:>16.4f}")

    if args.dry_run or not args.apply:
        db.rollback()
        print("\n(dry run — nothing written; pass --apply to commit)")
        db.close()
        return

    db.commit()
    print(f"\ncommitted: {written} snapshots now carry a point-in-time EB estimate")
    db.close()


if __name__ == "__main__":
    main()
