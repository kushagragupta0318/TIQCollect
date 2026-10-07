// Tech Ops › MLOps — what is serving, how well it really does, what is
// queued, and who may promote it.
//
// Two honesty rules are built into the page rather than left to whoever
// writes the copy:
//   1. The ONLY performance figures shown are the LIVE-EQUIVALENT ones
//      (ADR 0008 / ML-1). The artifact's own OOT metrics describe a model
//      measured with a borrower-stance feature the product never records, so
//      they are not displayed at all -- owner's decision, 2026-10-07: this is
//      a sales surface, and a screenshot of the flattering number travels
//      without its caveat however carefully the caveat is written.
//   2. Everything here was trained on SYNTHETIC borrowers. The warning comes
//      from the artifact's own metadata and sits at the top, not in a
//      footnote.
// Both figures come from the API. This page restates no number.
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, BadgeCheck, ShieldAlert } from "lucide-react";

import { errorDetail } from "@/lib/apiError";
import { useAuthStore } from "@/store/authStore";
import { AnalyticsError, AnalyticsLoading, Panel, Tile } from "../../components/analytics";
import { DataTable, type DataColumn } from "../../components/DataTable";
import { PageRoot, ToolHeader } from "../../components/PageTemplate";
import { Badge } from "../../ui/badge";
import { Button } from "../../ui/button";
import {
  approveCandidate, canApprove, canPromote, getCandidates, getModelsOverview, metric,
  promoteCandidate, reasonLabel, rejectCandidate, stateTone, type CandidateRow,
} from "./mlopsModel";

