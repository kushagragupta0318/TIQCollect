// Command Center `components/ui/select.jsx`, ported to TypeScript (spec §3.3).
// A native <select>, as in CC — no custom listbox. Class strings verbatim.
import type { ComponentProps } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "../lib/cn";

export function Select({ className, children, ...props }: ComponentProps<"select">) {
  return (
    <div className="relative">
      <select
        className={cn(
          "flex h-10 w-full appearance-none items-center justify-between rounded-control border border-input bg-card px-3 pr-9 py-1 text-sm font-medium text-foreground transition-colors focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/20 disabled:cursor-not-allowed disabled:opacity-50",
          className,
        )}
        {...props}
      >
        {children}
      </select>
      <ChevronDown
        size={14}
        className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground"
      />
    </div>
  );
}
