// /bank/_gallery — every ported token, primitive and composite on one page,
// with realistic sample data, so parity with Command Center can be
// screenshotted (task UI06) and so the next screen is assembled from parts
// that are already known to look right. Sample data: ./gallerySample.ts.
import { useRef, useState, type ReactNode } from "react";
import { Download, FlaskConical, Mail, Printer, Search, SlidersHorizontal } from "lucide-react";
import { AnalyticsSectionHeader, AnalyticsTabBar, Bar100, BucketChip, DrillRow, Headline, Panel, ShareBar, Tile } from "../components/analytics";
import { BarLineChart, RecoveryPaceChart, TrendAreaChart } from "../components/charts";
import { DataTable, type DataColumn } from "../components/DataTable";
import { DecisionAlerts } from "../components/DecisionAlerts";
import { DrillPanel, type DrillData } from "../components/DrillPanel";
import { ExecutiveHeader, PageRoot, ToolHeader, ToolHeaderAction } from "../components/PageTemplate";
import { CureRollBars, ExposureFunnel, HeatGrid, TransitionMatrix } from "../components/portfolioVisuals";
import { PulseKpiFlow } from "../components/PulseKpiFlow";
import { WorkspaceModal } from "../components/WorkspaceModal";
import { BRAND, CHART_SERIES, DPD_COLORS, RISK_COLORS } from "../theme/colors";
import { gradeColor } from "../theme/chartTheme";
import { cr, cr1, n, rs } from "../theme/format";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "../ui/card";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../ui/dialog";
import { Input } from "../ui/input";
import { Label } from "../ui/label";
import { Select } from "../ui/select";
import { Separator } from "../ui/separator";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../ui/table";
import { Textarea } from "../ui/textarea";
import {
  AGENCIES,
  ALERTS,
  ANALYTICS_TABS,
  AS_OF,
  BUDGET_ROWS,
  COST_BY_BUCKET,
  CURE_ROLL,
  DELINQUENT_TREND,
  DPD_LADDER,
  drillFor,
  FUNNEL,
  GRID_BUCKETS,
  HEADLINES,
  HEAT_GRID,
  KPI_NARRATIVE,
  KPI_ROWS,
  KPIS,
  MATRIX_LABELS,
  MATRIX_OBSERVED,
  RECOVERY_CURVE,
  RECOVERY_SUMMARY,
  TAB_TILES,
  TRANSITION_MATRIX,
  type AgencyRow,
  type LadderRow,
  type TabId,
  type TileSample,
} from "./gallerySample";

/* ── Gallery chrome ───────────────────────────────────────────────────── */

function GallerySection({ title, note, children }: { title: string; note?: ReactNode; children: ReactNode }) {
  return (
    <section className="space-y-4 pt-4">
      <div className="px-1">
        <h2 className="text-[11px] font-bold uppercase tracking-widest text-muted-foreground">{title}</h2>
        {note && <p className="text-[11px] text-muted-foreground mt-1">{note}</p>}
      </div>
      {children}
    </section>
  );
}

const TILE_TONE: Record<NonNullable<TileSample["tone"]>, string> = {
  success: BRAND.success,
  warning: BRAND.warning,
  destructive: BRAND.destructive,
  primary: BRAND.primary,
};

function TileRow({ tiles }: { tiles: TileSample[] }) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
      {tiles.map((t) => (
        <Tile key={t.label} label={t.label} value={t.value} sub={t.sub} color={t.tone ? TILE_TONE[t.tone] : undefined} />
      ))}
    </div>
  );
}

function Swatch({ name, hex }: { name: string; hex: string }) {
  return (
    <div className="space-y-1.5">
      <div className="h-10 rounded-inner border border-border/60" style={{ background: hex }} />
      <p className="text-[11px] font-semibold text-foreground truncate">{name}</p>
      <p className="text-[10px] text-muted-foreground">{hex}</p>
    </div>
  );
}

