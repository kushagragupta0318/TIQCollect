# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-28 (B16, d4) — NEW. Appendix C.4: each agency's hidden quality, and
#   the rates at which compliance breaches are injected. GENERATOR-ONLY: the
#   values here reach the book only through the events they cause, and are
#   written out solely to the ground-truth manifest (app/demo/manifest.py),
#   never to a product table. That is what lets the agency scorecard, the
#   case-mix-adjusted ranking (H01) and every compliance detector be scored
#   against a truth they cannot see.
#
#   No knob here targets a model metric (Gini, KS, AUC). The skill terms move
#   the ledger's existing agent effect (simulator.py: 0.65 * agent_skill on a
#   borrower's pay hazard after contact), and the contact multipliers move its
#   existing visit / call hazards; what any model then achieves is measured,
#   not set.
# ────────────────────────────────────────────────────────────────────────────
"""Latent agency quality and injected-breach rates (generator-only truth)."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date


@dataclass(frozen=True)
class AgencyLatent:
    #: Mean and spread of the agency's agents' skill, on the ledger's scale
    #: (its own default is mean 0, sd 0.35, clipped to ±0.9).
    skill_mean: float
    skill_sd: float
    #: Multipliers on the ledger's visit / call hazards (contact intensity).
    visit_rate: float = 1.0
    call_rate: float = 1.0
    #: Injected breaches, per recorded visit:
    #: an attempt refused for contact hours (audit row, no visit), a visit
    #: pushed through the fence as ADDRESS_ISSUE from far away, and a visit
    #: whose selfie was taken somewhere else (fabricated evidence).
    out_of_hours_rate: float = 0.010
    fence_gaming_rate: float = 0.015
    fabricated_photo_rate: float = 0.004
    #: A window in which fence gaming runs at `spike_rate` instead.
    spike_from: date | None = None
    spike_to: date | None = None
    spike_rate: float = 0.0
    story: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("spike_from", "spike_to"):
            d[k] = d[k].isoformat() if d[k] else None
        return d


#: Appendix C.4, as numbers. Aravalli is absent on purpose: its book is v1's,
#: transformed, not generated, so it carries no injected truth.
AGENCY_LATENT = {
    "SAHYADRI": AgencyLatent(0.35, 0.20, visit_rate=1.15, call_rate=1.10, out_of_hours_rate=0.004,
                             fence_gaming_rate=0.008, fabricated_photo_rate=0.001,
                             story="strong across the board"),
    "DECCAN": AgencyLatent(-0.25, 0.25, visit_rate=1.45, call_rate=1.40,
                           story="high contact rate, weak conversion"),
    "SABARMATI": AgencyLatent(0.30, 0.15, visit_rate=1.00, call_rate=1.05, out_of_hours_rate=0.005,
                              fence_gaming_rate=0.008, story="small but efficient"),
    "AWADH": AgencyLatent(-0.35, 0.30, visit_rate=0.95, call_rate=0.90, out_of_hours_rate=0.040,
                          fence_gaming_rate=0.060, fabricated_photo_rate=0.030,
                          spike_from=date(2026, 7, 15), spike_to=date(2026, 9, 1), spike_rate=0.30,
                          story="weak, with an evidence-integrity problem (hence suspended 2026-09-02)"),
    "SARTHAK": AgencyLatent(0.05, 0.30, story="mid-table"),
    "RAJPUTANA": AgencyLatent(-0.05, 0.30, visit_rate=0.90, story="mid-table, lighter contact"),
    "COROMANDEL": AgencyLatent(0.10, 0.25, call_rate=1.15, story="young book, steady"),
    "ALMORA": AgencyLatent(0.00, 0.30, story="Kumaon's only agency; exists for tenant isolation"),
}

#: Contact time of day (IST hours) — the channel `contact_risk` lacked. A met
#: visit / answered call skews to the evening, when borrowers are home; a
#: miss skews to mid-day. Drawn CONDITIONAL on the ledger's own met/answered
#: outcome, so the time carries information about contact without changing
#: whether contact happened.
CONTACT_HOUR = {
    "visit_met": (16.8, 1.9),         # mean, sd, then clipped to the RBI window
    "visit_missed": (13.0, 2.4),
    "call_answered": (17.5, 2.0),
    "call_missed": (12.5, 2.6),
}

#: The Indian collections calendar, as shifts on the ledger's payment
#: log-odds. For the demo books it REPLACES the ledger's generic sine, whose
#: phase ran from each agency's onboarding date, so payments were flat by
#: calendar month. By month: January's post-bonus cash, a March year-end
#: drive, the April slump after it, rabi-harvest cash in May-June, the
#: monsoon dip in July-August, festival spending in October-November.
#: Roughly zero-mean over a year, so the book's overall payment rate holds.
CALENDAR_SEASON = {1: 0.10, 2: 0.00, 3: 0.35, 4: -0.15, 5: 0.10, 6: 0.12,
                   7: -0.12, 8: -0.20, 9: -0.05, 10: -0.10, 11: -0.25, 12: 0.05}
#: Within a month: salary credit lifts the first week (and a little after);
#: the rest of the month gives it back, so the monthly mean is ~0.
SALARY_DAYS = ((1, 7, 0.15), (8, 10, 0.05), (11, 31, -0.055))


def calendar_season(day: date) -> float:
    """The demo books' payment log-odds shift on a calendar day."""
    within = next(v for lo, hi, v in SALARY_DAYS if lo <= day.day <= hi)
    return CALENDAR_SEASON[day.month] + within


#: Complaints (disputes.kind = COMPLAINT) per placed case over its life, and
#: the share about agent conduct; scaled up for an agency with a breach story.
COMPLAINT_RATE = 0.012
COMPLAINT_RATE_BREACHING = 0.045

#: A settlement the ledger records (lifecycle SETTLED) is preceded by an offer;
#: some offers are declined first.
SETTLEMENT_DECLINE_BEFORE_ACCEPT = 0.25
