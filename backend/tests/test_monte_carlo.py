# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-24 — New (task E02 / E04). Pins the properties the Command Center
#   simulator never had, each an executable check rather than a comment:
#   mass conservation, absorbing states, seed reproducibility (and
#   independence from the thread count), stress monotonicity, TRUE
#   percentiles (and that CC's summed per-state percentiles differ from
#   them), Dirichlet uncertainty widening a thin segment, levers moving in
#   their documented directions, account counts that are counted, and a
#   backtest whose 80% band covers ~80% on a panel generated from a known
#   matrix — and FAILS to on a shocked panel when the engine is told there
#   are no shocks, so the gate is shown to be load-bearing.
#
#   The synthetic panel generator below is deliberately NOT the engine's
#   kernel (plain rng.choice per state group), so the backtest is not the
#   engine agreeing with itself.
#
# 2026-09-24 (later) — Pins the audit's fixes: written-off balances leave the
#   ECL base; Doubtful is reached by the 12-month age rule and only by it;
#   the run carries its caveats until a real backtest passes; an NPA status
#   wins over the bucket; DPD_RANGE agrees with dpd_bucket_for; Sector Shock's
#   known miscalibration is declared; the backtest cohort uses no look-ahead.
#   Mass conservation now uses array_equal on the counts, not allclose.
#   The panel generator applies the same 12-month rule the engine does,
#   written independently.
# ───────────────────────────────────────────────────────────────────────────
from dataclasses import replace
from datetime import date

import numpy as np
import pytest

from app.strategy import monte_carlo as mc
from app.strategy.backtest import (
    EXCLUSION_FLAG_SHARE, HistoricalPanel, backtest, count_transitions, estimate_shock_sigma, npa_age_at,
)
from app.strategy.monte_carlo import (
    PRESETS, EngineConfig, Levers, Portfolio, SegmentMatrices, compare_scenarios,
    fit_cure_elasticity, lever_log_odds, macro_shift, sensitivity_grid, simulate, tornado,
)
from app.strategy.states import (
    CURRENT, DIRECTION, DPD_RANGE, LIVE, N_STATES, NPA, NPA_DOUBTFUL, NPA_SUB, REACHABLE, RESOLVED,
    SMA_0, SMA_1, SMA_2, STATE_INDEX, STATES, WRITTEN_OFF, months_between, portfolio_state,
    states_from_buckets,
)
from app.strategy.synthetic import reference_matrix, synthetic_book

FAST = EngineConfig(n_workers=2)


@pytest.fixture(scope="module")
def book():
    return synthetic_book(n_accounts=1500, seed=11, loan_types=("PERSONAL", "HOME"), regions=("N", "S"),
                          state_mix=(0.55, 0.12, 0.09, 0.08, 0.10, 0.06, 0.0, 0.0))


@pytest.fixture(scope="module")
def base_run(book):
    pf, mx = book
    return simulate(pf, mx, n_paths=120, horizon_months=12, seed=5, config=FAST)


# ── State space ───────────────────────────────────────────────────────────────


def test_every_dpd_bucket_and_loan_status_of_the_models_maps():
    from app.models.loan import DPDBucket, LoanStatus
    for bucket in DPDBucket:
        assert portfolio_state(bucket) in STATES
    for status in LoanStatus:
        assert portfolio_state(DPDBucket.CURRENT, status) in STATES
    assert portfolio_state(DPDBucket.BUCKET_2) == "SMA_1"
    assert portfolio_state("NPA", "WRITTEN_OFF") == "WRITTEN_OFF"   # terminal status wins
    assert portfolio_state("BUCKET_3", "SETTLED") == "RESOLVED"
    assert portfolio_state("CURRENT", "CLOSED") == "RESOLVED"


