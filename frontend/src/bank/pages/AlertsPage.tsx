// Command Center › Alerts (plan §5.4, task C06): GET /bank/alerts, grouped
// by severity into three DecisionAlerts.tsx feeds (that component already
// renders severity badges per card; three calls group the section headers,
// not the cards' own styling). The global FilterBar is shown for the same
// chrome every Command Center page carries, but /bank/alerts does not take
// its dimensions yet — every rule here answers over the whole bank, not a
// filtered slice (an agency's SLA miss is already scoped to that agency).
import { useNavigate } from "react-router";
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

// Every target a rule's own actions[].target names (alerts.py), to where a
// click should actually take the manager. "agency"/"placement" have no id
// to carry yet — they land on the list/leaderboard page, not one row of it.
const ACTION_ROUTES: Record<string, string> = {
  exposure: "/bank/analytics?tab=exposure",
  migration: "/bank/analytics?tab=migration",
  compliance: "/bank/analytics?tab=compliance",
  agency: "/bank/agencies/performance",
  placement: "/bank/agencies/placement",
};

export function AlertsPage() {
  const q = useQuery({ queryKey: ["bank", "alerts"], queryFn: fetchBankAlerts });
  const navigate = useNavigate();
  const data = q.data;

  const onAction = (target: string) => {
    const route = ACTION_ROUTES[target];
    if (route) navigate(route);
  };

  return (
    <PageRoot>
      <ExecutiveHeader title="Alerts" meta={["Rules over the placed book, computed fresh on every load"]} live={false}
                       scopeNote="Every rule reads the whole bank; none of them take the filter bar below yet." />
      <FilterBar />

      {q.isLoading && <AnalyticsLoading />}
      {(q.isError || (!q.isLoading && !data)) && (
        <AnalyticsError>{errorDetail(q.error, "Alerts could not be loaded.")}</AnalyticsError>
      )}
      {data && data.rules_failed.length > 0 && (
        <p className="text-[12px] font-semibold text-destructive px-1">
          {data.rules_failed.length} of {data.rules_total} rules could not be evaluated ({data.rules_failed.join(", ")})
          — the feed below is incomplete, not clean.
        </p>
      )}
      {data && data.alerts.length === 0 && data.rules_failed.length === 0 && (
        <p className="text-[12px] text-muted-foreground px-1">No rule is firing on the book right now.</p>
      )}
      {data && data.alerts.length === 0 && data.rules_failed.length > 0 && (
        <p className="text-[12px] text-muted-foreground px-1">No rule that DID run is firing right now.</p>
      )}
      {data && data.alerts.length > 0 && (
        <div className="space-y-8">
          {SECTIONS.map(({ severity, subtitle }) => {
            const alerts = data.alerts.filter((a) => a.severity === severity);
            if (alerts.length === 0) return null;
            return (
              <DecisionAlerts key={severity} alerts={alerts} onAction={onAction}
                             title={`${severity[0].toUpperCase()}${severity.slice(1)}`} subtitle={subtitle} />
            );
          })}
        </div>
      )}
    </PageRoot>
  );
}

export default AlertsPage;
