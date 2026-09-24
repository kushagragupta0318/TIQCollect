import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  BORROWER_STANCES, HARDSHIP_REASONS, NO_STANCE, STANCE_OPTIONS, nextStance,
  type StanceEvent, type StanceState,
} from "./borrowerStance";

// ML-1 option A (2026-09-24): the borrower's stance, recorded on the visit and
// call forms because recovery_risk 2.2.0 reads it and nothing wrote it.

const backendFile = (...p: string[]) =>
  readFileSync(join(__dirname, "..", "..", "..", "..", "backend", "app", ...p), "utf8");

function run(events: StanceEvent[], from: StanceState = NO_STANCE): StanceState {
  return events.reduce(nextStance, from);
}

describe("the vocabulary is the backend's", () => {
  it("matches BorrowerDisposition exactly, in order", () => {
    const src = backendFile("models", "call_log.py");
    const body = src.split("class BorrowerDisposition")[1].split("\nclass ")[0];
    const values = [...body.matchAll(/^\s{4}(\w+)\s*=\s*"(\w+)"/gm)].map((m) => m[2]);
    expect(values).toEqual([...BORROWER_STANCES]);
  });

  it("offers every stance once, and nothing else", () => {
    expect(STANCE_OPTIONS.map((o) => o.value)).toEqual([...BORROWER_STANCES]);
  });

  it("the hardship reasons are real DefaultReason members", () => {
    const src = backendFile("models", "visit.py");
    const body = src.split("class DefaultReason")[1].split("\nclass ")[0];
    const reasons = new Set([...body.matchAll(/"(\w+)"/g)].map((m) => m[1]));
    for (const r of HARDSHIP_REASONS) expect(reasons.has(r)).toBe(true);
  });
});

describe("no default", () => {
  it("starts empty", () => {
    expect(NO_STANCE).toEqual({ stance: null, source: null });
  });

  it.each(["PTP", "PART_PAID_PTP", "REVISIT", "PAID_FULL", "PART_PAID"])(
    "outcome %s pre-selects nothing (the stance is the agent's read, not a copy of the outcome)",
    (outcome) => {
      expect(run([{ kind: "outcome", value: outcome }]).stance).toBeNull();
    },
  );
});

describe("four outcomes and the hardship reasons pre-select", () => {
  it.each([
    ["RTP", "REFUSES"], ["BROKEN_PTP", "REFUSES"], ["DISPUTE", "DISPUTE"],
  ])("outcome %s -> %s", (outcome, stance) => {
    expect(run([{ kind: "outcome", value: outcome }])).toEqual({ stance, source: "outcome" });
  });

  it.each([...HARDSHIP_REASONS])("reason %s -> HARDSHIP", (reason) => {
    expect(run([{ kind: "reason", value: reason }])).toEqual({ stance: "HARDSHIP", source: "reason" });
  });

  it.each(["AMOUNT_DISPUTED", "ALREADY_PAID", "FRAUD_CLAIM", "MARITAL_DISPUTE", "OTHER"])(
    "reason %s pre-selects nothing", (reason) => {
      expect(run([{ kind: "reason", value: reason }]).stance).toBeNull();
    },
  );
});

describe("the last tap wins", () => {
  it("the agent can overrule a pre-selection", () => {
    expect(run([{ kind: "outcome", value: "RTP" }, { kind: "tap", value: "MAY_PAY" }]))
      .toEqual({ stance: "MAY_PAY", source: "agent" });
  });

  it("a later outcome that implies a stance overrules the agent", () => {
    expect(run([{ kind: "tap", value: "MAY_PAY" }, { kind: "outcome", value: "DISPUTE" }]).stance).toBe("DISPUTE");
  });

  it("a later outcome that implies nothing keeps the agent's own choice", () => {
    expect(run([{ kind: "tap", value: "MAY_PAY" }, { kind: "outcome", value: "PTP" }]).stance).toBe("MAY_PAY");
  });

  it("changing to an outcome that implies nothing clears a stale pre-selection", () => {
    expect(run([{ kind: "outcome", value: "RTP" }, { kind: "outcome", value: "PTP" }])).toEqual(NO_STANCE);
  });

  it("a reason that implies nothing leaves the outcome's pre-selection alone", () => {
    expect(run([{ kind: "outcome", value: "RTP" }, { kind: "reason", value: "AMOUNT_DISPUTED" }]).stance).toBe("REFUSES");
  });

  it("a hardship reason after a refusal outcome is the later tap", () => {
    expect(run([{ kind: "outcome", value: "RTP" }, { kind: "reason", value: "JOB_LOSS" }]).stance).toBe("HARDSHIP");
  });

  it("deselecting the reason that chose the stance clears it", () => {
    expect(run([{ kind: "reason", value: "MEDICAL" }, { kind: "reason", value: null }])).toEqual(NO_STANCE);
  });

  it("tapping the chosen stance again clears it", () => {
    expect(run([{ kind: "tap", value: "REFUSES" }, { kind: "tap", value: "REFUSES" }])).toEqual(NO_STANCE);
  });
});
