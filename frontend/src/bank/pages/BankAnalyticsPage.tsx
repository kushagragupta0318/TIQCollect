// Command Center › Analytics (plan §5.4, task C04): the eight-tab breakdown.
// Four tabs are built — Exposure, Migration, Agencies, Compliance — each its
// own GET /bank/analytics/{tab} call, filtered by the same global KpiFilter
// every bank page shares. The other four (Recovery, Field Operations, Cost
// to Collect, Concentration) say so honestly rather than rendering sample
// data; Field Operations and Recovery are partway (tracked in the tab's own
// note, not silently dropped from the tab bar).
import { useSearchParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import api from "@/api/axios";
import { errorDetail } from "@/lib/apiError";
import { AnalyticsError, AnalyticsLoading, AnalyticsTabBar, Panel, Tile, type AnalyticsTab } from "../components/analytics";
import { DataTable } from "../components/DataTable";
import { ExposureFunnel, HeatGrid, TransitionMatrix } from "../components/portfolioVisuals";
import { FilterBar } from "../components/FilterBar";
import { useKpiFilter } from "../components/useKpiFilter";
import { filterToParams } from "../components/kpiFilter";
import { ExecutiveHeader, PageRoot } from "../components/PageTemplate";
import {
  dpdLadderHeatRows, exposureFunnelStages, pct, securityCoverStages, transitionMatrixData,
  type AgenciesPanels, type AnalyticsTabResponse, type CompliancePanels, type ExposurePanels, type MigrationPanels,
} from "./analyticsModel";

type TabId = "exposure" | "migration" | "recovery" | "field_ops" | "agencies" | "cost" | "concentration" | "compliance";

const TABS: readonly AnalyticsTab<TabId>[] = [
  { id: "exposure", label: "Exposure" },
  { id: "migration", label: "Migration" },
  { id: "recovery", label: "Recovery" },
  { id: "field_ops", label: "Field Operations" },
  { id: "agencies", label: "Agencies" },
  { id: "cost", label: "Cost to Collect" },
  { id: "concentration", label: "Concentration" },
  { id: "compliance", label: "Compliance" },
];

// The backend's own tab ids (analytics_catalog.TABS) for the four that call
// the API; the rest render their own honest "not built" note below.
const API_TAB: Partial<Record<TabId, string>> = {
  exposure: "exposure", migration: "migration", agencies: "agencies", compliance: "compliance",
};

const NOT_BUILT: Partial<Record<TabId, string>> = {
  recovery: "Recovery vs Expected (recovery_risk-predicted) is next; the cumulative-target-line chart waits on " +
           "a bank-set collection target, which does not exist in the schema yet — a product decision, not a data gap.",
  field_ops: "Visits per agent, met rate and SLA coverage are built below the other three tabs land; beat " +
            "adherence and planned-vs-actual km wait on the demo book generating routed days, queued separately.",
  cost: "Commission and field cost are already in the Agencies scorecard (Cost per ₹100); a dedicated channel-" +
       "economics breakdown is not built yet.",
  concentration: "Zone / region / state / city / branch / product breakdowns need a new aggregation over " +
                 "mv_portfolio_daily joined to branches — not built yet.",
};

export function BankAnalyticsPage() {
  const [params, setParams] = useSearchParams();
  const [filter] = useKpiFilter();
  const active = ((params.get("tab") as TabId | null) && TABS.some((t) => t.id === params.get("tab"))
    ? (params.get("tab") as TabId) : "exposure");
  const onChange = (id: TabId) => setParams((p) => { const n = new URLSearchParams(p); n.set("tab", id); return n; }, { replace: true });

  const apiTab = API_TAB[active];
  const filterParams = filterToParams(filter).toString();
  const q = useQuery({
    queryKey: ["bank", "analytics", apiTab, filterParams],
    queryFn: async () => (await api.get<AnalyticsTabResponse>(
      `/bank/analytics/${apiTab}${filterParams ? `?${filterParams}` : ""}`)).data,
    enabled: !!apiTab,
  });

  return (
    <PageRoot>
      <ExecutiveHeader title="Analytics" meta={["Eight tabs across the placed book"]} live={false}
                       scopeNote="The whole book across every agency unless filtered." />
      <FilterBar />
      <AnalyticsTabBar tabs={TABS} active={active} onChange={onChange} />

      {!apiTab && (
        <Panel title={TABS.find((t) => t.id === active)?.label ?? ""}>
          <p className="text-[12px] text-muted-foreground">{NOT_BUILT[active]}</p>
        </Panel>
      )}

      {apiTab && q.isLoading && <AnalyticsLoading />}
      {apiTab && (q.isError || (!q.isLoading && !q.data)) && (
        <AnalyticsError>{errorDetail(q.error, "This tab could not be loaded.")}</AnalyticsError>
      )}
      {apiTab && q.data && !q.data.available && (
        <Panel title={TABS.find((t) => t.id === active)?.label ?? ""}>
          <p className="text-[12px] text-muted-foreground">{q.data.reason ?? "Not available."}</p>
        </Panel>
      )}
      {apiTab && q.data?.available && active === "exposure" && <ExposureTab panels={q.data.panels as unknown as ExposurePanels} />}
      {apiTab && q.data?.available && active === "migration" && <MigrationTab panels={q.data.panels as unknown as MigrationPanels} />}
      {apiTab && q.data?.available && active === "agencies" && <AgenciesTab panels={q.data.panels as unknown as AgenciesPanels} />}
      {apiTab && q.data?.available && active === "compliance" && <ComplianceTab panels={q.data.panels as unknown as CompliancePanels} />}
    </PageRoot>
  );
}

function ExposureTab({ panels }: { panels: ExposurePanels }) {
  return (
    <div className="space-y-6">
      <Panel title="Funnel" hint="Book → delinquent → placed → NPA">
        <ExposureFunnel stages={exposureFunnelStages(panels)} />
      </Panel>
      <Panel title="Product × DPD bucket">
        <HeatGrid rows={dpdLadderHeatRows(panels)} buckets={["Current", "0-30", "31-60", "61-90", "NPA"]} rowHeader="Product" />
      </Panel>
      <Panel title="Security cover">
        <div className="grid grid-cols-2 gap-4">
          {securityCoverStages(panels).map((s) => (
            <Tile key={s.security} label={s.security} value={`₹${s.exposureCr.toFixed(2)} Cr`} />
          ))}
        </div>
      </Panel>
    </div>
  );
}

function MigrationTab({ panels }: { panels: MigrationPanels }) {
  const { labels, matrix, observed } = transitionMatrixData(panels);
  return (
    <div className="space-y-6">
      <Panel title="Transition matrix" hint="One month, DPD state to DPD state">
        <TransitionMatrix labels={labels} matrix={matrix} observed={observed} />
      </Panel>
      <Panel title="12-month trajectory" hint="Accounts cured, held or rolled, by month-end">
        <DataTable
          rows={panels.trajectory_12m}
          rowKey={(r) => r.month_end}
          columns={[
            { key: "month_end", header: "Month" },
            { key: "cured", header: "Cured", align: "right" },
            { key: "held", header: "Held", align: "right" },
            { key: "rolled", header: "Rolled", align: "right" },
          ]}
        />
      </Panel>
    </div>
  );
}

function AgenciesTab({ panels }: { panels: AgenciesPanels }) {
  return (
    <Panel title="Agency scorecards" hint={`${panels.scorecards.length} agencies`}>
      <DataTable
        rows={panels.scorecards}
        rowKey={(r) => r.agency_id}
        columns={[
          { key: "name", header: "Agency", className: "font-bold text-foreground" },
          { key: "status", header: "Status" },
          { key: "collection_efficiency", header: "Collection eff.", align: "right", render: (r) => pct(r.collection_efficiency) },
          { key: "resolution_rate", header: "Resolution", align: "right", render: (r) => pct(r.resolution_rate) },
          { key: "recovery_vs_expected", header: "Recovery vs expected", align: "right", render: (r) => pct(r.recovery_vs_expected) },
          { key: "ptp_conversion", header: "PTP conversion", align: "right", render: (r) => pct(r.ptp_conversion) },
          { key: "contact_rate", header: "Contact rate", align: "right", render: (r) => pct(r.contact_rate) },
          { key: "sla_adherence", header: "SLA adherence", align: "right", render: (r) => pct(r.sla_adherence) },
          { key: "cost_per_100_inr", header: "Cost / ₹100", align: "right", render: (r) => r.cost_per_100_inr == null ? "Not available" : `₹${r.cost_per_100_inr.toFixed(2)}` },
          { key: "compliance_score", header: "Compliance", align: "right", render: (r) => r.compliance_score == null ? "Not available" : r.compliance_score.toFixed(1) },
        ]}
      />
    </Panel>
  );
}

function ComplianceTab({ panels }: { panels: CompliancePanels }) {
  return (
    <div className="space-y-6">
      <Panel title="Breaches over time">
        <DataTable
          rows={panels.breaches_over_time}
          rowKey={(r) => r.month_start}
          columns={[
            { key: "month_start", header: "Month" },
            { key: "out_of_hours", header: "Out of hours", align: "right" },
            { key: "geofence", header: "Geofence", align: "right" },
            { key: "consent_missing", header: "Consent missing", align: "right" },
            { key: "fraud_confirmed", header: "Fraud confirmed", align: "right" },
          ]}
        />
      </Panel>
      <Panel title="By agency">
        <DataTable
          rows={panels.by_agency}
          rowKey={(r) => r.agency_id}
          columns={[
            { key: "agency_id", header: "Agency" },
            { key: "visits", header: "Visits", align: "right" },
            { key: "out_of_hours", header: "Out of hours", align: "right" },
            { key: "geofence", header: "Geofence", align: "right" },
            { key: "fraud_confirmed", header: "Fraud confirmed", align: "right" },
          ]}
        />
      </Panel>
    </div>
  );
}