// DPD_COLORS and RISK_COLORS alias several names to one hex ("0-30" / "1-30");
// show each colour once, under its first name.
const uniqueColors = (entries: [string, string][]) => {
  const seen = new Set<string>();
  return entries.filter(([, hex]) => {
    if (seen.has(hex)) return false;
    seen.add(hex);
    return true;
  });
};

/* ── Analytics tables ─────────────────────────────────────────────────── */

const LADDER_COLUMNS: DataColumn<LadderRow>[] = [
  {
    key: "bucket",
    header: "Bucket",
    render: (b) => (
      <>
        <BucketChip bucket={b.bucket} />
        {b.isNpa && <span className="ml-1.5 text-[8.5px] font-bold text-destructive uppercase tracking-wider">NPA</span>}
      </>
    ),
  },
  { key: "accounts", header: "Accounts", className: "font-bold text-foreground", render: (b) => n(b.accounts) },
  { key: "exposure", header: "Exposure", className: "font-bold text-foreground", render: (b) => cr(b.exposureCr) },
  { key: "share", header: "Share of at-risk", className: "w-32", render: (b) => <ShareBar pct={b.sharePct} color={DPD_COLORS[b.bucket]} /> },
  { key: "collected", header: "Collected", className: "font-semibold", color: () => BRAND.success, render: (b) => cr(b.collectedCr) },
  { key: "efficiency", header: "Efficiency", className: "font-bold", color: (b) => gradeColor(b.efficiencyPct), render: (b) => `${b.efficiencyPct}%` },
  { key: "cure", header: "Cure", className: "font-semibold", color: () => BRAND.success, render: (b) => `${b.curePct}%` },
  { key: "roll", header: "Roll", className: "font-semibold", color: () => BRAND.destructive, render: (b) => `${b.rollPct}%` },
];

const AGENCY_COLUMNS: DataColumn<AgencyRow>[] = [
  { key: "agency", header: "Agency", className: "font-bold text-foreground", render: (a) => a.agency },
  { key: "region", header: "Region", className: "text-muted-foreground font-medium", render: (a) => a.region },
  { key: "cases", header: "Placed cases", className: "text-muted-foreground font-medium", render: (a) => n(a.placedCases) },
  { key: "placed", header: "Placed", className: "font-bold text-foreground", render: (a) => cr(a.placedCr) },
  { key: "resolved", header: "Resolved", className: "w-28", render: (a) => <ShareBar pct={a.resolvedPct} color={BRAND.primary} scale={3} /> },
  { key: "efficiency", header: "Efficiency", className: "font-bold", color: (a) => gradeColor(a.efficiencyPct), render: (a) => `${a.efficiencyPct}%` },
  { key: "cost", header: "Cost per ₹100", className: "font-bold", color: (a) => costColor(a.costPer100), render: (a) => `₹${a.costPer100}` },
];

// Lower is better, so gradeColor's thresholds run the other way here.
function costColor(per100: number): string {
  return per100 <= 3.5 ? BRAND.success : per100 <= 6 ? BRAND.warning : BRAND.destructive;
}

type BudgetRow = (typeof BUDGET_ROWS)[number];

const BUDGET_COLUMNS: DataColumn<BudgetRow>[] = [
  { key: "agency", header: "Agency", className: "font-bold text-foreground", render: (r) => r.agency },
  { key: "current", header: "Current", className: "text-muted-foreground font-medium", render: (r) => `₹${r.current} L` },
  { key: "proposed", header: "Proposed", className: "font-bold text-foreground", render: (r) => `₹${r.proposed} L` },
  {
    key: "lift",
    header: "Expected lift",
    className: "font-bold",
    color: (r) => (r.liftCr >= 0 ? BRAND.success : BRAND.destructive),
    render: (r) => `${r.liftCr >= 0 ? "+" : "−"}₹${Math.abs(r.liftCr)} Cr`,
  },
];

