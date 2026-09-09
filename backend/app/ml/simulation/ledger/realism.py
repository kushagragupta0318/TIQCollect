# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-09 — NEW. Phase 1 acceptance: is this book plausible ENOUGH to be
#   worth training on at all?
#
#   THE GATE RUNS BEFORE ANY MODEL DOES. A simulator that fails these checks is
#   rejected before a model is fitted on it, for the same reason the model gates
#   in ml/pipeline are two-sided and fail rather than warn: a number produced by
#   an implausible world is worse than no number, because it will be quoted.
#
#   NOTHING HERE MEASURES MODEL PERFORMANCE. Checks 7-10 of the design — the
#   Gini band, the DPD-alone ratio, the IV ceiling and the oracle gap — are
#   deliberately absent. They belong to Phase 2, and keeping them out of Phase 1
#   is what stops the generator being tuned against a model result.
#
#   EVERY BAND PRINTS ITS PROVENANCE. Most are PROJECT ASSUMPTIONS with no
#   external source, and the report says so on every line rather than letting a
#   plausible range read as an industry fact.
# ───────────────────────────────────────────────────────────────────────────
"""Phase 1 realism checks (design checks 1-6).

    report = realism_report(ledger, panel)
    print(format_report(report))

A check can PASS, FAIL, or be REPORTED (measured and printed, never gated).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.ml.simulation.ledger.config import BANDS, LedgerConfig
from app.ml.simulation.ledger.simulator import Ledger

BUCKET_ORDER = {"CURRENT": 0, "BUCKET_1": 1, "BUCKET_2": 2, "BUCKET_3": 3, "NPA": 4}


def _result(key: str, value: float | None, *, gated: bool = True) -> dict:
    band = BANDS[key]
    out = {"check": key, "value": None if value is None else round(float(value), 4),
           "low": band.low, "high": band.high,
           "provenance": band.provenance.value, "note": band.note}
    out["status"] = ("REPORTED" if not gated
                     else ("PASS" if band.contains(value) else "FAIL"))
    return out


# ── 1. roll rates ───────────────────────────────────────────────────────────

def roll_rates(panel: pd.DataFrame) -> tuple[float, float, pd.DataFrame]:
    """Bucket-to-bucket transitions between consecutive monthly snapshots.

    Only CONSECUTIVE observations of the same loan count. A loan that leaves the
    book and whose slot is reused would otherwise contribute a transition
    between two different borrowers.
    """
    p = panel[["loan_id", "month_index", "dpd_bucket"]].copy()
    p["ord"] = p.dpd_bucket.map(BUCKET_ORDER)
    p = p.sort_values(["loan_id", "month_index"])
    nxt = p.groupby("loan_id").shift(-1)
    ok = (nxt.month_index == p.month_index + 1)
    a, b = p["ord"][ok], nxt["ord"][ok]
    matrix = (pd.crosstab(a, b, normalize="index")
              if len(a) else pd.DataFrame())
    delinquent = a > 0
    forward = float((b[delinquent] > a[delinquent]).mean()) if delinquent.any() else None
    cure = float((b[delinquent] < a[delinquent]).mean()) if delinquent.any() else None
    return forward, cure, matrix


# ── 3. vintage ──────────────────────────────────────────────────────────────

def vintage_monotonicity(panel: pd.DataFrame) -> float:
    """Fraction of origination cohorts whose ever-delinquent curve never falls.

    A sanity check on the generator, not on the world: cumulative-ever-X is
    non-decreasing by arithmetic, so anything below 1.0 means the cohort or the
    months-on-book axis is being built wrongly.
    """
    p = panel.copy()
    p["cohort"] = p.month_index - p.months_on_book
    p["bad"] = (p.dpd > 30).astype(int)
    ok = 0
    cohorts = [c for c, g in p.groupby("cohort") if g.months_on_book.nunique() >= 3]
    for c in cohorts:
        g = p[p.cohort == c].groupby("months_on_book").bad.max().sort_index()
        if (g.cummax().diff().dropna() >= 0).all():
            ok += 1
    return ok / len(cohorts) if cohorts else None


# ── 4. payment shape ────────────────────────────────────────────────────────

def payment_shape(ledger: Ledger) -> dict:
    pays = ledger.payments
    if not len(pays):
        return {"exact_emi_share": None, "round_number_share": None,
                "partial_share": None, "n_payments": 0}
    emi = ledger.loans.set_index("loan_id").emi_amount
    amt = pays.amount.to_numpy(dtype=float)
    e = emi.reindex(pays.loan_id).to_numpy(dtype=float)
    ratio = amt / np.maximum(e, 1.0)
    return {
        "exact_emi_share": float(np.mean(np.abs(ratio - 1.0) <= 0.02)),
        "round_number_share": float(np.mean(np.abs(amt % 500.0) < 1e-6)),
        "partial_share": float(np.mean(ratio < 0.90)),
        "n_payments": int(len(pays)),
    }


# ── 5/6. promises and contact ───────────────────────────────────────────────

def promise_and_contact(ledger: Ledger) -> dict:
    ptps, visits = ledger.ptps, ledger.visits
    resolved = ptps[ptps.resolved_status.notna()] if len(ptps) else ptps
    return {
        "ptp_kept_rate": (float((resolved.resolved_status == "HONORED").mean())
                          if len(resolved) else None),
        "n_ptps_resolved": int(len(resolved)),
        "rpc_rate": float(visits.met.mean()) if len(visits) else None,
        "n_visits": int(len(visits)),
    }


# ── the report ──────────────────────────────────────────────────────────────

def realism_report(ledger: Ledger, panel: pd.DataFrame,
                   cfg: LedgerConfig | None = None) -> dict:
    forward, cure, matrix = roll_rates(panel)
    shape = payment_shape(ledger)
    pc = promise_and_contact(ledger)

    npa = float((panel.dpd_bucket == "NPA").mean())
    performing = float((panel.dpd <= 30).mean())
    current = float((panel.dpd_bucket == "CURRENT").mean())
    material = float(1.0 - panel.y.mean())

    checks = [
        _result("forward_flow_rate", forward),
        _result("cure_rate", cure),
        _result("npa_share", npa),
        _result("performing_share", performing),
        _result("current_share", current, gated=False),
        _result("vintage_monotone_frac", vintage_monotonicity(panel)),
        _result("exact_emi_share", shape["exact_emi_share"]),
        _result("round_number_share", shape["round_number_share"]),
        _result("partial_share", shape["partial_share"]),
        _result("ptp_kept_rate", pc["ptp_kept_rate"]),
        _result("rpc_rate", pc["rpc_rate"]),
        _result("material_payment_rate", material),
    ]
    gated = [c for c in checks if c["status"] != "REPORTED"]
    return {
        "passed": all(c["status"] == "PASS" for c in gated),
        "n_failed": sum(c["status"] == "FAIL" for c in gated),
        "checks": checks,
        "roll_matrix": matrix.round(3).to_dict() if len(matrix) else {},
        "descriptives": {
            "panel_rows": int(len(panel)),
            "distinct_loans": int(panel.loan_id.nunique()),
            "months": int(panel.month_index.nunique()),
            "dpd_mean": round(float(panel.dpd.mean()), 1),
            "dpd_median": float(panel.dpd.median()),
            "dpd_p95": float(panel.dpd.quantile(0.95)),
            "dpd_max": float(panel.dpd.max()),
            "bucket_mix": panel.dpd_bucket.value_counts(normalize=True).round(4).to_dict(),
            "n_payments": shape["n_payments"],
            "n_visits": pc["n_visits"],
            "n_ptps_resolved": pc["n_ptps_resolved"],
            "loans_written_off": int((ledger.lifecycle.event == "WRITTEN_OFF").sum()),
            "loans_settled": int((ledger.lifecycle.event == "SETTLED").sum()),
            "loans_closed": int((ledger.lifecycle.event == "CLOSED").sum()),
            "loans_opened": int((ledger.lifecycle.event == "OPENED").sum()),
        },
    }


def format_report(rep: dict) -> str:
    lines = ["", "REALISM CHECKS (Phase 1) — " +
             ("PASS" if rep["passed"] else f"FAIL ({rep['n_failed']})"), ""]
    lines.append(f"{'check':24s} {'value':>8s} {'band':>15s}  {'result':8s} provenance")
    lines.append("-" * 88)
    for c in rep["checks"]:
        v = "  n/a" if c["value"] is None else f"{c['value']:.4f}"
        band = f"[{c['low']:.2f}, {c['high']:.2f}]"
        lines.append(f"{c['check']:24s} {v:>8s} {band:>15s}  {c['status']:8s} "
                     f"{c['provenance']}")
    d = rep["descriptives"]
    lines += ["", f"rows {d['panel_rows']}  loans {d['distinct_loans']}  "
                  f"months {d['months']}  payments {d['n_payments']}  "
                  f"visits {d['n_visits']}",
              f"DPD mean {d['dpd_mean']} median {d['dpd_median']} "
              f"p95 {d['dpd_p95']} max {d['dpd_max']}",
              f"bucket mix {d['bucket_mix']}",
              f"lifecycle opened {d['loans_opened']} closed {d['loans_closed']} "
              f"settled {d['loans_settled']} written_off {d['loans_written_off']}", ""]
    return "\n".join(lines)
