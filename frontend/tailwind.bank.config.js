// ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
// 2026-09-24 — New file (task UI02). The bank portal's own Tailwind build.
//
//   WHY A SECOND CONFIG. Plan §2.5 says every bank screen must look exactly like
//   the Collections Command Center (CC). CC and TIQCollect are both on Tailwind
//   3.4.19, but their themes collide on the same keys: CC's `text-2xl` is 32px
//   where ours is 24px, `rounded-md` 12px against 10px, every `shadow-*` token
//   differs, and both declare shadcn variables of the same name on `:root`
//   (docs/ui/COMMAND-CENTER-UI-SPEC.md §7.1). Copied into our one build, CC's
//   class strings would render wrong. So this is CC's `tailwind.config.js`
//   VERBATIM, compiled only for `src/bank/**` by `src/bank/bank.css`
//   (`@config`), with the four changes spec §7.2 lists plus a fifth it did not
//   foresee (`container: false`, see corePlugins below):
//
//     content    `./src/bank/**` only.
//     important  ".bank-root" — every utility is emitted as `.bank-root .x`,
//                so it matches nothing outside the bank route tree.
//     preflight  off — TIQCollect's preflight (same Tailwind version, same
//                rules) already applies. `@tailwind base` then emits only the
//                `--tw-*` defaults, which are identical to ours.
//     container  off — the only core plugin `important` does not scope.
//     plugins    `tailwindcss-animate` dropped: its global `@keyframes enter`
//                would replace ours and silently change `animate-enter` across
//                the existing app (spec §7.4). No CC composite uses it.
//
//   Keyframes are renamed with a `bank-` prefix because keyframe names are
//   global. The `animation` keys keep CC's names, so `animate-fade-in` etc.
//   copy over verbatim and resolve to the bank's own keyframes.
// ─────────────────────────────────────────────────────────────────────────────

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./src/bank/**/*.{ts,tsx}"],
  important: ".bank-root",
  // `container` is the one core plugin in Tailwind's components layer, and
  // `important` does not wrap components — so it came out as a bare, global
  // `.container` (caught by bankCssScope.test.ts; the word appears in any
  // comment or `containerRef`). CC's pages do not use it (spec §1.12).
  corePlugins: { preflight: false, container: false },
  theme: {
    container: {
      center: true,
      padding: "1rem",
      screens: {
        "2xl": "1400px",
      },
    },
    extend: {
      fontFamily: {
        // Native system stack — no webfont loading, no flash, instant render.
        // SF Pro on macOS/iOS, Segoe UI on Windows, Roboto on Android/ChromeOS.
        sans: [
          '-apple-system',
          'BlinkMacSystemFont',
          '"SF Pro Text"',
          '"Segoe UI"',
          'Roboto',
          'Helvetica',
          'Arial',
          'sans-serif',
        ],
        display: [
          '-apple-system',
          'BlinkMacSystemFont',
          '"SF Pro Display"',
          '"Segoe UI"',
          'Roboto',
          'Helvetica',
          'Arial',
          'sans-serif',
        ],
        mono: [
          'ui-monospace',
          '"SF Mono"',
          'SFMono-Regular',
          'Menlo',
          'Consolas',
          'monospace',
        ],
      },
      fontSize: {
        '2xs': ['0.625rem', { lineHeight: '0.875rem' }], // 10px
        xs:   ['0.75rem',  { lineHeight: '1rem' }],      // 12px
        sm:   ['0.875rem', { lineHeight: '1.25rem' }],   // 14px
        base: ['1rem',     { lineHeight: '1.5rem' }],    // 16px
        lg:   ['1.25rem',  { lineHeight: '1.75rem' }],   // 20px
        xl:   ['1.5rem',   { lineHeight: '2rem' }],      // 24px
        '2xl':['2rem',     { lineHeight: '2.5rem' }],    // 32px
      },
      colors: {
        // shadcn semantic tokens (driven by CSS variables in bank.css)
        border:     "hsl(var(--border))",
        input:      "hsl(var(--input))",
        ring:       "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT:    "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
          dark:       "hsl(var(--primary-dark))",
        },
        teal:         "hsl(var(--teal))",
        secondary: {
          DEFAULT:    "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT:    "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        success: {
          DEFAULT:    "hsl(var(--success))",
          foreground: "hsl(var(--success-foreground))",
        },
        warning: {
          DEFAULT:    "hsl(var(--warning))",
          foreground: "hsl(var(--warning-foreground))",
        },
        muted: {
          DEFAULT:    "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT:    "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT:    "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT:    "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },

        // Legacy brand-* aliases — kept so existing markup re-skins automatically.
        brand: {
          navy:       '#1677ff',
          blue:       '#1677ff',
          success:    '#16A34A',
          positive:   '#16A34A',
          negative:   '#DC2626',
          bgApp:      '#F5F7FA',
          bgContent:  '#FFFFFF',
          dark:       '#111827',
          muted:      '#6B7280',
          border:     '#E5E7EB',
        },
      },
      borderRadius: {
        // Design-system named radii (design.md, validated against reference mockup):
        //   card    = 22px  — panels, cards, dropdowns
        //   control = 12px  — inputs, buttons, small interactive
        //   modal   = 26px  — modals, sheets, largest containers
        //   pill    = 9999px — badges, status chips
        // Nesting rule: parent radius > child radius always.
        // (As RENDERED these are 16 / 10 / 16px: bank.css ports CC's !important
        //  Soft Card overrides, exactly as CC's index.css does — spec §1.9.)
        'card':    '1.375rem',  // 22px
        'control': '0.75rem',   // 12px
        'modal':   '1.625rem',  // 26px
        'pill':    '9999px',
        // shadcn compat aliases (map to design system values)
        'lg':  'var(--radius)',     // 22px via CSS var — cards, panels
        'md':  '0.75rem',          // 12px — control radius, hardcoded (not calc-derived)
        'sm':  '0.5rem',           // 8px  — innermost controls inside modals
      },
      boxShadow: {
        // Two-level elevation system only — no shadow-lg/xl/2xl in this product.
        'resting': '0 1px 2px rgba(17,24,39,0.03), 0 1px 3px rgba(17,24,39,0.04)',
        'hover':   '0 4px 16px rgba(22,119,255,0.12), 0 1px 3px rgba(17,24,39,0.05)',
        // Legacy aliases kept for backward compat
        'premium': '0 4px 12px var(--tw-shadow-color, rgba(0,0,0,0.08))',
        'glow':    '0 0 0 3px rgba(22,119,255,0.1)',
        'card':    '0 1px 2px var(--tw-shadow-color, rgba(17,24,39,0.04)), 0 1px 3px var(--tw-shadow-color, rgba(17,24,39,0.06))',

        // Tinted shadows mapped to design.md — resting and hover states
        'tint-primary':       '0 2px 8px rgba(22,119,255,0.08), 0 1px 3px rgba(22,119,255,0.04)',
        'tint-primary-hover': '0 8px 24px rgba(22,119,255,0.18), 0 2px 6px rgba(22,119,255,0.08)',
        'tint-warning':       '0 2px 8px rgba(217,119,6,0.08), 0 1px 3px rgba(217,119,6,0.04)',
        'tint-destructive':   '0 2px 8px rgba(220,38,38,0.08), 0 1px 3px rgba(220,38,38,0.04)',
        'tint-success':       '0 2px 8px rgba(22,163,74,0.08), 0 1px 3px rgba(22,163,74,0.04)',
      },
      spacing: {
        '4.5': '1.125rem',
      },
      transitionTimingFunction: {
        // The one and only easing curve in this design system (design.md).
        'spring': 'cubic-bezier(0.16, 1, 0.3, 1)',
      },
      keyframes: {
        // The one easing curve: cubic-bezier(0.16, 1, 0.3, 1)
        // Applied to all entrances, all toggles, all route transitions.
        // bank- prefix: keyframe names are global (see header).
        "bank-accordion-down": {
          from: { height: "0" },
          to:   { height: "var(--radix-accordion-content-height)" },
        },
        "bank-accordion-up": {
          from: { height: "var(--radix-accordion-content-height)" },
          to:   { height: "0" },
        },
        "bank-slide-in": {
          from: { opacity: "0", transform: "translateY(8px)" },
          to:   { opacity: "1", transform: "translateY(0)" },
        },
        "bank-fade-in": {
          from: { opacity: "0" },
          to:   { opacity: "1" },
        },
        "bank-scale-in": {
          from: { opacity: "0", transform: "scale(0.95) translateY(8px)" },
          to:   { opacity: "1", transform: "scale(1) translateY(0)" },
        },
        "bank-zoom-in-95": {
          from: { opacity: "0", transform: "scale(0.95)" },
          to:   { opacity: "1", transform: "scale(1)" },
        },
        "bank-card-enter": {
          from: { opacity: "0", transform: "translateY(16px)" },
          to:   { opacity: "1", transform: "translateY(0)" },
        },
        "bank-sidebar-collapse": {
          from: { opacity: "1", height: "var(--sidebar-section-height)" },
          to:   { opacity: "0", height: "0" },
        },
      },
      animation: {
        // All use the spec easing curve. Duration bands from design.md:
        //   micro: 150-200ms (hover, toggle)
        //   entrance: 300-450ms (cards, panels)
        "accordion-down": "bank-accordion-down 200ms cubic-bezier(0.16, 1, 0.3, 1)",
        "accordion-up":   "bank-accordion-up 200ms cubic-bezier(0.16, 1, 0.3, 1)",
        "slide-in":       "bank-slide-in 300ms cubic-bezier(0.16, 1, 0.3, 1) both",
        "fade-in":        "bank-fade-in 200ms cubic-bezier(0.16, 1, 0.3, 1) both",
        "scale-in":       "bank-scale-in 280ms cubic-bezier(0.16, 1, 0.3, 1) both",
        "zoom-in-95":     "bank-zoom-in-95 200ms cubic-bezier(0.16, 1, 0.3, 1) both",
        "card-enter":     "bank-card-enter 400ms cubic-bezier(0.16, 1, 0.3, 1) both",
      },
    },
  },
  plugins: [],
}