// A transition cell's slice: the "from" bucket's book × the cell's probability.
function transitionSlice(from: string, pct: number): { exposureCr: number; accounts: number; sharePct: number } {
  const row = DPD_LADDER.find((b) => b.bucket === from);
  const baseCr = row ? row.exposureCr : FUNNEL[0].exposureCr - FUNNEL[1].exposureCr;
  const baseAccounts = row ? row.accounts : FUNNEL[0].accounts - FUNNEL[1].accounts;
  const exposureCr = Math.round(baseCr * pct) / 100;
  return {
    exposureCr,
    accounts: Math.round((baseAccounts * pct) / 100),
    sharePct: Math.round((exposureCr / FUNNEL[1].exposureCr) * 1000) / 10,
  };
}

/* ── The page ─────────────────────────────────────────────────────────── */

type DrillState = { key: string; data: DrillData | null };

const TYPE_SCALE: { role: string; className: string; sample: string }[] = [
  { role: "Page title", className: "text-2xl font-bold tracking-tight", sample: "Portfolio Overview" },
  { role: "Tool page title", className: "text-xl font-bold", sample: "Monte Carlo Simulator" },
  { role: "Section heading", className: "text-[17px] font-bold tracking-tight", sample: "Portfolio Analytics" },
  { role: "Drawer / modal title", className: "text-lg font-extrabold", sample: "90-180 DPD" },
  { role: "Card title", className: "text-[19px] font-semibold leading-[1.35] tracking-tight", sample: "Placement capacity" },
  { role: "Panel title", className: "text-[12.5px] font-semibold tracking-tight", sample: "DPD ladder — the collections book, bucket by bucket" },
  { role: "Eyebrow", className: "text-[11px] font-bold uppercase tracking-widest text-muted-foreground", sample: "Portfolio Health" },
  { role: "KPI value", className: "text-[23px] font-bold leading-none tracking-tight tabular-nums", sample: "₹612.4 Cr" },
  { role: "KPI label", className: "text-[11.5px] font-medium text-muted-foreground", sample: "Delinquent Exposure" },
  { role: "Body / narrative", className: "text-[13px] font-normal leading-relaxed text-muted-foreground", sample: "Agencies worked more of the book this month." },
  { role: "Table header", className: "uppercase text-[9.5px] tracking-wide font-semibold text-muted-foreground", sample: "Share of at-risk" },
  { role: "Table cell", className: "text-[11px] text-foreground", sample: "29,840" },
];

const BUTTON_VARIANTS = ["default", "outline", "secondary", "destructive", "success", "warning", "ghost", "link"] as const;
const BADGE_VARIANTS = ["default", "secondary", "destructive", "outline", "success", "warning", "softPrimary", "softSuccess", "softDanger", "softWarning"] as const;

