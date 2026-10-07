// Command Center's page template (spec §5.1–5.2), ported to TypeScript.
// Class strings verbatim from PortfolioOverview (variant A, "executive"),
// RiskSimulator / RiskRadar (variant B, "tool") and the overview's loading and
// failure states. Every bank page starts from these.
import { Fragment, type ReactNode } from "react";
import type { LucideIcon } from "lucide-react";

/** The page root: no max-width (unchanged from CC's own layout — the edge
 *  padding here is a small top-up on BankLayout's `main`, not this page's to
 *  touch); `space-y-8`, not CC's original `space-y-6` — more air between a
 *  page's own sections, a deliberate departure from verbatim for the bold
 *  pass, not a layout change (it moves nothing relative to the viewport,
 *  only the gap between siblings already stacked here). Also the one place
 *  every bank page gets its enter transition (bank.css's own .page-fade-in —
 *  a reduced-motion user already gets it collapsed to 1ms by bank.css's
 *  global `@media (prefers-reduced-motion: reduce)` guard, nothing extra
 *  needed here). This is a fresh element on every route change (<Outlet/>'s
 *  child remounts; the layout around it does not), so it replays per page —
 *  BankLayout's `main` cannot do that, it mounts once for the whole session. */
export function PageRoot({ children }: { children: ReactNode }) {
  return <div className="page-fade-in p-1 md:p-2 w-full mx-auto space-y-8">{children}</div>;
}

export interface ExecutiveHeaderProps {
  title: string;
  /** The meta line, e.g. ["Live book", "4,82,310 accounts", "As of 24 Sep 2026"]. */
  meta?: string[];
  /**
   * Show CC's pulsing green "live" dot before the meta line. Only for a page
   * whose figures really come from the live book — CC always shows it; here it
   * must be earned (sample and placeholder pages leave it off).
   */
  live?: boolean;
  /** The right-hand scope note. */
  scopeNote?: ReactNode;
}

/** Variant A — Overview, Decision Center: title left, scope note right, no actions. */
export function ExecutiveHeader({ title, meta = [], live = false, scopeNote }: ExecutiveHeaderProps) {
  return (
    <div className="mb-8 flex flex-col justify-between gap-4 md:flex-row md:items-end">
      <div>
        <h2 className="text-2xl font-bold text-foreground tracking-tight">{title}</h2>
        {meta.length > 0 && (
          <div className="flex items-center gap-2 text-[11px] text-muted-foreground font-semibold mt-1 flex-wrap">
            {live && <span className="w-1.5 h-1.5 rounded-full bg-success animate-pulse inline-block" aria-hidden="true" />}
            {meta.map((m, i) => (
              <Fragment key={`${i}-${m}`}>
                {i > 0 && <span>·</span>}
                <span>{m}</span>
              </Fragment>
            ))}
          </div>
        )}
      </div>
      {scopeNote && <p className="max-w-sm text-[11.5px] text-muted-foreground md:text-right">{scopeNote}</p>}
    </div>
  );
}

export interface ToolHeaderProps {
  title: string;
  icon: LucideIcon;
  description?: ReactNode;
  /** Right side: bordered btn-ghost actions, or a big stat. */
  actions?: ReactNode;
}

/** Variant B — tool pages (Risk Simulator): primary icon + title, description, actions. */
export function ToolHeader({ title, icon: Icon, description, actions }: ToolHeaderProps) {
  return (
    <div className="flex flex-col justify-between gap-4 md:flex-row md:items-start">
      <div>
        <h1 className="text-2xl font-extrabold tracking-tight flex items-center gap-2">
          <Icon className="size-6 text-primary" />
          {title}
        </h1>
        {description && <p className="text-sm text-muted-foreground mt-1 max-w-2xl">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-2 flex-wrap">{actions}</div>}
    </div>
  );
}

/** A bordered ghost action for ToolHeader (RiskSimulator.jsx:216-229). */
export function ToolHeaderAction({ icon: Icon, children, onClick }: { icon: LucideIcon; children: ReactNode; onClick?: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="btn-ghost text-xs flex items-center gap-1.5 border border-border rounded-lg px-3 py-1.5 hover:bg-muted"
    >
      <Icon className="size-3.5" />
      {children}
    </button>
  );
}

export function PageLoading({ label = "Loading portfolio…" }: { label?: string }) {
  return (
    <div className="flex h-[60vh] items-center justify-center">
      <p className="text-sm font-semibold text-muted-foreground animate-pulse">{label}</p>
    </div>
  );
}

export function PageFailure({ children = "This page could not load." }: { children?: ReactNode }) {
  return (
    <div className="p-10 text-center">
      <p className="text-sm font-semibold text-muted-foreground">{children}</p>
    </div>
  );
}
