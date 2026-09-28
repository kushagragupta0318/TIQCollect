// Command Center › Overview (plan §5.3, task C03): the twelve header KPIs,
// the book's totals and a rule-based summary, all from the bank's own
// analytics views via GET /bank/overview. A card that cannot be computed yet
// shows "—" and says why; nothing here is sample data.
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import api from "@/api/axios";
import { errorDetail } from "@/lib/apiError";
import { AnalyticsError, AnalyticsLoading, Panel } from "../components/analytics";
import { FilterBar } from "../components/FilterBar";
import { useKpiFilter } from "../components/useKpiFilter";
import { filterToParams } from "../components/kpiFilter";
import { ExecutiveHeader, PageRoot } from "../components/PageTemplate";
import { PulseKpiFlow } from "../components/PulseKpiFlow";
import { coverage, frameLabel, narrativeCaption, type OverviewResponse } from "./overviewModel";

export function BankOverviewPage() {
  const navigate = useNavigate();
  const [filter] = useKpiFilter();
  const params = filterToParams(filter).toString();
  const q = useQuery({
    queryKey: ["bank", "overview", params],
    queryFn: async () => (await api.get<OverviewResponse>(`/bank/overview${params ? `?${params}` : ""}`)).data,
  });

  if (q.isLoading) return <PageRoot><FilterBar /><AnalyticsLoading label="Loading portfolio…" /></PageRoot>;
  if (q.isError || !q.data) {
    return (
      <PageRoot>
        <FilterBar />
        <AnalyticsError>{errorDetail(q.error, "The overview could not be loaded.")}</AnalyticsError>
      </PageRoot>
    );
  }

  const ov = q.data;
  const cov = coverage(ov.kpis);
  const meta = [ov.bank_name || "Your bank", frameLabel(ov.as_of), `${cov.available} of ${cov.total} KPIs available`];

  return (
    <PageRoot>
      <ExecutiveHeader
        title="Portfolio Overview"
        meta={meta}
        live={cov.complete}
        scopeNote="The whole book across every agency unless filtered. Hover a card for how it is computed."
      />

      <FilterBar />

      <PulseKpiFlow kpis={ov.kpis} rows={ov.rows} frameLabel={frameLabel(ov.as_of)}
                    onSelect={(drill) => navigate(`/bank/analytics?tab=${encodeURIComponent(drill)}`)} />

      {ov.notes.length > 0 && (
        <div className="space-y-1 rounded-[12px] border border-border/60 bg-muted/30 px-4 py-3 text-[12px] text-muted-foreground">
          {ov.notes.map((n) => <p key={n}>{n}</p>)}
        </div>
      )}

      {ov.totals.length > 0 && (
        <Panel title="The book" hint={frameLabel(ov.as_of)}>
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-6">
            {ov.totals.map((t) => (
              <div key={t.label} title={t.basis}>
                <dt className="text-[11px] font-medium text-muted-foreground">{t.label}</dt>
                <dd className="mt-1 text-[18px] font-bold tabular-nums text-foreground">{t.value}</dd>
              </div>
            ))}
          </dl>
        </Panel>
      )}

      {ov.narrative.sentences.length > 0 && (
        <section className="border-l-2 border-border pl-4">
          <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground/80">
            {narrativeCaption(ov.narrative.generated_by)}
          </p>
          <p className="text-[13px] leading-relaxed text-muted-foreground">{ov.narrative.sentences.join(" ")}</p>
        </section>
      )}
    </PageRoot>
  );
}
