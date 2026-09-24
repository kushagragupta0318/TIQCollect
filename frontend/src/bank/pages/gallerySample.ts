// Sample data for the component gallery (/bank/_gallery). INVENTED, not any
// bank's book: a mid-sized bank's retail collections book, internally consistent so
// the screenshots read like a real screen — the DPD ladder, the funnel and the
// product × bucket grid all sum to the same ₹612.4 Cr delinquent exposure, and
// every transition-matrix row sums to 100. Agency names are invented.
import type { AnalyticsTab } from "../components/analytics";
import type { DecisionAlert } from "../components/DecisionAlerts";
import type { DrillData } from "../components/DrillPanel";
import type { Kpi, KpiRow } from "../components/kpi";
import type { CureRollFlow, FunnelStage, HeatGridRow } from "../components/portfolioVisuals";

export const AS_OF = "22 Sep 2026";

/* ── KPI flow — plan §5.3's twelve ────────────────────────────────────── */

export const KPI_ROWS: KpiRow[] = [
  {
    id: "position",
    caption: "Where the book stands",
    kpis: ["delinquent_exposure", "placed_share", "unworked_exposure", "gnpa", "roll_forward", "cure_rate"],
  },
  {
    id: "performance",
    caption: "What came back, what it cost, how it was done",
    kpis: ["collection_efficiency", "resolution_rate", "ptp_keep", "visit_to_pay", "cost_to_collect", "compliance"],
  },
];

export const KPIS: Kpi[] = [
  {
    id: "delinquent_exposure", label: "Delinquent Exposure", value: "₹612.4 Cr", trend: "+2.1% MoM",
    trendUp: true, good: false, sub: "12.6% of a ₹4,862.0 Cr book", drill: "exposure", tone: "warning",
    basis: "Σ total outstanding on loans with DPD > 0, as of 22 Sep 2026.",
  },
  {
    id: "placed_share", label: "Placed with Agencies", value: "71.3%", trend: "+3.4 pp vs last month",
    trendUp: true, good: true, sub: "₹436.6 Cr across 14 active agencies", drill: "agencies", tone: "success",
    basis: "Placed exposure ÷ delinquent exposure. A loan counts while its placement is open.",
  },
  {
    id: "unworked_exposure", label: "Unworked Exposure", value: "₹48.7 Cr", trend: "-6.2% MoM",
    trendUp: false, good: true, sub: "11.2% of placed — no visit or call in 7 days", drill: "field", tone: "warning",
    basis: "Placed exposure with no visit or call inside the SLA window (default 7 days).",
  },
  {
    id: "gnpa", label: "GNPA", value: "3.42%", trend: "+0.2 pp vs last month",
    trendUp: true, good: false, sub: "₹166.3 Cr · 21,480 NPA accounts", drill: "migration", tone: "critical",
    basis: "NPA exposure (90+ DPD) ÷ book exposure.",
  },
  {
    id: "roll_forward", label: "Roll-Forward Rate", value: "14.8%", trend: "+1.3 pp vs last month",
    trendUp: true, good: false, sub: "Exposure-weighted, one bucket or more", drill: "migration", tone: "warning",
    basis: "Exposure that moved to a worse DPD bucket this month ÷ delinquent exposure at the start of it.",
  },
  {
    id: "cure_rate", label: "Cure Rate", value: "22.6%", trend: "-0.9 pp vs last month",
    trendUp: false, good: false, sub: "Exposure back to Current in the month", drill: "migration", tone: "neutral",
    basis: "Exposure-weighted share of delinquent loans that returned to Current.",
  },
  {
    id: "collection_efficiency", label: "Collection Efficiency", value: "38.4%", trend: "+2.7 pp vs prior 30d",
    trendUp: true, good: true, sub: "₹71.9 Cr verified of ₹187.2 Cr due", drill: "recovery", tone: "success",
    basis: "VERIFIED collections ÷ collectible due in the period.",
  },
  {
    id: "resolution_rate", label: "Resolution Rate", value: "18.9%", trend: "+0.0 pp vs last month",
    trendUp: null, good: true, sub: "9,412 of 49,780 placed cases resolved", drill: "agencies", tone: "neutral",
    basis: "Placed cases resolved (paid, closed or settled) ÷ placed cases in the cohort.",
  },
  {
    id: "ptp_keep", label: "PTP Keep Rate", value: "61.2%", trend: "-2.4 pp vs last month",
    trendUp: false, good: false, sub: "Honoured ÷ matured, 28,306 promises", drill: "recovery", tone: "warning",
    basis: "Promises honoured ÷ promises matured in the period. Open promises are excluded.",
  },
  {
    id: "visit_to_pay", label: "Visit-to-Pay Conversion", value: "27.5%", trend: "+1.8 pp vs last month",
    trendUp: true, good: true, sub: "Met visits paid within 7 days", drill: "field", tone: "success",
    basis: "Met visits followed by a verified payment within 7 days ÷ met visits.",
  },
  {
    id: "cost_to_collect", label: "Cost to Collect", value: "₹3.84", trend: "-5.2% MoM",
    trendUp: false, good: true, sub: "per ₹100 recovered, commission + field", drill: "cost", tone: "success",
    basis: "(Agency commission accrued + field cost) per ₹100 recovered.",
  },
  {
    id: "compliance", label: "Compliance & Integrity", value: "96.2", trend: "-0.6 vs last month",
    trendUp: false, good: false, sub: "1.4 weighted breaches per 100 visits", drill: "compliance", tone: "neutral",
    basis: "100 − weighted breaches per 100 visits: out-of-hours attempts, geofence failures, confirmed fraud findings, missing consent.",
  },
];

