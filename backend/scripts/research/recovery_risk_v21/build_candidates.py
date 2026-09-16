"""Stage 1 — candidate discovery panel.

Derives a WIDE set of point-in-time candidate features from the ledger event
tables and joins them to the existing panel on (loan_id, month_index).

PIT rule for every feature: events with day < t (strict), window [t-W, t).
Nothing here reads the label, `recovered_amount`, or anything at/after t.
"""
import sys, time
sys.path.insert(0, ".")
import numpy as np, pandas as pd
from app.ml.simulation.ledger.simulator import HARDSHIP_REASONS

D = "data/ledger/full"
OUT = sys.argv[1] if len(sys.argv) > 1 else "candidates.parquet"
CYC = 30

panel = pd.read_parquet(f"{D}/panel.parquet")
pays = pd.read_parquet(f"{D}/payments.parquet")
visits = pd.read_parquet(f"{D}/visits.parquet")
ptps = pd.read_parquet(f"{D}/ptps.parquet")
calls = pd.read_parquet(f"{D}/calls.parquet")
loans = pd.read_parquet(f"{D}/loans.parquet").set_index("loan_id")

def status_at(p, day):
    return np.where(p.status_effective_day < day, p.final_status, p.initial_status)

def cnt(df, idx, col="loan_id"):
    return df.groupby(col).size().reindex(idx).fillna(0.0).astype(float)

def ratio(num, den):
    num = np.asarray(num, float); den = np.asarray(den, float)
    return np.where(den > 0, num / np.maximum(den, 1e-9), np.nan)

