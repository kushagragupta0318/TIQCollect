// ─── CHANGELOG (prototype → product) ────────────────────────────────────────
// 2026-08-21 — New file. Customer.risk_score existed for months and reached no
// pixel: one grep hit in the whole frontend, a type declaration. The number was
// dpd/90*60 + (750-cibil)/750*40 — two columns we already had, relabelled — and
// nobody noticed precisely because nothing rendered it.
//
// Three rules this component exists to enforce:
//
//   1. It says "Scorecard", never "AI". The backend sends is_modelled, which is
//      false for hand-chosen weights and true only for a trained model. Same
//      contract as AiBadge and LLMResult.ai_generated: a written-in answer must
//      never be presented as a modelled one.
//   2. Thin evidence shows a BAND, not a number. Below the coverage floor the
//      figure is withheld rather than dressed up — on 2026-08-21, 17% of the
//      book had no behavioural evidence at all, and a confident-looking 79
//      resting on nothing is worse than saying "not enough history".
//   3. Abstained factors are shown AS abstained, with their reason. A factor
//      that had no evidence must never be rendered as a contribution of zero;
//      that is how a fabricated reason ends up in front of an agent.
//
// It is decision support. It carries no verdict and must never be used to
// suppress a visit — eligibility lives in ml/eligibility.py.
// ────────────────────────────────────────────────────────────────────────────
import { useState } from "react";
import { ChevronDown, ChevronUp, Info } from "lucide-react";

export interface RepaymentFactor {
  code: string;
  direction?: "UP" | "DOWN";
  points?: number;
  summary?: string;
  evidence?: Record<string, unknown>;
  abstained?: boolean;
  reason?: string;
}

export interface RepaymentScoreData {
  likelihood: number;
  risk_score: number;
  band: string;
  risk_category: string;
  source: string;
  model_version: string;
  is_modelled: boolean;
  is_confident: boolean;
  evidence_coverage: number;
  as_of: string;
  factors: RepaymentFactor[];
}

const BAND_LABEL: Record<string, string> = {
  LIKELY: "Likely to pay",
  UNCERTAIN: "Uncertain",
  UNLIKELY: "Unlikely to pay",
  VERY_UNLIKELY: "Very unlikely to pay",
};

// Semantic colour, deliberately not the app accent — this encodes a judgement,
// not branding.
const BAND_STYLE: Record<string, { bg: string; fg: string; border: string }> = {
  LIKELY: { bg: "rgba(16,185,129,0.10)", fg: "#047857", border: "rgba(16,185,129,0.28)" },
  UNCERTAIN: { bg: "rgba(245,158,11,0.10)", fg: "#B45309", border: "rgba(245,158,11,0.28)" },
  UNLIKELY: { bg: "rgba(249,115,22,0.10)", fg: "#C2410C", border: "rgba(249,115,22,0.28)" },
  VERY_UNLIKELY: { bg: "rgba(239,68,68,0.10)", fg: "#B91C1C", border: "rgba(239,68,68,0.28)" },
};

const READABLE: Record<string, string> = {
  DELINQUENCY_DEPTH: "How overdue",
  PROMISE_HISTORY: "Promises kept",
  PAYMENT_TRACK: "Money received",
  CONTACTABILITY: "Found at home",
  ARREARS_BURDEN: "Size of arrears",
  BUREAU: "Credit bureau",
  CONDUCT: "Conduct",
  LEGAL_POSTURE: "Legal position",
  SECURITY: "Collateral",
  LAST_PAYMENT_SIZE: "Last payment",
  SEGMENT: "Income type",
};

const REASON: Record<string, string> = {
  NO_RESOLVED_PROMISES: "no promises have come due yet",
  TOO_FEW_VISITS: "too few visits so far",
  NO_CASE_TARGET: "no collection target on record",
  NO_BUREAU_SCORE: "no bureau score on file",
  NO_EMI_ON_RECORD: "no EMI on record",
  NO_ADVERSE_CONDUCT_RECORDED: "nothing adverse recorded",
  NO_LEGAL_ACTION: "no legal action",
  NO_PAYMENT_ON_RECORD: "no payment on record",
  SECURITY_UNDETERMINED: "secured status unclear",
  SEGMENT_UNKNOWN: "income type unknown",
};

