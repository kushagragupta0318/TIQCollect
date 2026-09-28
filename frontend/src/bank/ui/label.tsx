// Command Center `components/ui/label.jsx`, ported to TypeScript (spec §3.3). Class string verbatim.
import type { ComponentProps } from "react";
import { cn } from "../lib/cn";

export function Label({ className, ...props }: ComponentProps<"label">) {
  return <label className={cn("text-[13px] font-normal text-muted-foreground", className)} {...props} />;
}