def test_npa_splits_at_twelve_whole_months_of_npa_since():
    since = date(2025, 3, 31)
    assert portfolio_state("NPA", "NPA", since, date(2026, 3, 30)) == "NPA_SUB"
    assert portfolio_state("NPA", "NPA", since, date(2026, 3, 31)) == "NPA_DOUBTFUL"
    assert portfolio_state("NPA") == "NPA_SUB"  # unknown age reads as the lower bound
    assert months_between(date(2028, 1, 31), date(2028, 2, 29)) == 1
    assert months_between(date(2026, 1, 15), date(2026, 2, 14)) == 0
    got = states_from_buckets(["NPA", "NPA", "BUCKET_1", "CURRENT"], [11, 12, np.nan, 0],
                              ["ACTIVE", None, "ACTIVE", "WRITTEN_OFF"])
    assert [STATES[s] for s in got] == ["NPA_SUB", "NPA_DOUBTFUL", "SMA_0", "WRITTEN_OFF"]
    with pytest.raises(ValueError):
        portfolio_state("BUCKET_9")


def test_an_npa_status_wins_over_the_bucket_until_regularised():
    # A part-paid NPA whose DPD fell to 45 is still an NPA (RBI), not SMA_1.
    assert portfolio_state("BUCKET_2", "NPA") == "NPA_SUB"
    assert portfolio_state("CURRENT", "NPA", date(2025, 1, 1), date(2026, 2, 1)) == "NPA_DOUBTFUL"
    assert portfolio_state("BUCKET_2", "ACTIVE") == "SMA_1"
    got = states_from_buckets(["BUCKET_2", "BUCKET_2", "CURRENT"], [3, 14, np.nan], ["NPA", "NPA", "NPA"])
    assert [STATES[s] for s in got] == ["NPA_SUB", "NPA_DOUBTFUL", "NPA_SUB"]
    # ... and an NPA leaves only to CURRENT, WRITTEN_OFF or RESOLVED.
    for i in NPA:
        assert not REACHABLE[i, [SMA_0, SMA_1, SMA_2]].any()
        assert REACHABLE[i, CURRENT] and REACHABLE[i, RESOLVED] and REACHABLE[i, WRITTEN_OFF]
    assert not REACHABLE[NPA_DOUBTFUL, NPA_SUB]


def test_dpd_range_agrees_with_dpd_bucket_for():
    # DPD_RANGE restates 30/60/90 because importing models.loan would pull the
    # DB engine into a DB-free package; this is what keeps the copy honest.
    from app.models.loan import dpd_bucket_for
    sma = (SMA_0, SMA_1, SMA_2)
    for d in range(0, 401):
        s = STATE_INDEX[portfolio_state(dpd_bucket_for(d))]
        hits = [st for st in sma if DPD_RANGE[st][0] <= d <= DPD_RANGE[st][1]]
        if s in sma:
            assert hits == [s], d
        elif s == CURRENT:
            assert hits == [] and d == 0, d
        else:
            assert s == NPA_SUB and hits == [] and d >= DPD_RANGE[NPA_SUB][0], d


# ── Core invariants ───────────────────────────────────────────────────────────


def test_mass_is_conserved_on_every_path_every_month(book, base_run):
    pf, _ = book
    cnt = base_run.paths.state_count
    assert np.array_equal(cnt.sum(axis=-1), np.full(cnt.shape[:2], float(pf.n)))  # exact, not close
    shares = base_run.per_path("STATE_SHARE")
    assert np.allclose(shares.sum(axis=-1), 100.0)
    assert np.allclose(base_run.paths.state_balance.sum(axis=-1), pf.balance.sum())


def test_absorbing_states_stay_absorbed(book, base_run):
    cnt = base_run.paths.state_count
    for s in (WRITTEN_OFF, RESOLVED):
        assert np.all(np.diff(cnt[:, :, s], axis=1) >= 0)
    pf, mx = book
    n = 200
    gone = Portfolio(state=np.r_[np.full(n, WRITTEN_OFF), np.full(n, RESOLVED)], balance=np.full(2 * n, 1e5),
                     segment=np.zeros(2 * n, dtype=int))
    res = simulate(gone, mx, scenario=PRESETS["severely_adverse"], levers=Levers(agency_capacity=3.0,
                   settlement_discount=0.5, writeoff_policy_months=1), n_paths=20, horizon_months=6, seed=1, config=FAST)
    assert np.all(res.paths.state_count[:, :, WRITTEN_OFF] == n)
    assert np.all(res.paths.state_count[:, :, RESOLVED] == n)
    assert res.summary["RECOVERED_CASH"].mean[-1] == 0


