// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import L from "leaflet";
import { GpsPanel } from "./GpsPanel";

const HOSTILE = `<img src=x onerror="alert(1)"><script>alert(2)</script>"Stop' 1`;

beforeEach(() => {
  vi.spyOn(L.Map.prototype, "getSize").mockImplementation(() => L.point(800, 600));
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("GpsPanel", () => {
  it("draws the phone plus one point per stop, and a hostile stop label renders as text", async () => {
    const stops = [
      { id: "s1", label: HOSTILE, lat: 28.46, lon: 77.03 },
      { id: "s2", label: "Sector 29 market", lat: 28.47, lon: 77.06 },
    ];
    const { container } = render(
      <GpsPanel fix={[28.45, 77.02]} track={null} stops={stops} gpsLost={false} onPick={() => {}} />,
    );
    const paths = await waitFor(() => {
      const els = container.querySelectorAll<SVGPathElement>("path.leaflet-interactive");
      expect(els).toHaveLength(3);
      return els;
    });

    fireEvent.mouseOver(paths[0]);
    const tooltip = await waitFor(() => {
      const el = container.querySelector<HTMLElement>(".leaflet-tooltip");
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    expect(tooltip.querySelector("script, img, [onerror]")).toBeNull();
    expect(tooltip.textContent).toBe(`1. ${HOSTILE}`);
  });
});
