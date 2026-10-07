# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-24 — New file. E09: one complete ReportPayload that exercises every
#   unit, chart kind and table shape the renderers support. The renderer tests
#   render it; E10's template gallery can preview it.
#
#   Every organisation name below is FICTIONAL (docs/DATA-MODEL-V2.md,
#   Appendix C) and lives in DEMO_BANK / DEMO_AGENCIES only — the bank's name is
#   under review, so a rename must be one line here and nothing else. No test
#   and no renderer may spell these names out; they read them from this module.
# ───────────────────────────────────────────────────────────────────────────
from __future__ import annotations

from datetime import date, datetime, timezone

from app.reports.payload import (
    Chart, ChartKind, Column, Direction, Kpi, Narrative, ReportPayload, Section,
    Series, Table, Unit,
)

DEMO_BANK = "Meridian Trust Bank"

# (name, zone) — Appendix C.2, the seven ACTIVE agencies.
DEMO_AGENCIES: list[tuple[str, str]] = [
    ("Aravalli Field Services", "North"),
    ("Sarthak Recovery Services", "North"),
    ("Rajputana Credit Solutions", "North"),
    ("Konkan Asset Recovery", "West"),
    ("Sabarmati Collection Services", "West"),
    ("Deccan Resolve Associates", "South"),
    ("Coromandel Recovery Partners", "South"),
]

# Invented figures, one per agency, in DEMO_AGENCIES order.
_PLACED_INR = [4.21e7, 5.02e7, 3.14e7, 6.66e7, 3.38e7, 5.47e7, 2.96e7]
_RECOVERED_INR = [1.02e7, 1.31e7, 0.62e7, 1.88e7, 0.71e7, 1.49e7, 0.58e7]
_EXPECTED_INR = [0.97e7, 1.18e7, 0.71e7, 1.65e7, 0.77e7, 1.38e7, 0.66e7]
_AGENTS = [18, 22, 16, 28, 17, 24, 15]
_GEOFENCE_FAIL = [0.021, 0.034, 0.018, 0.027, None, 0.015, 0.041]   # one agency not yet measured


