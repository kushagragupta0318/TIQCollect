"""
Export ML Training Dataset from PostgreSQL to CSV/DataFrame.
============================================================
Exports frozen point-in-time features from repayment_snapshots and allocation_decisions
paired with actual resolved recovery ground truth.

Usage:
  python -m scripts.export_training_data
  python -m scripts.export_training_data --out data/my_dataset.csv
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
from app.core.database import engine

DEFAULT_OUT = Path(__file__).parent.parent / "data" / "ml_repayment_dataset.csv"


def export_dataset(output_path: Path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    query = """
    SELECT 
        rs.id AS snapshot_id,
        rs.loan_id,
        rs.customer_id,
        rs.as_of_date,
        rs.likelihood AS predicted_likelihood,
        rs.risk_category,
        rs.risk_score,
        rs.recovery_potential,
        rs.recovery_rate_90 AS predicted_recovery_rate_90,
        rs.outcome AS actual_outcome,
        rs.recovered_amount_30,
        rs.recovered_amount_60,
        rs.recovered_amount_90,
        rs.outcome_source,
        rs.features,
        c.loan_type,
        c.dpd_bucket,
        c.total_outstanding,
        c.bureau_score
    FROM repayment_snapshots rs
    LEFT JOIN (
        SELECT l.id, l.loan_type, l.dpd_bucket, l.total_outstanding, cust.cibil_score AS bureau_score
        FROM loans l
        JOIN customers cust ON l.customer_id = cust.id
    ) c ON rs.loan_id = c.id
    ORDER BY rs.as_of_date DESC;
    """

    print(f"Connecting to database and extracting snapshot training data...")
    df = pd.read_sql(query, engine)
    
    df.to_csv(output_path, index=False)
    print(f"✅ Successfully exported {len(df)} ML training records to: {output_path}")
    return df


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export ML Training Dataset")
    parser.add_argument("--out", type=str, default=str(DEFAULT_OUT), help="Output CSV path")
    args = parser.parse_args()
    
    export_dataset(Path(args.out))
