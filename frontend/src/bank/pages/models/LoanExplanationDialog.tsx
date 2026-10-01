// One loan's latest recovery_risk score, explained (GET /bank/loans/{id}/explanation).
// Opened from the Placements loan rows. Both probabilities are labelled; reasons
// read against the book's typical account; a decline reads as a decision; the
// provenance and the reconciliation sit behind an analyst expander.
import { useQuery } from "@tanstack/react-query";
import { Ban } from "lucide-react";
import { getLoanExplanation, type Prediction } from "@/api/bankModels";
import { errorDetail } from "@/lib/apiError";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "../../ui/dialog";
import { Badge } from "../../ui/badge";
import { Bar100 } from "../../components/analytics";
import { SyntheticBanner } from "./ModelsPage";
import { coverageText, explanationState, fixed, reasonLines, reconciliation } from "./modelsLogic";

const RISK_UP = "#D92D20";
const RISK_DOWN = "#039855";

function Reasons({ p }: { p: Prediction }) {
  const lines = reasonLines(p.reasons);
  if (lines.length === 0) return <p className="text-[12px] text-muted-foreground">No reason reached the reporting threshold.</p>;
  return (
    <ol className="space-y-3">
      {lines.map((l) => (
        <li key={l.key} className="space-y-1">
          <div className="flex items-baseline justify-between gap-3 text-[12.5px]">
            <span className="font-medium text-foreground">{l.what}</span>
            <span className={l.direction === "increases_risk" ? "text-destructive" : "text-success"}>{l.effect}</span>
          </div>
          <Bar100 pct={l.weight * 100} color={l.direction === "increases_risk" ? RISK_UP : RISK_DOWN} />
        </li>
      ))}
    </ol>
  );
}

function Provenance({ p }: { p: Prediction }) {
  const rec = reconciliation(p.contributions);
  const lines = reasonLines(p.reasons);
  return (
    <details className="rounded-[12px] border border-border/50 px-4 py-3 text-[12px]">
      <summary className="cursor-pointer font-semibold text-foreground">Analyst detail</summary>
      <dl className="mt-3 grid gap-x-6 gap-y-1.5 sm:grid-cols-[auto_1fr]">
        <dt className="text-muted-foreground">Model</dt>
        <dd>{p.model_name} {p.model_version}{p.is_serving_version ? " (serving)" : " (not the serving version)"}</dd>
        <dt className="text-muted-foreground">Scored</dt><dd>{p.scored_at ?? "—"} (features as of {p.as_of_date ?? "—"})</dd>
        <dt className="text-muted-foreground">Points</dt><dd className="tabular-nums">{p.points ?? "—"} (display scaling of the log-odds; not a scorecard)</dd>
        <dt className="text-muted-foreground">Checksum</dt><dd className="font-mono break-all">{p.artifact_sha256 ?? "—"}</dd>
        {Object.entries(p.scoring_versions).map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-muted-foreground">{k.replace(/_/g, " ")}</dt><dd className="font-mono break-all">{v}</dd>
          </div>
        ))}
        {lines.map((l) => (
          <div key={l.key} className="contents">
            <dt className="text-muted-foreground">{l.what}</dt><dd className="tabular-nums">{l.raw}</dd>
          </div>
        ))}
        {rec && (
          <>
            <dt className="text-muted-foreground">Reconciliation</dt>
            <dd className="tabular-nums">
              intercept {fixed(rec.intercept, 4)} + all contributions {fixed(rec.sum, 4)} = log-odds {fixed(rec.logit, 4)}
              {" "}(difference {rec.gap < 1e-4 ? "< 0.0001" : fixed(rec.gap, 4)})
            </dd>
          </>
        )}
      </dl>
    </details>
  );
}

function Body({ loanId }: { loanId: string }) {
  const q = useQuery({ queryKey: ["bank-loan-explanation", loanId], queryFn: () => getLoanExplanation(loanId) });
  if (q.isPending) return <p className="py-6 text-center text-[12.5px] text-muted-foreground">Loading the score…</p>;
  if (q.isError) return <p className="py-6 text-center text-[12.5px] text-destructive">{errorDetail(q.error, "The score could not be loaded.")}</p>;
  const p = q.data.prediction;
  const state = explanationState(p);
  if (state.kind === "unscored") {
    return (
      <p className="py-4 text-[12.5px] text-muted-foreground">
        This loan has not been scored by the recovery-risk model yet. It is scored when it enters an allocation pool.
      </p>
    );
  }
  if (!p) return null;
  return (
    <div className="space-y-5">
      {state.kind === "declined" ? (
        <div className="flex items-start gap-3 rounded-[16px] border border-border/60 bg-muted/30 px-4 py-3">
          <Ban className="mt-0.5 size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
          <div className="space-y-1 text-[12.5px]">
            <p className="font-semibold text-foreground">The model declined to score this account.</p>
            <p className="text-muted-foreground">
              {state.coverage}; below {state.floor} the model declines rather than guesses. It records the decline, and
              the allocation treats the account without the model.
            </p>
            <p className="text-[11.5px] text-muted-foreground">{state.reason}</p>
          </div>
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="rounded-[16px] border border-border/50 px-4 py-3">
            <p className="text-[11.5px] text-muted-foreground">Chance of a payment next cycle</p>
            <p className="mt-1.5 text-[22px] font-bold tabular-nums">{state.pPayment}</p>
          </div>
          <div className="rounded-[16px] border border-border/50 px-4 py-3">
            <p className="text-[11.5px] text-muted-foreground">Chance of no payment (what the model stores)</p>
            <p className="mt-1.5 text-[22px] font-bold tabular-nums">{state.pNoPayment}</p>
          </div>
          <div className="rounded-[16px] border border-border/50 px-4 py-3">
            <p className="text-[11.5px] text-muted-foreground">Risk band (A safest, E riskiest)</p>
            <p className="mt-1.5 text-[22px] font-bold">{state.band ?? "—"}</p>
          </div>
        </div>
      )}

      {state.kind === "scored" && (
        <section className="space-y-2">
          <h3 className="text-[13px] font-semibold text-foreground">Why, against the book's typical account</h3>
          <Reasons p={p} />
          <p className="text-[11px] text-muted-foreground">
            Associations the model learned from synthetic borrowers, not causes. {coverageText(p.feature_coverage, p.n_features)}.
          </p>
        </section>
      )}

      {state.kind === "scored" && p.stance_recorded === false && (
        <p className="flex items-start gap-2 text-[12px] text-muted-foreground">
          <Badge variant="softWarning" className="shrink-0 whitespace-nowrap">Stance not recorded</Badge>
          <span>No borrower stance was on record for this account when it was scored, so the model ran without its strongest behavioural input.</span>
        </p>
      )}

      <Provenance p={p} />
      <SyntheticBanner text={p.synthetic_warning} />
    </div>
  );
}

export function LoanExplanationDialog({ loanId, loanLabel, onClose }: { loanId: string | null; loanLabel: string; onClose: () => void }) {
  return (
    <Dialog open={loanId !== null} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-[720px]">
        <DialogHeader>
          <DialogTitle>Why this score: {loanLabel}</DialogTitle>
          <DialogDescription>The recovery-risk model's latest score for this loan, with its reasons and where it came from.</DialogDescription>
        </DialogHeader>
        {loanId && <div className="overflow-y-auto px-6 py-5"><Body loanId={loanId} /></div>}
      </DialogContent>
    </Dialog>
  );
}