export const KPI_NARRATIVE =
  "Delinquent exposure rose 2.1% to ₹612.4 Cr as 31-60 DPD rolled faster in personal loans and cards. Agencies worked more of " +
  "it — placed share is up 3.4 points and unworked exposure fell to ₹48.7 Cr — and efficiency improved to 38.4%, but promises " +
  "are being kept less often, which usually shows up in next month's roll rate.";

/* ── Analytics tabs — plan §5.4's eight ──────────────────────────────── */

export type TabId = "exposure" | "migration" | "recovery" | "field" | "agencies" | "cost" | "concentration" | "compliance";

export const ANALYTICS_TABS: AnalyticsTab<TabId>[] = [
  { id: "exposure", label: "Exposure" },
  { id: "migration", label: "Migration" },
  { id: "recovery", label: "Recovery" },
  { id: "field", label: "Field Operations" },
  { id: "agencies", label: "Agencies" },
  { id: "cost", label: "Cost to Collect" },
  { id: "concentration", label: "Concentration" },
  { id: "compliance", label: "Compliance" },
];

export const HEADLINES: Record<TabId, string> = {
  exposure:
    "₹612.4 Cr of the ₹4,862.0 Cr book is delinquent, and 71% of it is placed. 1-30 DPD carries a third of the exposure and still cures at 38%; the 90+ tail is ₹166.3 Cr and cures at 2%.",
  migration:
    "Roll-forward rose to 14.8%, led by 61-90 → 90-180 at 46.6%. Cures out of 1-30 held at 38%, so the damage is concentrated one bucket before NPA.",
  recovery:
    "₹71.9 Cr recovered against ₹187.2 Cr due — 38.4%, ahead of last month but behind the straight-line pace since the 14th.",
  field:
    "1,284 agents made 41,620 visits this month, meeting the borrower on 58% of them. Coverage within the 7-day SLA is 88.8% of placed exposure.",
  agencies:
    "Fourteen agencies hold ₹436.6 Cr. The top four resolve over 20% of what they are placed; two are below 12% and cost more than ₹6 per ₹100.",
  cost:
    "Collecting ₹100 costs ₹3.84 across commission and field effort — ₹1.9 in 1-30 DPD, ₹31.5 past 180. Effort rises exactly as recovery falls.",
  concentration:
    "Maharashtra, Uttar Pradesh and Karnataka carry 52% of delinquent exposure; the 12 largest accounts are ₹41.8 Cr, 6.8% of it.",
  compliance:
    "1.4 weighted breaches per 100 visits, up from 0.8. Out-of-hours attempts in two agencies account for most of the rise.",
};

/* ── Exposure ─────────────────────────────────────────────────────────── */

