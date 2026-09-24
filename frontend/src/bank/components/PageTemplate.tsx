// Command Center's page template (spec §5.1–5.2), ported to TypeScript.
// Class strings verbatim from PortfolioOverview (variant A, "executive"),
// RiskSimulator / RiskRadar (variant B, "tool") and the overview's loading and
// failure states. Every bank page starts from these.
import { Fragment, type ReactNode } from "react";
import type { LucideIcon } from "lucide-react";

/** The page root: no max-width, CC's `space-y-6` rhythm. */
export function PageRoot({ children }: { children: ReactNode }) {
  return <div className="p-1 md:p-2 w-full mx-auto space-y-6">{children}</div>;
}

export interface ExecutiveHeaderProps {
  title: string;
  /** The live-dot meta line, e.g. ["Live book", "4,82,310 accounts", "As of 24 Sep 2026"]. */
  meta?: string[];
  /** The right-hand scope note. */
  scopeNote?: ReactNode;
}

/** Variant A — Overview, Decision Center: title left, scope note right, no actions. */
export function ExecutiveHeader({ title, meta = [], scopeNote }: ExecutiveHeaderProps) {
  return (
    <div className="mb-8 flex flex-col justify-between gap-4 md:flex-row md:items-end">
      <div>
        <h2 className="text-2xl font-bold text-foreground tracking-tight">{title}</h2>
        {meta.length > 0 && (
          <div className="flex items-center gap-2 text-[11px] text-muted-foreground font-semibold mt-1 flex-wrap">
            <span className="w-1.5 h-1.5 rounded-full bg-success animate-pulse inline-block" />
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
