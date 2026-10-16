// @vitest-environment jsdom
/**
 * Wheel-zoom gating. A map sits inside a scrolling page, so rolling the wheel
 * over it must scroll the PAGE until the reader deliberately clicks the map —
 * otherwise the map swallows the scroll and traps them on the panel.
 *
 * Asserted through the real Leaflet handler (`map.scrollWheelZoom.enabled()`)
 * rather than a class name: the handler is what actually decides, and Leaflet
 * does not reflect its state in the DOM.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import type L from "leaflet";
import { MapCanvas } from "./MapCanvas";

afterEach(cleanup);

function mount() {
  let map: L.Map | undefined;
  const { container } = render(
    <MapCanvas centre={[28.4595, 77.0266]} zoom={5} onReady={(m) => { map = m; }} />,
  );
  if (!map) throw new Error("MapCanvas never produced a map");
  return { map, container };
}

describe("MapCanvas wheel-zoom gating", () => {
  it("starts with the wheel released, so the page scrolls past the map", () => {
    const { map } = mount();
    expect(map.scrollWheelZoom.enabled()).toBe(false);
  });

  it("takes the wheel once the map is clicked", () => {
    const { map } = mount();
    map.fire("click");
    expect(map.scrollWheelZoom.enabled()).toBe(true);
  });

  it("gives the wheel back when the reader clicks away from the map", () => {
    const { map } = mount();
    map.fire("click");
    expect(map.scrollWheelZoom.enabled()).toBe(true);

    const outside = document.createElement("button");
    document.body.appendChild(outside);
    outside.dispatchEvent(new MouseEvent("click", { bubbles: true }));

    expect(map.scrollWheelZoom.enabled()).toBe(false);
    outside.remove();
  });
});