export const FUNNEL: FunnelStage[] = [
  { stage: "Whole book", exposureCr: 4862.0, accounts: 512480 },
  { stage: "Delinquent (DPD > 0)", exposureCr: 612.4, accounts: 68215 },
  { stage: "Placed with agencies", exposureCr: 436.6, accounts: 49780 },
  { stage: "NPA (90+ DPD)", exposureCr: 166.3, accounts: 21480 },
  { stage: "Write-off candidates", exposureCr: 38.9, accounts: 4112 },
];

export interface LadderRow {
  bucket: string;
  isNpa: boolean;
  accounts: number;
  exposureCr: number;
  sharePct: number;
  collectedCr: number;
  efficiencyPct: number;
  curePct: number;
  rollPct: number;
}

export const DPD_LADDER: LadderRow[] = [
  { bucket: "1-30", isNpa: false, accounts: 29840, exposureCr: 214.6, sharePct: 35.0, collectedCr: 38.2, efficiencyPct: 52.6, curePct: 38.4, rollPct: 20.4 },
  { bucket: "31-60", isNpa: false, accounts: 11260, exposureCr: 128.9, sharePct: 21.0, collectedCr: 16.1, efficiencyPct: 34.1, curePct: 14.6, rollPct: 34.4 },
  { bucket: "61-90", isNpa: false, accounts: 5635, exposureCr: 102.6, sharePct: 16.8, collectedCr: 8.3, efficiencyPct: 21.7, curePct: 6.2, rollPct: 46.6 },
  { bucket: "90-180", isNpa: true, accounts: 12910, exposureCr: 97.4, sharePct: 15.9, collectedCr: 3.9, efficiencyPct: 9.8, curePct: 2.1, rollPct: 30.2 },
  { bucket: "180+", isNpa: true, accounts: 8570, exposureCr: 68.9, sharePct: 11.3, collectedCr: 1.1, efficiencyPct: 4.2, curePct: 0.6, rollPct: 0 },
];

export const GRID_BUCKETS = ["1-30", "31-60", "61-90", "90-180", "180+"];

const grid = (label: string, delqRatePct: number, cells: [number, number][]): HeatGridRow => ({
  label,
  delqRatePct,
  delqExposureCr: Math.round(cells.reduce((s, [cr]) => s + cr, 0) * 10) / 10,
  cells: Object.fromEntries(GRID_BUCKETS.map((b, i) => [b, { exposureCr: cells[i][0], accounts: cells[i][1] }])),
});

export const HEAT_GRID: HeatGridRow[] = [
  grid("Home Loan", 6.8, [[86.4, 3120], [42.8, 1410], [31.5, 980], [24.2, 760], [18.7, 590]]),
  grid("Personal Loan", 14.9, [[48.2, 9860], [31.6, 6120], [27.4, 5090], [29.8, 5480], [21.3, 3940]]),
  grid("Credit Card", 18.2, [[31.9, 11240], [22.4, 7860], [18.6, 6410], [19.7, 6880], [14.2, 4960]]),
  grid("Two-Wheeler", 11.4, [[14.3, 4120], [11.2, 3080], [9.1, 2440], [10.4, 2760], [7.6, 1980]]),
  grid("Business Loan", 9.7, [[24.6, 1030], [13.8, 560], [10.9, 430], [9.2, 370], [5.4, 220]]),
  grid("Gold Loan", 4.1, [[9.2, 1470], [7.1, 1130], [5.1, 810], [4.1, 650], [1.7, 270]]),
];

/* ── Migration ────────────────────────────────────────────────────────── */

export const MATRIX_LABELS = ["Current", "1-30", "31-60", "61-90", "90-180", "180+"];

export const TRANSITION_MATRIX: number[][] = [
  [91.2, 6.9, 1.3, 0.4, 0.2, 0],
  [38.4, 41.2, 16.1, 3.1, 1.2, 0],
  [14.6, 12.3, 38.7, 28.4, 6.0, 0],
  [6.2, 3.8, 9.9, 33.5, 46.6, 0],
  [2.1, 0.9, 1.6, 3.4, 61.8, 30.2],
  [0.6, 0.2, 0.3, 0.4, 1.1, 97.4],
];

export const MATRIX_OBSERVED = 784260;

