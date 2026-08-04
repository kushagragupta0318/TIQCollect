"""Field Ops contract stub — runs on :8300, alongside the real app in app/.

Two backends live in this folder and they do different jobs:

  app/           TIQCollect proper — the real Field Ops platform (:8400, needs
                 Postgres/Redis/MinIO, see ../docker-compose.yml)
  stub_main.py   this file — the /api/field-ops/* contract from
                 ../../FIELD_OPS_INTEGRATION.md, served from seeded data

Command Center proxies to FIELD_OPS_URL (default :8300) for that contract. This
stub answers it from randomly generated agents and visits — plausible shapes,
not TIQCollect data.

TIQCollect now implements the same four endpoints itself, translating them out
of its real cases/visits/payments schema:

    app/api/v1/endpoints/field_ops.py

so the contract no longer needs a stub. Set FIELD_OPS_URL=http://127.0.0.1:8400
in command-center/backend/.env and Command Center's Field Operations page reads
live data. This file stays only because :8400 needs Postgres/Redis/MinIO up,
while :8300 needs nothing — it is the fallback for a demo on a bare machine, not
the intended source.

It also HOSTS the Field Recovery UI (TIQCollect's Collection Agency overview) at
/tiqcollect — see the mount at the bottom of this file. The UI belongs to the
field-ops side of the boundary, so it is served from here rather than baked into
Command Center's own frontend bundle. Note the mount serves the prebuilt offline
bundle, so it is frozen at its last build; Command Center can be pointed at the
live :5473 dev server instead via command-center/frontend/.env.example.

    python -m uvicorn stub_main:app --port 8300 --app-dir field-ops-stub/backend
"""
import os
import random
import pathlib
import datetime
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Field Ops (dev stub)")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

_rng = random.Random(42)
_ZONES = ["North", "South", "East", "West", "Central"]
_STATUSES = ["In Transit", "At Location", "Deviation", "Absent"]
_STATUS_WEIGHTS = [0.35, 0.40, 0.10, 0.15]
_OUTCOMES = ["PTP Obtained", "Payment Collected", "Not Home", "Refused", "Address Not Found", "Disputed"]

_NAMES = [
    "Prerana Vinod Patole", "Pallavi Pandit Rao", "A Nagendra Rao", "D Bhaskar Naik",
    "T Srividya", "Kiran Kumar Reddy", "Meena Sundaram", "Rajesh Bhatia",
    "Suman Lata Verma", "Arjun Krishnamurthy", "Farida Sheikh", "Vivek Chauhan",
]

_ZONE_CENTERS = {
    "North": (28.6139, 77.2090), "South": (12.9716, 77.5946), "East": (22.5726, 88.3639),
    "West": (19.0760, 72.8777), "Central": (23.2599, 77.4126),
}


def _seed_agents():
    now = datetime.datetime.now(datetime.timezone.utc)
    agents = []
    for i, name in enumerate(_NAMES):
        zone = _ZONES[i % len(_ZONES)]
        status = _rng.choices(_STATUSES, weights=_STATUS_WEIGHTS)[0]
        target = _rng.randint(8, 16)
        completed = 0 if status == "Absent" else min(target, _rng.randint(0, target))
        mins_ago = _rng.randint(0, 240)
        lat, lng = _ZONE_CENTERS[zone]
        agents.append({
            "agent_id": f"FOS-{i+1:03d}",
            "name": name,
            "status": status,
            "zone": zone,
            "last_activity_at": (now - datetime.timedelta(minutes=mins_ago)).isoformat(),
            "last_activity_label": "Just now" if mins_ago < 5 else (
                f"{mins_ago} mins ago" if mins_ago < 60 else f"{mins_ago // 60} hr ago"),
            "visits_completed": completed,
            "visits_target": target,
            "ptp_collected_today": 0 if status == "Absent" else _rng.randint(0, 8) * 42500,
            "current_location": None if status == "Absent" else {
                "lat": round(lat + _rng.uniform(-0.15, 0.15), 4),
                "lng": round(lng + _rng.uniform(-0.15, 0.15), 4),
            },
        })
    return agents


