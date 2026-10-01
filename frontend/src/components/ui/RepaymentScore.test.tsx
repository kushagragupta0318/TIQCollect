/** The scorecard names itself and its provenance where the agent reads it (AI showcase, 2026-09-30). */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RepaymentScore, type RepaymentScoreData } from "./RepaymentScore";

const data = (o: Partial<RepaymentScoreData> = {}): RepaymentScoreData => ({
  likelihood: 63, risk_score: 37, band: "UNCERTAIN", risk_category: "MEDIUM", source: "scorecard",
  model_version: "scorecard-1.1.0", is_modelled: false, is_confident: true, evidence_coverage: 0.9,
  as_of: "2026-09-21", factors: [], ...o,
});

describe("RepaymentScore provenance", () => {
  it("says a hand-weighted scorecard is not a trained model, with its version and date, without expanding", () => {
    const html = renderToStaticMarkup(<RepaymentScore data={data()} />);
    expect(html).toContain("Hand-weighted scorecard scorecard-1.1.0 · not a trained model");
    expect(html).toContain("scored 2026-09-21");
    expect(html).not.toMatch(/>AI</);
  });

  it("names a trained model as one", () => {
    const html = renderToStaticMarkup(<RepaymentScore data={data({ is_modelled: true, model_version: "2.2.0" })} />);
    expect(html).toContain("Trained model 2.2.0");
    expect(html).not.toContain("not a trained model");
  });
});
