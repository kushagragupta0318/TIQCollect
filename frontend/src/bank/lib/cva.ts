// ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
// 2026-09-24 — New file (task UI03). A local stand-in for
//   `class-variance-authority` 0.7.1, which CC's button.jsx and badge.jsx call.
//
//   WHY NOT THE PACKAGE. It is not a dependency here, and the brief for the port
//   was to prefer none. CC uses only the part reproduced below — `variants` and
//   `defaultVariants` — and this reproduces cva's resolution rules exactly, so
//   the CVA tables in ui/buttonVariants.ts and ui/badgeVariants.ts are CC's,
//   character for character, and produce the same class strings:
//
//     prop === null      → that variant contributes nothing (default ignored)
//     prop undefined     → the default variant applies
//     booleans, 0        → matched as the strings "true" / "false" / "0"
//     class / className  → appended last, in that order
//
//   `compoundVariants` is deliberately absent: no CC primitive uses it. Adding
//   the package later is a drop-in swap — same call sites, same VariantProps.
// ─────────────────────────────────────────────────────────────────────────────

import { clsx, type ClassValue } from "clsx";

type Schema = Record<string, Record<string, ClassValue>>;
type StringToBoolean<T> = T extends "true" | "false" ? boolean : T;
type VariantChoice<S extends Schema> = {
  [V in keyof S]?: StringToBoolean<keyof S[V]> | null | undefined;
};
type ClassProps = { class?: ClassValue; className?: ClassValue };

export interface CvaConfig<S extends Schema> {
  variants?: S;
  defaultVariants?: VariantChoice<S>;
}

const falsyToString = (value: unknown): unknown =>
  typeof value === "boolean" ? `${value}` : value === 0 ? "0" : value;

export function cva<S extends Schema>(base?: ClassValue, config?: CvaConfig<S>) {
  return (props?: VariantChoice<S> & ClassProps): string => {
    if (config?.variants == null) return clsx(base, props?.class, props?.className);
    const { variants, defaultVariants } = config;
    const picked = (Object.keys(variants) as (keyof S)[]).map((variant) => {
      const prop = props?.[variant];
      if (prop === null) return null;
      const key = (falsyToString(prop) || falsyToString(defaultVariants?.[variant])) as string | undefined;
      return key === undefined ? undefined : variants[variant][key];
    });
    return clsx(base, picked, props?.class, props?.className);
  };
}

/** The variant props a `cva(...)` function accepts, minus class/className. */
export type VariantProps<F> = F extends (props?: infer P) => string
  ? Omit<NonNullable<P>, "class" | "className">
  : never;