_AGENTS = _seed_agents()

_VISITS = []
for _i in range(120):
    agent = _rng.choice(_AGENTS)
    _VISITS.append({
        "visit_id": f"V-{88000 + _i}",
        "account_id": f"LN{_rng.randint(1, 30000):07d}",
        "agent_id": agent["agent_id"],
        "visited_at": (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=_rng.randint(0, 21))).isoformat(),
        "outcome": _rng.choice(_OUTCOMES),
        "amount_collected": _rng.choice([0, 0, 0, 5000, 12000, 25000, 40000]),
        "notes": "",
    })


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "field-ops-stub"}


@app.get("/api/field-ops/summary")
def summary():
    active = sum(1 for a in _AGENTS if a["status"] != "Absent")
    deviations = sum(1 for a in _AGENTS if a["status"] == "Deviation")
    total_target = sum(a["visits_target"] for a in _AGENTS)
    total_completed = sum(a["visits_completed"] for a in _AGENTS)
    ptp_today = sum(a["ptp_collected_today"] for a in _AGENTS)
    return {
        "active_agents": active,
        "total_agents": len(_AGENTS),
        "geofence_deviations": deviations,
        "visit_target_pct": round(total_completed / total_target * 100, 1) if total_target else 0,
        "ptp_collected_today": ptp_today,
    }


@app.get("/api/field-ops/agents")
def agents():
    return _AGENTS


@app.get("/api/field-ops/coverage")
def coverage():
    result = []
    for zone in _ZONES:
        zone_agents = [a for a in _AGENTS if a["zone"] == zone]
        target = sum(a["visits_target"] for a in zone_agents)
        completed = sum(a["visits_completed"] for a in zone_agents)
        result.append({
            "zone": zone,
            "pct_covered": round(completed / target * 100, 1) if target else 0,
            "accounts_assigned": target * 3,
            "accounts_visited": completed * 3,
        })
    return result


@app.get("/api/field-ops/visits")
def visits(account_id: str | None = None):
    if account_id:
        return [v for v in _VISITS if v["account_id"] == account_id]
    return _VISITS[:50]


# ── Field Recovery UI ────────────────────────────────────────────────────────
# The static, backend-free TIQCollect build (Collection Agency's Overview, plus
# the manager analytics view its leaderboard links into). Command Center's Field
# Recovery page frames this over HTTP rather than shipping it in its own bundle,
# which keeps the whole field-ops surface — data AND UI — behind one boundary
# that FIELD_OPS_URL points at.
#
#   /tiqcollect/collection_dashboard/index.html   ← the agency overview (entry)
#   /tiqcollect/index.html#/manager-bridge        ← manager analytics
#
# The bundle is a build artifact of ../frontend (this repo now holds its source),
# rebuilt with: node field-ops-stub/frontend/scripts/build-offline-bundle.mjs
UI_DIR = pathlib.Path(__file__).parent / "static" / "tiqcollect-offline"
UI_PATH = os.getenv("FIELD_OPS_UI_PATH", "/tiqcollect")

class _RevalidatingStaticFiles(StaticFiles):
    """StaticFiles that always makes the browser revalidate.

    Plain StaticFiles sends ETag and Last-Modified but no Cache-Control, so
    browsers fall back to heuristic caching and may serve a stale copy without
    asking. That bites here because collection_dashboard/script.js is edited in
    place and the filename never changes: an edit would appear to have no
    effect until someone thought to hard-refresh, and a framed copy inside
    Command Center is even more prone to it.

    `no-cache` does not mean "do not cache" — it means "cache, but revalidate
    before reuse". The existing ETag still turns that into a cheap 304, so this
    costs a conditional request, not a re-download.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


if UI_DIR.is_dir():
    app.mount(UI_PATH, _RevalidatingStaticFiles(directory=UI_DIR, html=True), name="field-recovery-ui")
else:
    print(f"[field-ops-stub] Field Recovery UI not found at {UI_DIR} — "
          f"{UI_PATH} will 404. Build it with scripts/build-tiqcollect-offline.mjs")
