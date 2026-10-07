// Governance > Models: what scores the bank's book, as a set, and the one trained
// model's card. Written for a bank's model-risk reader: every figure comes from
// the artifact or the prediction rows (GET /bank/models), the honest figure is
// the headline, and what the model cannot yet tell you is stated, not implied.
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Layers, ShieldCheck } from "lucide-react";
import { ExecutiveHeader, PageFailure, PageLoading, PageRoot } from "../../components/PageTemplate";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../ui/card";
import { Badge } from "../../ui/badge";
import { Button } from "../../ui/button";
import { Tile } from "../../components/analytics";
import { getModelsOverview, type ModelsOverview, type ScoringLayer } from "@/api/bankModels";
import { errorDetail, errorStatus } from "@/lib/apiError";
import { KIND_LABELS, fixed, monitoringText, pct, stanceCoverageText } from "./modelsLogic";

export function SyntheticBanner({ text }: { text: string }) {
  return (
    <div role="note" className="flex items-start gap-2.5 rounded-[16px] border border-[#F79009]/40 bg-[#FDF0DC] px-4 py-3 text-[12.5px] text-[#B54708]">
      <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <p><span className="font-semibold">Trained on synthetic borrowers.</span> {text}</p>
    </div>
  );
}

function LayerCard({ layer }: { layer: ScoringLayer }) {
  return (
    <div className="flex flex-col gap-2 rounded-[16px] border border-border/50 bg-card px-5 py-4">
      <div className="flex items-start justify-between gap-2">
        <p className="text-[14px] font-semibold text-foreground">{layer.name}</p>
        <Badge variant={layer.is_modelled ? "softPrimary" : "outline"}>{KIND_LABELS[layer.kind]}</Badge>
      </div>
      <p className="text-[11px] tabular-nums text-muted-foreground">{layer.version ? `Version ${layer.version}` : "No version stamp: a formula with no fitted weights"}</p>
      <dl className="space-y-1.5 text-[12px]">
        <div><dt className="inline font-medium text-foreground">Decides: </dt><dd className="inline text-muted-foreground">{layer.decides}</dd></div>
        <div><dt className="inline font-medium text-foreground">How: </dt><dd className="inline text-muted-foreground">{layer.method}</dd></div>
        <div><dt className="inline font-medium text-foreground">Acts on: </dt><dd className="inline text-muted-foreground">{layer.acts_on}</dd></div>
        <div><dt className="inline font-medium text-foreground">Evidence: </dt><dd className="inline text-muted-foreground">{layer.evidence}</dd></div>
      </dl>
    </div>
  );
}