def test_same_seed_same_numbers_different_seed_different_any_thread_count(book):
    pf, mx = book
    a = simulate(pf, mx, n_paths=40, horizon_months=6, seed=9, config=EngineConfig(n_workers=1))
    b = simulate(pf, mx, n_paths=40, horizon_months=6, seed=9, config=EngineConfig(n_workers=3))
    c = simulate(pf, mx, n_paths=40, horizon_months=6, seed=10, config=EngineConfig(n_workers=1))
    for field in ("seg_state_count", "seg_state_balance", "recovered_cash", "written_off_balance", "pd_by_state"):
        assert np.array_equal(getattr(a.paths, field), getattr(b.paths, field)), field
    assert not np.array_equal(a.paths.seg_state_count, c.paths.seg_state_count)
    assert not np.array_equal(a.paths.recovered_cash, c.paths.recovered_cash)


def test_the_guide_table_draw_is_exact():
    rng = np.random.default_rng(0)
    R, K = 400, 64
    p9 = rng.dirichlet(np.full(9, 0.3), size=R)
    p9[::7] = 0.0
    p9[::7, 3] = 1.0                       # absorbing-like rows
    p9[1::5, 8] = 0.0                      # zero tail
    p9[1::5] /= p9[1::5].sum(axis=1, keepdims=True)
    thr9, guide = mc._tables(p9, K)
    rows = rng.integers(0, R, (3, 30_000)).astype(np.int32)
    buf = mc._Buffers(rows.shape)
    buf.u[:] = rng.random(rows.shape, dtype=np.float32)
    got = mc._draw(rows * K, thr9, guide, K, buf)
    thr = thr9.reshape(R, 9)[:, :8]
    assert np.array_equal(got, (buf.u[..., None] >= thr[rows]).sum(axis=-1))
    assert np.all(got[np.isin(rows, np.arange(0, R, 7))] == 3)        # impossible moves stay impossible


# ── Stress, percentiles, uncertainty ──────────────────────────────────────────


def test_stress_is_monotone_in_the_presets(book):
    pf, mx = book
    g = {k: simulate(pf, mx, scenario=PRESETS[k], n_paths=80, horizon_months=12, seed=3, config=FAST)
         .summary["GNPA_PCT"] for k in ("baseline", "adverse", "severely_adverse")}
    assert g["severely_adverse"].p50[-1] >= g["adverse"].p50[-1] >= g["baseline"].p50[-1]
    assert np.mean(g["severely_adverse"].p50[1:]) > np.mean(g["adverse"].p50[1:]) > np.mean(g["baseline"].p50[1:])


def test_true_percentiles_are_taken_per_path(base_run):
    per_path = base_run.per_path("GNPA_PCT")
    band = base_run.summary["GNPA_PCT"]
    assert np.allclose(band.p10, np.percentile(per_path, 10, axis=0))
    assert np.allclose(band.p90, np.percentile(per_path, 90, axis=0))
    assert np.all((band.p5 <= band.p10) & (band.p10 <= band.p50) & (band.p50 <= band.p90) & (band.p90 <= band.p95))
    # CC's construction (routers/simulate.py:139-141): per-state p10s, summed.
    bal = base_run.paths.state_balance
    live = bal[..., :6].sum(axis=-1, keepdims=True)
    share = 100 * bal / live
    cc_p10 = sum(np.percentile(share[..., s], 10, axis=0) for s in NPA)
    # The sum of p10s is not the p10 of the sum: different paths are low in
    # different states. It is a different number, not a rounding of this one.
    assert np.max(np.abs(cc_p10 - band.p10)[1:]) > 0.05


