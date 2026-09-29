// @vitest-environment jsdom
/**
 * Live map smoke tests: it loads, draws one marker per positioned agent, renders
 * agent-supplied text as text (the tooltip XSS hotfix), and the night toggle swaps tiles only.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import L from "leaflet";
import type { LiveAgentPosition } from "@/api/manager";
import { getAgentTrail, getAgentsLive } from "@/api/manager";
import ManagerLiveMapPage from "./ManagerLiveMapPage";

const TOKEN = "pk.eyJ1IjoidGlxIiwiYSI6ImFiYyJ9.test-signature";

vi.mock("@/api/manager", () => ({ getAgentsLive: vi.fn(), getAgentTrail: vi.fn() }));
vi.mock("@/hooks/useLiveEvents", () => ({ useLiveEvents: () => {} }));
// The test build has no token; give the page a Mapbox setup so the night toggle exists.
vi.mock("@/components/map/baseTiles", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/components/map/baseTiles")>();
  return {
    ...real,
    nightVariantAvailable: () => true,
    addBaseTiles: (map: L.Map, variant: "day" | "night") => real.addBaseTiles(map, variant, { VITE_MAPBOX_TOKEN: TOKEN }),
  };
});

const HOSTILE_NAME = `<img src=x onerror="alert(1)"><script>alert(2)</script>"Ravi' Kumar`;

function agent(id: string, over: Partial<LiveAgentPosition> = {}): LiveAgentPosition {
  return {
    agent_id: id,
    employee_code: `EMP${id}`,
    full_name: `Agent ${id}`,
    status: "ON_DUTY",
    sos_active: false,
    sos_triggered_at: null,
    latitude: 28.46 + Number(id) / 100,
    longitude: 77.03,
    accuracy_metres: 12,
    battery_pct: 80,
    recorded_at: "2026-09-29T05:00:00Z",
    age_seconds: 30,
    ...over,
  };
}

function tileUrls(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll<HTMLImageElement>("img.leaflet-tile")).map((img) => img.src);
}

beforeEach(() => {
  // jsdom has no layout; Leaflet needs a size to fit bounds and place tiles.
  vi.spyOn(L.Map.prototype, "getSize").mockReturnValue(L.point(800, 600));
  vi.mocked(getAgentTrail).mockResolvedValue({ agent_id: "1", points: [] } as never);
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.mocked(getAgentsLive).mockReset();
  vi.mocked(getAgentTrail).mockReset();
});

describe("ManagerLiveMapPage", () => {
  it("draws one marker per agent with a position, and none for an agent without one", async () => {
    vi.mocked(getAgentsLive).mockResolvedValue({
      agents: [agent("1"), agent("2"), agent("3", { latitude: null, longitude: null })],
      sos_count: 0,
    } as never);
    const { container } = render(<ManagerLiveMapPage />);

    await screen.findByText(/2 of 3 agents reporting/);
    await waitFor(() => expect(container.querySelectorAll(".leaflet-marker-icon.custom-agent-marker")).toHaveLength(2));
    expect(tileUrls(container).length).toBeGreaterThan(0);
    expect(tileUrls(container).every((u) => u.includes("/mapbox/streets-v12/"))).toBe(true);
    expect(container.querySelector(".leaflet-control-attribution")?.textContent).toContain("© Mapbox");
  });

  it("renders a hostile agent name as text in the marker, its tooltip and its popup", async () => {
    vi.mocked(getAgentsLive).mockResolvedValue({
      agents: [agent("1", { full_name: HOSTILE_NAME, employee_code: `E"1<b>` })],
      sos_count: 0,
    } as never);
    const { container } = render(<ManagerLiveMapPage />);

    const icon = await waitFor(() => {
      const el = container.querySelector<HTMLElement>(".leaflet-marker-icon.custom-agent-marker");
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    fireEvent.mouseOver(icon);
    const tooltip = await waitFor(() => {
      const el = container.querySelector(".leaflet-tooltip");
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    const tooltipText = tooltip.textContent;
    expect(tooltip.querySelector("script, img[src='x'], [onerror]")).toBeNull();

    fireEvent.click(icon);
    const popup = await waitFor(() => {
      const el = container.querySelector(".leaflet-popup-content");
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    for (const el of [icon, popup]) {
      expect(el.querySelector("script, img[src='x'], [onerror]")).toBeNull();
    }
    // The pin shows the initials, "<K" here: as text, not the start of a tag.
    expect(icon.textContent).toContain("<K");
    expect(tooltipText).toContain(HOSTILE_NAME);
    expect(tooltipText).toContain(`E"1<b>`);
    expect(popup.textContent).toContain(HOSTILE_NAME);
  });

  it("swaps only the tiles for the night map, and remembers the choice", async () => {
    vi.mocked(getAgentsLive).mockResolvedValue({ agents: [agent("1"), agent("2")], sos_count: 0 } as never);
    const { container } = render(<ManagerLiveMapPage />);
    await waitFor(() => expect(container.querySelectorAll(".leaflet-marker-icon.custom-agent-marker")).toHaveLength(2));
    const markerBefore = container.querySelector(".leaflet-marker-icon.custom-agent-marker");

    fireEvent.click(screen.getByRole("button", { name: /night map/i }));

    await waitFor(() => {
      const urls = tileUrls(container);
      expect(urls.length).toBeGreaterThan(0);
      expect(urls.every((u) => u.includes("/mapbox/dark-v11/"))).toBe(true);
    });
    expect(container.querySelectorAll(".tiq-mapbox-wordmark")).toHaveLength(1);
    expect(container.querySelector(".leaflet-marker-icon.custom-agent-marker")).toBe(markerBefore);
    expect(localStorage.getItem("tiq.liveMap.variant")).toBe("night");
    expect(screen.getByRole("button", { name: /day map/i }).getAttribute("aria-pressed")).toBe("true");
  });
});
