/**
 * Every visual token the simulator uses, in one place — matched to what the
 * Collections Command Center actually RENDERS (docs/ui/COMMAND-CENTER-UI-SPEC.md,
 * plan §2.5), not to its design.md, which describes an "Apple Glass" #1677FF
 * look that an unlayered block at the end of CC's index.css overrides.
 *
 * Written with arbitrary values because the scoped `.bank-root` Tailwind build
 * (task UI02) does not exist yet. When it lands, these become CC's own class
 * names (`text-2xl`, `rounded-card`, `btn` variants) inside `.bank-root`.
 *
 *   primary #4F46E5 · accent #EDEDFD · ink #101828 · muted #667085 · label #98A2B3
 *   page #F3F4F6 · card #FFF, border #ECEDF1 (hover #E1E3E9), radius 16, flat shadow
 *   controls radius 10, OUTLINED on white — CC has no filled primary button
 *   system font stack, weights capped at 600; page title 32/40 600 −0.025em
 */
const FONT =
  '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';

const btnBase =
  "inline-flex h-9 items-center justify-center gap-2 whitespace-nowrap rounded-[10px] border bg-white px-3 text-[13px] font-medium transition-colors duration-150 disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#4F46E5]/25 focus-visible:ring-offset-2 [&_svg]:size-3.5 [&_svg]:shrink-0";

export const sim = {
  font: FONT,
  page: "min-h-svh bg-[#F3F4F6] text-[#101828]",
  title: "text-[32px] leading-[40px] font-semibold tracking-[-0.025em] text-[#101828]",
  subtitle: "mt-1 text-[13px] text-[#667085]",
  card: "rounded-2xl border border-[#ECEDF1] bg-white shadow-[0_1px_2px_rgba(16,24,40,0.04)] transition-colors hover:border-[#E1E3E9]",
  sectionLabel: "text-[11px] font-semibold uppercase tracking-[0.06em] text-[#98A2B3]",
  eventRow: "flex gap-2.5 rounded-xl border border-[#ECEDF1] bg-white px-3 py-2",
  // CC buttonVariants: default = outlined primary; outline = neutral.
  buttonPrimary: `${btnBase} border-[#4F46E5] text-[#4F46E5] hover:bg-[#EDEDFD]`,
  button: `${btnBase} border-[#ECEDF1] text-[#101828] hover:bg-[#F7F8FA]`,
  buttonActive: `${btnBase} border-[#F04438] text-[#F04438] hover:bg-[#F04438]/10`,
  segment: "inline-flex rounded-[10px] border border-[#ECEDF1] bg-white p-0.5",
  segmentItem: (active: boolean) =>
    `h-8 rounded-lg px-3 text-[13px] font-medium transition-colors ${active ? "bg-[#EDEDFD] text-[#4F46E5]" : "text-[#667085] hover:text-[#101828]"}`,
  select:
    "h-9 min-w-0 flex-1 rounded-[10px] border border-[#ECEDF1] bg-white px-2 text-[13px] text-[#101828] focus:border-[#4F46E5]/50 focus:outline-none",
  chip: "inline-flex items-center gap-1.5 rounded-full bg-[#F7F8FA] px-2.5 py-1 text-[12px] font-medium text-[#101828]",
  link: "text-[12px] font-medium text-[#4F46E5] hover:underline",
  tone: {
    brand: "#4F46E5",
    good: "#12B76A",
    warn: "#F79009",
    danger: "#F04438",
    muted: "#98A2B3",
  },
  phone: {
    bezel: "#0B0F19",
    bezelPx: 12,
    statusBarPx: 30,
    homeBarPx: 22,
  },
} as const;
