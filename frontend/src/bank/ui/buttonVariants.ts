// Command Center `components/ui/button.jsx:5-40`, verbatim (spec §3.1).
// Every variant is an OUTLINED, white-fill button — CC has no filled primary.
// Kept apart from button.tsx so that file exports only a component
// (react-refresh/only-export-components; spec §7.5).
import { cva, type VariantProps } from "../lib/cva";

export const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-control border bg-card text-sm font-medium transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/25 focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0",
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
