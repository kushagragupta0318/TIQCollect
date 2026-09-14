import { useSyncExternalStore } from "react";

/**
 * Live GPS location + human-readable address for the field-agent view.
 *
 * Watches the device's real position and reverse-geocodes it to a readable
 * address so the agent's *actual* location is shown wherever on earth they are
 * — reinforcing that everything is live, not hardcoded.
 *
 * Reverse geocoding uses OpenStreetMap Nominatim: free, no API key, CORS-enabled,
 * worldwide, and street/neighbourhood-level precise (road + area + city), which
 * BigDataCloud's free endpoint is not (it only returns city/state). If you later
 * set GOOGLE_MAPS_API_KEY and want building-level precision, swap `reverseGeocode`
 * for the Google Geocoding API.
 *
 * Note: if the app is ever served under a strict Content-Security-Policy, add
 * `https://nominatim.openstreetmap.org` to `connect-src`. Nominatim asks for
 * <=1 request/second — the 40m move-threshold below keeps us well under that.
 */

export type LiveLocationStatus = "locating" | "ready" | "denied" | "error";

export interface LiveLocation {
  status: LiveLocationStatus;
  address: string | null;          // e.g. "Sector 44, Gurugram, Haryana, India"
  coords: { lat: number; lon: number } | null;
}

// Metres between two points — only re-geocode after the agent actually moves,
// so we don't hammer the geocoder on every tiny GPS jitter.
function metresBetween(aLat: number, aLon: number, bLat: number, bLon: number): number {
  const R = 6_371_000;
  const dLat = ((bLat - aLat) * Math.PI) / 180;
  const dLon = ((bLon - aLon) * Math.PI) / 180;
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((aLat * Math.PI) / 180) * Math.cos((bLat * Math.PI) / 180) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.atan2(Math.sqrt(s), Math.sqrt(1 - s));
}

