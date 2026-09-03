# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-03 — This script had never run. It selected `FROM repayment_snapshots`
#   and the table is `repayment_score_snapshots`, so every invocation died on
#   UndefinedTable. Nothing downstream noticed, because nothing downstream had
#   ever called it.
#
#   The join it carried was the same leak the shadow trainer had: it pulled
#   `dpd_bucket`, `total_outstanding` and `cibil_score` from the LIVE loans and
#   customers tables and set them beside an outcome that had already resolved.
#   Both columns move — total_outstanding is reduced by the very payments that
#   form the label. Those come out of the frozen `features` JSON now, which is
#   what that column was created to hold.
#
#   The point-in-time EB fields written by scripts/backfill_eb_features.py are
#   promoted to real columns here so a modeller does not have to parse JSON to
#   reach the V1 agent feature.
# ───────────────────────────────────────────────────────────────────────────
"""
Export ML Training Dataset from PostgreSQL to CSV/DataFrame.
============================================================
Exports frozen point-in-time features from repayment_score_snapshots paired
with the resolved recovery ground truth.

Every borrower column here comes from `features`, the snapshot's frozen record
of what was true on as_of_date. Nothing is read from the live loans or
customers tables: Loan.dpd and PTP.status are overwritten in place with no
history, so a value read today answers a different question from the one the
score was made against.

Usage:
  python -m scripts.export_training_data
  python -m scripts.export_training_data --out data/my_dataset.csv
  python -m scripts.export_training_data --labelled-only
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
from app.core.database import engine

DEFAULT_OUT = Path(__file__).parent.parent / "data" / "ml_repayment_dataset.csv"

QUERY = """
SELECT
    rs.id                        AS snapshot_id,
    rs.loan_id,
    rs.customer_id,
    rs.case_id,
    rs.as_of_date,
    rs.is_backfill,
    rs.likelihood                AS predicted_likelihood,
    rs.risk_category,
    rs.risk_score,
    rs.evidence_coverage,
    rs.recovery_potential,
    rs.recovery_rate_90          AS predicted_recovery_rate_90,
    rs.outcome                   AS actual_outcome,
    rs.outcome_amount,
    rs.outcome_source,
    rs.outcome_horizon_days,
    rs.feature_age_days,
    rs.recovered_amount_30,
    rs.recovered_amount_60,
    rs.recovered_amount_90,
    rs.recovery_labelled_through_days,
    -- Frozen borrower/loan attributes. NOT the live tables.
    rs.features ->> 'loan_type'                     AS loan_type,
    (rs.features ->> 'dpd')::numeric                AS dpd,
    (rs.features ->> 'total_outstanding')::numeric  AS total_outstanding,
    (rs.features ->> 'overdue_amount')::numeric     AS overdue_amount,
    (rs.features ->> 'emi_amount')::numeric         AS emi_amount,
    (rs.features ->> 'penal_charges')::numeric      AS penal_charges,
    (rs.features ->> 'cibil_score')::numeric        AS bureau_score,
    (rs.features ->> 'days_since_last_payment')::numeric AS days_since_last_payment,
    -- Point-in-time Empirical Bayes agent estimate. eb_evidence_n is not
    -- optional context: below the adjuster's threshold eb_shrunk_win IS the
    -- segment prior, and without the count the two are indistinguishable.
    (rs.features ->> 'eb_shrunk_win')::numeric      AS eb_shrunk_win,
    (rs.features ->> 'eb_evidence_n')::numeric      AS eb_evidence_n,
    (rs.features ->> 'eb_segment_prior')::numeric   AS eb_segment_prior,
    (rs.features ->> 'eb_raw_win')::numeric         AS eb_raw_win,
    rs.features ->> '_eb_agent_id'                  AS eb_agent_id,
    rs.features ->> '_eb_as_of'                     AS eb_as_of,
    rs.features                  AS features
FROM repayment_score_snapshots rs
{where}
ORDER BY rs.as_of_date DESC;
"""


def export_dataset(output_path: Path, labelled_only: bool = False):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    where = "WHERE rs.outcome IS NOT NULL" if labelled_only else ""
    print("Connecting to database and extracting snapshot training data...")
    df = pd.read_sql(QUERY.format(where=where), engine)

    df.to_csv(output_path, index=False)
    labelled = int(df["actual_outcome"].notna().sum()) if len(df) else 0
    with_eb = int(df["eb_shrunk_win"].notna().sum()) if len(df) else 0
    print(f"Exported {len(df)} rows to: {output_path}")
    print(f"   with an outcome label        : {labelled}")
    print(f"   with a point-in-time EB value: {with_eb}")
    if labelled == 0:
        print("   NOTE: nothing is labelled yet. The labeller fills `outcome` one "
              "horizon after the score; until a snapshot is older than that "
              "horizon there is nothing to train on.")
    return df


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export ML Training Dataset")
    parser.add_argument("--out", type=str, default=str(DEFAULT_OUT), help="Output CSV path")
    parser.add_argument("--labelled-only", action="store_true",
                        help="export only rows that already carry an outcome")
    args = parser.parse_args()

    export_dataset(Path(args.out), labelled_only=args.labelled_only)
