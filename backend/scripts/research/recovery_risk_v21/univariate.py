"""Stage 2 — univariate discovery on the DEVELOPMENT split only (train+valid).

OOT months are never touched here.
"""
import sys, json, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from optbinning import OptimalBinning
from app.ml.pipeline import evaluate as ev
from app.ml.pipeline.config import RECOVERY_RISK_V2, FEED_ONLY_FEATURES, NO_HISTORY_FEATURES

S = sys.argv[1]
pd.set_option("display.width", 250); pd.set_option("display.max_rows", 500)
df = pd.read_parquet(f"{S}/candidates.parquet")
months = np.sort(df.month_index.unique()); n = len(months)
tr_m, va_m, oot_m = months[:int(n*.6)], months[int(n*.6):int(n*.75)], months[int(n*.75):]
dev = df[df.month_index.isin(np.r_[tr_m, va_m])]
print("dev rows", len(dev), "months", tr_m.min(), "-", va_m.max(), "| OOT months", oot_m.min(), "-", oot_m.max(), "(untouched)")

EXCLUDE = set(["loan_id", "borrower_id", "as_of_date", "month_index", "y", "recovered_amount",
               "outcome_threshold", "baseline_overdue_amount", "baseline_emi_amount"]) | set(FEED_ONLY_FEATURES) | set(NO_HISTORY_FEATURES)
EXCLUDE |= {"monthly_income", "dti_ratio", "credit_vintage_months", "num_open_loans", "num_enquiries_6m",
            "other_lender_delinq", "utilization_pct", "mail_returned_count", "address_vintage_months",
            "phone_verified", "thin_file", "sourcing_channel", "residence_type", "is_secured"}
FAMILY = {}
def fam(names, f):
    for c in names: FAMILY[c] = f
fam(["dpd","dpd_bucket","overdue_amount","penal_charges","total_outstanding","arrears_ratio","penal_ratio","outstanding_principal","outstanding_to_sanction"], "delinquency")
fam(["cibil_score","loan_type","sanction_amount","emi_amount","tenure_months","interest_rate","months_on_book","branch_code","age","city","employment_type"], "static")
fam(["paid_ratio_3m","paid_ratio_6m","paid_ratio_12m","days_since_last_payment","bounce_count_6m","payments_6m","partial_payment_share_6m","payment_gap_cv_12m","last_payment_to_emi","paid_momentum",
     "pay_count_3m","pay_count_12m","paid_ratio_1m","paid_delta_q","paid_to_overdue_6m","token_payment_share_6m","full_emi_share_6m","cycles_paid_6m","pay_amount_cv_6m","max_gap_12m","last_payment_amount","last_payment_to_overdue"], "payment")
fam(["ptp_set_6m","ptp_kept_6m","ptp_kept_ratio","ptp_broken_6m","ptp_rescheduled_6m","ptp_amount_to_emi","ptp_set_3m","ptp_resolved_rate_6m","ptp_broken_ratio_6m","ptp_kept_of_resolved_6m","ptp_kept_ratio_12m",
     "days_since_last_ptp","days_since_last_kept_ptp","last_ptp_status","ptp_live_at_asof","days_to_ptp_due","ptp_amount_to_overdue","last_ptp_amount_to_emi"], "ptp")
fam(["calls_3m","call_answer_rate_6m","no_answer_streak","days_since_last_answered_call","intent_calls_3m","last_call_intent","calls_6m","call_answer_rate_3m","call_answer_rate_12m","intent_rate_6m","intent_rate_3m","intent_calls_6m","intent_last3","days_since_last_call","days_since_intent_call"], "call")
fam(["visits_3m","visits_6m","contact_rate_6m","distinct_agents_6m","days_since_last_contact","rtp_visits_6m","dispute_visits_6m","adverse_visit_ratio_6m","hardship_visits_6m","met_visits_3m","contact_rate_3m","rtp_rate_of_met_6m","rtp_visits_12m","last_visit_outcome","last_met_outcome","days_since_last_visit","not_met_streak","last_met_was_ptp"], "visit")
fam(["hardship_rate_of_met_6m","hardship_visits_12m","hardship_visits_3m","last_reason_hardship"], "hardship")
fam(["is_hostile","fraud_flag"], "flag")
fam(["any_contact_rate_6m","attempts_6m","days_since_any_contact","intent_or_ptp_recent"], "cross")

cands = [c for c in df.columns if c not in EXCLUDE]
y = dev.y.to_numpy(int)
rows = []
for c in cands:
    x = dev[c]
    is_cat = x.dtype == object or c in ("dpd_bucket", "last_ptp_status", "last_visit_outcome", "last_met_outcome")
    miss = float(x.isna().mean())
    try:
        ob = OptimalBinning(name=c, dtype="categorical" if is_cat else "numerical", solver="cp",
                           max_n_bins=8, min_bin_size=0.05, monotonic_trend="auto_asc_desc" if not is_cat else None)
        ob.fit(x.to_numpy() if not is_cat else x.astype(str).to_numpy(), y)
        woe = ob.transform(x.to_numpy() if not is_cat else x.astype(str).to_numpy(), metric="woe")
        iv = float(ob.binning_table.build().IV.iloc[-1]) if hasattr(ob.binning_table.build(), "IV") else float(ob.binning_table.iv)
        g = abs(ev.gini(y, -woe))
        # temporal stability of the univariate WOE Gini across dev months
        gm = []
        for m in np.r_[tr_m, va_m]:
            mk = (dev.month_index == m).to_numpy()
            gm.append(abs(ev.gini(y[mk], -woe[mk])))
        rows.append(dict(feature=c, family=FAMILY.get(c, "?"), n_unique=int(x.nunique()), missing=round(miss, 3),
                         iv=round(iv, 4), gini_woe=round(g, 4), gini_month_min=round(min(gm), 3), gini_month_max=round(max(gm), 3),
                         gini_month_sd=round(float(np.std(gm)), 3), n_bins=int(ob.n_bins_ if hasattr(ob, "n_bins_") else len(ob.splits) + 1)))
    except Exception as e:
        rows.append(dict(feature=c, family=FAMILY.get(c, "?"), n_unique=int(x.nunique()), missing=round(miss, 3), iv=np.nan, gini_woe=np.nan, err=str(e)[:60]))
res = pd.DataFrame(rows).sort_values(["family", "iv"], ascending=[True, False])
print(res.to_string(index=False))
res.to_csv(f"{S}/univariate_dev.csv", index=False)
print("\nTOP 25 by IV")
print(res.sort_values("iv", ascending=False).head(25)[["feature", "family", "iv", "gini_woe", "missing", "gini_month_sd"]].to_string(index=False))
print("\nFAMILY SUMMARY")
print(res.groupby("family").agg(n=("feature", "size"), best_iv=("iv", "max"), best_gini=("gini_woe", "max")).to_string())
