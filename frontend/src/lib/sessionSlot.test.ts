import { describe, expect, it } from "vitest";
import { resolveSlotFrom, slotKey } from "./sessionSlot";

describe("resolveSlotFrom", () => {
  it("takes the slot from the query and carries it in window.name", () => {
    expect(resolveSlotFrom("?slot=agent", "")).toEqual({
      slot: "agent",
      windowName: "tiq-slot:agent",
    });
  });

  it("keeps the slot after navigation drops the query", () => {
    expect(resolveSlotFrom("", "tiq-slot:manager").slot).toBe("manager");
  });

  it("lets a new query re-slot the frame", () => {
    expect(resolveSlotFrom("?slot=bank", "tiq-slot:manager").slot).toBe("bank");
  });

  it("is no slot on an ordinary load, leaving window.name alone", () => {
    expect(resolveSlotFrom("", "")).toEqual({ slot: null, windowName: "" });
    expect(resolveSlotFrom("?tab=cases", "someone-elses-name")).toEqual({
      slot: null,
      windowName: "someone-elses-name",
    });
  });

  it("refuses a slot that is not a safe storage-key fragment", () => {
    expect(resolveSlotFrom("?slot=../tiq_auth", "").slot).toBeNull();
    expect(resolveSlotFrom("?slot=" + "x".repeat(33), "").slot).toBeNull();
  });
});

describe("slotKey", () => {
  it("leaves the historical key untouched outside a slot", () => {
    expect(slotKey("tiq_auth", null)).toBe("tiq_auth");
  });

  it("gives two slots two different keys", () => {
    expect(slotKey("tiq_auth", "agent")).not.toBe(slotKey("tiq_auth", "manager"));
  });
});
