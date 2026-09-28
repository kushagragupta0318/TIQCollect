// Command Center `components/ui/button.jsx`, ported to TypeScript (spec §3.1).
import type { ComponentProps } from "react";
import { cn } from "../lib/cn";
import { buttonVariants, type ButtonVariantProps } from "./buttonVariants";

export type ButtonProps = ComponentProps<"button"> & ButtonVariantProps & { asChild?: boolean };

export function Button({ className, variant, size, asChild = false, ...props }: ButtonProps) {
  // CC accepts `asChild` and discards it — its `Comp` is always "button"
  // (button.jsx:44). Kept for parity, so a CC call site ports unchanged.
  void asChild;
  return <button className={cn(buttonVariants({ variant, size, className }))} {...props} />;
}
