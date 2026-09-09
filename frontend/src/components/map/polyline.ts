/**
 * Decoder for Google/OSRM encoded polylines.
 *
 * WHY NOT A LIBRARY. This is the whole algorithm — about twenty lines — and
 * adding a dependency for it would mean a package, a version to keep current
 * and a supply-chain surface, all to avoid code that has not changed since 2005.
 * The encoding is fixed by the format, so there is nothing here to maintain.
 *
 * PRECISION 5 IS OSRM'S DEFAULT and what core/routing.py requests
 * (`geometries=polyline`). OSRM can also emit polyline6; if the backend ever
 * asks for that, pass precision 6 or every coordinate lands off the coast of
 * Africa — a failure that looks like a map bug rather than a units bug.
 */

export type LatLng = [number, number];

export function decodePolyline(encoded: string, precision = 5): LatLng[] {
  if (!encoded) return [];

  const factor = Math.pow(10, precision);
  const points: LatLng[] = [];
  let index = 0;
  let lat = 0;
  let lng = 0;

  while (index < encoded.length) {
    let result = 0;
    let shift = 0;
    let byte: number;

    do {
      byte = encoded.charCodeAt(index++) - 63;
      result |= (byte & 0x1f) << shift;
      shift += 5;
    } while (byte >= 0x20);
    lat += result & 1 ? ~(result >> 1) : result >> 1;

    result = 0;
    shift = 0;
    do {
      byte = encoded.charCodeAt(index++) - 63;
      result |= (byte & 0x1f) << shift;
      shift += 5;
    } while (byte >= 0x20);
    lng += result & 1 ? ~(result >> 1) : result >> 1;

    points.push([lat / factor, lng / factor]);
  }

  return points;
}