export const CURE_ROLL: CureRollFlow[] = [
  { bucket: "1-30", exposureCr: 214.6, accounts: 29840, curePct: 38.4, improvePct: 0, holdPct: 41.2, rollPct: 20.4, curableCr: 82.4, atRiskCr: 43.8 },
  { bucket: "31-60", exposureCr: 128.9, accounts: 11260, curePct: 14.6, improvePct: 12.3, holdPct: 38.7, rollPct: 34.4, curableCr: 18.8, atRiskCr: 44.3 },
  { bucket: "61-90", exposureCr: 102.6, accounts: 5635, curePct: 6.2, improvePct: 13.7, holdPct: 33.5, rollPct: 46.6, curableCr: 6.4, atRiskCr: 47.8 },
  { bucket: "90-180", exposureCr: 97.4, accounts: 12910, curePct: 2.1, improvePct: 5.9, holdPct: 61.8, rollPct: 30.2, curableCr: 2.0, atRiskCr: 29.4 },
];

const MONTHS = ["Oct 25", "Nov 25", "Dec 25", "Jan 26", "Feb 26", "Mar 26", "Apr 26", "May 26", "Jun 26", "Jul 26", "Aug 26", "Sep 26"];

export const DELINQUENT_TREND = MONTHS.map((month, i) => ({
  month,
  delinquent: [61240, 62180, 63920, 62710, 63380, 64150, 64870, 65420, 66010, 66830, 67390, 68215][i],
  "90-180": [11020, 11180, 11460, 11390, 11610, 11840, 12020, 12210, 12380, 12560, 12740, 12910][i],
  "180+": [7240, 7310, 7420, 7510, 7630, 7760, 7890, 8010, 8140, 8290, 8430, 8570][i],
}));

/* ── Recovery ─────────────────────────────────────────────────────────── */

export const RECOVERY_SUMMARY = { targetCr: 187.2, achievedCr: 71.9, prevAchievedCr: 64.3, gapCr: 115.3, efficiencyPct: 38.4 };

// 30 days to 22 Sep. Daily collections follow the EMI calendar — a spike
// after the 5th and the 10th, softer weekends. They sum to ₹71.9 Cr.
const DAILY = [
  2.1, 1.6, 1.4, 1.8, 2.0, 2.2, 1.7, 1.3, 1.1, 2.4, 3.9, 4.6, 3.2, 2.6,
  2.2, 1.5, 1.2, 4.1, 5.2, 3.8, 3.1, 2.7, 2.0, 1.4, 2.3, 2.1, 2.4, 1.9, 1.6, 2.5,
];

export const RECOVERY_CURVE = DAILY.map((collected, i) => {
  const day = new Date(2026, 7, 24 + i);
  return {
    date: `${day.getDate()} ${["Aug", "Sep"][day.getMonth() - 7]}`,
    collected,
    cumulative: Math.round(DAILY.slice(0, i + 1).reduce((s, v) => s + v, 0) * 10) / 10,
    targetPace: Math.round(((RECOVERY_SUMMARY.targetCr / DAILY.length) * (i + 1)) * 10) / 10,
  };
});

/* ── Cost to collect ──────────────────────────────────────────────────── */

/** Share of each bucket's accounts worked this cycle (%). Falls with DPD, as effort per account rises. */
export const COVERAGE_PCT_BY_BUCKET: Record<string, number> = {
  "1-30": 72.4,
  "31-60": 61.8,
  "61-90": 48.2,
  "90-180": 36.5,
  "180+": 22.9,
};

export const COST_BY_BUCKET = [
  { bucket: "1-30", costPerAccount: 186, costPer100: 1.9 },
  { bucket: "31-60", costPerAccount: 342, costPer100: 3.6 },
  { bucket: "61-90", costPerAccount: 518, costPer100: 6.8 },
  { bucket: "90-180", costPerAccount: 764, costPer100: 14.2 },
  { bucket: "180+", costPerAccount: 912, costPer100: 31.5 },
];

/* ── Agencies ─────────────────────────────────────────────────────────── */

export interface AgencyRow {
  agency: string;
  region: string;
  placedCases: number;
  placedCr: number;
  resolvedPct: number;
  efficiencyPct: number;
  costPer100: number;
}

