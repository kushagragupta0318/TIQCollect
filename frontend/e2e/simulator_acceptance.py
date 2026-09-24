"""P0-08 — acceptance run for the mobile app simulator, in a real browser.

What it proves, end to end through the running stack (web :5473 → api):
  1. both frames log in side by side and keep separate sessions (P0-01);
  2. the phone's GPS comes from the simulator, not the laptop (P0-02/P0-04):
     a fix set on the simulator map reaches the backend as an agent.location;
  3. the manager is PUSHED events (P0-06/P0-07): check-in, a GPS ping, an SOS
     raised on the phone, and a visit recorded as the agent, each appear in
     the simulator's timeline — and the script reports how long each took;
  4. SOS lights the manager frame's bell.

The visit is submitted through the agent API with the phone frame's own
token rather than by filling the 2,300-line Record Visit form: the question
here is whether the WEB side hears the phone within 2 s, and the form is the
same POST. Run it inside RBI contact hours (08:00–19:00 IST) or the server
correctly refuses the visit.

    pip install playwright && python -m playwright install chromium
    python frontend/e2e/simulator_acceptance.py [--headed] [--base http://localhost:5473]

Credentials come from TIQ_AGENT_EMAIL / TIQ_AGENT_PASSWORD /
TIQ_MANAGER_EMAIL / TIQ_MANAGER_PASSWORD, defaulting to the demo fixture's
accounts (backend/fixtures/README.md).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import Frame, Page, sync_playwright

AGENT = (os.environ.get("TIQ_AGENT_EMAIL", "agent002@tiqcollect.in"),
         os.environ.get("TIQ_AGENT_PASSWORD", "Agent@123"))
MANAGER = (os.environ.get("TIQ_MANAGER_EMAIL", "manager1@tiqcollect.in"),
           os.environ.get("TIQ_MANAGER_PASSWORD", "Manager@123"))
BUDGET_MS = 2_000
SHOTS = Path(__file__).with_name("screenshots")

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + detail) if detail else ''}")


def frame(page: Page, name: str, timeout_s: float = 20.0) -> Frame:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for f in page.frames:
            if f.name == name:
                return f
        page.wait_for_timeout(200)
    raise RuntimeError(f"frame {name} not found")


def login(f: Frame, email: str, password: str) -> None:
    f.wait_for_selector("input[type=email]", timeout=20_000)
    f.fill("input[type=email]", email)
    f.fill("input[type=password]", password)
    f.click("button[type=submit]")


def rows(page: Page, label: str) -> int:
    return page.get_by_text(label, exact=True).count()


def timeline_has(page: Page, label: str, timeout_ms: int, before: int) -> float | None:
    """Wait for a NEW timeline row with `label` (count rises above `before`).

    The timeline replays recent history on connect, so an earlier run's rows
    are already on screen — waiting for "a row" would measure nothing (the
    first draft of this script reported 13 ms that way)."""
    t0 = time.monotonic()
    deadline = t0 + timeout_ms / 1000
    while time.monotonic() < deadline:
        if rows(page, label) > before:
            return (time.monotonic() - t0) * 1000
        page.wait_for_timeout(25)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:5473")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()
    SHOTS.mkdir(exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000},
                                  permissions=["geolocation"], geolocation={"latitude": 12.97, "longitude": 77.59})
        page = ctx.new_page()
        console_errors: list[str] = []
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

        print("simulator:", f"{args.base}/simulator")
        page.goto(f"{args.base}/simulator", wait_until="domcontentloaded")
        page.get_by_text("Mobile App Simulator").wait_for(timeout=20_000)

        agent_f = frame(page, "tiq-slot:agent")
        mgr_f = frame(page, "tiq-slot:manager")
        if "/login" in agent_f.url:
            login(agent_f, *AGENT)
        if "/login" in mgr_f.url:
            login(mgr_f, *MANAGER)
        agent_f.wait_for_url("**/agent/**", timeout=20_000)
        mgr_f.wait_for_url("**/manager/**", timeout=20_000)

        slots = page.evaluate("""() => ({
            agent: JSON.parse(localStorage.getItem('tiq_auth:agent')||'{}').state?.user?.role,
            manager: JSON.parse(localStorage.getItem('tiq_auth:manager')||'{}').state?.user?.role })""")
        check("two sessions side by side", slots == {"agent": "FIELD_AGENT", "manager": "AGENCY_MANAGER"},
              json.dumps(slots))

        page.get_by_text("Live — pushed").wait_for(timeout=20_000)
        check("manager stream is live (pushed, not polling)", True)

        # The beat loads once the phone is signed in; wait for its stops.
        page.wait_for_function(
            "() => [...document.querySelectorAll('select option')].length > 1", timeout=20_000)
        agent_token = page.evaluate("() => JSON.parse(localStorage.getItem('tiq_auth:agent')).state.accessToken")

        # ── GPS from the simulator reaches the backend ────────────────────
        before = rows(page, "GPS ping")
        page.locator("select").select_option(index=1)          # go to stop 1
        # The phone's reporter batches fixes and flushes every 15 s, so this
        # measures its cadence, not push latency.
        t = timeline_has(page, "GPS ping", 25_000, before)
        check("simulated GPS reaches the backend (agent.location)", t is not None,
              f"after {t:.0f} ms (15 s reporter cadence)" if t else "no ping in 25 s")

        # ── check-in on the phone ────────────────────────────────────────
        checkin = agent_f.get_by_role("button", name="Check In")
        if checkin.count():
            checkin.first.click()
            agent_f.wait_for_timeout(1_200)                     # camera falls back to the placeholder selfie
            before = rows(page, "Checked in")
            for label in ("Confirm Check-In", "Confirm", "Check In"):
                btn = agent_f.get_by_role("button", name=label)
                if btn.count():
                    btn.last.click()
                    break
            t = timeline_has(page, "Checked in", 10_000, before)
            check("check-in on phone → manager within 2 s", t is not None and t <= BUDGET_MS,
                  f"{t:.0f} ms" if t else "not received")
        else:
            check("check-in on phone → manager within 2 s", True, "already on duty — skipped")

        # ── visit, as the phone's agent ──────────────────────────────────
        beat = page.evaluate("""async (tok) => (await fetch('/api/v1/agent/beat',
            {headers:{Authorization:'Bearer '+tok}})).json()""", agent_token)
        pending = [c for c in beat.get("cases", []) if c["id"] not in set(beat.get("visited_today_ids") or [])]
        if pending:
            c = pending[0]
            lat, lon = c["customer"]["latitude"], c["customer"]["longitude"]
            body = {"check_in_latitude": lat + 0.0001, "check_in_longitude": lon, "outcome": "NOT_AVAILABLE",
                    "customer_met": False, "not_met_reason": "PREMISES_LOCKED",
                    "notes": "simulator acceptance run"}
            before = rows(page, "Visit recorded")
            # Fired without awaiting the response: the POST returns only after
            # the AI visit report (an LLM call, ~1.5 s) that runs AFTER the
            # event is published. Timing from the send measures what the manager
            # experiences — agent taps Submit, manager is told.
            page.evaluate("""([tok, id, body]) => { window.__visit = fetch('/api/v1/agent/cases/'+id+'/visit',
                {method:'POST', headers:{Authorization:'Bearer '+tok,'Content-Type':'application/json'},
                 body: JSON.stringify(body)}).then(r => r.status); }""", [agent_token, c["id"], body])
            t = timeline_has(page, "Visit recorded", 10_000, before)
            status = page.evaluate("() => window.__visit")
            check("visit recorded → manager within 2 s of submit", status == 200 and t is not None and t <= BUDGET_MS,
                  f"HTTP {status}, {t:.0f} ms" if t else f"HTTP {status}, not received")
        else:
            check("visit recorded → manager within 2 s", True, "no pending case today — skipped")

        # ── SOS on the phone ─────────────────────────────────────────────
        try:
            before = rows(page, "SOS raised")
            agent_f.get_by_role("button", name="SOS", exact=True).first.click()
            t = timeline_has(page, "SOS raised", 12_000, before)
            check("SOS tap on phone → manager within 2 s", t is not None and t <= BUDGET_MS,
                  f"{t:.0f} ms incl. GPS read" if t else "not received")
            try:
                agent_f.get_by_text("your manager has your exact location").first.wait_for(timeout=5_000)
                check("SOS carried the simulated LIVE fix, not a fallback", True)
            except Exception:
                check("SOS carried the simulated LIVE fix, not a fallback", False)
            try:
                mgr_f.locator("text=/SOS/i").first.wait_for(timeout=5_000)
                check("SOS lights the manager's bell", True)
            except Exception:
                check("SOS lights the manager's bell", False)
            page.screenshot(path=str(SHOTS / "simulator.png"))
        finally:
            cancelled_before = rows(page, "SOS cancelled")
            # Stand the agent down whatever happened above, so a failed run can
            # never leave the demo book with an agent in SOS. Uses the phone's
            # own token — a fresh login would rotate its refresh token away.
            page.evaluate("""async (tok) => fetch('/api/v1/agent/sos/cancel',
                {method:'POST', headers:{Authorization:'Bearer '+tok}})""", agent_token)
        t = timeline_has(page, "SOS cancelled", 8_000, cancelled_before)
        check("SOS cancel reaches the manager", t is not None, f"{t:.0f} ms" if t else "not received")

        serious = [e for e in console_errors if "favicon" not in e.lower()]
        check("no console errors on the simulator page", not serious, "; ".join(serious[:3]))
        browser.close()

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed · screenshot: {SHOTS / 'simulator.png'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
