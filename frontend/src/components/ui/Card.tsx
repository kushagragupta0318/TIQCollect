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
      className={clsx("card", hover && "cursor-pointer hover:shadow-md hover:border-slate-200 transition-shadow", className)}
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
      className={clsx("card", onClick && "cursor-pointer active:scale-[0.97] transition-transform")}
      onClick={onClick}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
    >
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs font-medium text-slate-500 uppercase tracking-wide">{label}</p>
          <p className={clsx("text-2xl font-bold mt-1", colorClass)}>{value}</p>
          {subtext && <p className="text-xs text-slate-400 mt-0.5">{subtext}</p>}
        </div>
        {icon && <div className={clsx("p-2 rounded-lg bg-slate-50", colorClass)}>{icon}</div>}
      </div>
    </div>
  );
}