export const AGENCIES: AgencyRow[] = [
  { agency: "Sahyadri Recovery Services", region: "West", placedCases: 8420, placedCr: 78.6, resolvedPct: 24.1, efficiencyPct: 46.2, costPer100: 2.9 },
  { agency: "Ganga Credit Management", region: "North", placedCases: 9160, placedCr: 71.3, resolvedPct: 21.7, efficiencyPct: 41.8, costPer100: 3.2 },
  { agency: "Coromandel Collections", region: "South", placedCases: 6230, placedCr: 64.9, resolvedPct: 20.6, efficiencyPct: 39.5, costPer100: 3.4 },
  { agency: "Deccan Field Associates", region: "South", placedCases: 6840, placedCr: 58.2, resolvedPct: 16.2, efficiencyPct: 31.4, costPer100: 4.6 },
  { agency: "Narmada Resolution Partners", region: "Central", placedCases: 5310, placedCr: 44.7, resolvedPct: 14.8, efficiencyPct: 28.9, costPer100: 5.1 },
  { agency: "Brahmaputra Credit Care", region: "East", placedCases: 4780, placedCr: 39.5, resolvedPct: 11.3, efficiencyPct: 19.6, costPer100: 6.4 },
  { agency: "Thar Recovery Associates", region: "North", placedCases: 3920, placedCr: 31.8, resolvedPct: 10.7, efficiencyPct: 14.2, costPer100: 7.9 },
];

/* ── Tiles for the tabs that have no dedicated visual in the gallery ────── */

export interface TileSample {
  label: string;
  value: string;
  sub?: string;
  tone?: "success" | "warning" | "destructive" | "primary";
}

export const TAB_TILES: Record<"field" | "concentration" | "compliance", TileSample[]> = {
  field: [
    { label: "Visits this month", value: "41,620", sub: "1,284 agents · 32.4 per agent" },
    { label: "Met rate", value: "58.1%", sub: "borrower met at the door", tone: "primary" },
    { label: "Coverage within SLA", value: "88.8%", sub: "of placed exposure, 7 days", tone: "success" },
    { label: "Planned vs actual km", value: "+11.6%", sub: "from beat reconciliation", tone: "warning" },
  ],
  concentration: [
    { label: "Top 3 states", value: "52.4%", sub: "Maharashtra, Uttar Pradesh, Karnataka" },
    { label: "Top 12 accounts", value: "₹41.8 Cr", sub: "6.8% of delinquent exposure", tone: "warning" },
    { label: "Metro branches", value: "₹284.1 Cr", sub: "46.4% of delinquent exposure" },
    { label: "Unsecured share", value: "63.7%", sub: "personal loan, card, business", tone: "destructive" },
  ],
  compliance: [
    { label: "Weighted breaches", value: "1.4", sub: "per 100 visits (0.8 last month)", tone: "destructive" },
    { label: "Out-of-hours attempts", value: "212", sub: "refused and logged", tone: "warning" },
    { label: "Geofence failures", value: "1.9%", sub: "visits outside 100 m" },
    { label: "Consent captured", value: "99.3%", sub: "of recorded calls", tone: "success" },
  ],
};

/* ── Drill ────────────────────────────────────────────────────────────── */

const DRILL_ROWS: DrillData["rows"] = [
  { accountId: "BL-KA-2204817", bucket: "90-180", exposure: 4862500, emi: 118400, paid: 0, keepRatePct: 12 },
  { accountId: "HL-MH-7730412", bucket: "90-180", exposure: 3945200, emi: 41850, paid: 41850, keepRatePct: 38 },
  { accountId: "BL-TN-1189065", bucket: "90-180", exposure: 3210760, emi: 86200, paid: 0, keepRatePct: 0 },
  { accountId: "HL-UP-5402238", bucket: "90-180", exposure: 2874300, emi: 32600, paid: 16300, keepRatePct: 44 },
  { accountId: "PL-GJ-9937120", bucket: "90-180", exposure: 1245880, emi: 38900, paid: 0, keepRatePct: 17 },
  { accountId: "PL-MH-6612094", bucket: "90-180", exposure: 1102460, emi: 34150, paid: 12000, keepRatePct: 29 },
  { accountId: "CC-DL-4481553", bucket: "90-180", exposure: 986720, emi: 49340, paid: 0, keepRatePct: 8 },
  { accountId: "TW-WB-3370981", bucket: "90-180", exposure: 214650, emi: 7420, paid: 7420, keepRatePct: 57 },
];

