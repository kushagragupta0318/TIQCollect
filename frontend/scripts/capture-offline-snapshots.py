"""Capture TIQCollect manager API responses into one static snapshot bundle.

Run once, with the TIQCollect backend live. Output is a single JSON file keyed
by "GET <path>?<sorted query>" (path relative to /api/v1), which the frontend's
offline axios adapter reads instead of talking to a backend.
"""
import json
import sys
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8400"
OUT = sys.argv[2]

EMAIL = "manager1@tiqcollect.in"
PASSWORD = "Manager@123"
DEVICE_ID = "snapshot-capture-device"

API = BASE.rstrip("/") + "/api/v1"


def request(method, path, params=None, body=None, token=None, timeout=120):
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def key(path, params=None):
    if params:
        qs = urllib.parse.urlencode(sorted(params.items()))
        return f"GET {path}?{qs}"
    return f"GET {path}"


snapshots = {}
skipped = []

# Re-runs are incremental: anything already in the bundle is kept as-is unless
# --refresh is passed, so adding endpoints doesn't re-spend LLM calls.
REFRESH = "--refresh" in sys.argv
try:
    with open(OUT, encoding="utf-8") as fh:
        snapshots = json.load(fh)
    print(f"loaded {len(snapshots)} existing snapshots from {OUT}")
except (OSError, ValueError):
    pass


def capture(path, params=None, timeout=120, required=True):
    k = key(path, params)
    if k in snapshots and not REFRESH:
        return snapshots[k]
    try:
        snapshots[k] = request("GET", path, params=params, token=TOKEN, timeout=timeout)
        print("  ok   ", k)
        return snapshots[k]
    except Exception as exc:  # noqa: BLE001 - a miss is data we simply won't ship
        skipped.append((k, str(exc)[:120]))
        print("  SKIP ", k, "->", str(exc)[:120])
        if required:
            print("        (required endpoint — page may render incomplete)")
        return None


print("Logging in as", EMAIL)
login = request("POST", "/auth/login", body={"email": EMAIL, "password": PASSWORD, "device_id": DEVICE_ID})
TOKEN = login["access_token"]
print("  role:", login.get("role"), "user:", login.get("full_name"))

# The adapter replays these two for the login/session bootstrap.
snapshots["POST /auth/login"] = {
    **login,
    "access_token": "offline-access-token",
    "refresh_token": "offline-refresh-token",
}
me = request("GET", "/auth/me", token=TOKEN)
snapshots["GET /auth/me"] = me

print("Analytics core")
analytics = capture("/manager/analytics")
perf = capture("/manager/agents/performance", {"months": 6})

months = (perf or {}).get("months", [])
agents = (perf or {}).get("agents", [])
print(f"  months={months}  agents={len(agents)}")

print("Team-level, all months")
capture("/manager/analytics/dpd-breakdown")
capture("/manager/analytics/team-attendance")
for m in months:
    capture("/manager/analytics/dpd-breakdown", {"month": m})
    capture("/manager/analytics/team-attendance", {"month": m})

print("Per-agent calendar + DPD (all months and each month)")
for a in agents:
    aid = a["agent_id"]
    capture(f"/manager/agents/{aid}/availability-calendar")
    capture(f"/manager/agents/{aid}/dpd-breakdown")
    for m in months:
        capture(f"/manager/agents/{aid}/dpd-breakdown", {"month": m})

print("AI monthly report — team scope, per month (LLM-backed, may be slow)")
for m in months:
    capture("/manager/ai/monthly-report", {"month": m}, timeout=180, required=False)

print("Per-agent AI insight + reallocation plan (Agents page drawers)")
for a in agents:
    aid = a["agent_id"]
    capture(f"/manager/agents/{aid}/ai-insight", timeout=180, required=False)
    capture(f"/manager/agents/{aid}/reallocation-plan", timeout=180, required=False)

print("Other manager pages (so in-app navigation still renders)")
capture("/manager/dashboard", required=False)
capture("/manager/agents", required=False)
capture("/manager/compliance", required=False)
capture("/manager/cases", required=False)
capture("/manager/ai/briefing", timeout=180, required=False)

# Cases page: every page of the list (PAGE_SIZE 50 in ManagerCasesPage), plus the
# detail behind each row so the case drawer opens.
PAGE_SIZE = 50
first_page = capture("/manager/cases", {"limit": PAGE_SIZE, "offset": 0}, required=False) or {}
total_cases = first_page.get("total", 0)
print(f"Case list pages + case details (total {total_cases})")

case_ids = [c["id"] for c in first_page.get("cases", [])]
for offset in range(PAGE_SIZE, total_cases, PAGE_SIZE):
    page = capture("/manager/cases", {"limit": PAGE_SIZE, "offset": offset}, required=False)
    case_ids += [c["id"] for c in (page or {}).get("cases", [])]

for i, cid in enumerate(case_ids, 1):
    if i % 50 == 0:
        print(f"  … {i}/{len(case_ids)} case details")
    capture(f"/manager/cases/{cid}", required=False)

with open(OUT, "w", encoding="utf-8") as fh:
    json.dump(snapshots, fh, separators=(",", ":"))

print()
print(f"wrote {len(snapshots)} snapshots -> {OUT}")
print(f"skipped {len(skipped)}")
for k, err in skipped:
    print("   ", k, "->", err)
