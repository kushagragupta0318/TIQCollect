// Command Center `components/ui/separator.jsx`, ported to TypeScript (spec §3.3). Class strings verbatim.
import type { ComponentProps } from "react";
import { cn } from "../lib/cn";

export function Separator({
  className,
  orientation = "horizontal",
  ...props
}: ComponentProps<"div"> & { orientation?: "horizontal" | "vertical" }) {
  return (
    <div
      role="separator"
      aria-orientation={orientation}
      className={cn("shrink-0 bg-border", orientation === "horizontal" ? "h-px w-full" : "h-full w-px", className)}
      {...props}
    />
  );
}