async function reverseGeocode(lat: number, lon: number): Promise<string> {
  // zoom=18 + addressdetails=1 => building/street-level detail.
  const url =
    `https://nominatim.openstreetmap.org/reverse` +
    `?format=jsonv2&lat=${lat}&lon=${lon}&zoom=18&addressdetails=1`;
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`geocode ${res.status}`);
  const d = await res.json();
  const a = d.address || {};
  // Build from most specific → coarser so the fullest available address shows:
  // plot/road → neighbourhood → suburb → city-district → city → state → pincode.
  const place =
    [a.house_number, a.road].filter(Boolean).join(" ") ||
    a.pedestrian || a.footway || a.building || a.amenity || a.shop || a.office;
  const neighbourhood = a.neighbourhood || a.quarter || a.residential || a.hamlet;
  const suburb = a.suburb;
  const cityDistrict = a.city_district;
  const city = a.city || a.town || a.village || a.municipality || a.county;
  const state = a.state || a.state_district;
  const pincode = a.postcode;

  const seen = new Set<string>();
  const parts = [place, neighbourhood, suburb, cityDistrict, city, state]
    .filter(Boolean)
    .filter((p: string) => (seen.has(p) ? false : seen.add(p)));
  // Keep it readable: up to 4 locality parts, then append the pincode.
  let address = parts.slice(0, 4).join(", ");
  if (pincode) address = address ? `${address} - ${pincode}` : pincode;

  // Fall back to Nominatim's full label, else raw coords, so it's never blank.
  const fallback =
    typeof d.display_name === "string"
      ? d.display_name.split(",").slice(0, 4).map((s: string) => s.trim()).join(", ")
      : "";
  return address || fallback || `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
}

/**
 * Below this many metres of movement, a new fix is treated as the same place
 * and `coords` keeps its object identity.
 *
 * watchPosition with enableHighAccuracy fires roughly once a second, and a
 * stationary phone still jitters by a few metres. Handing out a fresh
 * `{lat, lon}` each time changed the identity of `coords`, which invalidated
 * every `useMemo([..., here])` downstream — so the agent case list re-sorted
 * and every card re-rendered about once a second, for movement that rounds to
 * nothing.
 *
 * 15m, not 5m: consumer GPS wanders by roughly 3-10m while the phone sits
 * still, so a 5m gate still let noise through, and each republish re-sorted the
 * case list — two cases a few metres apart visibly traded places under the
 * agent's thumb. 15m is comfortably inside the 100m geo-fence, and the fence is
 * enforced on the case-detail page and again server-side from the submitted
 * GPS, so nothing that gates a visit depends on this value.
 */
const COORD_STABLE_M = 15;

/** Re-geocode only after this much movement. Nominatim asks for <= 1 req/sec. */
const GEOCODE_MOVE_M = 40;

/**
 * One watcher for the whole app.
 *
 * This used to be per-component state. Two components call this hook
 * (AgentLayout's header line and the agent case list) and StrictMode
 * double-invokes effects in dev, so a single page ran up to four independent
 * watchPosition subscriptions, each with its own reverse-geocode pipeline and
 * its own idea of the current position. That meant duplicate Nominatim traffic
 * for one move, and consumers disagreeing about `coords` — which is what made
 * the case list appear to re-sort at random.
 *
 * Now: one subscription, shared snapshot, ref-counted. `snapshot` is only
 * reassigned when something genuinely changed, so useSyncExternalStore hands
 * back a stable reference and downstream memos stay valid.
 */
/**
 * A raw fix, exactly as the device reported it — no movement gate applied.
 *
 * `useLiveLocation` deliberately suppresses sub-15m movement so the UI does not
 * re-render on GPS jitter. The location TRAIL needs the unfiltered stream: it
 * applies its own thinning (see lib/locationReporter.ts) and must be able to
 * distinguish "the agent stood still" from "the UI chose not to update".
 */
export interface RawFix {
  lat: number;
  lon: number;
  accuracy: number | null;
  at: number;          // epoch ms, device clock
}

let snapshot: LiveLocation = { status: "locating", address: null, coords: null };
const listeners = new Set<() => void>();

// Separate from `listeners`: those drive React re-renders and are gated by the
// movement threshold, these receive every fix. Kept on the SAME watchPosition
// subscription on purpose — a second watcher would double GPS power draw on a
// phone that has to last a full shift.
const fixListeners = new Set<(fix: RawFix) => void>();

/**
 * Subscribe to every raw fix. Returns an unsubscribe function.
 *
 * Does not itself start the watcher — a subscriber here only receives fixes
 * while something is also using `useLiveLocation()`. In the agent app
 * AgentLayout always is, for the header address line.
 */
export function subscribeToFixes(cb: (fix: RawFix) => void): () => void {
  fixListeners.add(cb);
  return () => { fixListeners.delete(cb); };
}

let watchId: number | undefined;
let subscriberCount = 0;
let stopTimer: ReturnType<typeof setTimeout> | undefined;

let lastGeocoded: { lat: number; lon: number } | null = null;
let hasAddress = false;
let geocodeInFlight = false;

function publish(next: LiveLocation) {
  // Don't hand out a new object for an unchanged value: useSyncExternalStore
  // compares by identity, so a no-op publish would re-render every consumer and
  // invalidate every downstream memo for nothing.
  const c = snapshot.coords, n = next.coords;
  const sameCoords = c === n || (!!c && !!n && c.lat === n.lat && c.lon === n.lon);
  if (sameCoords && snapshot.status === next.status && snapshot.address === next.address) return;
  snapshot = next;
  listeners.forEach((l) => l());
}

async function onPos(p: GeolocationPosition) {
  const lat = p.coords.latitude;
  const lon = p.coords.longitude;

  // Emit the raw fix first, before any movement gate or geocoding — the trail
  // must not inherit the UI's thresholds, and must not wait on Nominatim.
  if (fixListeners.size > 0) {
    const fix: RawFix = {
      lat, lon,
      accuracy: Number.isFinite(p.coords.accuracy) ? p.coords.accuracy : null,
      at: p.timestamp || Date.now(),
    };
    fixListeners.forEach((cb) => { try { cb(fix); } catch { /* a bad subscriber must not kill the watcher */ } });
  }

  // Only publish new coords when the agent has actually moved. Recovering from
  // an error also has to publish, or a transient failure would strand the UI.
  const cur = snapshot.coords;
  if (!cur || metresBetween(cur.lat, cur.lon, lat, lon) >= COORD_STABLE_M || snapshot.status === "error") {
    publish({ ...snapshot, status: hasAddress ? "ready" : snapshot.status, coords: { lat, lon } });
  }

  if (lastGeocoded && metresBetween(lastGeocoded.lat, lastGeocoded.lon, lat, lon) < GEOCODE_MOVE_M && hasAddress) return;
  if (geocodeInFlight) return;

  geocodeInFlight = true;
  try {
    const address = await reverseGeocode(lat, lon);
    lastGeocoded = { lat, lon };
    hasAddress = true;
    publish({ ...snapshot, status: "ready", address });
  } catch {
    // Keep coords visible even if the geocoder is unreachable.
    lastGeocoded = { lat, lon };
    hasAddress = true;
    publish({ ...snapshot, status: "ready", address: `${lat.toFixed(4)}, ${lon.toFixed(4)}` });
  } finally {
    geocodeInFlight = false;
  }
}

function onErr(err: GeolocationPositionError) {
  // Only a permission revocation actually invalidates the position we hold.
  if (err.code === err.PERMISSION_DENIED) {
    publish({ status: "denied", address: null, coords: null });
    return;
  }

  // TIMEOUT and POSITION_UNAVAILABLE are transient — indoors, in a lift, or
  // simply no new fix within the 20s window while the phone sits still.
  // Throwing away the last known position on those was the real cause of the
  // agent case list "re-sorting at random": coords went null, the list fell
  // back to priority order, then snapped back to distance order on the next
  // fix, over and over. A minute-old fix is far better than none for ordering
  // a list by distance. The visit geo-fence does not rely on this — it is
  // checked on the case-detail page and again server-side from the GPS
  // submitted with the visit.
  if (snapshot.coords) return;
  publish({ status: "error", address: null, coords: null });
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  subscriberCount += 1;
  if (stopTimer !== undefined) {
    clearTimeout(stopTimer);
    stopTimer = undefined;
  }
  if (watchId === undefined) {
    if (!("geolocation" in navigator)) {
      publish({ status: "error", address: null, coords: null });
    } else {
      watchId = navigator.geolocation.watchPosition(onPos, onErr, {
        enableHighAccuracy: true,
        maximumAge: 30_000,
        timeout: 20_000,
      });
    }
  }
  return () => {
    listeners.delete(listener);
    subscriberCount -= 1;
    // Navigating between agent pages unmounts one consumer a tick before the
    // next mounts. Tearing the watch down immediately would restart GPS
    // acquisition on every route change, so wait to see if anyone else claims
    // it. Also absorbs StrictMode's mount/unmount/mount in dev.
    if (subscriberCount <= 0 && stopTimer === undefined) {
      stopTimer = setTimeout(() => {
        stopTimer = undefined;
        if (subscriberCount <= 0 && watchId !== undefined) {
          navigator.geolocation.clearWatch(watchId);
          watchId = undefined;
        }
      }, 10_000);
    }
  };
}

/**
 * Agent-initiated refresh — the header button.
 *
 * 2026-09-14. The watcher already runs continuously, so this is not about
 * making a good fix better; it is the agent's one lever when the line is
 * stuck. Three things it forces that nothing else does: a fresh fix with
 * `maximumAge: 0` (the watcher accepts one up to 30 s old, and after a
 * TIMEOUT indoors it may not deliver again for a while); a re-request of
 * permission after the agent has turned GPS back on in settings, which a
 * "denied" watcher never retries; and a re-geocode, which otherwise waits for
 * GEOCODE_MOVE_M of movement. The fresh fix is also pushed to `fixListeners`,
 * so the trail (locationReporter) sees it like any other.
 *
 * Resolves true when a fix arrived, false when it did not — the caller shows
 * the outcome; this module only knows positions.
 */
export function refreshLocation(): Promise<boolean> {
  if (!("geolocation" in navigator)) {
    publish({ status: "error", address: null, coords: null });
    return Promise.resolve(false);
  }
  // Keep the last coords visible while locating: a list ordered by distance
  // must not re-sort to nothing for the two seconds this takes.
  publish({ ...snapshot, status: "locating" });
  lastGeocoded = null;
  hasAddress = false;

  // Restart the watch so a stalled or denied one re-acquires with the same
  // options the app always uses; the one-shot below is what answers now.
  if (watchId !== undefined) {
    navigator.geolocation.clearWatch(watchId);
    watchId = navigator.geolocation.watchPosition(onPos, onErr, {
      enableHighAccuracy: true, maximumAge: 30_000, timeout: 20_000,
    });
  }
  return new Promise((resolve) => {
    navigator.geolocation.getCurrentPosition(
      (p) => { void onPos(p); resolve(true); },
      (e) => {
        onErr(e);
        // onErr keeps stale coords on a transient error, on purpose; but a
        // refresh that found nothing must still stop saying "Locating…".
        if (snapshot.status === "locating") {
          publish({ ...snapshot, status: snapshot.coords ? "ready" : "error" });
        }
        resolve(false);
      },
      { enableHighAccuracy: true, maximumAge: 0, timeout: 15_000 },
    );
  });
}

const getSnapshot = () => snapshot;

export function useLiveLocation(): LiveLocation {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}
