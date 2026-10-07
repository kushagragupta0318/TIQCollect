// @vitest-environment jsdom
/**
 * The MLOps console's two honesty rules and its one authorisation rule.
 *
 * The metric test is the important one. ADR 0008 / ML-1: the artifact's OOT
 * figures were measured WITH a borrower-stance feature the product never
 * records, so they describe a model that cannot exist in production. If this
 * page ever leads with them, a bank reads a better number than the model can
 * deliver — and it is exactly the number somebody would act on. The test
 * pins the live-equivalent figure as the headline and the artifact figure as
 * explicitly development-time.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { useAuthStore } from "@/store/authStore";
import { MLOpsPage } from "./MLOpsPage";
import * as model from "./mlopsModel";

vi.mock("./mlopsModel", async (orig) => {
  const real = await orig<typeof import("./mlopsModel")>();
  return { ...real, getModelsOverview: vi.fn(), getCandidates: vi.fn(),
           approveCandidate: vi.fn(), rejectCandidate: vi.fn(), promoteCandidate: vi.fn() };
});

const mocked = model as unknown as {
  getModelsOverview: ReturnType<typeof vi.fn>;
  getCandidates: ReturnType<typeof vi.fn>;
  approveCandidate: ReturnType<typeof vi.fn>;
  promoteCandidate: ReturnType<typeof vi.fn>;
};

afterEach(() => { cleanup(); vi.clearAllMocks(); });

function overview(over: Partial<model.ModelsOverview["recovery_risk"]> = {}): model.ModelsOverview {
  return {
    synthetic_warning: "Trained on synthetic borrowers; no real lending outcome informed it.",
    scoring_enabled: true,
    checked_at: "2026-10-07T00:00:00+00:00",
    recovery_risk: {
      serving_version: "2.2.0",
      configured_version: "2.2.0",
      artifact_loaded: true,
      artifact_sha256: "abc",
      method: "GAM",
      features: [],
      artifact_metrics: { gini: 0.5122, ks: 38.66, auc: 0.7561 },
      live_equivalent: { gini: 0.4796, ks: 35.74, measured_on: "2026-09-24", basis: null },
      monitoring: { status: "not_ready", matured: 0, required_matured: 500,
                    horizon_days: 30, first_outcomes_mature_from: "2026-10-08" },
      governance: { auto_retrain_enabled: true, latest_candidate: null },
      ...over,
    },
  } as model.ModelsOverview;
}

const CANDIDATE: model.CandidateRow = {
  candidate_id: "c1", model_name: "recovery_risk", candidate_version: "2.3.0",
  incumbent_version: "2.2.0", state: "PENDING_APPROVAL",
  trigger_reasons: ["drift"], cohort_rows: 12000, created_at: "2026-10-06T00:00:00+00:00",
};

function show(role: string, ov = overview(), candidates = [CANDIDATE]) {
  useAuthStore.setState({ user: { id: "u1", email: "t@t.test", full_name: "T", role, is_active: true } } as never);
  mocked.getModelsOverview.mockResolvedValue(ov);
  mocked.getCandidates.mockResolvedValue({ candidates, count: candidates.length });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MLOpsPage /></QueryClientProvider>);
}

describe("MLOpsPage honesty", () => {
  it("leads with the LIVE-EQUIVALENT figures, not the artifact's", async () => {
    show("BANK_TECHOPS");
    await waitFor(() => expect(screen.getByText("Live-equivalent performance")).toBeTruthy());
    expect(screen.getByText("0.480")).toBeTruthy();      // live-equivalent Gini, the headline
    expect(screen.getByText("35.74")).toBeTruthy();      // live-equivalent KS
  });

  it("shows the artifact's figures ONLY as development-time, never as performance", async () => {
    show("BANK_TECHOPS");
    await waitFor(() => expect(screen.getByText(/development figures, not live ones/)).toBeTruthy());
    const line = screen.getByText(/development figures, not live ones/).textContent ?? "";
    expect(line).toContain("0.512");                     // the artifact Gini lives in THAT sentence
    expect(line).toContain("38.66");
  });

  it("refuses to fall back to the flattering number when there is no live-equivalent", async () => {
    show("BANK_TECHOPS", overview({ live_equivalent: null }));
    await waitFor(() => expect(screen.getByText(/No live-equivalent measurement is published/)).toBeTruthy());
    expect(screen.getByText(/are NOT a substitute/)).toBeTruthy();
  });

  it("puts the synthetic-training warning on the page, not in a footnote", async () => {
    show("BANK_ADMIN");
    await waitFor(() => expect(screen.getByText(/Trained on synthetic borrowers/)).toBeTruthy());
  });

  it("says monitoring is not ready rather than computing a number from too little", async () => {
    show("BANK_ADMIN");
    await waitFor(() => expect(screen.getByText("not_ready")).toBeTruthy());
    expect(screen.getByText("500")).toBeTruthy();
  });
});

describe("MLOpsPage authorisation (F12)", () => {
  it("offers no approve or promote control to a BANK_ADMIN", async () => {
    show("BANK_ADMIN");
    await waitFor(() => expect(screen.getByText("2.3.0")).toBeTruthy());
    expect(screen.queryByText("Approve")).toBeNull();
    expect(screen.queryByText("Promote")).toBeNull();
    expect(screen.getByText(/restricted to Tech Ops/)).toBeTruthy();
  });

  it("offers approve to Tech Ops on a pending candidate, and promote only once approved", async () => {
    show("BANK_TECHOPS");
    await waitFor(() => expect(screen.getByText("Approve")).toBeTruthy());
    expect(screen.queryByText("Promote")).toBeNull();     // PENDING_APPROVAL is not promotable
    cleanup();
    show("BANK_TECHOPS", overview(), [{ ...CANDIDATE, state: "APPROVED" }]);
    await waitFor(() => expect(screen.getByText("Promote")).toBeTruthy());
    expect(screen.queryByText("Approve")).toBeNull();
  });

  it("calls the server for an approval rather than deciding anything itself", async () => {
    show("BANK_TECHOPS");
    await waitFor(() => expect(screen.getByText("Approve")).toBeTruthy());
    fireEvent.click(screen.getByText("Approve"));
    await waitFor(() => expect(mocked.approveCandidate).toHaveBeenCalledWith("c1"));
  });

  it("shows the server's refusal verbatim instead of second-guessing it", async () => {
    show("BANK_TECHOPS");
    await waitFor(() => expect(screen.getByText("Approve")).toBeTruthy());
    mocked.approveCandidate.mockRejectedValue({
      response: { data: { detail: "The promoter must differ from the approver." } },
    });
    fireEvent.click(screen.getByText("Approve"));
    await waitFor(() => expect(screen.getByText(/must differ from the approver/)).toBeTruthy());
  });
});

describe("mlopsModel", () => {
  it("knows which state each action belongs to", () => {
    expect(model.canApprove("PENDING_APPROVAL")).toBe(true);
    expect(model.canApprove("APPROVED")).toBe(false);
    expect(model.canPromote("APPROVED")).toBe(true);
    expect(model.canPromote("PENDING_APPROVAL")).toBe(false);
  });

  it("formats a missing metric as a dash rather than NaN", () => {
    expect(model.metric(null)).toBe("—");
    expect(model.metric(undefined)).toBe("—");
    expect(model.metric(0.4796)).toBe("0.480");
  });
});
