/**
 * The pipeline donut groups statuses the way the product defines them — and
 * nothing is invented in the fold: the three slices sum to the input.
 */
import { describe, expect, it } from "vitest";
import { pipelineSlices, statusWords } from "./casePipeline";

// The live manager1 book on 2026-09-16, straight from the database.
const LIVE = {
  PAID: 273, ASSIGNED: 261, PARTIALLY_PAID: 231, IN_PROGRESS: 59,
  ESCALATED: 41, PTP_SET: 15, CLOSED: 3, WRITTEN_OFF: 0, UNASSIGNED: 0,
};

describe("pipelineSlices", () => {
  it("groups by the product's own RESOLVED_STATUSES: PAID, CLOSED, WRITTEN_OFF", () => {
    const [resolved] = pipelineSlices(LIVE);
    expect(resolved.key).toBe("resolved");
    expect([...resolved.statuses]).toEqual(["PAID", "CLOSED", "WRITTEN_OFF"]);
    expect(resolved.count).toBe(276);
  });

  it("puts ESCALATED in progress, never in resolved — it is open and visitable", () => {
    const [resolved, inProgress] = pipelineSlices(LIVE);
    expect(resolved.statuses).not.toContain("ESCALATED");
    expect(inProgress.statuses).toContain("ESCALATED");
    expect(inProgress.count).toBe(231 + 59 + 15 + 41);
  });

  it("'not started' is exactly ASSIGNED", () => {
    const [, , notStarted] = pipelineSlices(LIVE);
    expect([...notStarted.statuses]).toEqual(["ASSIGNED"]);
    expect(notStarted.count).toBe(261);
  });

  it("the three slices sum to every case the agents hold — nothing dropped, nothing double-counted", () => {
    const total = pipelineSlices(LIVE).reduce((t, s) => t + s.count, 0);
    // UNASSIGNED is not held by an agent and is not a slice; everything else is.
    const held = Object.entries(LIVE).filter(([k]) => k !== "UNASSIGNED").reduce((t, [, v]) => t + v, 0);
    expect(total).toBe(held);
    expect(total).toBe(883);
  });

  it("lists the statuses inside a slice largest first and drops zeros", () => {
    const [resolved, inProgress] = pipelineSlices(LIVE);
    expect(resolved.parts.map((p) => p.status)).toEqual(["PAID", "CLOSED"]); // WRITTEN_OFF 0 omitted
    expect(inProgress.parts.map((p) => p.status)).toEqual(["PARTIALLY_PAID", "IN_PROGRESS", "ESCALATED", "PTP_SET"]);
  });

  it("survives an absent or empty payload without inventing counts", () => {
    for (const input of [undefined, null, {}]) {
      const slices = pipelineSlices(input);
      expect(slices).toHaveLength(3);
      expect(slices.every((s) => s.count === 0 && s.parts.length === 0)).toBe(true);
    }
  });

  it("uses three colours that are not the status palette's semantic reds", () => {
    // Validated with the dataviz palette checker on 2026-09-16 (light + dark).
    expect(pipelineSlices(LIVE).map((s) => s.colour)).toEqual(["#059669", "#2563EB", "#D97706"]);
  });
});

describe("statusWords", () => {
  it("reads the enum as a manager says it", () => {
    expect(statusWords("PARTIALLY_PAID")).toBe("Partially paid");
    expect(statusWords("PTP_SET")).toBe("Ptp set");
    expect(statusWords("PAID")).toBe("Paid");
  });
});