export function MLOpsPage() {
  const role = useAuthStore((s) => s.user?.role);
  const mayAct = role === "BANK_TECHOPS";          // ml.approve / ml.promote (F12)
  const qc = useQueryClient();
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  const overview = useQuery({ queryKey: ["bank", "models"], queryFn: getModelsOverview });
  const candidates = useQuery({ queryKey: ["bank", "mlops", "candidates"], queryFn: () => getCandidates() });

  const act = useMutation({
    mutationFn: async ({ id, kind, }: { id: string; kind: "approve" | "reject" | "promote" }) => {
      if (kind === "approve") return approveCandidate(id);
      if (kind === "reject") return rejectCandidate(id);
      return promoteCandidate(id);
    },
    onMutate: ({ id }) => { setBusy(id); setFailed(null); },
    onSettled: () => setBusy(null),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["bank", "mlops", "candidates"] });
                       qc.invalidateQueries({ queryKey: ["bank", "models"] }); },
    // The server owns the two-person rule; a refusal is shown verbatim rather
    // than second-guessed here.
    onError: (e) => setFailed(errorDetail(e, "The server refused that action.")),
  });

  if (overview.isError) {
    return <PageRoot><AnalyticsError>{errorDetail(overview.error, "Could not load the model.")}</AnalyticsError></PageRoot>;
  }
  if (!overview.data) return <PageRoot><AnalyticsLoading label="Loading the model…" /></PageRoot>;

  const o = overview.data;
  const rr = o.recovery_risk;
  const live = rr.live_equivalent;
  const mon = rr.monitoring ?? {};

  const columns: DataColumn<CandidateRow>[] = [
    { key: "candidate_version", header: "Candidate", render: (c) => c.candidate_version ?? "—" },
    { key: "incumbent_version", header: "Against", render: (c) => c.incumbent_version ?? "—" },
    {
      key: "state", header: "State",
      render: (c) => {
        const tone = stateTone(c.state);
        return <Badge variant={tone === "ok" ? "default" : tone === "danger" ? "destructive" : "secondary"}>
          {c.state.toLowerCase().replace(/_/g, " ")}
        </Badge>;
      },
    },
        // Classified, never echoed: monitor.py builds these strings with the
    // artifact's own metrics inside them. See reasonLabel.
    { key: "trigger_reasons", header: "Why",
      render: (c) => (c.trigger_reasons?.length
        ? [...new Set(c.trigger_reasons.map(reasonLabel))].join(", ")
        : "—") },
    { key: "cohort_rows", header: "Rows", align: "right", render: (c) => c.cohort_rows?.toLocaleString() ?? "—" },
    {
      key: "actions", header: "",
      render: (c) => {
        if (!mayAct) return null;
        const running = busy === c.candidate_id;
        if (canApprove(c.state)) {
          return (
            <span className="flex gap-2">
              <Button size="sm" disabled={running}
                      onClick={() => act.mutate({ id: c.candidate_id, kind: "approve" })}>Approve</Button>
              <Button size="sm" variant="outline" disabled={running}
                      onClick={() => act.mutate({ id: c.candidate_id, kind: "reject" })}>Reject</Button>
            </span>
          );
        }
        if (canPromote(c.state)) {
          return <Button size="sm" disabled={running}
                         onClick={() => act.mutate({ id: c.candidate_id, kind: "promote" })}>Promote</Button>;
        }
        return null;
      },
    },
  ];

  return (
    <PageRoot>
      <ToolHeader
        title="MLOps"
        icon={Activity}
        description="The serving model, its live-equivalent performance, and the retraining queue."
      />

      {/* Not a footnote. Every figure below describes a model trained on
          invented borrowers. */}
      <div className="flex items-start gap-2 rounded-xl border p-3 text-[12px]"
           style={{ borderColor: "#F0C36D", background: "#FFF8E8" }}>
        <ShieldAlert className="size-4 shrink-0 mt-0.5" style={{ color: "#B47B12" }} />
        <p>{o.synthetic_warning}</p>
      </div>

      <Panel title="Serving champion"
             hint={rr.artifact_loaded ? undefined : "The configured artifact did NOT load on this deployment."}>
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <Tile label="Serving version" value={rr.serving_version ?? "not loaded"} />
          <Tile label="Configured (champion.txt)" value={rr.configured_version ?? "—"} />
          <Tile label="Scoring" value={o.scoring_enabled ? "enabled" : "disabled"} />
          <Tile label="Auto-retrain" value={rr.governance.auto_retrain_enabled ? "on (stops at approval)" : "off"} />
        </div>

        {/* RULE 1, in the layout: live-equivalent is the headline. */}
        <div className="mt-5">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            Live-equivalent performance
          </p>
          {live ? (
            <>
              <div className="mt-2 grid grid-cols-2 gap-4 md:grid-cols-3">
                <Tile label="Gini" value={metric(live.gini)} />
                <Tile label="KS" value={metric(live.ks, 2)} />
                <Tile label="Measured on" value={live.measured_on ?? "—"} />
              </div>
              <p className="mt-2 text-[11px] text-muted-foreground">
                {live.basis ?? "Measured with the borrower-stance feature held at NONE, because the product never records it (ADR 0008)."}
              </p>
            </>
          ) : (
            <p className="mt-2 text-[11px] text-muted-foreground">
              No live-equivalent measurement is published for this version, so no performance figure is
              shown. The artifact's own development metrics are deliberately not displayed anywhere on
              this page: they were measured with an input this product never records, and a figure that
              cannot be reproduced in production is worse than none.
            </p>
          )}
        </div>
      </Panel>

      <Panel title="Monitoring">
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <Tile label="Status" value={mon.status ?? "—"} />
          <Tile label="Matured outcomes" value={mon.matured?.toLocaleString() ?? "—"} />
          <Tile label="Required" value={mon.required_matured?.toLocaleString() ?? "—"} />
          <Tile label="First mature from" value={mon.first_outcomes_mature_from ?? "—"} />
        </div>
        <p className="mt-3 text-[11px] text-muted-foreground">
          Monitoring compares what the model scored against what then happened. Until the required
          number of outcomes has matured it reports <em>not ready</em> rather than a number computed
          from too little evidence.
        </p>
      </Panel>

      <Panel title="Retraining queue"
             hint={mayAct ? undefined : "Approving and promoting are restricted to Tech Ops (F12)."}>
        {failed && <p className="mb-3 text-[12px] font-medium text-danger-600">{failed}</p>}
        {candidates.isError ? (
          <AnalyticsError>{errorDetail(candidates.error, "Could not load the candidates.")}</AnalyticsError>
        ) : !candidates.data ? (
          <AnalyticsLoading label="Loading candidates…" />
        ) : candidates.data.candidates.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">No retraining attempts yet.</p>
        ) : (
          <DataTable columns={columns} rows={candidates.data.candidates}
                     rowKey={(c) => c.candidate_id} minWidth={820} />
        )}
        <p className="mt-3 flex items-start gap-2 text-[11px] text-muted-foreground">
          <BadgeCheck className="size-3.5 shrink-0 mt-0.5" />
          Approving is not promoting. Promotion rewrites champion.txt and needs a DIFFERENT person from
          the approver — the server enforces that, and a refusal here is the server's, shown as it came.
        </p>
      </Panel>
    </PageRoot>
  );
}

export default MLOpsPage;
