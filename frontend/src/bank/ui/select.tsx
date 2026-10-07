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
          // focus-visible, not focus (a deliberate departure from this
          // file's usual verbatim port): input.tsx and textarea.tsx next to
          // it already use focus-visible, so a mouse click into this one
          // control showed a ring its siblings don't.
          "flex h-10 w-full appearance-none items-center justify-between rounded-control border border-input bg-card px-3 pr-9 py-1 text-sm font-medium text-foreground transition-colors focus-visible:outline-none focus-visible:border-primary focus-visible:ring-2 focus-visible:ring-primary/20 disabled:cursor-not-allowed disabled:opacity-50",
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
