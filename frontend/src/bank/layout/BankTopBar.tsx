// Command Center `components/TopBar.jsx`, ported to TypeScript (spec §2.3).
// Class strings verbatim. The standing alert pill is data-driven: CC shows
// "₹N Cr slipping to NPA" once its pulse loads; the bank passes it once task
// C03 computes projected slippage, and until then the pill is absent — which
// is also what CC renders before its pulse arrives.
import { useState } from "react";
import { Activity, Calendar, LifeBuoy, MapPin, Package } from "lucide-react";
import { chromeDate } from "../theme/format";
import { BankSearchBar } from "./BankSearchBar";
import type { SearchEntry } from "./searchIndex";

export interface StandingAlert {
  /** e.g. "₹38.6 Cr slipping to NPA". */
  label: string;
  /** Native tooltip with the derivation. */
  title?: string;
}

export interface BankTopBarProps {
  product: string;
  geography: string;
  /**
   * CC hard-codes a pulsing "Live System" dot. Here it shows only when the
   * caller has a real signal to put behind it (e.g. the bank feed's last
   * successful ingest); absent, nothing is claimed. Recorded as a deviation
   * in the UI spec §9.
   */
  systemStatus?: { label: string; live: boolean };
  standingAlert?: StandingAlert;
  searchExtra?: SearchEntry[];
  onRaiseQuery?: () => void;
}

export function BankTopBar({ product, geography, systemStatus, standingAlert, searchExtra, onRaiseQuery }: BankTopBarProps) {
  // The chrome date, fixed at mount (CC recomputes it on every render).
  const [today] = useState(() => chromeDate(new Date()));

  return (
    <header className="min-h-14 bg-card border border-border flex items-center justify-between px-5 py-2 shrink-0 gap-4 rounded-card shadow-resting z-30">
      <div className="flex items-center gap-5 shrink-0">
        <div className="flex items-center gap-2">
          <h1 className="text-[15px] font-bold text-foreground">Command Center</h1>
        </div>
        <div className="flex items-center gap-4 text-[12px] text-muted-foreground font-medium">
          {systemStatus && (
            <span className="flex items-center gap-1.5">
              <span className="relative flex h-2 w-2" aria-hidden="true">
                {systemStatus.live && <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-success opacity-75" />}
                <span className={`relative inline-flex rounded-full h-2 w-2 ${systemStatus.live ? "bg-success" : "bg-muted-foreground"}`} />
              </span>
              {systemStatus.label}
            </span>
          )}
          <span className="flex items-center gap-1 capitalize">
            <Package size={11} />
            {product}
          </span>
          <span className="flex items-center gap-1">
            <MapPin size={11} />
            {geography}
          </span>
          <span className="hidden lg:flex items-center gap-1">
            <Calendar size={11} />
            {today}
          </span>
        </div>
      </div>

      <BankSearchBar extra={searchExtra} />

      <div className="flex items-center gap-3 shrink-0">
        <button
          onClick={onRaiseQuery}
          className="flex min-h-10 items-center gap-1.5 bg-card text-foreground border border-input rounded-control px-3 text-[13px] font-medium transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/20"
        >
          <LifeBuoy size={12} />
          Raise a Query
        </button>
        {standingAlert && (
          <div
            className="flex min-h-10 items-center gap-1.5 bg-warning/10 text-foreground rounded-pill px-3 text-[13px] font-medium"
            title={standingAlert.title}
          >
            <span className="size-1.5 rounded-full bg-warning" />
            <Activity size={12} className="text-warning" />
            {standingAlert.label}
          </div>
        )}
      </div>
    </header>
  );
}
