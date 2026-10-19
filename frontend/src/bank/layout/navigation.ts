// The bank portal's navigation — plan §5.1, laid onto CC's Sidebar.jsx
// structure (one icon per section; its pages inline when expanded, in a hover
// flyout on the collapsed rail). Every route the shell registers comes from
// here, so the sidebar, the search index and the router cannot disagree.
import {
  ArrowRightLeft,
  BarChart3,
  BellRing,
  Bot,
  Boxes,
  BrainCircuit,
  Building2,
  Cpu,
  Database,
  FileText,
  FlaskConical,
  Gauge,
  LayoutDashboard,
  Map as MapIcon,
  MessageSquare,
  ScrollText,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  TrendingUp,
  Trophy,
  UserPlus,
  Users,
  type LucideIcon,
} from "lucide-react";

export interface BankNavItem {
  name: string;
  /** Path under /bank, without the leading "/bank/". */
  path: string;
  icon: LucideIcon;
  /** What this screen is for — the placeholder page's scope note. */
  summary: string;
  /** The STANDALONE-TASKS.md items that build it. */
  tasks: string;
}

export interface BankNavSection {
  label: string;
  icon: LucideIcon;
  items: BankNavItem[];
}

export const BANK_BASE = "/bank";

export const BANK_SECTIONS: BankNavSection[] = [
  {
    label: "Command Center",
    icon: LayoutDashboard,
    items: [
      {
        name: "Overview",
        path: "overview",
        icon: LayoutDashboard,
        summary: "Twelve header KPIs across the placed book — where it stands, what came back and what it cost.",
        tasks: "C01–C03",
      },
      {
        name: "Analytics",
        path: "analytics",
        icon: BarChart3,
        summary: "Eight tabs: Exposure, Migration, Recovery, Field Operations, Agencies, Cost to Collect, Concentration, Compliance.",
        tasks: "C04–C05",
      },
      {
        name: "Alerts",
        path: "alerts",
        icon: BellRing,
        summary: "Seven portfolio rules and four field rules, each derived live from the book.",
        tasks: "C06",
      },
    ],
  },
  {
    label: "AI Strategy",
    icon: BrainCircuit,
    items: [
      {
        name: "Monte Carlo Simulator",
        path: "strategy/monte-carlo",
        icon: FlaskConical,
        summary: "Account-level roll-rate simulation with percentile bands, IFRS-9 staging and sensitivity.",
        tasks: "E01–E05",
      },
      {
        name: "Cash Forecast",
        path: "strategy/cash-forecast",
        icon: TrendingUp,
        summary: "Thirteen-week collections forecast with quantile bands and a forecast-vs-actual tracker.",
        tasks: "E06–E07",
      },
      {
        name: "Scenario Lab",
        path: "strategy/scenario-lab",
        icon: SlidersHorizontal,
        summary: "Budget, outreach, agency-reallocation and commission what-ifs on one cost table.",
        tasks: "E08",
      },
      {
        name: "Board Reports",
        path: "strategy/board-reports",
        icon: FileText,
        summary: "Board, risk, audit and agency-review packs as PDF, PPTX and XLSX.",
        tasks: "E09–E12",
      },
    ],
  },
  {
    label: "Agencies",
    icon: Building2,
    items: [
      {
        name: "Directory",
        path: "agencies/directory",
        icon: Building2,
        summary: "Every empanelled agency with coverage, contract status and score.",
        tasks: "D05",
      },
      {
        name: "Performance",
        path: "agencies/performance",
        icon: Trophy,
        summary: "Agency scorecards, recovery against expectation and the regional leaderboard.",
        tasks: "D06–D07",
      },
      {
        name: "Placement",
        path: "agencies/placement",
        icon: ArrowRightLeft,
        summary: "Place delinquent loans with agencies within capacity and coverage, with reasons.",
        tasks: "D08–D09",
      },
      {
        name: "Onboard Agency",
        path: "agencies/onboard",
        icon: UserPlus,
        summary: "Six steps: identity, coverage, contract, documents, master login, review.",
        tasks: "D01–D03",
      },
      {
        name: "Messaging",
        path: "agencies/messaging",
        icon: MessageSquare,
        summary: "Threads with your agencies — reversal disputes and escalations, every message audited.",
        tasks: "L07",
      },
    ],
  },
  {
    // Model-risk readers look for governance, not operations (tiqcollect-06).
    label: "Governance",
    icon: BrainCircuit,
    items: [
      {
        name: "Models",
        path: "governance/models",
        icon: BrainCircuit,
        summary: "The six scoring layers as a set, and the trained model's card: honest figures, coverage, monitoring, control.",
        tasks: "F07/F09 (read-only slice: the AI showcase)",
      },
    ],
  },
  {
    label: "Tech Ops",
    icon: Cpu,
    items: [
      {
        name: "AI Agents",
        path: "tech-ops/agents",
        icon: Bot,
        summary: "Agent registry, prompt versions, test console, run traces and the approvals inbox.",
        tasks: "F02–F06",
      },
      {
        name: "MLOps",
        path: "tech-ops/mlops",
        icon: Boxes,
        summary: "Models, monitoring, retrain candidates and the serving state.",
        tasks: "F07–F09",
      },
      {
        name: "Data Quality",
        path: "tech-ops/data-quality",
        icon: Database,
        summary: "Bank-feed checks and the quarantine view.",
        tasks: "F10",
      },
      {
        name: "Usage & Cost",
        path: "tech-ops/usage",
        icon: Gauge,
        summary: "LLM calls, tokens and spend by purpose.",
        tasks: "F11",
      },
    ],
  },
  {
    label: "Admin",
    icon: ShieldCheck,
    items: [
      {
        name: "Bank Users",
        path: "admin/users",
        icon: Users,
        summary: "Invite bank users, assign roles, see MFA status and sessions.",
        tasks: "K01",
      },
      {
        name: "Regions",
        path: "admin/regions",
        icon: MapIcon,
        summary: "The zone → region → state → city hierarchy every filter and agency contract uses.",
        tasks: "K01",
      },
      {
        name: "Settings",
        path: "admin/settings",
        icon: Settings,
        summary: "Bank-wide settings: SLA windows, contact hours, thresholds.",
        tasks: "K01",
      },
      {
        name: "Audit",
        path: "admin/audit",
        icon: ScrollText,
        summary: "Every audited action in the bank's tenancy.",
        tasks: "K01",
      },
    ],
  },
];

