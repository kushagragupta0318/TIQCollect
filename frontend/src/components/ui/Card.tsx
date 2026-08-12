import { clsx } from "clsx";
import type { ReactNode } from "react";

interface CardProps {
  children: ReactNode;
  className?: string;
  onClick?: () => void;
  hover?: boolean;
}

export function Card({ children, className, onClick, hover }: CardProps) {
  return (
    <div
      className={clsx("card", hover && "cursor-pointer hover:bg-[#F7F8FA] transition-colors", className)}
      onClick={onClick}
    >
      {children}
    </div>
  );
}

export function StatCard({ label, value, subtext, icon, colorClass = "text-brand-600", onClick }: {
  label: string;
  value: string | number;
  subtext?: string;
  icon?: ReactNode;
  colorClass?: string;
  onClick?: () => void;
}) {
  return (
    <div
      className={clsx("card", onClick && "cursor-pointer hover:bg-[#F7F8FA] transition-colors")}
      onClick={onClick}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
    >
      {/* At 360px a 2-col KPI grid gives each tile ~148px. Without min-w-0 the
          label's intrinsic width pushes the icon out of the card; the value
          scales via --kpi-value so long figures like "₹12.5L" still fit. */}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          {/* Wraps rather than truncates. At 360px a 2-col tile gives the label
              ~100px and "AGENTS ON DUTY" needs ~105px, so truncating clipped it
              to "AGENTS O…" — losing information the desktop layout shows. */}
          <p className="text-[11px] font-semibold uppercase tracking-[0.06em] text-[#98A2B3] leading-snug">{label}</p>
          <p
            className={clsx("font-bold mt-1 leading-tight", colorClass)}
            style={{ fontSize: "var(--kpi-value)" }}
          >
            {value}
          </p>
          {subtext && <p className="mt-1 truncate text-[13px] text-[#667085]">{subtext}</p>}
        </div>
        {icon && <div className={clsx("flex-shrink-0 rounded-control bg-brand-50 p-2 text-primary", colorClass)}>{icon}</div>}
      </div>
    </div>
  );
}