frames = []
t0 = time.time()
for m in sorted(panel.month_index.unique()):
    t = m * CYC
    rows = panel[panel.month_index == m]
    idx = pd.Index(rows.loan_id.to_numpy(), name="loan_id")
    emi = pd.Series(rows.emi_amount.to_numpy(float), index=idx)
    overdue = pd.Series(rows.overdue_amount.to_numpy(float), index=idx)
    f = pd.DataFrame(index=idx)
    f["month_index"] = m

    # ── PAYMENTS ───────────────────────────────────────────────────────────
    h = pays[(pays.payment_day < t) & pays.loan_id.isin(idx)]
    ok = h[status_at(h, t) == "VERIFIED"]
    def w(days): return ok[ok.payment_day >= t - days]
    p1, p3, p6, p12 = w(30), w(90), w(180), w(365)
    f["pay_count_3m"] = cnt(p3, idx)
    f["pay_count_12m"] = cnt(p12, idx)
    f["paid_ratio_1m"] = np.clip(ratio(p1.groupby("loan_id").amount.sum().reindex(idx).fillna(0), emi), 0, 1.5)
    # previous quarter [t-180, t-90) — deterioration / improvement
    prev = ok[(ok.payment_day >= t - 180) & (ok.payment_day < t - 90)]
    pr3 = p3.groupby("loan_id").amount.sum().reindex(idx).fillna(0) / (3 * emi)
    prp = prev.groupby("loan_id").amount.sum().reindex(idx).fillna(0) / (3 * emi)
    f["paid_delta_q"] = np.clip(pr3, 0, 1.5) - np.clip(prp, 0, 1.5)
    f["paid_to_overdue_6m"] = np.clip(ratio(p6.groupby("loan_id").amount.sum().reindex(idx).fillna(0), overdue), 0, 5)
    tok = p6[p6.amount.to_numpy() < 0.25 * emi.reindex(p6.loan_id).to_numpy()]
    f["token_payment_share_6m"] = ratio(cnt(tok, idx), cnt(p6, idx))
    full = p6[p6.amount.to_numpy() >= 0.9 * emi.reindex(p6.loan_id).to_numpy()]
    f["full_emi_share_6m"] = ratio(cnt(full, idx), cnt(p6, idx))
    # distinct cycles with a payment in 6m: regularity
    cyc6 = p6.assign(cy=(p6.payment_day // CYC)).groupby("loan_id").cy.nunique().reindex(idx).fillna(0)
    f["cycles_paid_6m"] = cyc6.astype(float)
    # amount volatility
    amt_cv = p6.groupby("loan_id").amount.agg(["std", "mean", "size"])
    cv = pd.Series(np.where(amt_cv["size"] >= 2, amt_cv["std"].fillna(0) / amt_cv["mean"], np.nan), index=amt_cv.index)
    f["pay_amount_cv_6m"] = cv.reindex(idx).to_numpy(float)
    # max gap 12m (days) incl. gap to t
    last12 = p12.groupby("loan_id").payment_day.max().reindex(idx)
    g = p12.sort_values("payment_day").groupby("loan_id").payment_day.apply(lambda s: np.diff(s.to_numpy()).max() if len(s) >= 2 else np.nan)
    f["max_gap_12m"] = g.reindex(idx).to_numpy(float)
    f["last_payment_amount"] = p12.sort_values("payment_day").groupby("loan_id").amount.last().reindex(idx).to_numpy(float)
    f["last_payment_to_overdue"] = np.clip(ratio(f["last_payment_amount"].fillna(0), overdue), 0, 5)
    f.loc[f.last_payment_amount.isna(), "last_payment_to_overdue"] = np.nan

    # ── PTPs ────────────────────────────────────────────────────────────────
    hp = ptps[(ptps.created_day < t) & ptps.loan_id.isin(idx)]
    h6 = hp[hp.created_day >= t - 180]
    h3 = hp[hp.created_day >= t - 90]
    res6 = h6[(h6.resolved_day >= 0) & (h6.resolved_day < t)]
    kept6 = res6[res6.resolved_status == "HONORED"]
    brk6 = res6[res6.resolved_status == "BROKEN"]
    f["ptp_set_3m"] = cnt(h3, idx)
    f["ptp_resolved_rate_6m"] = ratio(cnt(res6, idx), cnt(h6, idx))
    f["ptp_broken_ratio_6m"] = ratio(cnt(brk6, idx), cnt(res6, idx))
    f["ptp_kept_of_resolved_6m"] = ratio(cnt(kept6, idx), cnt(res6, idx))
    res12 = hp[(hp.created_day >= t - 365) & (hp.resolved_day >= 0) & (hp.resolved_day < t)]
    f["ptp_kept_ratio_12m"] = ratio(cnt(res12[res12.resolved_status == "HONORED"], idx), cnt(hp[hp.created_day >= t - 365], idx))
    lastp = hp.sort_values("created_day").groupby("loan_id").tail(1).set_index("loan_id")
    f["days_since_last_ptp"] = (t - lastp.created_day).reindex(idx).to_numpy(float)
    lastk = kept6.sort_values("resolved_day").groupby("loan_id").resolved_day.last().reindex(idx)
    f["days_since_last_kept_ptp"] = (t - lastk).to_numpy(float)
    # status of the most recent promise AS KNOWN at t
    st = np.where(lastp.resolved_day.to_numpy() >= 0, np.where(lastp.resolved_day.to_numpy() < t, lastp.resolved_status.astype(str).to_numpy(), "OPEN"), "OPEN")
    f["last_ptp_status"] = pd.Series(st, index=lastp.index).reindex(idx).fillna("NONE").to_numpy()
    # a LIVE promise at t: created before t, committed on/after t, unresolved before t
    live = hp[(hp.committed_day >= t) & ((hp.resolved_day < 0) | (hp.resolved_day >= t))]
    f["ptp_live_at_asof"] = (cnt(live, idx) > 0).astype(float)
    f["days_to_ptp_due"] = (live.groupby("loan_id").committed_day.min().reindex(idx) - t).to_numpy(float)
    f["ptp_amount_to_overdue"] = np.clip(ratio(h6.groupby("loan_id").committed_amount.mean().reindex(idx), overdue), 0, 5)
    f["last_ptp_amount_to_emi"] = np.clip(ratio(lastp.committed_amount.reindex(idx), emi), 0, 3)

    # ── CALLS ───────────────────────────────────────────────────────────────
    hc = calls[(calls.day < t) & calls.loan_id.isin(idx)]
    c3, c6, c12 = hc[hc.day >= t - 90], hc[hc.day >= t - 180], hc[hc.day >= t - 365]
    f["calls_6m"] = cnt(c6, idx)
    f["call_answer_rate_3m"] = ratio(cnt(c3[c3.answered], idx), cnt(c3, idx))
    f["call_answer_rate_12m"] = ratio(cnt(c12[c12.answered], idx), cnt(c12, idx))
    a6 = c6[c6.answered]
    f["intent_rate_6m"] = ratio(cnt(a6[a6.payment_intent.eq(True)], idx), cnt(a6, idx))
    a3 = c3[c3.answered]
    f["intent_rate_3m"] = ratio(cnt(a3[a3.payment_intent.eq(True)], idx), cnt(a3, idx))
    f["intent_calls_6m"] = cnt(a6[a6.payment_intent.eq(True)], idx)
    # consistency: share of intent among the last 3 answered calls (any age)
    ans = hc[hc.answered].sort_values("day")
    last3 = ans.groupby("loan_id").tail(3)
    f["intent_last3"] = ratio(cnt(last3[last3.payment_intent.eq(True)], idx), cnt(last3, idx))
    f["days_since_last_call"] = (t - hc.groupby("loan_id").day.max().reindex(idx)).to_numpy(float)
    lastc = ans.groupby("loan_id").day.max().reindex(idx)
    li = ans.groupby("loan_id").tail(1).set_index("loan_id")
    f["days_since_intent_call"] = (t - ans[ans.payment_intent.eq(True)].groupby("loan_id").day.max().reindex(idx)).to_numpy(float)

    # ── VISITS ──────────────────────────────────────────────────────────────
    hv = visits[(visits.day < t) & visits.loan_id.isin(idx)]
    v3, v6, v12 = hv[hv.day >= t - 90], hv[hv.day >= t - 180], hv[hv.day >= t - 365]
    f["met_visits_3m"] = cnt(v3[v3.met], idx)
    f["contact_rate_3m"] = ratio(cnt(v3[v3.met], idx), cnt(v3, idx))
    met6 = v6[v6.met]
    f["rtp_rate_of_met_6m"] = ratio(cnt(met6[met6.outcome == "RTP"], idx), cnt(met6, idx))
    f["rtp_visits_12m"] = cnt(v12[v12.outcome == "RTP"], idx)
    f["hardship_rate_of_met_6m"] = ratio(cnt(met6[met6.default_reason.isin(HARDSHIP_REASONS)], idx), cnt(met6, idx))
    f["hardship_visits_12m"] = cnt(v12[v12.default_reason.isin(HARDSHIP_REASONS)], idx)
    f["hardship_visits_3m"] = cnt(v3[v3.default_reason.isin(HARDSHIP_REASONS)], idx)
    lastv = hv.sort_values("day").groupby("loan_id").tail(1).set_index("loan_id")
    f["last_visit_outcome"] = lastv.outcome.reindex(idx).fillna("NONE").to_numpy()
    lastmet = met6.sort_values("day").groupby("loan_id").tail(1).set_index("loan_id")
    f["last_met_outcome"] = lastmet.outcome.reindex(idx).fillna("NONE").to_numpy()
    lr = lastmet.default_reason.reindex(idx)
    f["last_reason_hardship"] = np.where(lr.isna(), np.nan, lr.isin(HARDSHIP_REASONS).astype(float))
    f["days_since_last_visit"] = (t - hv.groupby("loan_id").day.max().reindex(idx)).to_numpy(float)
    # unsuccessful-contact streak: visits since last met
    streak = pd.Series(0.0, index=idx)
    for lid, g in hv.groupby("loan_id"):
        mm = g.sort_values("day", ascending=False).met.to_numpy()
        k = 0
        for a in mm:
            if a: break
            k += 1
        streak[lid] = k
    f["not_met_streak"] = streak.to_numpy()
    # PTP outcome of the most recent met visit: did the last met visit produce a promise
    f["last_met_was_ptp"] = np.where(lastmet.outcome.reindex(idx).isna(), np.nan, (lastmet.outcome.reindex(idx) == "PTP").astype(float))

    # ── CROSS-CHANNEL ───────────────────────────────────────────────────────
    att6 = cnt(v6, idx) + cnt(c6, idx)
    suc6 = cnt(v6[v6.met], idx) + cnt(a6, idx)
    f["any_contact_rate_6m"] = ratio(suc6, att6)
    f["attempts_6m"] = att6
    dsc = pd.Series(rows.days_since_last_contact.to_numpy(float), index=idx)
    dsa = (t - lastc).astype(float)
    f["days_since_any_contact"] = np.fmin(dsc, dsa)
    # promise/payment consistency: kept promises per verified payment count
    f["intent_or_ptp_recent"] = np.fmax(f["intent_rate_3m"].fillna(0), (cnt(h3, idx) > 0).astype(float))
    frames.append(f.reset_index())
    print(f"month {m:2d} live {len(idx)} {time.time()-t0:.0f}s", flush=True)

cand = pd.concat(frames, ignore_index=True)
merged = panel.merge(cand, on=["loan_id", "month_index"], how="left", validate="1:1")
assert len(merged) == len(panel)
merged.to_parquet(OUT)
print("written", OUT, merged.shape)