def test_dirichlet_uncertainty_widens_a_thin_segment():
    P = reference_matrix("PERSONAL")
    thick = SegmentMatrices(("x",), (P * 50_000)[None], loan_types=("PERSONAL",))
    thin = SegmentMatrices(("x",), (P * 25)[None], loan_types=("PERSONAL",))
    rng = np.random.default_rng(2)
    pf = Portfolio(state=rng.choice(6, 2000, p=[0.5, 0.15, 0.1, 0.1, 0.1, 0.05]), balance=rng.lognormal(12, 0.5, 2000),
                   segment=np.zeros(2000, dtype=int))
    cfg = EngineConfig(shock_sigma=0.0, n_workers=2)
    w = {}
    for name, m in (("thick", thick), ("thin", thin)):
        b = simulate(pf, m, n_paths=150, horizon_months=12, seed=4, config=cfg).summary["GNPA_PCT"]
        w[name] = b.p90[-1] - b.p10[-1]
    assert w["thin"] > 2 * w["thick"]


# ── Levers ────────────────────────────────────────────────────────────────────


def test_status_quo_levers_are_exactly_neutral():
    assert np.all(lever_log_odds(Levers(), EngineConfig()) == 0.0)
    assert Levers().settlement_hazard() == 0.0


def test_levers_move_results_in_their_documented_directions(book):
    pf, mx = book

    def run(**kw):
        return simulate(pf, mx, levers=Levers(**kw), n_paths=60, horizon_months=12, seed=8, config=FAST).summary

    base = run()
    g = lambda s: s["GNPA_PCT"].mean[-1]  # noqa: E731
    assert g(run(agency_capacity=1.5)) < g(base) < g(run(agency_capacity=0.5))    # capacity lowers GNPA
    assert g(run(placement_rate=0.8)) < g(base)          # field_effect 0.5 > capacity_elasticity 0.35
    assert g(run(legal_threshold_days=60)) < g(base)     # legal reaches into SMA_2
    settle = run(settlement_discount=0.4)
    assert settle["SETTLEMENT_CASH"].mean[-1] > 0 and g(settle) < g(base)
    wo = run(writeoff_policy_months=3)
    assert wo["WRITE_OFF_ACCOUNTS"].mean[-1] > base["WRITE_OFF_ACCOUNTS"].mean[-1] and g(wo) < g(base)
    richer = {k: v + 5.0 for k, v in mc.DEFAULT_COMMISSION_PCT.items()}
    assert run(commission_pct=richer)["COST"].mean[-1] > base["COST"].mean[-1]


# ── Outputs ───────────────────────────────────────────────────────────────────


def test_account_counts_are_counted_from_the_paths(base_run):
    cnt = base_run.paths.state_count
    wo_accounts = base_run.per_path("WRITE_OFF_ACCOUNTS")
    assert np.allclose(wo_accounts, cnt[:, :, WRITTEN_OFF] - cnt[:, :1, WRITTEN_OFF])
    assert np.allclose(base_run.per_path("NPA_ACCOUNTS"), cnt[..., list(NPA)].sum(axis=-1))


def test_ecl_is_pd_times_lgd_times_ead(base_run):
    ifrs9 = mc.Ifrs9Params()
    s3 = base_run.ifrs9["stage3"]["COVERAGE"]
    assert np.allclose(s3.p50, ifrs9.lgd_stage3)          # stage 3: PD = 1
    pd1 = base_run.ifrs9["stage1"]["PD"].p50
    assert np.all((pd1 > 0) & (pd1 < 1))
    ead = base_run.paths.ead_stage
    lgd = np.array([ifrs9.lgd_stage1, ifrs9.lgd_stage2, ifrs9.lgd_stage3])
    assert np.all(base_run.paths.ecl_stage <= ead * lgd + 1e-6)