export function RepaymentScore({ data }: { data: RepaymentScoreData | null }) {
  const [open, setOpen] = useState(false);
  if (!data) return null;

  const style = BAND_STYLE[data.band] ?? BAND_STYLE.UNCERTAIN;
  const spoke = data.factors.filter((f) => !f.abstained);
  const silent = data.factors.filter((f) => f.abstained);

  return (
    <div
      className="rounded-xl p-4 tiq-glass-card"
      style={{ background: style.bg, border: `1px solid ${style.border}` }}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-wide opacity-70">
            How likely to pay
          </div>
          <div className="flex items-baseline gap-2 mt-0.5">
            {/* Rule 2: the number is withheld, not softened, when evidence is thin. */}
            {data.is_confident ? (
              <span className="text-2xl font-bold" style={{ color: style.fg }}>
                {Math.round(data.likelihood)}
                <span className="text-sm font-semibold opacity-60"> / 100</span>
              </span>
            ) : (
              <span className="text-lg font-bold" style={{ color: style.fg }}>
                {BAND_LABEL[data.band] ?? data.band}
              </span>
            )}
          </div>
          {data.is_confident && (
            <div className="text-xs font-medium mt-0.5" style={{ color: style.fg }}>
              {BAND_LABEL[data.band] ?? data.band}
            </div>
          )}
        </div>

        {/* Rule 1: names the source. Never an "AI" chip for hand-chosen weights. */}
        <span
          className="text-[10px] font-semibold px-2 py-0.5 rounded-md whitespace-nowrap"
          style={{ background: "rgba(0,0,0,0.05)", color: style.fg }}
          title={
            data.is_modelled
              ? `Trained model ${data.model_version}`
              : `Rule-based scorecard ${data.model_version} — not an AI model`
          }
        >
          {data.is_modelled ? "Model" : "Scorecard"}
        </span>
      </div>

      {!data.is_confident && (
        <div className="flex items-start gap-1.5 mt-2 text-[11px] leading-snug opacity-80">
          <Info className="w-3.5 h-3.5 flex-shrink-0 mt-px" />
          <span>
            Not enough history to give a number — {spoke.length} of{" "}
            {data.factors.length} checks had anything to go on. Treat this as a
            rough steer, not a verdict.
          </span>
        </div>
      )}

      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="mt-2 inline-flex items-center gap-1 text-[11px] font-semibold underline-offset-2 hover:underline"
        style={{ color: style.fg }}
      >
        {open ? "Hide reasons" : "Why this score"}
        {open ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
      </button>

      {open && (
        <div className="mt-3 space-y-1.5">
          {spoke
            .slice()
            .sort((a, b) => Math.abs(b.points ?? 0) - Math.abs(a.points ?? 0))
            .map((f) => (
              <div key={f.code} className="flex items-start gap-2 text-[11.5px]">
                <span
                  className="font-mono font-semibold tabular-nums w-11 text-right flex-shrink-0"
                  style={{ color: (f.points ?? 0) >= 0 ? "#047857" : "#B91C1C" }}
                >
                  {(f.points ?? 0) >= 0 ? "+" : ""}
                  {(f.points ?? 0).toFixed(1)}
                </span>
                <span className="font-medium w-28 flex-shrink-0">
                  {READABLE[f.code] ?? f.code}
                </span>
                <span className="opacity-75">{f.summary}</span>
              </div>
            ))}

          {/* Rule 3: silence is shown as silence, never as a zero contribution. */}
          {silent.length > 0 && (
            <div className="pt-1.5 mt-1.5 border-t border-black/10 text-[11px] opacity-60">
              <span className="font-medium">Nothing to go on yet: </span>
              {silent
                .map((f) => `${READABLE[f.code] ?? f.code} (${REASON[f.reason ?? ""] ?? "no data"})`)
                .join(" · ")}
            </div>
          )}

          <div className="pt-1 text-[10px] opacity-50">
            {data.model_version} · as of {data.as_of} · decision support only,
            it does not decide whether to visit
          </div>
        </div>
      )}
    </div>
  );
}
