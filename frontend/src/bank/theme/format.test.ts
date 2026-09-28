import { describe, expect, it } from "vitest";
import { chromeDate, cr, cr1, fmtINR, n, pulseCr, pulseMoney, pyDate, rs, signed } from "./format";

describe("CC client formatters (input already in crores)", () => {
  it("cr prints two decimals and a spaced unit", () => {
    expect(cr(612.4)).toBe("₹612.40 Cr");
    expect(cr("97.4")).toBe("₹97.40 Cr");
  });

  it("cr1 prints one decimal", () => {
    expect(cr1(4862)).toBe("₹4862.0 Cr");
  });

  it("treats null and undefined as zero, as CC's `?? 0` does", () => {
    expect(cr(null)).toBe("₹0.00 Cr");
    expect(cr1(undefined)).toBe("₹0.0 Cr");
    expect(rs(undefined)).toBe("₹0");
    expect(n(null)).toBe("0");
  });

  it("rs and n group in lakhs and crores, not thousands", () => {
    expect(rs(1845230)).toBe("₹18,45,230");
    expect(n(512480)).toBe("5,12,480");
    expect(n(12345678)).toBe("1,23,45,678");
  });
});

describe("fmtINR — adaptive, no space before the unit", () => {
  it("switches to Cr at one crore and to L at one lakh", () => {
    expect(fmtINR(1e7)).toBe("₹1.00Cr");
    expect(fmtINR(48625000)).toBe("₹4.86Cr");
    expect(fmtINR(1e5)).toBe("₹1.00L");
    expect(fmtINR(9999999)).toBe("₹100.00L");
    expect(fmtINR(99999)).toBe("₹99,999");
  });

  it("reads garbage as zero", () => {
    expect(fmtINR("not a number")).toBe("₹0");
    expect(fmtINR(undefined)).toBe("₹0");
  });
});

describe("server KPI shapes (portfolio_pulse.py)", () => {
  it("pulseCr groups in thousands like Python's `,` spec, with a spaced unit", () => {
    expect(pulseCr(6124000000)).toBe("₹612.4 Cr");
    expect(pulseCr(48620000000)).toBe("₹4,862.0 Cr");
    expect(pulseCr(48620000000, 2)).toBe("₹4,862.00 Cr");
  });

  it("pulseMoney falls back to lakhs below one crore", () => {
    expect(pulseMoney(1e7)).toBe("₹1.0 Cr");
    expect(pulseMoney(8520000)).toBe("₹85.2 L");
    expect(pulseMoney(-8520000)).toBe("₹-85.2 L");
  });

  it("signed always prints a sign, including Python's -0.0", () => {
    expect(signed(2.14)).toBe("+2.1");
    expect(signed(-0.86)).toBe("-0.9");
    expect(signed(0)).toBe("+0.0");
    expect(signed(-0.04)).toBe("-0.0");
    expect(signed(-0)).toBe("-0.0");
  });
});

describe("dates", () => {
  it("the chrome date is en-GB day-month-year, unpadded day", () => {
    // ICU 72+ abbreviates September as "Sept" in en-GB; older ICU says "Sep".
    // CC calls the same toLocaleDateString, so both apps print the same thing.
    expect(chromeDate(new Date(2026, 8, 24))).toMatch(/^24 Sept? 2026$/);
    expect(chromeDate(new Date(2026, 4, 4))).toBe("4 May 2026");
  });

  it("pyDate zero-pads the day like %d", () => {
    expect(pyDate(new Date(2026, 8, 4))).toBe("04 Sep 2026");
    expect(pyDate(new Date(2026, 0, 22))).toBe("22 Jan 2026");
  });
});
