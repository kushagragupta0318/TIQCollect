// Command Center `components/ui/badge.jsx:5-32`, verbatim (spec §3.2).
// The text is always ink; status reads from the fill colour alone.
import { cva, type VariantProps } from "../lib/cva";

export const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-full border-0 px-2.5 py-1 text-[13px] font-medium text-foreground transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
  {
    variants: {
      variant: {
        default:
          "bg-accent",
        secondary:
          "bg-[#ECE9FE]",
        destructive:
          "bg-[#FDE7E6]",
        outline: "border border-input bg-card text-foreground",
        success:
          "bg-[#DCF5E9]",
        warning:
          "bg-[#FDF0DC]",
        // Desaturated tints — useful for KPI deltas and chips
        softPrimary: "badge-premium-blue",
        softSuccess: "badge-premium-green",
        softDanger:  "badge-premium-red",
        softWarning: "badge-premium-amber",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
);

export type BadgeVariantProps = VariantProps<typeof badgeVariants>;
