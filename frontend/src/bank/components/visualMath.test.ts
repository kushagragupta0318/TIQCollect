import { describe, expect, it } from "vitest";
import { BRAND } from "../theme/colors";
import { toCsv } from "./workspace";
import {
  bar100Width,
  chipBackground,
  funnelColor,
  funnelWidth,
  heatCellBackground,
  hexByte,
  splitBarWidth,
  transitionCellStyle,
} from "./visualMath";

describe("bar widths keep a visible sliver and never overflow", () => {
  it("Bar100: 1.5% floor, 100% ceiling", () => {
    expect(bar100Width(0)).toBe(1.5);
    expect(bar100Width(42)).toBe(42);
    expect(bar100Width(180)).toBe(100);
  });

  it("drill split bars: 2% floor", () => {
    expect(splitBarWidth(0.4)).toBe(2);
  });

  it("funnel: 12% floor so the label fits, relative to the first stage", () => {
    expect(funnelWidth(4862, 4862)).toBe(100);
    expect(funnelWidth(38.9, 4862)).toBe(12);
    expect(funnelWidth(612.4, 4862)).toBeCloseTo(12.6, 1);
    expect(funnelWidth(10, 0)).toBe(12);
  });

  it("funnel colours run slate → primary → warning → destructive, then stay destructive", () => {
    expect([0, 1, 2, 3, 4].map(funnelColor)).toEqual([BRAND.slate, BRAND.primary, BRAND.warning, BRAND.destructive, BRAND.destructive]);
  });
});

describe("tints", () => {
  it("hexByte pads and clamps", () => {
    expect(hexByte(8)).toBe("08");
    expect(hexByte(255)).toBe("ff");
    expect(hexByte(300)).toBe("ff");
    expect(hexByte(-4)).toBe("00");
  });

  it("heat cell alpha is round(intensity*40+8): 0x08 empty, 0x30 at the row max", () => {
    expect(heatCellBackground("#EF4444", 0)).toBe("#EF444408");
    expect(heatCellBackground("#EF4444", 1)).toBe("#EF444430");
    expect(heatCellBackground("#EF4444", 0.5)).toBe("#EF44441c");
  });

  it("a bucket chip is the colour at 0x15 alpha", () => {
    expect(chipBackground("#1677FF")).toBe("#1677FF15");
  });
});

describe("transition matrix cells", () => {
  it("rolling to a worse bucket is destructive, curing is success, staying is slate and outlined", () => {
    expect(transitionCellStyle(20, 1, 2).background.startsWith(BRAND.destructive)).toBe(true);
    expect(transitionCellStyle(20, 2, 1).background.startsWith(BRAND.success)).toBe(true);
    const diag = transitionCellStyle(20, 2, 2);
    expect(diag.background.startsWith(BRAND.slate)).toBe(true);
    expect(diag.boxShadow).toBe(`inset 0 0 0 1.5px ${BRAND.ink}55`);
    expect(transitionCellStyle(20, 1, 2).boxShadow).toBe("none");
  });

  it("alpha is capped at 0.85 and text turns white past 45", () => {
    expect(transitionCellStyle(97.4, 5, 5).background).toBe(`${BRAND.slate}d9`);
    expect(transitionCellStyle(45, 0, 1).textColor).toBe(BRAND.ink);
    expect(transitionCellStyle(46.6, 3, 4).textColor).toBe("#fff");
  });
});

describe("workspace CSV export", () => {
  it("leaves headers bare, quotes every value, and doubles embedded quotes", () => {
    expect(toCsv({ headers: ["Agency", "Lift"], rows: [["Sahyadri \"West\" Services", 1.9], ["Ganga", -0.4]] })).toBe(
      'Agency,Lift\n"Sahyadri ""West"" Services","1.9"\n"Ganga","-0.4"',
    );
  });
});