function ModelCard({ data }: { data: ModelsOverview }) {
  const m = data.recovery_risk;
  const live = m.live_equivalent;
  const stanceFeatures = new Set(m.stance.related_features);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><ShieldCheck className="size-5 text-primary" />Recovery risk: model card</CardTitle>
        <CardDescription>
          Version {m.serving_version ?? m.configured_version ?? "unknown"}. {m.method}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {!m.artifact_loaded && (
          <p role="alert" className="rounded-[12px] border border-destructive/40 bg-destructive/5 px-4 py-3 text-[12.5px] font-medium text-destructive">
            The serving artifact could not be loaded on this deployment.
            {m.configured_version ? ` champion.txt names ${m.configured_version}, but nothing is serving it, so scoring is falling back to the non-model path.` : " No champion is recorded."}
          </p>
        )}
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Tile label="Gini, on the data the product records" value={fixed(live?.gini, 3)}
                sub={live ? `Out of time, synthetic · measured ${live.measured_on}` : "No such figure for this version"} />
          <Tile label="KS, on the data the product records" value={fixed(live?.ks, 1)}
                sub={live ? `Out of time, synthetic · measured ${live.measured_on}` : "No such figure for this version"} />
          <Tile label="Borrower stance recorded" value={pct(m.stance.share, 1)}
                sub={m.stance.latest_scoring_day ? `Of accounts scored ${m.stance.latest_scoring_day}` : "Nothing scored yet"} />
          <Tile label="Declined to score" value={`${m.abstention.latest_day_declined.toLocaleString("en-IN")}`}
                sub={`Latest day; ${m.abstention.latest_day_scored.toLocaleString("en-IN")} scored`} />
        </div>

        <section className="space-y-2 text-[12.5px] leading-relaxed text-muted-foreground">
          <h3 className="text-[13px] font-semibold text-foreground">Borrower stance coverage</h3>
          <p>
            Scored the way the product actually records a borrower's stance (will pay, hardship, refuses…), which the
            product has captured since {m.stance.capture_since}.
          </p>
          <p>{stanceCoverageText(m.stance)}</p>
        </section>

        <section className="space-y-2 text-[12.5px] leading-relaxed text-muted-foreground">
          <h3 className="text-[13px] font-semibold text-foreground">What the number means</h3>
          <p>
            The stored score is {m.stored_probability_means}. The nightly allocation uses its complement, the chance of a
            payment. Every account view on this portal labels which of the two it shows.
          </p>
          <p>
            Below {pct(m.abstention.coverage_floor)} input coverage the model declines to score an account rather than
            guess, and records that it declined. A declined account is allocated without the model.
          </p>
        </section>

        <section className="space-y-2">
          <h3 className="text-[13px] font-semibold text-foreground">Inputs ({m.features.length})</h3>
          <div className="flex flex-wrap gap-1.5">
            {m.features.map((f) => (
              <Badge key={f.code} variant={stanceFeatures.has(f.code) ? "softWarning" : "outline"}
                     title={stanceFeatures.has(f.code) ? "Depends on the borrower stance, recorded since the capture began; see coverage above" : f.code}>
                {f.label}
              </Badge>
            ))}
          </div>
        </section>

        <section className="space-y-2 text-[12.5px] leading-relaxed text-muted-foreground">
          <h3 className="text-[13px] font-semibold text-foreground">Monitoring and control</h3>
          <p>{monitoringText(m.monitoring)}</p>
          <p>
            Retraining {m.governance.auto_retrain_enabled ? "runs automatically" : "is started by hand"} and stops at a
            candidate. A person approves the candidate and a different person promotes it; nothing reaches your book
            without both.
            {m.governance.latest_candidate
              ? ` Latest candidate: ${m.governance.latest_candidate.version ?? "unversioned"}, ${m.governance.latest_candidate.state.toLowerCase().replace(/_/g, " ")}.`
              : " No candidate has been trained yet."}
          </p>
        </section>

        <details className="rounded-[12px] border border-border/50 px-4 py-3 text-[12px]">
          <summary className="cursor-pointer font-semibold text-foreground">Provenance, for an analyst</summary>
          <dl className="mt-3 grid gap-x-6 gap-y-1.5 sm:grid-cols-[auto_1fr]">
            <dt className="text-muted-foreground">Artifact checksum</dt>
            <dd className="font-mono break-all">{m.artifact_sha256 ?? "—"}</dd>
            {Object.entries(m.scoring_versions).map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-muted-foreground">{k.replace(/_/g, " ")}</dt>
                <dd className="font-mono break-all">{v}</dd>
              </div>
            ))}
          </dl>
        </details>
      </CardContent>
    </Card>
  );
}

export default function ModelsPage() {
  const q = useQuery({ queryKey: ["bank-models"], queryFn: getModelsOverview, staleTime: 60_000 });
  const header = (
    <ExecutiveHeader
      title="Models"
      meta={["Governance", q.data ? `recovery_risk ${q.data.recovery_risk.serving_version ?? q.data.recovery_risk.configured_version ?? "?"}` : "Loading…"]}
      scopeNote="What scores your book, what each layer is, and what it cannot yet tell you."
    />
  );
  if (q.isPending) return <PageRoot>{header}<PageLoading label="Loading the model register…" /></PageRoot>;
  if (q.isError) {
    return (
      <PageRoot>
        {header}
        <PageFailure>
          {errorStatus(q.error) === 403
            ? "Your role cannot read the model register (it needs the MLOps read permission)."
            : errorDetail(q.error, "The model register could not be loaded.")}
        </PageFailure>
        <div className="flex justify-center"><Button variant="outline" onClick={() => q.refetch()}>Retry</Button></div>
      </PageRoot>
    );
  }
  const data = q.data;
  return (
    <PageRoot>
      {header}
      <SyntheticBanner text={data.synthetic_warning} />
      {!data.scoring_enabled && (
        <p role="note" className="text-[12.5px] font-semibold text-destructive">
          Model scoring is switched off on this deployment: the allocation is running on its non-model rule.
        </p>
      )}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Layers className="size-5 text-primary" />Six scoring layers, kept separate</CardTitle>
          <CardDescription>
            One trained model and five rules. No layer's output is fed to another as an input: each one's score was
            measured as an extra input to the trained model and added nothing, and keeping them apart means a change to
            one never silently moves another.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {data.layers.map((l) => <LayerCard key={l.key} layer={l} />)}
        </CardContent>
      </Card>
      <ModelCard data={data} />
    </PageRoot>
  );
}
