// ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
// 2026-09-24 — New file (task UI04). The bank portal's shell: Command Center's
//   App.jsx layout (spec §2.1), ported. Floating sidebar 20px from the
//   viewport, a 116px (collapsed) / 292px (expanded) content offset, the white
//   top-bar card, then `main.app-content` — the only scroll container, 16px
//   corners, `p-4 md:p-5`, no max-width.
//
//   bank.css is imported HERE and nowhere else. This module is reached only
//   through the lazy `/bank/*` route, so the bank stylesheet is a separate CSS
//   chunk that the agency and agent views never load. Everything in it is
//   scoped to `.bank-root` in any case, so a user who opens the bank tree and
//   then navigates back to /manager sees no change there either.
//
//   The wrapper `<div className="bank-root">` carries no utilities on purpose:
//   `.bank-root .x` only matches descendants (spec §7.2.3).
// ─────────────────────────────────────────────────────────────────────────────
import "../bank.css";
import { useState } from "react";
import { Outlet, useLocation } from "react-router";
import { SidebarInset, SidebarProvider } from "../ui/sidebar";
import { BankErrorBoundary } from "../components/BankErrorBoundary";
import { BankSidebar, type BankPersona } from "./BankSidebar";
import { BankTopBar, type StandingAlert } from "./BankTopBar";
import { RaiseQueryDialog } from "./RaiseQueryDialog";

export interface BankLayoutProps {
  persona: BankPersona;
  onSignOut?: () => void;
  standingAlert?: StandingAlert;
}

export function BankLayout({ persona, onSignOut, standingAlert }: BankLayoutProps) {
  const [queryOpen, setQueryOpen] = useState(false);
  // A render error in one page is caught here, keeping the shell; keyed on the
  // location so moving to another screen (or tab) clears a crashed one.
  const location = useLocation();

  return (
    <div className="bank-root">
      <SidebarProvider defaultOpen={false}>
        <div className="flex min-h-screen w-full bg-background font-sans text-foreground">
          <BankSidebar persona={persona} onSignOut={onSignOut} />
          <SidebarInset className="flex flex-col flex-1 h-screen overflow-hidden py-5 pr-5 gap-4">
            <BankTopBar
              product={persona.product}
              geography={persona.geography}
              standingAlert={standingAlert}
              onRaiseQuery={() => setQueryOpen(true)}
            />
            <main className="app-content flex-1 overflow-y-auto overflow-x-hidden p-4 md:p-5 page-fade-in bg-background w-full rounded-card">
              <div className="w-full mx-auto">
                <BankErrorBoundary key={`${location.pathname}${location.search}`}>
                  <Outlet />
                </BankErrorBoundary>
              </div>
            </main>
          </SidebarInset>
        </div>
      </SidebarProvider>
      <RaiseQueryDialog open={queryOpen} onOpenChange={setQueryOpen} />
    </div>
  );
}
