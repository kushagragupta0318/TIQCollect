/** I02 — capture times are stamped on the server's clock (lib/serverClock.ts). */
import { describe, expect, it } from "vitest";
import { noteServerDate, offsetFrom, serverNow } from "./serverClock";

describe("serverClock", () => {
  it("reads the offset from a Date header", () => {
    const local = Date.parse("Tue, 29 Sep 2026 10:05:00 GMT");     // phone 5 min fast
    expect(offsetFrom("Tue, 29 Sep 2026 10:00:00 GMT", local)).toBe(-300_000);
  });

  it("ignores a missing or unparseable header", () => {
    expect(offsetFrom(undefined, 0)).toBeNull();
    expect(offsetFrom("not a date", 0)).toBeNull();
  });

  it("stamps in server time after a response, and keeps the offset offline", () => {
    const local = Date.parse("Tue, 29 Sep 2026 10:05:00 GMT");
    noteServerDate("Tue, 29 Sep 2026 10:00:00 GMT", local);
    expect(serverNow(local + 60_000).toISOString()).toBe("2026-09-29T10:01:00.000Z");
    noteServerDate(undefined, local);                                 // offline: unchanged
    expect(serverNow(local).toISOString()).toBe("2026-09-29T10:00:00.000Z");
  });
});
