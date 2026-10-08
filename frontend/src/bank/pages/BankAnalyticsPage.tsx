// Command Center › Analytics (plan §5.4, task C04): the eight-tab breakdown.
// Six tabs are built — Exposure, Migration, Agencies, Recovery, Cost to
// Collect, Compliance — each its own GET /bank/analytics/{tab} call,
// filtered by the same global KpiFilter every bank page shares. The other
// two (Field Operations, Concentration) say so honestly rather than
// rendering sample data; Field Operations is partway (tracked in the tab's
// own note, not silently dropped from the tab bar).
import { useSearchParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import api from "@/api/axios";
import { errorDetail } from "@/lib/apiError";
import { AnalyticsError, AnalyticsLoading, AnalyticsTabBar, Panel, Tile, type AnalyticsTab } from "../components/analytics";
import { DataTable } from "../components/DataTable";
import { GroupedBarLineChart } from "../components/charts";
import { BRAND } from "../theme/colors";
import { ExposureFunnel, HeatGrid, TransitionMatrix } from "../components/portfolioVisuals";
import { FilterBar } from "../components/FilterBar";
import { useKpiFilter } from "../components/useKpiFilter";
import { filterToParams } from "../components/kpiFilter";
import { ExecutiveHeader, PageRoot } from "../components/PageTemplate";
import {
  complianceBreachRows, concentrationRows, CONCENTRATION_CHART_LIMIT, costMonthRows, dpdLadderHeatRows,
  exposureFunnelStages, fieldOpsCoverageRows, fieldOpsMonthRows, migrationTrajectoryRows,
  moneyCr, pct, recoveryMonthRows, securityCoverStages, transitionMatrixData,
  type AgenciesPanels, type AnalyticsTabResponse, type CompliancePanels, type ConcentrationPanels,
  type ConcentrationRow, type CostPanels, type ExposurePanels, type FieldOpsPanels,
  type MigrationPanels, type RecoveryPanels,
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
      <ExecutiveHeader title="Analytics"
                       meta={q.data?.as_of
                         ? ["Eight tabs across the placed book", `Portfolio snapshot ${fmtSnapshot(q.data.as_of)}`]
                         : ["Eight tabs across the placed book"]}
                       live={false}
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

// Owner, 2026-10-07: "only data no actual analytics, charts/graphs/aggregations
// should be there". Migration, Recovery, Cost and Compliance each charted their
// monthly series below, with the numbers kept in a compact table under the
// chart — a chart is how a trend is read, a table is how a figure is quoted,
// and dropping the table would have answered the complaint by losing data.
//
// A MISSING FIGURE IS A GAP, NEVER A ZERO. The row transforms omit the key
// (analyticsModel.ts), so Recharts draws no bar and no point, and the table
// still says "Not available" in words. A zero bar would read as "nothing was
// spent" or "nothing was collected" rather than "this is not recorded".

/** Crores, as the bars carry them. One formatter for the axis ticks and the
 *  tooltip both, which is what GroupedBarLineChart takes. */
// Read the date off the ISO digits, not new Date(iso): a bare date parses as
// UTC midnight and prints the day before in any zone behind UTC (issue 3).
function fmtSnapshot(iso: string): string {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${d} ${months[m - 1]} ${y}`;
}

const crFmt = (v: number) => `₹${v.toFixed(2)} Cr`;
const pctFmt = (v: number) => `${v.toFixed(1)}%`;
const countFmt = (v: number) => String(v);

/** The table under a chart: same numbers, smaller type, no heading of its own. */
function BelowChart({ children }: { children: React.ReactNode }) {
  return <div className="mt-5 border-t border-border/60 pt-4">{children}</div>;
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
      <Panel title="12-month trajectory"
             hint="Accounts cured, held or rolled by month-end, with the cure rate as the line — the counts alone cannot show it, because a month with twice the accounts has twice the cures at the same rate.">
        <GroupedBarLineChart
          data={migrationTrajectoryRows(panels)}
          xKey="month"
          bars={[
            { key: "cured", name: "Cured", color: BRAND.success },
            { key: "held", name: "Held", color: BRAND.slate },
            { key: "rolled", name: "Rolled", color: BRAND.destructive },
          ]}
          line={{ key: "cure_rate_pct", name: "Cure rate", color: BRAND.primary }}
          barFormat={countFmt}
          lineFormat={pctFmt}
        />
        <BelowChart>
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
        </BelowChart>
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

function RecoveryTab({ panels }: { panels: RecoveryPanels }) {
  return (
    <Panel title="Recovery vs expected" hint="Actual collections against the recovery_risk-predicted figure, by month — the owner's target line: there is no bank-set number in the schema, and the predicted figure is the one that exists without inventing one.">
      {/* Both bars are INR and directly comparable, so they share the left
          axis; recovery-vs-expected is a fraction and takes its own. A month
          with no prediction charts no expected bar -- the model abstained, it
          did not expect zero. */}
      <GroupedBarLineChart
        data={recoveryMonthRows(panels)}
        xKey="month"
        bars={[
          { key: "actual_cr", name: "Actual", color: BRAND.primary },
          { key: "expected_cr", name: "Expected (predicted)", color: BRAND.slate },
        ]}
        line={{ key: "vs_expected_pct", name: "Recovery vs expected", color: BRAND.success }}
        barFormat={crFmt}
        lineFormat={pctFmt}
      />
      <BelowChart>
        <DataTable
          rows={panels.by_month}
          rowKey={(r) => r.month_start}
          columns={[
            { key: "month_start", header: "Month" },
            { key: "actual_inr", header: "Actual", align: "right", render: (r) => moneyCr(r.actual_inr) },
            { key: "expected_inr", header: "Expected (predicted)", align: "right",
              render: (r) => r.expected_inr == null ? "Not available" : moneyCr(r.expected_inr) },
            { key: "recovery_vs_expected", header: "Recovery vs expected", align: "right", render: (r) => pct(r.recovery_vs_expected) },
          ]}
        />
      </BelowChart>
    </Panel>
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
  return (
    <div className="space-y-6">
      <Panel title="Channel economics, by month"
             hint="Commission and field cost bank-wide, with cost per ₹100 collected as the line — the ratio is what says whether a bigger spend bought more.">
        <GroupedBarLineChart
          data={costMonthRows(panels)}
          xKey="month"
          bars={[
            { key: "commission_cr", name: "Commission", color: BRAND.primary },
            { key: "field_cost_cr", name: "Field cost", color: BRAND.secondary },
          ]}
          line={{ key: "cost_per_100", name: "Cost / ₹100", color: BRAND.warning }}
          barFormat={crFmt}
          lineFormat={(v) => `₹${v.toFixed(2)}`}
        />
        <BelowChart>
          <DataTable rows={panels.by_month} rowKey={(r) => r.month_start}
                    columns={[{ key: "month_start", header: "Month" }, ...COST_COLUMNS]} />
        </BelowChart>
      </Panel>
      <Panel title="Channel economics, by agency">
        <DataTable rows={panels.by_agency} rowKey={(r) => r.agency_id}
                  columns={[{ key: "agency_name", header: "Agency", className: "font-bold text-foreground" }, ...COST_COLUMNS]} />
      </Panel>
    </div>
  );
}

function ComplianceTab({ panels }: { panels: CompliancePanels }) {
  return (
    <div className="space-y-6">
      <Panel title="Breaches over time"
             hint="The four breach types by month, with their total as the line. The total is the sum of exactly these four, so the line and the bars cannot disagree.">
        <GroupedBarLineChart
          data={complianceBreachRows(panels)}
          xKey="month"
          bars={[
            { key: "out_of_hours", name: "Out of hours", color: BRAND.warning },
            { key: "geofence", name: "Geofence", color: BRAND.secondary },
            { key: "consent_missing", name: "Consent missing", color: BRAND.slate },
            { key: "fraud_confirmed", name: "Fraud confirmed", color: BRAND.destructive },
          ]}
          line={{ key: "total", name: "All breaches", color: BRAND.ink }}
          barFormat={countFmt}
          lineFormat={countFmt}
        />
        <BelowChart>
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
        </BelowChart>
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
  return (
    <div className="space-y-6">
      <Panel title="Effort and reach, by month"
             hint="Visits and the ones where the borrower was met, with the met rate as the line. The gap between the bars is the miss.">
        <GroupedBarLineChart
          data={fieldOpsMonthRows(panels)}
          xKey="month"
          bars={[
            { key: "visits", name: "Visits", color: BRAND.primary },
            { key: "met_visits", name: "Met", color: BRAND.success },
          ]}
          line={{ key: "met_rate_pct", name: "Met rate", color: BRAND.warning }}
          barFormat={countFmt}
          lineFormat={pctFmt}
        />
        <BelowChart>
          <DataTable
            rows={panels.by_month}
            rowKey={(r) => r.month_start}
            columns={[
              { key: "month_start", header: "Month" },
              { key: "visits", header: "Visits", align: "right" },
              { key: "met_rate", header: "Met rate", align: "right", render: (r) => pct(r.met_rate) },
              { key: "contact_rate", header: "Contact rate", align: "right", render: (r) => pct(r.contact_rate) },
              { key: "ptps_set", header: "PTPs", align: "right" },
              { key: "payments_verified_count", header: "Payments", align: "right" },
              { key: "visits_per_agent_day", header: "Visits / agent-day", align: "right",
                render: (r) => r.visits_per_agent_day == null ? "Not available" : r.visits_per_agent_day.toFixed(2) },
            ]}
          />
        </BelowChart>
      </Panel>

      {/* Stop coverage is the one figure here that can be honestly ZERO and
          honestly UNKNOWN in the same month: zero planned stops is a real
          measurement (no routed day), while the coverage ratio over it is 0/0
          and is not charted at all. */}
      <Panel title="Plan against actual, by month"
             hint="Stops planned by the nightly router against stops actually visited. A month with no routed day shows zero planned stops and no coverage line — the ratio is unknown, not 0%.">
        <GroupedBarLineChart
          data={fieldOpsCoverageRows(panels)}
          xKey="month"
          bars={[
            { key: "planned_stops", name: "Planned stops", color: BRAND.slate },
            { key: "visited_stops", name: "Visited stops", color: BRAND.primary },
          ]}
          line={{ key: "stop_coverage_pct", name: "Stop coverage", color: BRAND.success }}
          barFormat={countFmt}
          lineFormat={pctFmt}
        />
        <BelowChart>
          <DataTable
            rows={panels.by_month}
            rowKey={(r) => r.month_start}
            columns={[
              { key: "month_start", header: "Month" },
              { key: "planned_stops", header: "Planned", align: "right" },
              { key: "visited_stops", header: "Visited", align: "right" },
              { key: "stop_coverage", header: "Coverage", align: "right", render: (r) => pct(r.stop_coverage) },
              { key: "planned_km", header: "Planned km", align: "right",
                render: (r) => r.planned_km == null ? "Not available" : r.planned_km.toFixed(1) },
              { key: "agent_days", header: "Agent-days", align: "right" },
            ]}
          />
        </BelowChart>
      </Panel>

      <Panel title="By agency" hint={`${panels.by_agency.length} agencies`}>
        <DataTable
          rows={panels.by_agency}
          rowKey={(r) => r.agency_id}
          columns={[
            { key: "agency_name", header: "Agency", className: "font-bold text-foreground" },
            { key: "agents", header: "Agents", align: "right" },
            { key: "visits", header: "Visits", align: "right" },
            { key: "met_rate", header: "Met rate", align: "right", render: (r) => pct(r.met_rate) },
            { key: "contact_rate", header: "Contact rate", align: "right", render: (r) => pct(r.contact_rate) },
            { key: "visits_per_agent_day", header: "Visits / agent-day", align: "right",
              render: (r) => r.visits_per_agent_day == null ? "Not available" : r.visits_per_agent_day.toFixed(2) },
            { key: "stop_coverage", header: "Stop coverage", align: "right", render: (r) => pct(r.stop_coverage) },
          ]}
        />
      </Panel>
    </div>
  );
}

/** One concentration dimension: the biggest slices charted, every slice in the
 *  table under it. `dim` is both the row key the name arrives under and the
 *  column header. */
function ConcentrationPanel({ title, rows, dim }: {
  title: string;
  rows: ConcentrationRow[];
  dim: "zone" | "state" | "city" | "product";
}) {
  const charted = Math.min(rows.length, CONCENTRATION_CHART_LIMIT);
  const hint = rows.length > charted
    ? `Biggest ${charted} by outstanding, of ${rows.length} — the table below carries them all.`
    : `${rows.length} ${rows.length === 1 ? "slice" : "slices"}, biggest first.`;
  return (
    <Panel title={title} hint={hint}>
      <GroupedBarLineChart
        data={concentrationRows(rows, dim)}
        xKey="name"
        bars={[
          { key: "outstanding_cr", name: "Outstanding", color: BRAND.primary },
          { key: "placed_cr", name: "Placed", color: BRAND.secondary },
          { key: "npa_cr", name: "NPA", color: BRAND.destructive },
        ]}
        line={{ key: "share_pct", name: "Share of book", color: BRAND.ink }}
        barFormat={crFmt}
        lineFormat={pctFmt}
      />
      <BelowChart>
        <DataTable
          rows={rows}
          rowKey={(r) => String(r[dim] ?? "Unassigned")}
          columns={[
            { key: dim, header: title, className: "font-bold text-foreground",
              render: (r) => String(r[dim] ?? "Unassigned") },
            { key: "outstanding", header: "Outstanding", align: "right", render: (r) => moneyCr(r.outstanding) },
            { key: "placed", header: "Placed", align: "right", render: (r) => moneyCr(r.placed) },
            { key: "npa", header: "NPA", align: "right", render: (r) => moneyCr(r.npa) },
            { key: "accounts", header: "Accounts", align: "right", render: (r) => r.accounts ?? 0 },
            { key: "share", header: "Share", align: "right", render: (r) => pct(r.share) },
          ]}
        />
      </BelowChart>
    </Panel>
  );
}

function ConcentrationTab({ panels }: { panels: ConcentrationPanels }) {
  return (
    <div className="space-y-6">
      <ConcentrationPanel title="Zone" rows={panels.by_zone} dim="zone" />
      <ConcentrationPanel title="State" rows={panels.by_state} dim="state" />
      <ConcentrationPanel title="City" rows={panels.by_city} dim="city" />
      <ConcentrationPanel title="Product" rows={panels.by_product} dim="product" />
      {/* Said once, here, rather than left for a reader to notice is missing:
          mv_portfolio_daily carries region_id and agency_id, not branch_id, so
          a branch breakdown would need a column the grain does not have. */}
      <p className="text-[11px] text-muted-foreground">
        No branch breakdown: the portfolio reading carries region and agency, not branch, so a branch
        column would have to be invented rather than measured.
      </p>
    </div>
  );
}
