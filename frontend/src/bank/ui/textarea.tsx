// Command Center `components/ui/textarea.jsx`, ported to TypeScript (spec §3.3). Class string verbatim.
import type { ComponentProps } from "react";
import { cn } from "../lib/cn";

export function Textarea({ className, ...props }: ComponentProps<"textarea">) {
  return (
    <textarea
      className={cn(
        "flex min-h-[96px] w-full rounded-control border border-input bg-card px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:border-primary focus-visible:ring-2 focus-visible:ring-primary/20 disabled:cursor-not-allowed disabled:opacity-50 transition-colors",
        className,
      )}
      {...props}
    />
  );
}
