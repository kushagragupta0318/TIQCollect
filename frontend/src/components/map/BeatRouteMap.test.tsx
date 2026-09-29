// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import L from "leaflet";
import { BeatRouteMap, type BeatStop } from "./BeatRouteMap";

const HOSTILE = `<img src=x onerror="alert(1)"><script>alert(2)</script>"Sunita' Rao`;
const HOSTILE_CITY = `Gurugram"><b onmouseover="x">`;

/** Google's encoded-polyline format, the inverse of ./polyline.ts's decoder. */
function encode(points: [number, number][]): string {
  let out = "";
  let prev: [number, number] = [0, 0];
  const num = (v: number) => {
    let n = v < 0 ? ~(v << 1) : v << 1;
    while (n >= 0x20) {
      out += String.fromCharCode((0x20 | (n & 0x1f)) + 63);
      n >>= 5;
    }
    out += String.fromCharCode(n + 63);
  };
  for (const p of points) {
    const e: [number, number] = [Math.round(p[0] * 1e5), Math.round(p[1] * 1e5)];
    num(e[0] - prev[0]);
    num(e[1] - prev[1]);
    prev = e;
  }
  return out;
}

function stops(n: number): BeatStop[] {
  return Array.from({ length: n }, (_, i) => ({ id: `c${i}`, lat: 28.4 + i * 0.002, lon: 77.0, label: `Customer ${i}` }));
}

beforeEach(() => {
  vi.spyOn(L.Map.prototype, "getSize").mockImplementation(() => L.point(800, 600));
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("BeatRouteMap", () => {
  it("draws the base plus one numbered pin per stop, never clustered (D3), even past 40", async () => {
    const { container } = render(<BeatRouteMap stops={stops(45)} start={{ lat: 28.39, lon: 77.0 }} />);
    await waitFor(() => expect(container.querySelectorAll(".leaflet-marker-icon")).toHaveLength(46));
    expect(container.querySelector(".marker-cluster")).toBeNull();
    expect(container.querySelector(".leaflet-marker-icon:nth-child(2)")?.textContent).toBe("1");
  });

  it("draws the road route simplified: 300 points on a straight road become its two ends", async () => {
    const polyline = vi.spyOn(L, "polyline");
    const road = Array.from({ length: 300 }, (_, i) => [28.4 + i * 0.0001, 77.0] as [number, number]);
    render(<BeatRouteMap stops={stops(2)} geometry={encode(road)} />);
    await waitFor(() => expect(polyline).toHaveBeenCalled());
    const drawn = polyline.mock.calls[0][0] as [number, number][];
    expect(drawn).toHaveLength(2);
    expect(drawn[0][0]).toBeCloseTo(28.4, 5);
    expect(drawn[1][0]).toBeCloseTo(28.4299, 5);
  });

  it("renders a hostile stop label and city as text in the tooltip", async () => {
    const one: BeatStop[] = [{ id: "c1", lat: 28.4, lon: 77.0, label: HOSTILE, sublabel: HOSTILE_CITY }];
    const { container } = render(<BeatRouteMap stops={one} />);
    const pin = await waitFor(() => {
      const el = container.querySelector<HTMLElement>(".leaflet-marker-icon");
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    fireEvent.mouseOver(pin);
    const tooltip = await waitFor(() => {
      const el = container.querySelector<HTMLElement>(".leaflet-tooltip");
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    expect(tooltip.querySelector("script, img, [onerror], [onmouseover]")).toBeNull();
    expect(tooltip.querySelector("strong")?.textContent).toBe(HOSTILE);
    expect(tooltip.textContent).toContain(HOSTILE_CITY);
  });
});
