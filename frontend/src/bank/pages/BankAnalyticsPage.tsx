// Command Center › Analytics (plan §5.4, task C04): the eight-tab breakdown.
// All eight tabs are built, each its own GET /bank/analytics/{tab} call,
// filtered by the same global KpiFilter every bank page shares. Every tab
// leads with a chart and keeps the exact-figure DataTable beneath it; a
// view with nothing to show (an abstained month, an unranked agency) leaves
// that series or row off rather than drawing a fabricated zero.
import { useSearchParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import api from "@/api/axios";
import { errorDetail } from "@/lib/apiError";
import { AnalyticsError, AnalyticsLoading, AnalyticsTabBar, Panel, Tile, type AnalyticsTab } from "../components/analytics";
import { BarLineChart, GroupedBarLineChart, TrendAreaChart } from "../components/charts";
import { DataTable } from "../components/DataTable";
import { ExposureFunnel, HeatGrid, TransitionMatrix } from "../components/portfolioVisuals";
import { FilterBar } from "../components/FilterBar";
import { useKpiFilter } from "../components/useKpiFilter";
import { filterToParams } from "../components/kpiFilter";
import { ExecutiveHeader, PageRoot } from "../components/PageTemplate";
import { BRAND } from "../theme/colors";
import {
  agencyLeaderboardRows, complianceChartRows, concentrationBranchChartRows, concentrationCityChartRows,
  concentrationProductChartRows, costByAgencyChartRows, costMonthlyChartRows, dpdLadderHeatRows, exposureFunnelStages,
  fieldOpsAgentChartRows, fieldOpsBeatAdherenceChartRows, fieldOpsSlaTrend, fieldOpsVisitsChartRows, moneyCr, pct,
  recoveryChartRows, securityCoverStages, transitionMatrixData,
  type AgenciesPanels, type AnalyticsTabResponse, type CompliancePanels, type ConcentrationGeoRow,
  type ConcentrationPanels, type CostPanels, type ExposurePanels, type FieldOpsPanels, type MigrationPanels,
  type RecoveryPanels,
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
  exposure: "exposure", migration: "migration", agencies: "agencies", recovery: "recovery", cost: "cost",
  compliance: "compliance", field_ops: "field_ops", concentration: "concentration",
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
      {apiTab && q.data?.available && active === "recovery" && <RecoveryTab panels={q.data.panels as unknown as RecoveryPanels} />}
      {apiTab && q.data?.available && active === "cost" && <CostTab panels={q.data.panels as unknown as CostPanels} />}
      {apiTab && q.data?.available && active === "compliance" && <ComplianceTab panels={q.data.panels as unknown as CompliancePanels} />}
      {apiTab && q.data?.available && active === "field_ops" && <FieldOpsTab panels={q.data.panels as unknown as FieldOpsPanels} />}
      {apiTab && q.data?.available && active === "concentration" && <ConcentrationTab panels={q.data.panels as unknown as ConcentrationPanels} />}
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
  const leaderboard = agencyLeaderboardRows(panels);
  const unranked = panels.scorecards.length - leaderboard.length;
  return (
    <div className="space-y-6">
      {leaderboard.length > 0 && (
        <Panel title="Leaderboard" hint="Collection efficiency, recovery vs expected alongside">
          <BarLineChart
            data={leaderboard}
            xKey="code"
            bar={{ key: "collection_efficiency_pct", name: "Collection eff. (%)", color: BRAND.primary }}
            line={{ key: "recovery_vs_expected_pct", name: "Recovery vs expected (%)", color: BRAND.ink }}
          />
          {unranked > 0 && (
            <p className="mt-2 text-[11px] text-muted-foreground">
              {unranked} agency(ies) with no collection-efficiency reading yet are left off this chart — see the table below.
            </p>
          )}
        </Panel>
      )}
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
    </div>
  );
}

function RecoveryTab({ panels }: { panels: RecoveryPanels }) {
  const rows = recoveryChartRows(panels);
  return (
    <div className="space-y-6">
      {rows.length > 0 && (
        <Panel title="Recovery vs expected, by month" hint="₹ Cr — actual against the recovery_risk-predicted figure">
          <TrendAreaChart
            data={rows}
            xKey="month"
            area={{ key: "actual_cr", name: "Actual (₹ Cr)", color: BRAND.primary }}
            lines={[{ key: "expected_cr", name: "Expected, predicted (₹ Cr)", color: BRAND.muted }]}
          />
        </Panel>
      )}
      <Panel title="Month by month" hint="There is no bank-set target in the schema; expected is the recovery_risk-predicted figure, the one that exists without inventing one.">
        <DataTable
          rows={panels.by_month}
          rowKey={(r) => r.month_start}
          columns={[
            { key: "month_start", header: "Month" },
            { key: "actual_inr", header: "Actual", align: "right", render: (r) => moneyCr(r.actual_inr) },
            { key: "expected_inr", header: "Expected (predicted)", align: "right", render: (r) => moneyCr(r.expected_inr) },
            { key: "recovery_vs_expected", header: "Recovery vs expected", align: "right", render: (r) => pct(r.recovery_vs_expected) },
          ]}
        />
      </Panel>
    </div>
  );
}

const COST_COLUMNS = [
  { key: "commission_inr", header: "Commission", align: "right" as const, render: (r: { commission_inr: number }) => moneyCr(r.commission_inr) },
  { key: "field_cost_inr", header: "Field cost", align: "right" as const,
    render: (r: { field_cost_inr: number | null }) => r.field_cost_inr == null ? "Not available" : moneyCr(r.field_cost_inr) },
  { key: "collected_inr", header: "Collected", align: "right" as const, render: (r: { collected_inr: number }) => moneyCr(r.collected_inr) },
  { key: "cost_per_100_inr", header: "Cost / ₹100", align: "right" as const,
    render: (r: { cost_per_100_inr: number | null }) => r.cost_per_100_inr == null ? "Not available" : `₹${r.cost_per_100_inr.toFixed(2)}` },
];

function CostTab({ panels }: { panels: CostPanels }) {
  const monthly = costMonthlyChartRows(panels);
  const byAgency = costByAgencyChartRows(panels);
  const unpriced = panels.by_agency.length - byAgency.length;
  return (
    <div className="space-y-6">
      {monthly.length > 0 && (
        <Panel title="Commission and field cost, by month" hint="₹ Cr (left), cost per ₹100 collected (right)">
          <GroupedBarLineChart
            data={monthly}
            xKey="month"
            bars={[
              { key: "commission_cr", name: "Commission (₹ Cr)", color: BRAND.primary },
              { key: "field_cost_cr", name: "Field cost (₹ Cr)", color: BRAND.secondary },
            ]}
            line={{ key: "cost_per_100", name: "Cost / ₹100", color: BRAND.ink }}
            barFormat={(v) => `₹${v.toFixed(1)} Cr`}
            lineFormat={(v) => `₹${v.toFixed(2)}`}
          />
        </Panel>
      )}
      <Panel title="Channel economics, by month" hint="Commission and field cost, bank-wide">
        <DataTable rows={panels.by_month} rowKey={(r) => r.month_start}
                  columns={[{ key: "month_start", header: "Month" }, ...COST_COLUMNS]} />
      </Panel>
      {byAgency.length > 0 && (
        <Panel title="Cost per ₹100 collected, by agency" hint="₹ collected (right axis) riding alongside">
          <BarLineChart
            data={byAgency}
            xKey="agency_name"
            bar={{ key: "cost_per_100", name: "Cost / ₹100", color: BRAND.warning }}
            line={{ key: "collected_cr", name: "Collected (₹ Cr)", color: BRAND.ink }}
          />
          {unpriced > 0 && (
            <p className="mt-2 text-[11px] text-muted-foreground">
              {unpriced} agency(ies) with no cost/₹100 reading yet are left off this chart — see the table below.
            </p>
          )}
        </Panel>
      )}
      <Panel title="Channel economics, by agency">
        <DataTable rows={panels.by_agency} rowKey={(r) => r.agency_id}
                  columns={[{ key: "agency_name", header: "Agency", className: "font-bold text-foreground" }, ...COST_COLUMNS]} />
      </Panel>
    </div>
  );
}

function ComplianceTab({ panels }: { panels: CompliancePanels }) {
  const trend = complianceChartRows(panels);
  return (
    <div className="space-y-6">
      {trend.length > 0 && (
        <Panel title="Breaches over time" hint="Counts, by category">
          <TrendAreaChart
            data={trend}
            xKey="month"
            area={{ key: "out_of_hours", name: "Out of hours", color: BRAND.primary }}
            lines={[
              { key: "geofence", name: "Geofence", color: BRAND.warning },
              { key: "consent_missing", name: "Consent missing", color: BRAND.secondary },
              { key: "fraud_confirmed", name: "Fraud confirmed", color: BRAND.destructive },
            ]}
          />
        </Panel>
      )}
      <Panel title="Month by month">
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
            { key: "agency_name", header: "Agency" },
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

function FieldOpsTab({ panels }: { panels: FieldOpsPanels }) {
  const visits = fieldOpsVisitsChartRows(panels);
  const beat = fieldOpsBeatAdherenceChartRows(panels);
  const byAgent = fieldOpsAgentChartRows(panels);
  const sla = fieldOpsSlaTrend(panels);
  return (
    <div className="space-y-6">
      <p className="text-[11px] text-muted-foreground">
        Two grains here: agent activity below is daily, SLA adherence is monthly per agency — kept as separate
        charts rather than one shared axis.
      </p>
      {visits.length > 0 && (
        <Panel title="Visits & met rate, by day" hint="Visit count (left axis), met rate % (right axis)">
          <BarLineChart
            data={visits} xKey="day"
            bar={{ key: "visits", name: "Visits", color: BRAND.primary }}
            line={{ key: "met_rate_pct", name: "Met rate (%)", color: BRAND.ink }}
          />
        </Panel>
      )}
      {beat.length > 0 && (
        <Panel title="Beat adherence, by day" hint="Share of planned stops actually visited — days with no routed beat are left off, not plotted as zero">
          <TrendAreaChart data={beat} xKey="day" area={{ key: "beat_adherence_pct", name: "Beat adherence (%)", color: BRAND.secondary }} />
        </Panel>
      )}
      {byAgent.length > 0 && (
        <Panel title="Visits by agent" hint="Met rate alongside, summed over the whole window">
          <BarLineChart
            data={byAgent} xKey="agent_name"
            bar={{ key: "visits", name: "Visits", color: BRAND.primary }}
            line={{ key: "met_rate_pct", name: "Met rate (%)", color: BRAND.ink }}
          />
        </Panel>
      )}
      {sla.rows.length > 0 && sla.series.length > 0 && (
        <Panel title="SLA adherence, by agency" hint="Monthly — the same sla_adherence definition the Agencies tab uses">
          <TrendAreaChart data={sla.rows} xKey="month" area={sla.series[0]} lines={sla.series.slice(1)} />
        </Panel>
      )}
      <Panel title="Day by day">
        <DataTable
          rows={panels.by_day}
          rowKey={(r) => r.day}
          columns={[
            { key: "day", header: "Day" },
            { key: "visits", header: "Visits", align: "right" },
            { key: "met_visits", header: "Met", align: "right" },
            { key: "met_rate_pct", header: "Met rate", align: "right", render: (r) => r.met_rate_pct == null ? "Not available" : `${r.met_rate_pct.toFixed(1)}%` },
            { key: "planned_stops", header: "Planned stops", align: "right" },
            { key: "visited_stops", header: "Visited stops", align: "right" },
            { key: "beat_adherence_pct", header: "Beat adherence", align: "right", render: (r) => r.beat_adherence_pct == null ? "Not available" : `${r.beat_adherence_pct.toFixed(1)}%` },
            { key: "planned_km", header: "Planned km", align: "right", render: (r) => r.planned_km.toFixed(1) },
            { key: "actual_km", header: "Actual km", align: "right", render: (r) => r.actual_km == null ? "Not available" : r.actual_km.toFixed(1) },
          ]}
        />
      </Panel>
      <Panel title="By agent">
        <DataTable
          rows={panels.by_agent}
          rowKey={(r) => r.agent_id}
          columns={[
            { key: "agent_name", header: "Agent", className: "font-bold text-foreground" },
            { key: "visits", header: "Visits", align: "right" },
            { key: "met_rate_pct", header: "Met rate", align: "right", render: (r) => r.met_rate_pct == null ? "Not available" : `${r.met_rate_pct.toFixed(1)}%` },
            { key: "beat_adherence_pct", header: "Beat adherence", align: "right", render: (r) => r.beat_adherence_pct == null ? "Not available" : `${r.beat_adherence_pct.toFixed(1)}%` },
          ]}
        />
      </Panel>
      <Panel title="SLA adherence by agency, by month">
        <DataTable
          rows={panels.sla_by_agency}
          rowKey={(r) => `${r.agency_id}-${r.month_start}`}
          columns={[
            { key: "agency_name", header: "Agency" },
            { key: "month_start", header: "Month" },
            { key: "sla_adherence_pct", header: "SLA adherence", align: "right", render: (r) => r.sla_adherence_pct == null ? "Not available" : `${r.sla_adherence_pct.toFixed(1)}%` },
          ]}
        />
      </Panel>
    </div>
  );
}

const GEO_COLUMNS = [
  { key: "name", header: "Name", className: "font-bold text-foreground" as const },
  { key: "exposure", header: "Exposure", align: "right" as const, render: (r: ConcentrationGeoRow) => moneyCr(r.exposure) },
  { key: "npa_exposure", header: "NPA exposure", align: "right" as const, render: (r: ConcentrationGeoRow) => moneyCr(r.npa_exposure) },
  { key: "accounts", header: "Accounts", align: "right" as const },
  { key: "npa_accounts", header: "NPA accounts", align: "right" as const },
];

function ConcentrationTab({ panels }: { panels: ConcentrationPanels }) {
  const cities = concentrationCityChartRows(panels);
  const products = concentrationProductChartRows(panels);
  const branches = concentrationBranchChartRows(panels);
  const ratios = panels.concentration_ratios;
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4">
        <Tile label="Top 5 cities' share of exposure" value={`${ratios.top_5_cities_share_pct.toFixed(1)}%`} />
        <Tile label="Top 5 branches' share of exposure" value={`${ratios.top_5_branches_share_pct.toFixed(1)}%`} />
      </div>
      {cities.length > 0 && (
        <Panel title="Top cities by exposure" hint="₹ Cr exposure (left axis), NPA exposure (right axis)">
          <BarLineChart
            data={cities} xKey="name"
            bar={{ key: "exposure_cr", name: "Exposure (₹ Cr)", color: BRAND.primary }}
            line={{ key: "npa_exposure_cr", name: "NPA exposure (₹ Cr)", color: BRAND.destructive }}
          />
        </Panel>
      )}
      {products.length > 0 && (
        <Panel title="By product" hint="₹ Cr exposure (left axis), NPA exposure (right axis)">
          <BarLineChart
            data={products} xKey="name"
            bar={{ key: "exposure_cr", name: "Exposure (₹ Cr)", color: BRAND.secondary }}
            line={{ key: "npa_exposure_cr", name: "NPA exposure (₹ Cr)", color: BRAND.destructive }}
          />
        </Panel>
      )}
      {branches.length > 0 && (
        <Panel title="Top branches by exposure" hint="No NPA split yet — mv_portfolio_daily has no branch column; loan count rides the right axis instead">
          <BarLineChart
            data={branches} xKey="name"
            bar={{ key: "exposure_cr", name: "Exposure (₹ Cr)", color: BRAND.warning }}
            line={{ key: "loan_count", name: "Loans", color: BRAND.ink }}
          />
        </Panel>
      )}
      <Panel title="By city" hint={`Top ${cities.length} of ${panels.by_city.length} charted above`}>
        <DataTable rows={panels.by_city} rowKey={(r) => r.id} columns={GEO_COLUMNS} />
      </Panel>
      <Panel title="By state">
        <DataTable rows={panels.by_state} rowKey={(r) => r.id} columns={GEO_COLUMNS} />
      </Panel>
      <Panel title="By region">
        <DataTable rows={panels.by_region} rowKey={(r) => r.id} columns={GEO_COLUMNS} />
      </Panel>
      <Panel title="By zone">
        <DataTable rows={panels.by_zone} rowKey={(r) => r.id} columns={GEO_COLUMNS} />
      </Panel>
      <Panel title="By product">
        <DataTable
          rows={panels.by_product}
          rowKey={(r) => r.product}
          columns={[
            { key: "product", header: "Product", className: "font-bold text-foreground" },
            { key: "exposure", header: "Exposure", align: "right", render: (r) => moneyCr(r.exposure) },
            { key: "npa_exposure", header: "NPA exposure", align: "right", render: (r) => moneyCr(r.npa_exposure) },
            { key: "accounts", header: "Accounts", align: "right" },
            { key: "npa_accounts", header: "NPA accounts", align: "right" },
          ]}
        />
      </Panel>
      <Panel title="By branch" hint="Exposure only — no NPA split (see the chart note above)">
        <DataTable
          rows={panels.by_branch}
          rowKey={(r) => r.branch_code}
          columns={[
            { key: "branch_name", header: "Branch", className: "font-bold text-foreground" },
            { key: "exposure", header: "Exposure", align: "right", render: (r) => moneyCr(r.exposure) },
            { key: "loan_count", header: "Loans", align: "right" },
          ]}
        />
      </Panel>
    </div>
  );
}
