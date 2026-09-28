import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  DEMO_UPI_REFERENCE_PREFIX,
  demoUpiAutoconfirmEnabled,
  demoUpiReference,
  paymentReferenceOk,
  upiQrValue,
  upiReferenceOk,
} from "./upiPayment";

// Hotfix PAY-1 / PAY-2 (2026-09-24). The page used to flip to "Payment
// received" 10 s after showing a static UPI QR, in every build, and then waive
// the transaction-ID requirement; and the QR paid a hardcoded VPA.

describe("the demo auto-confirm is off unless the build asks for it exactly", () => {
  it.each([
    [{}, false],
    [{ VITE_DEMO_UPI_AUTOCONFIRM: "" }, false],
    [{ VITE_DEMO_UPI_AUTOCONFIRM: "0" }, false],
    [{ VITE_DEMO_UPI_AUTOCONFIRM: "true" }, false],
    [{ VITE_DEMO_UPI_AUTOCONFIRM: "yes" }, false],
    [{ VITE_DEMO_UPI_AUTOCONFIRM: "1" }, true],
  ])("%j -> %s", (env, want) => {
    expect(demoUpiAutoconfirmEnabled(env)).toBe(want);
  });

  it("is never set by the production image", () => {
    const dockerfile = readFileSync(join(__dirname, "..", "..", "..", "..", "Dockerfile"), "utf8");
    expect(dockerfile).not.toMatch(/VITE_DEMO_UPI_AUTOCONFIRM/);
  });
});

describe("every payment mode carries the evidence the server requires", () => {
  it("a UPI reference is a 12-digit UTR", () => {
    for (const ref of ["", "   ", null, undefined, "12345", "4123456789012", "41234567890a"]) {
      expect(upiReferenceOk(ref)).toBe(false);
    }
    expect(upiReferenceOk("412345678901")).toBe(true);
    expect(upiReferenceOk(" 4123 4567 8901 ")).toBe(true);
  });

  it("the demo reference passes only in a demo build, and never looks like a UTR", () => {
    const ref = demoUpiReference(1_727_164_800_000);
    expect(ref.startsWith(DEMO_UPI_REFERENCE_PREFIX)).toBe(true);
    expect(upiReferenceOk(ref)).toBe(false);                 // a normal build refuses it
    expect(upiReferenceOk(ref, { demo: true })).toBe(true);
    expect(ref).not.toMatch(/^\d{12}$/);
  });

  const none = { upiRef: "", neftRef: "", chequeNumber: "" };
  it.each([
    ["CASH", none, true],
    ["UPI", none, false],
    ["UPI", { ...none, upiRef: "412345678901" }, true],
    ["NEFT", none, false],
    ["NEFT", { ...none, neftRef: "UTIBN52026092400123" }, true],
    ["RTGS", none, false],
    ["DD", none, false],
    ["CHEQUE", none, false],
    ["CHEQUE", { ...none, chequeNumber: "004512" }, true],
  ])("%s with %j -> %s", (mode, refs, want) => {
    expect(paymentReferenceOk(mode, refs)).toBe(want);
  });
});

describe("the QR pays only a payee the server configured", () => {
  const cfg = { available: true, vpa: "collections@examplebank", payee_name: "Example Recovery Desk" };

  it("offers no QR without a payee", () => {
    expect(upiQrValue(undefined, 5000, "Loan Recovery XXXX1234")).toBeNull();
    expect(upiQrValue({ available: false, vpa: null, payee_name: null }, 5000, "n")).toBeNull();
    expect(upiQrValue({ ...cfg, vpa: "  " }, 5000, "n")).toBeNull();
    expect(upiQrValue({ ...cfg, payee_name: "" }, 5000, "n")).toBeNull();
  });

  it("offers no QR for a zero amount", () => {
    expect(upiQrValue(cfg, 0, "n")).toBeNull();
  });

  it("builds the upi:// payload from the configured payee, encoded", () => {
    const v = upiQrValue(cfg, 5000, "Loan Recovery XXXX1234")!;
    const q = new URLSearchParams(v.replace("upi://pay?", ""));
    expect(v.startsWith("upi://pay?")).toBe(true);
    expect(q.get("pa")).toBe(cfg.vpa);
    expect(q.get("pn")).toBe(cfg.payee_name);
    expect(q.get("am")).toBe("5000");
    expect(q.get("cu")).toBe("INR");
    expect(q.get("tn")).toBe("Loan Recovery XXXX1234");
  });

  it("carries no trace of the payee that used to be hardcoded", () => {
    const page = readFileSync(join(__dirname, "RecordVisitPage.tsx"), "utf8");
    expect(page).not.toMatch(/@ptsbi|8015935790/);
    expect(page).not.toMatch(/pn=ABC/);
  });
});