/** A drill for any clicked slice. The headline figures follow the slice; splits and accounts are the 90-180 sample. */
export function drillFor(title: string, exposureCr: number, accounts: number, sharePct: number): DrillData {
  return {
    title,
    subtitle: `${accounts.toLocaleString("en-IN")} accounts · ₹${exposureCr.toFixed(1)} Cr outstanding`,
    accounts,
    sharePct,
    exposureCr,
    stats: [
      { label: "Outstanding", value: `₹${exposureCr.toFixed(1)} Cr` },
      { label: "Accounts", value: accounts.toLocaleString("en-IN") },
      { label: "Average ticket", value: `₹${Math.round((exposureCr * 1e7) / Math.max(accounts, 1)).toLocaleString("en-IN")}` },
      { label: "Collected (30d)", value: "₹3.9 Cr" },
      { label: "Efficiency", value: "9.8%" },
      { label: "PTP keep rate", value: "41.2%" },
      { label: "Placed", value: "88.6%" },
      { label: "Agencies working it", value: "12" },
    ],
    splits: [
      {
        title: "By DPD bucket",
        bucketColors: true,
        items: [
          { label: "90-180", sharePct: 58.6, exposureCr: 57.1, accounts: 7565 },
          { label: "180+", sharePct: 41.4, exposureCr: 40.3, accounts: 5345 },
        ],
      },
      {
        title: "By product",
        items: [
          { label: "Personal Loan", sharePct: 30.6, exposureCr: 29.8, accounts: 5480 },
          { label: "Home Loan", sharePct: 24.8, exposureCr: 24.2, accounts: 760 },
          { label: "Credit Card", sharePct: 20.2, exposureCr: 19.7, accounts: 6880 },
          { label: "Two-Wheeler", sharePct: 10.7, exposureCr: 10.4, accounts: 2760 },
          { label: "Business Loan", sharePct: 9.4, exposureCr: 9.2, accounts: 370 },
        ],
      },
      {
        title: "By state",
        items: [
          { label: "Maharashtra", sharePct: 22.1, exposureCr: 21.5, accounts: 2830 },
          { label: "Uttar Pradesh", sharePct: 17.4, exposureCr: 16.9, accounts: 2410 },
          { label: "Karnataka", sharePct: 12.9, exposureCr: 12.6, accounts: 1560 },
          { label: "Tamil Nadu", sharePct: 11.2, exposureCr: 10.9, accounts: 1390 },
          { label: "Gujarat", sharePct: 8.6, exposureCr: 8.4, accounts: 1040 },
        ],
      },
      {
        title: "By agency",
        items: [
          { label: "Ganga Credit", sharePct: 19.8, exposureCr: 19.3, accounts: 2610 },
          { label: "Sahyadri Recovery", sharePct: 17.2, exposureCr: 16.8, accounts: 2140 },
          { label: "Coromandel", sharePct: 13.5, exposureCr: 13.1, accounts: 1720 },
          { label: "Deccan Field", sharePct: 11.9, exposureCr: 11.6, accounts: 1580 },
        ],
      },
    ],
    rows: DRILL_ROWS,
  };
}

/* ── Alerts ───────────────────────────────────────────────────────────── */

