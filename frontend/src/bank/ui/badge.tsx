// Command Center `components/ui/badge.jsx`, ported to TypeScript (spec §3.2). Renders a <div>.
import type { ComponentProps } from "react";
import { cn } from "../lib/cn";
import { badgeVariants, type BadgeVariantProps } from "./badgeVariants";

export type BadgeProps = ComponentProps<"div"> & BadgeVariantProps;

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}