export const BANK_HOME_PATH = `${BANK_BASE}/${BANK_SECTIONS[0].items[0].path}`;

/** "/bank/" + item path. */
export const bankHref = (item: Pick<BankNavItem, "path">): string => `${BANK_BASE}/${item.path}`;

export const BANK_NAV_ITEMS: BankNavItem[] = BANK_SECTIONS.flatMap((s) => s.items);

/**
 * The nav paths that resolve to a real page. ONE list: BankApp routes by it and
 * the sidebar renders by it, so a screen cannot be routed and un-navigable, or
 * listed in the rail and dead.
 *
 * 2026-10-01 — the rail offered 13 items that all landed on "Not built yet",
 * which in a demo reads as a broken product rather than an unfinished one.
 * Unbuilt items are no longer rendered; their ROUTES stay registered, so a deep
 * link or a bookmark still reaches the placeholder and says what it is.
 * Add a path here the moment its page exists — the rail follows automatically.
 */
export const BUILT_NAV_PATHS: ReadonlySet<string> = new Set([
  "overview",
  "analytics",
  "alerts",
  "agencies/directory",
  "agencies/performance",
  "agencies/placement",
  "agencies/onboard",
  "agencies/messaging",
  "governance/models",
  "strategy/monte-carlo",
  "strategy/board-reports",
  "strategy/cash-forecast",
  "admin/audit",
  "admin/regions",
  "admin/settings",
  "admin/users",
  "tech-ops/mlops",
  "tech-ops/usage",
  "tech-ops/data-quality",
]);

export const isBuiltPath = (path: string): boolean => BUILT_NAV_PATHS.has(path);

/** The sections the rail shows: built items only, and no empty section heading. */
export const BUILT_BANK_SECTIONS: BankNavSection[] = BANK_SECTIONS
  .map((s) => ({ ...s, items: s.items.filter((i) => isBuiltPath(i.path)) }))
  .filter((s) => s.items.length > 0);

/** The nav item a pathname belongs to (exact, or a sub-route of it). */
export function findNavItem(pathname: string): BankNavItem | undefined {
  const clean = pathname.replace(/\/+$/, "");
  return BANK_NAV_ITEMS.find((item) => {
    const href = bankHref(item);
    return clean === href || clean.startsWith(`${href}/`);
  });
}
