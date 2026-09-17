"""
Field Activity — what happened to the cases PLANNED for field work inside one
time window. One definition, used by two endpoints: the overview's funnel
(GET /manager/dashboard/field-activity) and the Cases list's `activity`
filter, so a number on the funnel and the rows behind its link are the same
set by construction rather than by two queries agreeing.

─── CHANGELOG (prototype → product) ─────────────────────────────────────────
2026-09-17 — NEW.

THE RULE THAT MATTERS: TODAY MEANS TODAY'S VISITS ONLY. Every visit-based
stage reads visits whose `check_in_time` falls INSIDE the selected window and
nothing else. A case visited yesterday and not today is NOT visited today,
whatever its latest historical outcome says. The classifier below is handed
only in-window visits (`load_field_activity` filters them before it is
called), and it has no access to any other case state, so it cannot leak a
historical outcome into a window even by mistake.

Definitions (each is a pure function of the window):

  planned           distinct case ids on the manager's agents' beats whose
                    beat_date lies in [window_start_date, effective_date] —
                    Beat.ordered_case_ids, the field plan's own source of truth.
                    Counted once however many beats or days carry the case.
  visited           planned cases with >= 1 visit in the window, by one of the
                    manager's agents.
  met               visited cases with >= 1 in-window visit whose outcome is
                    NOT in NOT_MET_OUTCOMES (NOT_AVAILABLE, ADDRESS_ISSUE).
                    REVISIT IS MET: the agent reached the household and needs
                    to come back; it is never a not-met reason.
  paid_or_promised  visited cases with >= 1 in-window visit whose outcome is in
                    PAID_OR_PROMISED_OUTCOMES (PAID_FULL, PART_PAID,
                    PART_PAID_PTP, PTP).

A case is counted ONCE per stage regardless of how many in-window visits it
has: the stage is the best in-window outcome, so three NOT_AVAILABLEs and one
PTP is one paid-or-promised case, not four visited ones.

Reasons partition the drop-offs exactly:
  visited == not_met + met
  met     == paid_or_promised + met_no_money
A not-met case's reason is the outcome of its LATEST in-window visit (all of
them are not-met outcomes by definition). A met-but-no-money case's reason is
the outcome of its latest in-window MET visit. Planned-but-not-visited cases
contribute to the planned -> visited drop-off only and appear in NO reason
list — "nobody went" is a different fact from "went, not home".

Window anchoring: the effective date and end-of-day come from the dashboard's
`_effective_today` (manager.py) and are passed in, never recomputed here, so
the funnel's "today" is the same day as every other "today" figure on the
overview — including its UTC day boundaries.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Literal, Optional, Sequence

from app.models.beat import Beat
from app.models.visit import Visit, VisitOutcome

Window = Literal["today", "7d", "30d"]
WINDOW_DAYS: dict[str, int] = {"today": 1, "7d": 7, "30d": 30}

Stage = Literal["planned", "visited", "met", "paid_or_promised", "not_met", "met_no_money"]
STAGES: tuple[str, ...] = ("planned", "visited", "met", "paid_or_promised", "not_met", "met_no_money")

# The only two outcomes that mean the household was NOT reached. Everything
# else — including REVISIT, DISPUTE, RTP, BROKEN_PTP and DECEASED — means the
# agent got through to somebody, so it is MET.
NOT_MET_OUTCOMES: frozenset[str] = frozenset({
    VisitOutcome.NOT_AVAILABLE.value,
    VisitOutcome.ADDRESS_ISSUE.value,
})
PAID_OR_PROMISED_OUTCOMES: frozenset[str] = frozenset({
    VisitOutcome.PAID_FULL.value,
    VisitOutcome.PART_PAID.value,
    VisitOutcome.PART_PAID_PTP.value,
    VisitOutcome.PTP.value,
})


def _outcome_str(o) -> str:
    return o.value if hasattr(o, "value") else str(o)


@dataclass(frozen=True)
class VisitRow:
    case_id: str
    outcome: str
    check_in_time: datetime


@dataclass
class FunnelResult:
    planned: int
    visited: int
    met: int
    paid_or_promised: int
    not_met_reasons: dict[str, int] = field(default_factory=dict)
    met_no_money_reasons: dict[str, int] = field(default_factory=dict)
    # Case ids per stage — what the Cases list filter serves.
    ids: dict[str, set[str]] = field(default_factory=dict)

    @property
    def not_met(self) -> int:
        return self.visited - self.met

    @property
    def met_no_money(self) -> int:
        return self.met - self.paid_or_promised

    def drop_offs(self) -> dict[str, Optional[float]]:
        """Per-transition drop-off as a percentage of the PRECEDING stage.
        None when the preceding stage is zero — there is nothing to drop from."""
        def pct(prev: int, nxt: int) -> Optional[float]:
            return None if prev <= 0 else round((prev - nxt) / prev * 100.0, 2)
        return {
            "planned_to_visited": pct(self.planned, self.visited),
            "visited_to_met": pct(self.visited, self.met),
            "met_to_paid_or_promised": pct(self.met, self.paid_or_promised),
        }


def classify(planned_ids: Iterable[str], visits: Iterable[VisitRow]) -> FunnelResult:
    """The whole funnel from (planned ids, IN-WINDOW visits). Pure.

    `visits` must already be restricted to the window and to the caller's
    tenant; this function does not know what a window is, which is what makes
    it impossible for it to read a visit from outside one.
    """
    planned: set[str] = set(planned_ids)
    by_case: dict[str, list[VisitRow]] = {}
    for v in visits:
        if v.case_id in planned:
            by_case.setdefault(v.case_id, []).append(v)

    visited: set[str] = set()
    met: set[str] = set()
    paid: set[str] = set()
    not_met_ids: set[str] = set()
    met_no_money_ids: set[str] = set()
    not_met_reasons: Counter[str] = Counter()
    met_no_money_reasons: Counter[str] = Counter()
    reason_ids: dict[str, set[str]] = {}

    for cid, rows in by_case.items():
        rows.sort(key=lambda r: r.check_in_time)
        outcomes = [_outcome_str(r.outcome) for r in rows]
        visited.add(cid)
        met_rows = [o for o in outcomes if o not in NOT_MET_OUTCOMES]
        if any(o in PAID_OR_PROMISED_OUTCOMES for o in outcomes):
            paid.add(cid); met.add(cid)
        elif met_rows:
            met.add(cid); met_no_money_ids.add(cid)
            reason = met_rows[-1]           # latest in-window MET visit
            met_no_money_reasons[reason] += 1
            reason_ids.setdefault(reason, set()).add(cid)
        else:
            not_met_ids.add(cid)
            reason = outcomes[-1]           # latest in-window visit, a not-met outcome
            not_met_reasons[reason] += 1
            reason_ids.setdefault(reason, set()).add(cid)

    return FunnelResult(
        planned=len(planned), visited=len(visited), met=len(met), paid_or_promised=len(paid),
        not_met_reasons=dict(not_met_reasons.most_common()),
        met_no_money_reasons=dict(met_no_money_reasons.most_common()),
        ids={
            "planned": planned, "visited": visited, "met": met, "paid_or_promised": paid,
            "not_met": not_met_ids, "met_no_money": met_no_money_ids,
            **{f"outcome:{k}": v for k, v in reason_ids.items()},
        },
    )


@dataclass(frozen=True)
class WindowBounds:
    window: str
    start_date: date          # first calendar day of the window
    end_date: date            # the dashboard's effective date
    start: datetime           # UTC midnight of start_date
    end: datetime             # the dashboard's end_of_day (see _effective_today)


def window_bounds(window: str, eff_date: date, end_of_day: datetime) -> WindowBounds:
    """Anchor a window on the dashboard's effective date.

    `end_of_day` is the value `_effective_today` returns — end of
    max(eff_date, wall-clock today) in UTC — so "today" here is exactly the
    dashboard's today, including its boundary convention. `start` is UTC
    midnight of the first day, matching how `_effective_today` builds its own
    start. Unknown windows fall back to today rather than raising: a bad query
    string should not 500 a dashboard card.
    """
    days = WINDOW_DAYS.get(window, 1)
    start_date = eff_date - timedelta(days=days - 1)
    start = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    return WindowBounds(window if window in WINDOW_DAYS else "today", start_date, eff_date, start, end_of_day)


def planned_case_ids(db, agent_ids: Sequence[str], bounds: WindowBounds) -> set[str]:
    """Distinct case ids on the agents' beats dated inside the window."""
    if not agent_ids:
        return set()
    rows = (
        db.query(Beat.ordered_case_ids)
        .filter(Beat.agent_id.in_(list(agent_ids)),
                Beat.beat_date >= bounds.start_date,
                Beat.beat_date <= bounds.end_date)
        .all()
    )
    out: set[str] = set()
    for (ids,) in rows:
        out.update(ids or [])
    return out


