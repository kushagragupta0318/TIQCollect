// Command Center › Alerts (plan §5.4, task C06): GET /bank/alerts, grouped
// by severity into three DecisionAlerts.tsx feeds (that component already
// renders severity badges per card; three calls group the section headers,
// not the cards' own styling). The global FilterBar is shown for the same
// chrome every Command Center page carries, but /bank/alerts does not take
// its dimensions yet — every rule here answers over the whole bank, not a
// filtered slice (an agency's SLA miss is already scoped to that agency).
import { useQuery } from "@tanstack/react-query";
import { AnalyticsError, AnalyticsLoading } from "../components/analytics";
import { DecisionAlerts, type DecisionAlert } from "../components/DecisionAlerts";
import { FilterBar } from "../components/FilterBar";
import { ExecutiveHeader, PageRoot } from "../components/PageTemplate";
import { fetchBankAlerts } from "@/api/bankAlerts";
import { errorDetail } from "@/lib/apiError";

const SECTIONS: { severity: DecisionAlert["severity"]; subtitle: string }[] = [
  { severity: "critical", subtitle: "Needs attention now" },
  { severity: "warning", subtitle: "Trending the wrong way" },
  { severity: "info", subtitle: "Worth knowing" },
];

export function AlertsPage() {
  const q = useQuery({ queryKey: ["bank", "alerts"], queryFn: fetchBankAlerts });

  return (
    <PageRoot>
      <ExecutiveHeader title="Alerts" meta={["Rules over the placed book, computed fresh on every load"]} live={false}
                       scopeNote="Every rule reads the whole bank; none of them take the filter bar below yet." />
      <FilterBar />

      {q.isLoading && <AnalyticsLoading />}
      {(q.isError || (!q.isLoading && !q.data)) && (
        <AnalyticsError>{errorDetail(q.error, "Alerts could not be loaded.")}</AnalyticsError>
      )}
      {q.data && q.data.length === 0 && (
        <p className="text-[12px] text-muted-foreground px-1">No rule is firing on the book right now.</p>
      )}
      {q.data && q.data.length > 0 && (
        <div className="space-y-8">
          {SECTIONS.map(({ severity, subtitle }) => {
            const alerts = q.data!.filter((a) => a.severity === severity);
            if (alerts.length === 0) return null;
            return <DecisionAlerts key={severity} alerts={alerts} title={`${severity[0].toUpperCase()}${severity.slice(1)}`} subtitle={subtitle} />;
          })}
        </div>
      )}
    </PageRoot>
  );
}

export default AlertsPage;