export const ALERTS: DecisionAlert[] = [
  {
    id: "untouched_placed",
    severity: "critical",
    title: "₹48.7 Cr placed and untouched past the 7-day SLA",
    summary: "6,214 placed accounts across 9 agencies have had no visit or call since they were placed",
    metrics: [
      { label: "Untouched exposure", value: "₹48.7 Cr" },
      { label: "Accounts", value: "6,214" },
      { label: "Agencies", value: "9" },
      { label: "Oldest placement", value: "23 days" },
    ],
    breakdown: [
      { label: "1-30", value: 2410 },
      { label: "31-60", value: 1580 },
      { label: "61-90", value: 1120 },
      { label: "90-180", value: 804 },
      { label: "180+", value: 300 },
    ],
    rows: [
      { accountId: "HL-MH-7730412", product: "Home Loan", bucket: "61-90", state: "Maharashtra", exposure: 3945200, monthsDelinquent: 3, keepRatePct: 38, agency: "Sahyadri Recovery" },
      { accountId: "BL-KA-2204817", product: "Business Loan", bucket: "31-60", state: "Karnataka", exposure: 2980400, monthsDelinquent: 2, keepRatePct: 12, agency: "Deccan Field" },
      { accountId: "PL-UP-8821407", product: "Personal Loan", bucket: "31-60", state: "Uttar Pradesh", exposure: 842300, monthsDelinquent: 2, keepRatePct: 25, agency: "Ganga Credit" },
      { accountId: "CC-TN-5530162", product: "Credit Card", bucket: "1-30", state: "Tamil Nadu", exposure: 386150, monthsDelinquent: 1, keepRatePct: 60, agency: "Coromandel" },
    ],
    rowExtra: { key: "agency", label: "Agency" },
    actions: [
      { label: "Open in Placement", target: "placement" },
      { label: "Notify the agencies", target: "notify" },
    ],
    basis: "Placed cases with no visit or call in the SLA window (default 7 days), as of 22 Sep 2026.",
  },
  {
    id: "agency_efficiency_drop",
    severity: "warning",
    title: "Deccan Field Associates: efficiency down 24% month on month",
    summary: "31.4% against 41.3% last month on ₹58.2 Cr placed in Karnataka and Telangana",
    metrics: [
      { label: "Placed exposure", value: "₹58.2 Cr" },
      { label: "Efficiency", value: "31.4%" },
      { label: "Last month", value: "41.3%" },
      { label: "Placed cases", value: "6,840" },
    ],
    breakdown: [
      { label: "1-30", value: 3120 },
      { label: "31-60", value: 1710 },
      { label: "61-90", value: 940 },
      { label: "90-180", value: 760 },
      { label: "180+", value: 310 },
    ],
    actions: [{ label: "Open agency scorecard", target: "agency" }],
    basis: "Verified collections ÷ collectible due, this month against last, for one agency's placed book. Fires past −20%.",
  },
  {
    id: "capacity_gap",
    severity: "warning",
    title: "Agent capacity below placed volume in Uttar Pradesh East",
    summary: "1,960 placed cases for 38 active agents — 51.6 each against a 40-case day",
    metrics: [
      { label: "Placed cases", value: "1,960" },
      { label: "Active agents", value: "38" },
      { label: "Cases per agent", value: "51.6" },
      { label: "Shortfall", value: "11 agents" },
    ],
    actions: [
      { label: "Rebalance placement", target: "placement" },
      { label: "Open capacity forecast", target: "forecast" },
    ],
    basis: "Open placed cases ÷ agents on duty in the region, against the configured daily capacity.",
  },
  {
    id: "self_cure",
    severity: "info",
    title: "₹12.6 Cr of 1-30 DPD likely to self-cure this cycle",
    summary: "4,380 accounts with a standing instruction and a clean 12-month history — a reminder, not a visit",
    metrics: [
      { label: "Exposure", value: "₹12.6 Cr" },
      { label: "Accounts", value: "4,380" },
      { label: "Avg PTP keep", value: "86.4%" },
      { label: "Field visits saved", value: "≈ 3,900" },
    ],
    actions: [{ label: "Open Scenario Lab", target: "scenario" }],
    basis: "1-30 DPD accounts with an active mandate and no broken promise in 12 months.",
  },
];

/* ── Workspace ────────────────────────────────────────────────────────── */

export const BUDGET_ROWS = [
  { agency: "Sahyadri Recovery Services", current: 18.4, proposed: 21.6, liftCr: 1.9 },
  { agency: "Ganga Credit Management", current: 16.9, proposed: 18.2, liftCr: 0.8 },
  { agency: "Coromandel Collections", current: 14.2, proposed: 15.0, liftCr: 0.5 },
  { agency: "Deccan Field Associates", current: 13.6, proposed: 11.1, liftCr: -0.4 },
  { agency: "Brahmaputra Credit Care", current: 9.8, proposed: 7.0, liftCr: -0.3 },
];
