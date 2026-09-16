/**
 * "Navigate" from the manager's Live Map to wherever an agent's marker is.
 *
 * Added 2026-09-16. The agent app already opens Google Maps directions to a
 * borrower in four places (BeatMapPage, AgentCaseDetailPage, AgentCasesPage)
 * with the same free URL scheme — no key, no SDK. This is that action pointed
 * at an agent's last fix, for a manager who needs to reach them (an SOS, a
 * stalled agent, a handover in the field).
 *
 * Extracted from the page so the two places that offer the button — the
 * marker popup and the selected-agent bar — build one URL and say one thing,
 * and so the wording can be tested without mounting Leaflet.
 */

import { STALE_AFTER_S } from "./liveMapConstants";

/**
 * Google Maps directions to a point, from the DEVICE'S OWN location.
 *
 * `origin` is deliberately omitted: with no origin, Google Maps uses the
 * device's current position itself — so this page never has to ask the
 * browser for geolocation, never shows a permission prompt, and behaves the
 * same on a laptop (Maps web) and a phone (the Maps app). Adding an origin
 * here would mean reading `navigator.geolocation` for a value Google is
 * about to read anyway.
 */
export function directionsUrl(lat: number, lng: number): string {
  return `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}&travelmode=driving`;
}

export interface NavigateAction {
  url: string;
  /** Button text. */
  label: string;
  /** What the destination actually is, in words a manager can weigh. */
  note: string;
  /** True when the fix is older than STALE_AFTER_S — the marker may not be
   *  where the agent is now. */
  stale: boolean;
}

/**
 * The button for one agent, or null when there is nowhere to navigate to.
 *
 * THE MARKER IS THE LAST FIX, NOT THE AGENT. On the demo book most fixes are
 * days old, and the button says so rather than pretending: a stale fix reads
 * "last known position · 14 d ago", a fresh one "agent · 4 min ago". It is
 * never withheld on staleness — in an SOS, the last known position is exactly
 * what somebody is trying to reach, however old it is.
 */
export function navigateAction(agent: {
  latitude: number | null;
  longitude: number | null;
  age_seconds: number | null;
}, ageWords: (s: number | null) => string): NavigateAction | null {
  const { latitude, longitude, age_seconds } = agent;
  if (latitude == null || longitude == null) return null;
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return null;
  const stale = age_seconds == null || age_seconds > STALE_AFTER_S;
  return {
    url: directionsUrl(latitude, longitude),
    label: "Navigate",
    note: stale
      ? `to last known position · ${ageWords(age_seconds)}`
      : `to agent · ${ageWords(age_seconds)}`,
    stale,
  };
}