def test_write_offs_leave_the_ecl_base(book, base_run):
    # A write-off derecognises the asset (IFRS 9 5.4.4): the staged EAD is the
    # LIVE book and stage 3 is exactly the NPA balance at LGD3 (PD 1).
    bal = base_run.paths.state_balance
    assert np.allclose(base_run.paths.ead_stage.sum(axis=-1), bal[..., list(LIVE)].sum(axis=-1))
    lgd3 = mc.Ifrs9Params().lgd_stage3
    assert np.allclose(base_run.paths.ecl_stage[..., 2], bal[..., list(NPA)].sum(axis=-1) * lgd3)
    # So a harsher write-off policy moves loss into WRITE_OFFS and cannot
    # raise ECL through the balances it wrote off.
    pf, mx = book
    soft = simulate(pf, mx, n_paths=60, horizon_months=12, seed=8, config=FAST).summary
    harsh = simulate(pf, mx, levers=Levers(writeoff_policy_months=3), n_paths=60, horizon_months=12,
                     seed=8, config=FAST).summary
    assert harsh["WRITE_OFFS"].mean[-1] > soft["WRITE_OFFS"].mean[-1]
    assert harsh["ECL"].mean[-1] < soft["ECL"].mean[-1]


def test_doubtful_is_reached_by_age_and_only_by_age():
    # The observed matrix carries a 1/12 Sub -> Doubtful cell AND a Doubtful ->
    # Sub cell; neither may move anyone. Only 12 months of NPA age does.
    counts = np.zeros((1, N_STATES, N_STATES))
    counts[0, NPA_SUB, NPA_SUB], counts[0, NPA_SUB, NPA_DOUBTFUL] = 11_000, 1_000
    counts[0, NPA_DOUBTFUL, NPA_DOUBTFUL], counts[0, NPA_DOUBTFUL, NPA_SUB] = 1_000, 500
    mx = SegmentMatrices(("x",), counts, loan_types=("PERSONAL",))
    n = 1000
    pf = Portfolio(state=np.full(n, NPA_SUB), balance=np.full(n, 1e5), segment=np.zeros(n, dtype=int),
                   npa_age_months=np.r_[np.full(n // 2, 6.0), np.full(n - n // 2, np.nan)])  # 6, and unknown -> 0
    cfg = replace(FAST, prior_strength=0.0, prior_floor=0.0)
    d = simulate(pf, mx, scenario=PRESETS["severely_adverse"], n_paths=16, horizon_months=13, seed=2,
                 config=cfg).paths.state_count[..., NPA_DOUBTFUL]
    assert np.all(d[:, :6] == 0)              # nobody Doubtful before 12 months as an NPA
    assert np.all(d[:, 6:12] == n // 2)       # the age-6 half at month 6, all of it, every path
    assert np.all(d[:, 12:] == n)             # the age-0 half at month 12
    # A Sub-standard input already 12+ months old is reconciled at the start, and says so.
    old = Portfolio(state=[NPA_SUB, NPA_SUB], balance=[1e5, 1e5], segment=[0, 0], npa_age_months=[15.0, 2.0])
    r = simulate(old, mx, n_paths=4, horizon_months=1, seed=0, config=cfg)
    assert np.all(r.paths.state_count[:, 0, NPA_DOUBTFUL] == 1)
    assert r.diagnostics["npa_sub_reclassified_doubtful_at_start"] == 1


def test_the_run_carries_its_caveats_until_a_real_backtest_passes(book, base_run):
    h = base_run.headline()
    assert h["synthetic_inputs"] and not h["calibrated_by_backtest"]
    assert h["synthetic_warning"].startswith("SYNTHETIC") and len(h["assumptions"]) >= 10
    assert all(r["synthetic_warning"] == h["synthetic_warning"] and r["calibrated_by_backtest"] is False
               for r in base_run.to_records())
    pf, mx = book
    real_pf, real_mx = replace(pf, synthetic=False), replace(mx, synthetic=False)

    def run(p=real_pf, m=real_mx, **kw):
        return simulate(p, m, n_paths=4, horizon_months=2, seed=1, config=FAST, **kw)

    # Real counts alone are NOT calibration: every elasticity is still assumed.
    plain = run()
    assert not plain.calibrated_by_backtest and plain.synthetic_warning.startswith("UNCALIBRATED")
    rep = backtest(_panel(100), horizon_months=6, n_paths=40, config=FAST)
    good = replace(rep, synthetic=False, coverage=0.80, exclusion_high=False)
    ok = run(calibration=good)
    assert ok.calibrated_by_backtest and ok.synthetic_warning is None
    assert ok.run_record()["assumptions"] and ok.calibration["passes"]
    for bad in (rep if rep.synthetic else replace(rep, synthetic=True), replace(good, coverage=0.40),
                replace(good, engine_version="mc-0.0.0"), replace(good, exclusion_high=True)):
        r = run(calibration=bad)
        assert not r.calibrated_by_backtest and r.synthetic_warning
    assert run(pf, mx, calibration=good).synthetic_warning.startswith("SYNTHETIC")


def test_sector_shock_calibration_is_declared_not_tuned():
    # CC's sector loadings make "sector_shock" the worst case for a secured
    # book and milder than "adverse" for personal loans. Recalibration is an
    # E05/E08 item; when it lands, these numbers change on purpose.
    def sh(preset, lt):
        return macro_shift(PRESETS[preset], lt)
    assert sh("sector_shock", "HOME") == pytest.approx(2.11, abs=1e-9)
    assert sh("sector_shock", "GOLD") == pytest.approx(2.4375, abs=1e-9)
    assert sh("sector_shock", "PERSONAL") == pytest.approx(0.1225, abs=1e-9)
    assert sh("sector_shock", "HOME") > sh("severely_adverse", "HOME")
    assert sh("sector_shock", "GOLD") > sh("severely_adverse", "GOLD")
    assert sh("sector_shock", "PERSONAL") < sh("adverse", "PERSONAL")
    assert any("Sector Shock" in a and "E05/E08" in a for a in mc.ASSUMPTIONS)


def test_records_are_shaped_for_simulation_results(base_run):
    rows = base_run.to_records(include_segments=True)
    T = base_run.horizon_months
    assert {r["unit"] for r in rows} <= {"INR", "PCT", "COUNT"}
    assert all("calibrated_by_backtest" in r and "synthetic_warning" in r for r in rows)
    share = [r for r in rows if r["metric"] == "STATE_SHARE"]
    assert len(share) == N_STATES * (T + 1)
    seg = [r for r in rows if r["segment_key"] != "ALL"]
    assert len(seg) == 2 * len(base_run.segment_keys) * (T + 1)
    h = base_run.headline()
    assert h["recovery_at_risk"] == base_run.summary["NET_RECOVERY"].p5[-1]
    assert set(base_run.breakdown("loan_type")) == {"PERSONAL", "HOME"}


def test_stratified_subsample_is_horvitz_thompson_weighted(book):
    pf, mx = synthetic_book(n_accounts=6000, seed=3, loan_types=("PERSONAL", "HOME"), regions=("N", "S"))
    sub = pf.subsample(1500, seed=1)
    assert sub.n <= 1600
    assert np.isclose(sub.weights.sum(), pf.n)
    assert abs((sub.weights * sub.balance).sum() / pf.balance.sum() - 1) < 0.03
    full = simulate(pf, mx, n_paths=60, horizon_months=6, seed=2, config=FAST)
    part = simulate(pf, mx, n_paths=60, horizon_months=6, seed=2, config=FAST, max_accounts=1500)
    assert part.subsampled and part.n_simulated_accounts == sub.n
    assert abs(part.summary["GNPA_PCT"].p50[-1] - full.summary["GNPA_PCT"].p50[-1]) < 1.0


# ── Tornado, grid, comparison ─────────────────────────────────────────────────


def test_tornado_grid_and_comparison(book):
    pf, mx = book
    bars = tornado(pf, mx, steps={"agency_capacity": 0.3, "unemployment": 1.0}, n_paths=40, horizon_months=6,
                   max_accounts=None, config=FAST)
    assert bars[0].swing >= bars[1].swing
    by = {b.factor: b for b in bars}
    assert by["agency_capacity"].metric_high < by["agency_capacity"].metric_low
    assert by["unemployment"].metric_high > by["unemployment"].metric_low
    grid = sensitivity_grid(pf, mx, x_factor="gdp", x_values=[0.0, -4.0], y_factor="unemployment",
                            y_values=[0.0, 2.5], n_paths=30, horizon_months=6, max_accounts=None, config=FAST)
    z = np.array(grid["z"])
    assert z[1, 1] > z[0, 0]
    cmp = compare_scenarios(pf, mx, {"base": PRESETS["baseline"], "adverse": PRESETS["adverse"]},
                            n_paths=60, horizon_months=6, config=FAST)
    row = next(r for r in cmp["table"] if r["scenario"] == "adverse" and r["metric"] == "GNPA_PCT")
    unpaired = np.hypot(cmp["results"]["base"].summary["GNPA_PCT"].sem[-1], row["sem"])
    assert row["delta_mean_vs_first"] > 0 and row["delta_sem_paired"] < unpaired


def test_fit_cure_elasticity_recovers_a_known_slope():
    rng = np.random.default_rng(1)
    x = np.linspace(-0.7, 0.7, 40)
    n = np.full(x.size, 800)
    y = rng.binomial(n, 1 / (1 + np.exp(-(-1.0 + 0.35 * x))))
    beta, se = fit_cure_elasticity(x, y, n)
    assert abs(beta - 0.35) < 3 * se and se < 0.1


# ── Backtest (E04) ────────────────────────────────────────────────────────────


def _panel(seed: int, sigma: float = 0.0, rho: float = 0.6, n: int = 2500, months: int = 24,
           loan_types=("PERSONAL", "HOME", "AUTO")) -> HistoricalPanel:
    """Accounts walked through KNOWN matrices, optionally under a common AR(1)
    shock applied as the engine documents it, with Sub-standard / Doubtful
    decided by NPA age as RBI decides it. Independent code: rng.choice."""
    rng = np.random.default_rng(seed)
    mats = [reference_matrix(lt) for lt in loan_types]
    seg = rng.integers(0, len(mats), n)
    st = np.empty((n, months), dtype=np.int8)
    st[:, 0] = rng.choice(N_STATES, n, p=[0.6, 0.12, 0.08, 0.07, 0.13, 0.0, 0, 0])
    age = np.where(st[:, 0] == NPA_SUB, rng.integers(0, 20, n), -1)
    st[age >= 12, 0] = NPA_DOUBTFUL
    z = rng.standard_normal()
    for m in range(1, months):
        z = rho * z + np.sqrt(1 - rho * rho) * rng.standard_normal()
        nxt = np.empty(n, dtype=np.int8)
        for s, P in enumerate(mats):
            num = P * np.exp(DIRECTION * sigma * z)
            Ps = num / num.sum(axis=1, keepdims=True)
            for i in range(N_STATES):
                a = np.flatnonzero((seg == s) & (st[:, m - 1] == i))
                if a.size:
                    nxt[a] = rng.choice(N_STATES, a.size, p=Ps[i])
        was, now = np.isin(st[:, m - 1], NPA), np.isin(nxt, NPA)
        age = np.where(now, np.where(was, age + 1, 0), -1)
        st[:, m] = np.where(now, np.where(age >= 12, NPA_DOUBTFUL, NPA_SUB), nxt)
    return HistoricalPanel(st, rng.lognormal(12, 0.8, n), seg, tuple(loan_types),
                           segment_loan_types=tuple(loan_types), synthetic=True)


def test_backtest_band_covers_near_nominal_on_a_known_matrix():
    reps = [backtest(_panel(100 + k), horizon_months=6, n_paths=200, seed=k, config=FAST) for k in range(5)]
    coverage = np.mean([r.coverage for r in reps])
    assert reps[0].nominal == pytest.approx(0.80)
    assert 0.65 <= coverage <= 0.95, coverage
    assert all(r.max_excluded_share == 0 and not r.exclusion_high and r.cohort_accounts == 2500 for r in reps)


def test_backtest_cohort_uses_no_look_ahead():
    p = _panel(9)
    origin, n = p.n_months - 1 - 6, p.state.shape[0]
    rng = np.random.default_rng(3)
    live_then = np.flatnonzero(np.isin(p.state[:, origin + 2], LIVE))
    gone = rng.choice(live_then, 200, replace=False)
    closed = rng.choice(np.setdiff1d(np.arange(n), gone), 100, replace=False)
    st = p.state.copy()
    st[gone, origin + 3:] = -1                    # live accounts that stop reporting
    st[closed, origin + 2] = RESOLVED             # closed loans that then stop reporting
    st[closed, origin + 3:] = -1
    q = HistoricalPanel(st, p.balance, p.segment, p.segment_keys, p.segment_loan_types, synthetic=True)
    rep = backtest(q, horizon_months=6, origin=origin, n_paths=40, config=FAST)
    assert rep.cohort_accounts == n               # nothing after the origin decides membership
    assert rep.excluded_share_by_month[:2] == (0.0, 0.0)
    assert all(e == pytest.approx(200 / n) for e in rep.excluded_share_by_month[2:])
    assert rep.exclusion_high and not rep.passes()   # 8% > EXCLUSION_FLAG_SHARE
    assert 200 / n > EXCLUSION_FLAG_SHARE
    live = np.setdiff1d(np.arange(n), gone)
    last = st[live, origin + 6]
    expected = (np.sum(last == RESOLVED) + np.sum(last < 0)) / live.size   # the closed ones carried as RESOLVED
    cell = next(c for c in rep.cells if c["month"] == 6 and c["state"] == "RESOLVED")
    assert cell["actual"] == pytest.approx(expected)


def test_npa_age_at_origin_is_read_from_the_panel():
    st = np.array([[4, 4, 4, 4], [0, 4, 4, 4], [-1, 4, 4, 4], [4, 4, 0, 4], [0, 0, 0, 0]], dtype=np.int8)
    p = HistoricalPanel(st, np.ones(5), np.zeros(5, dtype=int), ("x",))
    age, lower = npa_age_at(p, 3)
    assert age.tolist() == [3, 2, 2, 0, -1]
    assert lower.tolist() == [True, False, True, False, False]   # reaches the panel start / a gap


def test_backtest_catches_an_engine_that_ignores_shocks():
    blind = replace(FAST, shock_sigma=0.0)
    panels = [_panel(300 + k, sigma=0.6) for k in range(5)]
    told_none = np.mean([backtest(p, n_paths=150, seed=k, config=blind, estimate_sigma=False).coverage
                         for k, p in enumerate(panels)])
    fitted = np.mean([backtest(p, n_paths=150, seed=k, config=FAST).coverage for k, p in enumerate(panels)])
    assert told_none < 0.65
    assert fitted > told_none


def test_backtest_fits_on_the_past_only():
    p = _panel(7, months=14)
    origin = 8
    before = count_transitions(p, 0, origin)
    scrambled = p.state.copy()
    scrambled[:, origin + 1:] = np.random.default_rng(0).integers(0, N_STATES, scrambled[:, origin + 1:].shape)
    q = HistoricalPanel(scrambled, p.balance, p.segment, p.segment_keys, p.segment_loan_types)
    assert np.array_equal(count_transitions(q, 0, origin), before)
    rep = backtest(p, horizon_months=5, origin=origin, n_paths=40, config=FAST)
    assert rep.fit_window == (0, origin) and rep.transitions_used == int(before.sum())
    with pytest.raises(ValueError):
        backtest(p, horizon_months=6, origin=origin)   # past the end of the panel


def test_estimate_shock_sigma_recovers_iid_volatility():
    est = [estimate_shock_sigma(_panel(500 + k, sigma=0.4, rho=0.0), 0, 20) for k in range(3)]
    assert 0.28 <= np.mean(est) <= 0.52
    calm = [estimate_shock_sigma(_panel(600 + k), 0, 20) for k in range(3)]
    assert np.mean(calm) < 0.15
