// What the check-in screen may tell an agent about their own check-in: the fix
// the phone returned and nothing inferred. It used to print a fixed city and a
// liveness verdict; neither was ever computed (N1, docs/business/PRIORITIES.md).

export interface CheckInFix {
  lat: number;
  lon: number;
  /** Metres, as the browser reports it; null when it reports none. */
  accuracy: number | null;
}

export function fixFromPosition(p: { coords: { latitude: number; longitude: number; accuracy?: number } }): CheckInFix {
  const accuracy = p.coords.accuracy;
  return {
    lat: p.coords.latitude,
    lon: p.coords.longitude,
    accuracy: typeof accuracy === "number" && Number.isFinite(accuracy) ? accuracy : null,
  };
}

export function describeFix(fix: CheckInFix | null): string {
  if (!fix) return "Waiting for GPS…";
  const accuracy = fix.accuracy == null ? "" : ` (±${Math.round(fix.accuracy)} m)`;
  return `${fix.lat.toFixed(5)}, ${fix.lon.toFixed(5)}${accuracy}`;
}