def in_window_visits(db, agent_ids: Sequence[str], case_ids: set[str], bounds: WindowBounds) -> list[VisitRow]:
    """Visits by the agents, on the planned cases, with check_in_time inside
    the window. The ONLY place visits enter the funnel."""
    if not agent_ids or not case_ids:
        return []
    rows = (
        db.query(Visit.case_id, Visit.outcome, Visit.check_in_time)
        .filter(Visit.agent_id.in_(list(agent_ids)),
                Visit.case_id.in_(list(case_ids)),
                Visit.check_in_time >= bounds.start,
                Visit.check_in_time <= bounds.end)
        .all()
    )
    return [VisitRow(case_id=r[0], outcome=_outcome_str(r[1]), check_in_time=r[2]) for r in rows]


def load_field_activity(db, agent_ids: Sequence[str], bounds: WindowBounds) -> FunnelResult:
    planned = planned_case_ids(db, agent_ids, bounds)
    visits = in_window_visits(db, agent_ids, planned, bounds)
    return classify(planned, visits)


def case_ids_for(db, agent_ids: Sequence[str], bounds: WindowBounds,
                 stage: str, outcomes: Optional[Iterable[str]] = None) -> set[str]:
    """The case ids behind one funnel figure — the Cases list filter.

    `stage` is one of STAGES; `outcomes` optionally narrows a reason stage
    (not_met / met_no_money) to the cases whose classifying outcome is one of
    them, so "Not available · 137" links to exactly those 137.
    """
    result = load_field_activity(db, agent_ids, bounds)
    ids = set(result.ids.get(stage, set()))
    if outcomes:
        wanted: set[str] = set()
        for o in outcomes:
            wanted |= result.ids.get(f"outcome:{o}", set())
        ids &= wanted
    return ids


def payload(result: FunnelResult, bounds: WindowBounds) -> dict:
    return {
        "window": bounds.window,
        "window_start": bounds.start_date.isoformat(),
        "window_end": bounds.end_date.isoformat(),
        # The exact timestamps the visit filter used, so a reader can reproduce
        # the count against the visits table.
        "visits_from": bounds.start.isoformat(),
        "visits_to": bounds.end.isoformat(),
        "planned": result.planned,
        "visited": result.visited,
        "met": result.met,
        "paid_or_promised": result.paid_or_promised,
        "not_met": result.not_met,
        "met_no_money": result.met_no_money,
        "drop_offs": result.drop_offs(),
        "not_met_reasons": result.not_met_reasons,
        "met_no_money_reasons": result.met_no_money_reasons,
        "definitions": {
            "not_met_outcomes": sorted(NOT_MET_OUTCOMES),
            "paid_or_promised_outcomes": sorted(PAID_OR_PROMISED_OUTCOMES),
        },
    }