export default function BankComponentGalleryPage() {
  const [tab, setTab] = useState<TabId>("exposure");
  const [drill, setDrill] = useState<DrillState | null>(null);
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [budgetRuns, setBudgetRuns] = useState(0);
  const analyticsRef = useRef<HTMLDivElement>(null);

  // Opens the drawer in its loading state first, as a real drill would.
  const openDrill = (key: string, exposureCr: number, accounts: number, sharePct: number) => {
    setDrill({ key, data: null });
    window.setTimeout(() => {
      setDrill((d) => (d && d.key === key ? { key, data: drillFor(key, exposureCr, accounts, sharePct) } : d));
    }, 450);
  };

  // KPI click → its analytics tab, then scroll there (PortfolioOverview.jsx:36-41).
  const jumpToTab = (drillId: string) => {
    const target = ANALYTICS_TABS.find((t) => t.id === drillId);
    if (target) setTab(target.id);
    analyticsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <PageRoot>
      <ExecutiveHeader
        title="Component Gallery"
        meta={["Illustrative sample book", `As of ${AS_OF}`, "Tasks UI02–UI05"]}
        scopeNote="Every Command Center token, primitive and composite ported to the bank portal, on sample data. Parity screenshots are taken from this page."
      />

      {/* ── KPI flow ───────────────────────────────────────────────────── */}
      <PulseKpiFlow kpis={KPIS} rows={KPI_ROWS} frameLabel={`30 days to ${AS_OF}`} narrative={KPI_NARRATIVE} onSelect={jumpToTab} />

      {/* ── Analytics ──────────────────────────────────────────────────── */}
      <div id="portfolio-analytics" ref={analyticsRef} className="scroll-mt-4">
        <section className="space-y-6">
          <AnalyticsSectionHeader
            title="Portfolio Analytics"
            subtitle="The header numbers taken apart. Rows and cells are clickable — every slice opens the accounts behind it."
          />
          <AnalyticsTabBar tabs={ANALYTICS_TABS} active={tab} onChange={setTab} />

          <div className="space-y-5">
            <Headline text={HEADLINES[tab]} />

            {tab === "exposure" && (
              <>
                <Panel title="From the whole book down to the write-off tail" hint="each stage is a subset of the one above">
                  <ExposureFunnel stages={FUNNEL} />
                </Panel>
                <Panel title="DPD ladder — the collections book, bucket by bucket">
                  <DataTable
                    columns={LADDER_COLUMNS}
                    rows={DPD_LADDER}
                    rowKey={(b) => b.bucket}
                    onRowClick={(b) => openDrill(`${b.bucket} DPD`, b.exposureCr, b.accounts, b.sharePct)}
                  />
                </Panel>
                <Panel title="Product × bucket exposure">
                  <HeatGrid
                    rows={HEAT_GRID}
                    buckets={GRID_BUCKETS}
                    onDrill={(row) =>
                      openDrill(
                        row.label,
                        row.delqExposureCr,
                        Object.values(row.cells).reduce((sum, c) => sum + c.accounts, 0),
                        Math.round((row.delqExposureCr / FUNNEL[1].exposureCr) * 1000) / 10,
                      )
                    }
                  />
                </Panel>
              </>
            )}

            {tab === "migration" && (
              <>
                <div className="grid grid-cols-1 lg:grid-cols-5 gap-5">
                  <Panel title="Month-over-month transition matrix" className="lg:col-span-3">
                    <TransitionMatrix
                      labels={MATRIX_LABELS}
                      matrix={TRANSITION_MATRIX}
                      observed={MATRIX_OBSERVED}
                      onDrill={(from, to) => {
                        const pct = TRANSITION_MATRIX[MATRIX_LABELS.indexOf(from)][MATRIX_LABELS.indexOf(to)];
                        const s = transitionSlice(from, pct);
                        openDrill(`${from} → ${to}`, s.exposureCr, s.accounts, s.sharePct);
                      }}
                    />
                  </Panel>
                  <Panel title="Cure against roll, by bucket" hint="exposure at stake" className="lg:col-span-2">
                    <CureRollBars flows={CURE_ROLL} />
                  </Panel>
                </div>
                <Panel title="Delinquent population — 12-month trajectory" hint="accounts, from the monthly DPD panel">
                  <TrendAreaChart
                    data={DELINQUENT_TREND}
                    xKey="month"
                    area={{ key: "delinquent", name: "All delinquent", color: BRAND.primary }}
                    lines={[
                      { key: "180+", name: "180+ DPD", color: DPD_COLORS["180+"] },
                      { key: "90-180", name: "90-180 DPD", color: DPD_COLORS["90-180"] },
                    ]}
                  />
                </Panel>
              </>
            )}

            {tab === "recovery" && (
              <>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  <Tile label="Target (30d)" value={cr(RECOVERY_SUMMARY.targetCr)} sub="EMI due, delinquent book" />
                  <Tile label="Achieved (30d)" value={cr(RECOVERY_SUMMARY.achievedCr)} sub={`prior period ${cr(RECOVERY_SUMMARY.prevAchievedCr)}`} color={BRAND.success} />
                  <Tile label="Shortfall" value={cr(RECOVERY_SUMMARY.gapCr)} sub="target less achieved" color={BRAND.destructive} />
                  <Tile label="Collections efficiency" value={`${RECOVERY_SUMMARY.efficiencyPct}%`} sub="achieved ÷ target" color={BRAND.primary} />
                </div>
                <div className="grid grid-cols-1 lg:grid-cols-5 gap-5">
                  <Panel title="Recovery pace against target" hint="cumulative, delinquent book" className="lg:col-span-3">
                    <RecoveryPaceChart data={RECOVERY_CURVE} />
                    <p className="text-[9.5px] text-muted-foreground font-medium mt-2">
                      The dashed line is a flat daily pace to the {cr(RECOVERY_SUMMARY.targetCr)} target. The gap between the two curves is the shortfall.
                    </p>
                  </Panel>
                  <Panel title="Target vs achieved by bucket" className="lg:col-span-2">
                    <div className="space-y-3.5">
                      {DPD_LADDER.map((b) => (
                        <DrillRow key={b.bucket} onDrill={() => openDrill(`${b.bucket} DPD`, b.exposureCr, b.accounts, b.sharePct)} className="block p-1.5 -m-1.5">
                          <div className="flex items-baseline justify-between mb-1.5">
                            <span className="text-[11px] font-semibold" style={{ color: DPD_COLORS[b.bucket] }}>{b.bucket}</span>
                            <span className="text-[10px] font-bold text-muted-foreground">
                              {cr(b.collectedCr)} of {cr(b.exposureCr)} ·{" "}
                              <span style={{ color: gradeColor(b.efficiencyPct) }}>{b.efficiencyPct}%</span>
                            </span>
                          </div>
                          <Bar100 pct={b.efficiencyPct} color={gradeColor(b.efficiencyPct)} height={8} />
                        </DrillRow>
                      ))}
                    </div>
                  </Panel>
                </div>
              </>
            )}

            {tab === "cost" && (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
                <Panel title="Cost per account worked, by bucket" hint="effort rises as recovery falls">
                  <BarLineChart
                    data={COST_BY_BUCKET}
                    xKey="bucket"
                    bar={{ key: "costPerAccount", name: "Cost per account (₹)", color: BRAND.primary }}
                    line={{ key: "costPer100", name: "Cost per ₹100 recovered", color: BRAND.ink }}
                    barColor={(row) => DPD_COLORS[String(row.bucket)] ?? BRAND.slate}
                  />
                </Panel>
                <Panel title="Coverage — who actually got worked">
                  <div className="space-y-3.5">
                    {DPD_LADDER.map((b, i) => {
                      const coverage = [72.4, 61.8, 48.2, 36.5, 22.9][i];
                      return (
                        <DrillRow key={b.bucket} onDrill={() => openDrill(`${b.bucket} DPD`, b.exposureCr, b.accounts, b.sharePct)} className="block p-1.5 -m-1.5">
                          <div className="flex items-baseline justify-between mb-1.5">
                            <span className="text-[11px] font-semibold" style={{ color: DPD_COLORS[b.bucket] }}>{b.bucket}</span>
                            <span className="text-[10px] font-bold text-muted-foreground">{n(Math.round((b.accounts * coverage) / 100))} of {n(b.accounts)} worked</span>
                          </div>
                          <Bar100 pct={coverage} color={gradeColor(coverage, { good: 60, fair: 35 })} height={8} />
                          <p className="text-[9.5px] font-semibold text-muted-foreground mt-1">{coverage}% coverage · {cr(b.collectedCr)} recovered</p>
                        </DrillRow>
                      );
                    })}
                  </div>
                </Panel>
              </div>
            )}

            {tab === "agencies" && (
              <Panel title="Agency scorecards" hint="placed book, this month">
                <DataTable
                  columns={AGENCY_COLUMNS}
                  rows={AGENCIES}
                  rowKey={(a) => a.agency}
                  density="compact"
                  onRowClick={(a) => openDrill(a.agency, a.placedCr, a.placedCases, Math.round((a.placedCr / FUNNEL[2].exposureCr) * 1000) / 10)}
                />
              </Panel>
            )}

            {(tab === "field" || tab === "concentration" || tab === "compliance") && <TileRow tiles={TAB_TILES[tab]} />}
          </div>
        </section>
      </div>

      {/* ── Alerts: every severity; the first opens with its detail ─────── */}
      <div className="pt-4">
        <DecisionAlerts alerts={ALERTS} defaultOpenId="untouched_placed" subtitle="Derived live from the placed book — the same population and period as the Overview" />
      </div>

      {/* ── Overlays ───────────────────────────────────────────────────── */}
      <GallerySection title="Overlays" note="The drill drawer and workspace portal into a .bank-root container, so they keep the theme.">
        <div className="flex flex-wrap gap-3">
          <Button onClick={() => openDrill("90-180 DPD", 97.4, 12910, 15.9)}>Open drill-down (90-180 DPD)</Button>
          <Button variant="outline" onClick={() => setWorkspaceOpen(true)}>
            <SlidersHorizontal /> Open workspace (Budget Optimizer)
          </Button>
          <Button variant="outline" onClick={() => setDialogOpen(true)}>Open dialog</Button>
        </div>
      </GallerySection>

      {/* ── Page headers ───────────────────────────────────────────────── */}
      <GallerySection title="Page header — tool variant" note="Variant A (executive) heads this page.">
        <ToolHeader
          title="Monte Carlo Simulator"
          icon={FlaskConical}
          description="Ten thousand paths of the placed book through next quarter's roll rates, with IFRS-9 staging and a sensitivity grid."
          actions={
            <>
              <ToolHeaderAction icon={Download}>Export CSV</ToolHeaderAction>
              <ToolHeaderAction icon={Printer}>Print</ToolHeaderAction>
            </>
          }
        />
      </GallerySection>

      {/* ── Charts ─────────────────────────────────────────────────────── */}
      <GallerySection title="Charts" note="Recharts on the ported chart theme: dashed horizontal grid, muted ticks, no axis lines, dark tooltip.">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          <Panel title="Recovery pace against target" hint="cumulative, ₹ Cr">
            <RecoveryPaceChart data={RECOVERY_CURVE} height={220} />
          </Panel>
          <Panel title="Cost per account worked, by bucket">
            <BarLineChart
              data={COST_BY_BUCKET}
              xKey="bucket"
              bar={{ key: "costPerAccount", name: "Cost per account (₹)", color: BRAND.primary }}
              line={{ key: "costPer100", name: "Cost per ₹100 recovered", color: BRAND.ink }}
              barColor={(row) => DPD_COLORS[String(row.bucket)] ?? BRAND.slate}
              height={220}
            />
          </Panel>
        </div>
      </GallerySection>

      {/* ── Primitives ─────────────────────────────────────────────────── */}
      <GallerySection title="Buttons" note="Every CC button is outlined on white — there is no filled primary.">
        <Card className="p-6 space-y-4">
          {BUTTON_VARIANTS.map((variant) => (
            <div key={variant} className="flex flex-wrap items-center gap-3">
              <span className="w-24 text-[11px] font-medium text-muted-foreground">{variant}</span>
              <Button variant={variant} size="lg">Place cases</Button>
              <Button variant={variant}>
                <Mail /> Notify agency
              </Button>
              <Button variant={variant} size="sm">Approve</Button>
              <Button variant={variant} size="xs">Recall</Button>
              <Button variant={variant} size="icon" aria-label="Search">
                <Search />
              </Button>
              <Button variant={variant} disabled>
                Disabled
              </Button>
            </div>
          ))}
        </Card>
      </GallerySection>

      <GallerySection title="Badges" note="Ink text on every badge; status reads from the fill alone.">
        <Card className="p-6 flex flex-wrap gap-3">
          {BADGE_VARIANTS.map((variant) => (
            <Badge key={variant} variant={variant}>
              {variant}
            </Badge>
          ))}
        </Card>
      </GallerySection>

      <GallerySection title="Card, form controls and table">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          <Card>
            <CardHeader>
              <CardTitle>Placement capacity</CardTitle>
              <CardDescription>How many more cases each region can take this week.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="gal-agency">Agency</Label>
                <Select id="gal-agency" defaultValue="Ganga Credit Management">
                  {AGENCIES.map((a) => (
                    <option key={a.agency}>{a.agency}</option>
                  ))}
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="gal-cases">Cases to place</Label>
                <Input id="gal-cases" inputMode="numeric" defaultValue="1,250" />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="gal-note">Instruction to the agency</Label>
                <Textarea id="gal-note" defaultValue="Prioritise 31-60 DPD personal loans in Lucknow and Kanpur; visits only between 8 am and 7 pm." />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="gal-disabled">Contract ID (locked)</Label>
                <Input id="gal-disabled" disabled defaultValue="AGR-2026-UP-0114" />
              </div>
            </CardContent>
            <CardFooter className="justify-end gap-3">
              <Button variant="outline">Cancel</Button>
              <Button>Place 1,250 cases</Button>
            </CardFooter>
          </Card>

          <Card className="p-6 space-y-5">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Agency</TableHead>
                  <TableHead>Region</TableHead>
                  <TableHead>Placed</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {AGENCIES.slice(0, 5).map((a) => (
                  <TableRow key={a.agency}>
                    <TableCell>{a.agency}</TableCell>
                    <TableCell>{a.region}</TableCell>
                    <TableCell>{cr1(a.placedCr)}</TableCell>
                    <TableCell>
                      <Badge variant={a.efficiencyPct >= 40 ? "success" : a.efficiencyPct >= 15 ? "warning" : "destructive"}>
                        {a.efficiencyPct >= 40 ? "On track" : a.efficiencyPct >= 15 ? "Watch" : "At risk"}
                      </Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <Separator />
            <p className="text-[11px] text-muted-foreground">
              The shadcn-style Table primitive. Its row padding and hover are overridden by CC's default table rules, as in CC.
            </p>
          </Card>
        </div>
      </GallerySection>

      <GallerySection title="Global classes" note="CC's index.css component classes, as the Soft Card layer renders them.">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
          <div className="panel">
            <p className="panel-title">.panel · .panel-title</p>
            <p className="text-[13px] text-foreground">₹436.6 Cr placed across 14 agencies.</p>
          </div>
          <div className="card-base p-6 space-y-3">
            <div className="flex items-center gap-3">
              <div className="icon-circle bg-primary">
                <SlidersHorizontal size={18} />
              </div>
              <p className="text-[13px] font-semibold text-foreground">.card-base · .icon-circle</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <span className="chip-badge bg-success/10 text-success">Live</span>
              <span className="chip-badge bg-warning/10 text-warning">Simulation</span>
            </div>
          </div>
          <div className="card-base p-6 space-y-3">
            <div className="flex flex-wrap gap-2">
              <button className="btn-primary">.btn-primary</button>
              <button className="btn-secondary">.btn-secondary</button>
            </div>
            <button className="btn-ghost">.btn-ghost</button>
            <div className="flex flex-wrap gap-2">
              <span className="badge-premium-blue">Placed</span>
              <span className="badge-premium-green">Resolved</span>
              <span className="badge-premium-red">Recalled</span>
              <span className="badge-premium-amber">Expiring</span>
            </div>
          </div>
        </div>
      </GallerySection>

      {/* ── Tokens ─────────────────────────────────────────────────────── */}
      <GallerySection title="Colour tokens" note="BRAND, DPD buckets, chart series and risk tiers — theme/colors.ts, verbatim from CC.">
        <Card className="p-6 space-y-6">
          <div className="grid grid-cols-3 sm:grid-cols-5 xl:grid-cols-10 gap-4">
            {Object.entries(BRAND).map(([name, hex]) => (
              <Swatch key={name} name={name} hex={hex} />
            ))}
          </div>
          <Separator />
          <div className="grid grid-cols-3 sm:grid-cols-5 xl:grid-cols-10 gap-4">
            {uniqueColors(Object.entries(DPD_COLORS)).map(([name, hex]) => (
              <Swatch key={name} name={`DPD ${name}`} hex={hex} />
            ))}
            {uniqueColors(Object.entries(RISK_COLORS)).map(([name, hex]) => (
              <Swatch key={name} name={`Risk ${name}`} hex={hex} />
            ))}
          </div>
          <Separator />
          <div className="grid grid-cols-3 sm:grid-cols-5 xl:grid-cols-10 gap-4">
            {CHART_SERIES.map((hex, i) => (
              <Swatch key={hex} name={`Series ${i + 1}`} hex={hex} />
            ))}
          </div>
        </Card>
      </GallerySection>

      <GallerySection title="Type scale" note="System font stack; weights stop at 600 — bold, extrabold and black all render 600.">
        <Card className="p-6 divide-y divide-border/40">
          {TYPE_SCALE.map((t) => (
            <div key={t.role} className="flex items-baseline gap-6 py-3">
              <span className="w-40 shrink-0 text-[11px] font-medium text-muted-foreground">{t.role}</span>
              <span className={t.className}>{t.sample}</span>
            </div>
          ))}
        </Card>
      </GallerySection>

      <GallerySection title="Formatters" note="theme/format.ts — CC's three money shapes, kept distinct.">
        <Card className="p-6">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <Tile label="cr(612.4)" value={cr(612.4)} sub="input already in crores" />
            <Tile label="cr1(4862)" value={cr1(4862)} sub="one decimal" />
            <Tile label="rs(1845230)" value={rs(1845230)} sub="Indian grouping" />
            <Tile label="n(512480)" value={n(512480)} sub="a count" />
          </div>
        </Card>
      </GallerySection>

      {drill && <DrillPanel drillKey={drill.key} data={drill.data} onClose={() => setDrill(null)} />}

      {workspaceOpen && (
        <WorkspaceModal
          tool={{ panel: "budget", label: "Budget Optimizer" }}
          mode="Simulation — allocation is not applied until you confirm"
          onRunAnalysis={() => setBudgetRuns((r) => r + 1)}
          getExport={() => ({
            filename: "budget-optimizer.csv",
            headers: ["Agency", "Current (₹ L)", "Proposed (₹ L)", "Expected lift (₹ Cr)"],
            rows: BUDGET_ROWS.map((r) => [r.agency, r.current, r.proposed, r.liftCr]),
          })}
          onClose={() => setWorkspaceOpen(false)}
        >
          <div className="space-y-5">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <Tile label="Monthly field budget" value="₹73.0 L" sub="commission pool, all agencies" />
              <Tile label="Reallocated" value="₹6.2 L" sub="8.5% of the pool" color={BRAND.primary} />
              <Tile label="Expected lift" value="₹2.5 Cr" sub="recovered, next 30 days" color={BRAND.success} />
              <Tile label="Runs" value={String(budgetRuns)} sub="Run Analysis recomputes" />
            </div>
            <Panel title="Proposed allocation by agency" hint="₹ lakh per month">
              <DataTable columns={BUDGET_COLUMNS} rows={BUDGET_ROWS} rowKey={(r) => r.agency} minWidth={0} />
            </Panel>
          </div>
        </WorkspaceModal>
      )}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogClose onClose={() => setDialogOpen(false)} />
          <DialogHeader>
            <DialogTitle>Recall 312 cases from Thar Recovery Associates?</DialogTitle>
            <DialogDescription>They return to the unplaced pool and can be re-placed tonight. The agency is notified.</DialogDescription>
          </DialogHeader>
          <div className="px-6 py-5 space-y-2">
            <p className="text-[13px] text-foreground">₹4.1 Cr outstanding · efficiency 14.2% · 23 cases with a live promise.</p>
            <p className="text-[12px] text-muted-foreground">Cases with a promise due in the next 7 days stay with the agency until it matures.</p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              Keep with agency
            </Button>
            <Button variant="destructive" onClick={() => setDialogOpen(false)}>
              Recall 312 cases
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </PageRoot>
  );
}
