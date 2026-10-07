// Command Center `components/ui/button.jsx:5-40`, verbatim (spec §3.1).
// Every variant is an OUTLINED, white-fill button — CC has no filled primary.
// Kept apart from button.tsx so that file exports only a component
// (react-refresh/only-export-components; spec §7.5).
import { cva, type VariantProps } from "../lib/cva";

export const buttonVariants = cva(
  // tap-target: a touch/narrow-viewport-only 44px floor (index.css) — every
  // size variant below is already that tall on desktop, so this only grows
  // the smaller ones (xs/sm) where they actually need it, same floor
  // manager's own buttons apply by hand per button.
  // motion-safe:hover/active: a 1px lift and a press-in, gated so a
  // prefers-reduced-motion user gets the state change with no transform at
  // all rather than a cancelled one (motion-reduce: would still lose a
  // same-specificity race against the hover/active rules it's meant to beat).
  "tap-target inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-control border bg-card text-sm font-medium transition-[color,background-color,transform] duration-150 motion-safe:hover:-translate-y-px motion-safe:active:translate-y-0 motion-safe:active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/25 focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default:
          "border-primary text-primary hover:bg-accent",
        destructive:
          "border-destructive text-destructive hover:bg-destructive/10",
        outline:
          "border-input text-foreground hover:bg-muted",
        secondary:
          "border-secondary text-secondary hover:bg-secondary/10",
        ghost:
          "border-transparent text-muted-foreground hover:bg-muted hover:text-foreground",
        link:
          "border-transparent text-primary underline-offset-4 hover:underline",
        success:
          "border-success text-[#067647] hover:bg-success/10",
        warning:
          "border-warning text-[#B54708] hover:bg-warning/10",
      },
      size: {
        default: "h-10 px-4 py-2 [&_svg]:size-4",
        sm: "h-9 px-3 text-[13px] [&_svg]:size-3.5",
        lg: "h-11 px-5 text-sm [&_svg]:size-4",
        xs: "h-8 px-2.5 text-[12px] [&_svg]:size-3",
        icon: "h-10 w-10 [&_svg]:size-4",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
);

export type ButtonVariantProps = VariantProps<typeof buttonVariants>;
