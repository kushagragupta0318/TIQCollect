import { describe, expect, it } from "vitest";
import {
  EMPTY_FILTERS, MAX_BATCH, blockedByReason, headroomLabel, loanQuery, pageSelection, recallReasonError,
  refusalCode, regionOptions, toggle, togglePage, verdictText, applyBlocker, explorationRate,
  type AgencyRoom, type EngineRun, type LoanVerdict, type PlaceableLoan,
} from "./placementModel";

const loan = (id: string, placed = false): PlaceableLoan => ({
  loan_id: id, loan_account_number: `LN${id}`, customer_name: "Farhan Siddiqui", loan_type: "PERSONAL",
  dpd: 47, dpd_bucket: "BUCKET_2", total_outstanding: 281000, overdue_amount: 24600, branch_code: "GGN044",
  region_id: "r", region_name: "Gurugram", region_path: "NORTH.HR.GGN",
  placement_id: placed ? `p-${id}` : null, placed_with_agency_id: placed ? "a" : null,
  placed_with_agency_name: placed ? "Aravalli" : null,
});

const verdict = (outcome: LoanVerdict["outcome"], reason = ""): LoanVerdict => ({
  loan_id: "l", loan_account_number: "LN1", outcome, reason, gates: {}, placement_id: null, case_id: null,
  case_number: null,
});

const agency = (over: Partial<AgencyRoom> = {}): AgencyRoom => ({
  agency_id: "a", code: "AGY", name: "Aravalli", status: "ACTIVE", placeable: true, contract_no: "C/1",
  contract_end: "2027-03-31", max_placed_cases: 2500, active_placements: 1260, headroom: 1240, coverage: [], ...over,
});

describe("loanQuery", () => {
  it("sends only the filters that are set, and always placed + paging", () => {
    expect(loanQuery(EMPTY_FILTERS, 1, 50).toString()).toBe("placed=no&page=1&page_size=50");
  });

  it("drops a DPD bound that is not a whole number instead of sending it", () => {
    const q = loanQuery({ ...EMPTY_FILTERS, dpd_min: "-3", dpd_max: "4.5" }, 1, 50);
    expect(q.has("dpd_min")).toBe(false);
    expect(q.has("dpd_max")).toBe(false);
    expect(loanQuery({ ...EMPTY_FILTERS, dpd_min: " 031 " }, 1, 50).get("dpd_min")).toBe("31");
  });

  it("trims and caps the account search and clamps the page", () => {
    const q = loanQuery({ ...EMPTY_FILTERS, search: `  ${"9".repeat(40)} ` }, 0, 50);
    expect(q.get("search")).toHaveLength(30);
    expect(q.get("page")).toBe("1");
  });
});

describe("selection", () => {
  it("never selects past the batch cap", () => {
    let s = new Set<string>();
    for (let i = 0; i < MAX_BATCH + 5; i++) s = toggle(s, `l${i}`);
    expect(s.size).toBe(MAX_BATCH);
    expect(toggle(s, "l0").has("l0")).toBe(false);          // deselecting still works at the cap
  });

  it("selects a page's unplaced loans only, and clears them when all are selected", () => {
    const page = [loan("1"), loan("2", true), loan("3")];
    const s = togglePage(new Set(), page);
    expect([...s].sort()).toEqual(["1", "3"]);
    expect(pageSelection(s, page)).toBe("all");
    expect(togglePage(s, page).size).toBe(0);
    expect(pageSelection(new Set(["1"]), page)).toBe("some");
    expect(pageSelection(new Set(), page)).toBe("none");
  });

  it("keeps selections from other pages when a page is toggled", () => {
    const s = togglePage(new Set(["other"]), [loan("1")]);
    expect([...s].sort()).toEqual(["1", "other"]);
  });
});

describe("verdicts", () => {
  it("reads the refusal code before the colon", () => {
    const v = verdict("BLOCKED", "NOT_COVERED: contract C/1 does not cover region NORTH.HRX");
    expect(refusalCode(v)).toBe("NOT_COVERED");
    expect(verdictText(v)).toBe("Outside the agency's contracted territory");
    expect(refusalCode(verdict("PLACED", "every gate passed"))).toBeNull();
  });

  it("shows an unknown refusal verbatim rather than guessing", () => {
    expect(verdictText(verdict("BLOCKED", "SOMETHING_NEW: detail"))).toBe("SOMETHING_NEW: detail");
  });

  it("groups blocked loans by reason, most frequent first", () => {
    const vs = [
      verdict("BLOCKED", "CONTRACT_FULL: cap"), verdict("PLACED"), verdict("BLOCKED", "NOT_COVERED: x"),
      verdict("BLOCKED", "CONTRACT_FULL: cap"), verdict("BLOCKED", "weird"),
    ];
    expect(blockedByReason(vs).map((r) => [r.code, r.count])).toEqual([
      ["CONTRACT_FULL", 2], ["NOT_COVERED", 1], ["OTHER", 1],
    ]);
  });
});

describe("agencies", () => {
  it("labels room, no cap, and why an agency cannot take loans", () => {
    expect(headroomLabel(agency())).toBe("1,240 of 2,500 free");
    expect(headroomLabel(agency({ headroom: null, max_placed_cases: null }))).toBe("No cap");
    expect(headroomLabel(agency({ placeable: false }))).toBe("No contract in force");
    expect(headroomLabel(agency({ placeable: false, status: "SUSPENDED" }))).toBe("Agency suspended");
  });

  it("lists each covered region once, in path order", () => {
    const r = (id: string, path: string) => ({ region_id: id, name: id, path });
    const out = regionOptions([
      agency({ coverage: [r("hr", "NORTH.HR"), r("n", "NORTH")] }),
      agency({ agency_id: "b", coverage: [r("hr", "NORTH.HR")] }),
    ]);
    expect(out.map((x) => x.region_id)).toEqual(["n", "hr"]);
  });
});

describe("recall reason", () => {
  it("needs 1-500 characters after trimming", () => {
    expect(recallReasonError("   ")).not.toBeNull();
    expect(recallReasonError("x".repeat(501))).not.toBeNull();
    expect(recallReasonError(" Borrower moved ")).toBeNull();
  });
});

describe("engine runs", () => {
  const run = (over: Partial<EngineRun> = {}): EngineRun => ({
    run_id: "r", plan_date: "2026-10-15", status: "PLANNED", simulate: false, strategy: "MIN_COST_FLOW",
    exploration_rate: 0, seed: 20261015, created_by: "u-planner", applied_by: null, applied_at: null,
    totals: { evaluated: 3, placed: 3, kept: 0, blocked: 0, deferred: 0, recalled: 0 },
    expected_recovery_total: 1000, summary: {}, ...over,
  });

  it("lets only a second person apply a planned run", () => {
    expect(applyBlocker(run(), "u-other")).toBeNull();
    expect(applyBlocker(run(), "u-planner")).toMatch(/Another bank admin/);
    expect(applyBlocker(run(), undefined)).not.toBeNull();
    expect(applyBlocker(run({ status: "SIMULATED", simulate: true }), "u-other")).toMatch(/simulation/);
    expect(applyBlocker(run({ status: "APPLIED" }), "u-other")).toMatch(/applied/);
  });

  it("reads the exploration percent within 0-20", () => {
    expect(explorationRate("")).toBe(0);
    expect(explorationRate("10")).toBeCloseTo(0.1);
    expect(explorationRate("20")).toBeCloseTo(0.2);
    expect(explorationRate("20.5")).toBeNull();
    expect(explorationRate("-1")).toBeNull();
    expect(explorationRate("1e1")).toBeNull();
  });
});