def sample_payload() -> ReportPayload:
    agencies = [name for name, _ in DEMO_AGENCIES]
    zones = sorted({z for _, z in DEMO_AGENCIES})
    by_zone = {z: sum(r for (_, zz), r in zip(DEMO_AGENCIES, _RECOVERED_INR) if zz == z) for z in zones}
    placed_total, recovered_total = sum(_PLACED_INR), sum(_RECOVERED_INR)

    months = ["Apr", "May", "Jun", "Jul", "Aug", "Sep"]
    scorecard_rows = [
        {"agency": name, "zone": zone, "agents": _AGENTS[i], "placed": _PLACED_INR[i],
         "recovered": _RECOVERED_INR[i], "expected": _EXPECTED_INR[i],
         "rve": _RECOVERED_INR[i] / _EXPECTED_INR[i], "geofence_fail": _GEOFENCE_FAIL[i]}
        for i, (name, zone) in enumerate(DEMO_AGENCIES)
    ]
    # Long enough to cross a PDF page and split across PPTX slides.
    ledger_rows = [
        {"week": f"W{w:02d}", "agency": agencies[w % len(agencies)],
         "visits": 380 + (w * 37) % 140, "ptps": 60 + (w * 11) % 45,
         "collected": 1.1e6 + ((w * 173_000) % 900_000), "kept_rate": 0.41 + ((w * 7) % 20) / 100}
        for w in range(1, 31)
    ]

    return ReportPayload(
        report_id="sample-board-2026-09",
        template="board",
        title="Collections Board Pack",
        subtitle="Field recovery through placed agencies",
        organisation=DEMO_BANK,
        scope="All agencies · all zones",
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 30),
        generated_at=datetime(2026, 9, 30, 18, 30, tzinfo=timezone.utc),
        prepared_for="Board of Directors",
        synthetic=True,
        sections=[
            Section(
                section_id="summary",
                title="Portfolio summary",
                narrative=Narrative(
                    text=(f"Agencies recovered {recovered_total / 1e7:.2f} crore of "
                          f"{placed_total / 1e7:.2f} crore placed this month. The West zone "
                          "led on recovery; geofence failures stayed under five percent everywhere measured."),
                    ai_generated=True),
                kpis=[
                    Kpi(kpi_id="placed_balance", label="Balance placed", value=placed_total, unit=Unit.INR,
                        basis="Principal + overdue placed with agencies at month end", prior=placed_total * 0.96),
                    Kpi(kpi_id="recovered_amount", label="Recovered", value=recovered_total, unit=Unit.INR,
                        direction=Direction.HIGHER_IS_BETTER, prior=recovered_total * 0.91),
                    Kpi(kpi_id="recovery_rate", label="Recovery rate", value=recovered_total / placed_total,
                        unit=Unit.PCT, direction=Direction.HIGHER_IS_BETTER, prior=0.192),
                    Kpi(kpi_id="active_agents", label="Field agents", value=float(sum(_AGENTS)), unit=Unit.COUNT),
                    Kpi(kpi_id="days_to_first_visit", label="Days to first visit", value=2.4, unit=Unit.DAYS,
                        direction=Direction.LOWER_IS_BETTER, prior=3.1),
                    Kpi(kpi_id="recovery_vs_expected", label="Recovery vs expected",
                        value=recovered_total / sum(_EXPECTED_INR), unit=Unit.RATIO,
                        direction=Direction.HIGHER_IS_BETTER),
                    Kpi(kpi_id="cost_to_collect", label="Cost to collect", value=None, unit=Unit.PCT,
                        basis="Not measurable until commission invoices are loaded"),
                ],
                charts=[
                    Chart(chart_id="recovered_by_zone", title="Recovered by zone", kind=ChartKind.BAR,
                          unit=Unit.INR, categories=zones,
                          series=[Series(name="Recovered", values=[by_zone[z] for z in zones])]),
                    Chart(chart_id="recovery_rate_trend", title="Recovery rate, last six months", kind=ChartKind.LINE,
                          unit=Unit.PCT, categories=months,
                          series=[Series(name="Actual", values=[0.171, 0.183, 0.179, 0.188, 0.192, 0.201]),
                                  Series(name="Expected", values=[0.175, 0.178, 0.181, 0.184, 0.187, None],
                                         reference=True)]),
                ],
            ),
            Section(
                section_id="agencies",
                title="Agency performance",
                narrative=Narrative(
                    text="Recovery against expectation adjusts for case mix: above 1.00x an agency "
                         "recovered more than its book predicted.",
                    ai_generated=False),
                charts=[
                    Chart(chart_id="dpd_mix", title="Placed balance by DPD bucket", kind=ChartKind.STACKED_BAR,
                          unit=Unit.INR, categories=[n.split()[0] for n in agencies], scale="sequential",
                          series=[Series(name=b, values=[p * f for p in _PLACED_INR])
                                  for b, f in [("0-30", 0.22), ("31-60", 0.27), ("61-90", 0.24), ("90+", 0.27)]]),
                ],
                tables=[
                    Table(table_id="agency_scorecard", title="Agency scorecard", columns=[
                        Column(key="agency", label="Agency"),
                        Column(key="zone", label="Zone"),
                        Column(key="agents", label="Agents", unit=Unit.COUNT),
                        Column(key="placed", label="Placed", unit=Unit.INR),
                        Column(key="recovered", label="Recovered", unit=Unit.INR),
                        Column(key="expected", label="Expected", unit=Unit.INR),
                        Column(key="rve", label="Recovery vs expected", unit=Unit.RATIO),
                        Column(key="geofence_fail", label="Geofence failures", unit=Unit.PCT),
                    ], rows=scorecard_rows, note="Geofence failures: one agency not yet measured."),
                ],
            ),
            Section(
                section_id="field_activity",
                title="Field activity by week",
                tables=[
                    Table(table_id="weekly_activity", title="Weekly field activity", columns=[
                        Column(key="week", label="Week"),
                        Column(key="agency", label="Agency"),
                        Column(key="visits", label="Visits", unit=Unit.COUNT),
                        Column(key="ptps", label="PTPs", unit=Unit.COUNT),
                        Column(key="collected", label="Collected", unit=Unit.INR),
                        Column(key="kept_rate", label="PTP kept", unit=Unit.PCT),
                    ], rows=ledger_rows),
                ],
            ),
        ],
    )
