# Command Center UI specification (port-ready)

Task **UI01** of [STANDALONE-TASKS.md](../STANDALONE-TASKS.md). This file supports the §2.5 rule of
[STANDALONE-PRODUCT-PLAN.md](../STANDALONE-PRODUCT-PLAN.md): every new surface (the bank portal under `/bank/*` and the
`/simulator` chrome) must look exactly like the Collections product's Command Center (CC).

**Source.** This spec was read from, and nothing in these locations was modified:
- `collections-platform/command-center/frontend`. The `COLLECTIONS` branch's working tree is clean. `src/` last changed at
  commit `9e7e6a8` (2026-08-19), and HEAD is `1758448` (2026-09-21).
- `collections-platform/shared/design.md`.

In this file, every `src/...` path is relative to `command-center/frontend/`.

**Method.** Each value below was read from source, then checked against the CSS that Tailwind actually emits. The CC
stylesheet was compiled with CC's own `tailwindcss` 3.4.19 and `tailwind.config.js` into a scratch file, and nothing was
written into either repo. That second step matters, because the rendered values differ from the declared ones, as §0
explains.

---

## 0. Read this first: the design system as rendered is not the one declared

CC has **three styling layers**, and the one that renders is the last:

| Layer | Where | Says | Renders? |
|---|---|---|---|
| "Apple Glass" design doc | `shared/design.md:1-44` | primary `#1677FF`, cards 22px, glass `rgba(255,255,255,0.68)` + 20px blur, bold 700-800 numerals, hover lift, tinted shadows | **No** |
| Apple Glass tokens | `src/index.css:9-51` (`@layer base :root`), `@layer components` 75-265, `tailwind.config.js` | same values as the doc | **Partly.** These are overridden |
| **"Soft Card UI theme" compatibility layer** | `src/index.css:398-650` (unlayered, last in file) | primary indigo `#4F46E5`, cards 16px, **no glass**, **no hover lift**, flat 1px shadows, **max weight 600**, outlined buttons | **Yes, this is what renders** |

The compiled CSS confirms the order. The Soft Card `:root` sits at compiled line 5387, after every layered rule.
Tailwind 3 then appends its **variant** rules (`hover:`, `focus-visible:`, `group-hover:`, `md:`…`xl:`, `dark:`)
**after** the Soft Card block, at compiled lines 5753-6600. So a variant utility still wins over a Soft Card class of
equal specificity.

To look *exactly* like CC, port the CSS **verbatim, in the same order**, so the cascade resolves the same way. Do not
re-derive the look from `design.md`. The rest of this document gives the effective value first and the declared value
second.

Ten further facts that "exactly like CC" depends on. Each was checked in source or in the compiled CSS:

1. **`animate-slide-up` is undefined** anywhere: not in the config, not in the CSS, and not in `tailwindcss-animate`. So
   the KPI cards (`PulseKpiFlow.jsx:46`), the alert cards (`DecisionAlerts.jsx:160`), the context strip
   (`AIDecisionCenter.jsx:31`) and the search dropdown (`SearchBar.jsx:209`) **do not animate in**, and their inline
   `animationDelay` staggers do nothing.
2. **The ambient background blobs are invisible.** `App.jsx:87-122` puts two blurred blobs at `position:fixed; z-index:-1`.
   The opaque `bg-background` flex container at `App.jsx:124` paints over them.
3. **Default table rules beat the utilities on most tables.** `table:not(.tiq-table) th|td|tbody tr:hover`
   (`index.css:232-243`) has specificity (0,1,2) or (0,2,3), which beats `.pb-2.5`, `.py-3`, `.font-medium` and
   `.hover:bg-accent/50`. The effects:
   - Every such `th` renders **padding-bottom 12px and weight 600**.
   - Every `td` renders **padding-y 10px**.
   - Every row hover renders **`hsl(var(--muted)/0.3)`**, whatever hover class the row carries.
4. **Weights above 600 do not exist.** `.font-bold, .font-extrabold, .font-black { font-weight: 600 !important }`
   (`index.css:646-650`).
5. **Monospace does not exist.** `.font-mono, .font-display { font-family: inherit !important }` (`index.css:641-644`).
   Account IDs render in the system sans.
6. **Transitions use one curve, and it is not the design-doc curve.** `[class*="transition-"] { transition-timing-function: cubic-bezier(0.2, 0, 0, 1) }`
   (`index.css:430-432`) overrides both the layered `(0.16,1,0.3,1)` rule and any `ease-in-out` / `ease-out` utility on
   the same element. Keyframe **animations** still use `cubic-bezier(0.16, 1, 0.3, 1)`.
7. **The KPI `tone` field is ignored.** The pulse API sends `tone` (`critical` / `warning` / `success` / `neutral`,
   `backend/engines/portfolio_pulse.py:314-319`), but no frontend file reads it. All KPI cards look the same. Only the
   trend line is coloured.
8. **`/field`, `/field/analytics` and `/field/cases` are a scoped island** in the *old* TIQCollect look: `#1677FF`,
   `.fo-analytics` (`src/pages/FieldAnalytics.css:1-13`). They are not the core CC look.
9. **CC is light-only.** There is no `darkMode` config and no dark variables. The only `dark:` classes are 10 stray ones
   in `AICoPilot.jsx`. They fire under OS dark mode, because Tailwind 3 defaults to `media`.
10. **`.app-content > .p-6 { padding: 0 }` (`index.css:493`) never matches.** Page roots are grandchildren of `main`, so
    lifecycle pages keep their `p-6`.

---

## 1. Tokens

### 1.1 CSS custom properties: effective values

These are the effective values, after `index.css:401-423` overrides `index.css:10-51`. The hex column was computed from
the HSL.

| Variable | Effective HSL | ≈ Hex | Source | Declared earlier (not rendered) |
|---|---|---|---|---|
| `--primary` | `243 75% 59%` | `#5048E5` (literal twin `#4F46E5`) | `index.css:402` | `213 100% 54%` / `#1677FF` (:12) |
| `--primary-foreground` | `0 0% 100%` | `#FFFFFF` | :403 | same |
| `--primary-dark` | `245 58% 51%` | `#463ACB` | :404 | `217 88% 37%` (:26) |
| `--secondary` | `255 91% 66%` | `#8159F7` (literal twin `#7A5AF8`) | :405 | `258 90% 66%` (:14) |
| `--secondary-foreground` | `0 0% 100%` | `#FFFFFF` | :15 | — |
| `--success` | `153 82% 39%` | `#12B56C` (literal twin `#12B76A`) | :406 | `142 71% 36%` (:18) |
| `--success-foreground` | `0 0% 100%` | | :19 | — |
| `--warning` | `35 93% 50%` | `#F69309` (literal twin `#F79009`) | :407 | `32 95% 44%` (:20) |
| `--warning-foreground` | `20 14% 12%` | `#231D1A` | :21 | — |
| `--destructive` | `4 86% 58%` | `#F04438` | :408 | `0 72% 51%` (:22) |
| `--destructive-foreground` | `0 0% 100%` | | :23 | — |
| `--teal` | `175 84% 32%` | `#0D968B` | :27 | — |
| `--background` | `220 13% 96%` | `#F3F4F6` | :409 | `240 15% 98%` (:30) |
| `--foreground` | `220 43% 11%` | `#101828` | :410 | `240 10% 12%` (:31) |
| `--card` | `0 0% 100%` | `#FFFFFF` | :411 | — |
| `--card-foreground` | `240 10% 12%` | `#1C1C22` | :33 (**not** overridden) | — |
| `--popover` / `--popover-foreground` | `0 0% 100%` / `240 10% 12%` | | :34-35 | — |
| `--muted` | `220 18% 97%` | `#F6F7F9` | :412 | `240 5% 95%` (:36) |
| `--muted-foreground` | `218 15% 46%` | `#647187` (literal twin `#667085`) | :413 | `240 4% 46%` (:37) |
| `--accent` | `242 80% 96%` | `#EDEDFD` (literal twin `#EEEDFD`) | :414 | `213 100% 96%` (:38) |
| `--accent-foreground` | `243 75% 59%` | `#5048E5` | :415 | `213 100% 32%` (:39) |
| `--border` | `230 14% 94%` | `#EEEEF2` (literal twin `#ECEDF1`) | :416 | `240 6% 90%` (:40) |
| `--input` | `225 15% 90%` | `#E2E4E9` (literal twin `#E1E3E9`) | :417 | `240 6% 90%` (:41) |
| `--ring` | `243 75% 59%` | `#5048E5` | :418 | `213 100% 54%` (:42) |
| `--radius` | `1rem` (16px) | | :419 | `1.375rem` (:45) |
| `--color-bg-app` / `--color-bg-content` / `--color-primary` | `#F4F5F7` / `#FFFFFF` / `#4F46E5` | | :420-422 | `#FAFAFC` / `#FFFFFF` / `#1677ff` |

Global base rules:
- `* { border-color: hsl(var(--border) / 0.6); }` (`index.css:53-55`), so a bare `border` renders at 60% of `#EEEEF2`.
- `body`: `bg-background text-foreground font-sans antialiased`, `font-feature-settings: "rlig" 1, "calt" 1`
  (`:61-66`). The effective `line-height: 1.5; letter-spacing: 0` comes from `:425-428`; it overrides the earlier
  `-0.011em`.

Tailwind maps each semantic name to its variable (`tailwind.config.js:55-98`): `border input ring background foreground
primary{DEFAULT,foreground,dark} teal secondary destructive success warning muted accent popover card`, each as
`"hsl(var(--x))"`. The `/NN` opacity modifiers therefore work, as in `bg-primary/10`.

### 1.2 Literal hexes in the Soft Card layer (`index.css:434-626`)

| Use | Value |
|---|---|
| Card surface / border / hover border | `#FFFFFF` / `#ECEDF1` / `#E1E3E9` |
| Table header background, secondary-button hover, `.alert-detail` rows | `#F7F8FA` |
| Muted label text (`.panel-title`, `th`, `.tiq-label`, `.hf-l`) | `#98A2B3` |
| Ink (`td`, `.tiq-input`, badges, `.hf-v`) | `#101828` |
| Primary button border and text / hover fill | `#4F46E5` / `#EEEDFD` |
| Badge fills (blue / green / red / amber) | `#E8F1FE` / `#DCF5E9` / `#FDE7E6` / `#FDF0DC` |
| Row divider in `.alert-detail` | `#ECEDF1` |

### 1.3 `BRAND`: the chart and SVG palette (`src/lib/colors.js:6-17`), verbatim

```js
export const BRAND = {
  primary: "#4F46E5",
  secondary: "#7A5AF8",
  success: "#12B76A",
  warning: "#F79009",
  destructive: "#F04438",
  slate: "#667085",
  ink: "#101828",
  muted: "#98A2B3",
  grid: "#ECEDF1",
  axis: "#98A2B3",
};
```

### 1.4 DPD bucket colours (`colors.js:21-32`), verbatim

```js
export const DPD_COLORS = {
  Current: "#16A34A",
  "0-30": "#1677FF",
  "1-30": "#1677FF",
  "31-60": "#F59E0B",
  "30-60": "#F59E0B",
  "61-90": "#F97316",
  "60-90": "#F97316",
  "90-180": "#EF4444",
  "180+": "#B91C1C",
  NPA: "#1F2937",
};
```

How the bucket colours are applied:

| Where | Text | Background / fill |
|---|---|---|
| Bucket chip | the hex | hex + `15` (≈8% alpha). Class `text-[10px] font-semibold px-2 py-0.5 rounded`; in drill and alert tables, `text-[9px] font-bold px-1.5 py-0.5 rounded` (`PortfolioAnalytics.jsx:151-152,744-745`, `DrillPanel.jsx:139-140`) |
| Funnel bar | the hex | `${color}22` with `borderLeft: 3px solid ${color}` (`PortfolioAnalytics.jsx:119`) |
| Heat grid cell | — | `${DPD}${hex(round(intensity*40+8))}`, an alpha of 0x08-0x30 (`:208`) |

### 1.5 Series, pie and risk palettes (`colors.js:35-76`), verbatim

```js
export const CHART_SERIES = ["#2563EB","#F59E0B","#06B6D4","#DB2777","#059669","#7C3AED","#475569"];
export const PIE_COLORS   = ["#2563EB","#F59E0B","#06B6D4","#DB2777","#059669","#7C3AED","#475569"];
export const RISK_COLORS = { Low:"#53B1FD","Low Risk":"#53B1FD", Medium:"#8098F9","Medium Risk":"#8098F9",
                             High:"#FEC84B","High Risk":"#FEC84B", Critical:"#F97066" };
export const RISK_SOFT_COLORS = { Low:"#EFF8FF","Low Risk":"#EFF8FF", Medium:"#F0F3FF","Medium Risk":"#F0F3FF",
                                  High:"#FFFAEB","High Risk":"#FFFAEB", Critical:"#FFF1F0" };
```

### 1.6 Status and severity

- **Grade thresholds.** From `src/lib/chartTheme.js:65-66`:
  `gradeColor = (pct, { good = 40, fair = 15 } = {}) => (pct >= good ? BRAND.success : pct >= fair ? BRAND.warning : BRAND.destructive)`.
  The same 40 / 15 split is inlined everywhere efficiency is shown. The exceptions:

  | Metric | Thresholds | Source |
  |---|---|---|
  | Coverage | 60 / 35 | `PortfolioAnalytics.jsx:625` |
  | State efficiency | 30 / 18 | `:679` |
  | Channel cost per ₹100 | ≤1 / ≤25 | `:582` |

- **Alert severity.** Verbatim from `DecisionAlerts.jsx:14-27`:

  | Severity | Icon (lucide) | Icon chip | Icon colour | Card background when open | Accent |
  |---|---|---|---|---|---|
  | critical | `AlertCircle` | `bg-[#FDE7E6]` | `text-[#B42318]` | `bg-[#FFF8F7]` | `BRAND.destructive` |
  | warning | `AlertTriangle` | `bg-[#FDF0DC]` | `text-[#B54708]` | `bg-[#FFFBF5]` | `BRAND.warning` |
  | info | `Info` | `bg-[#E8F1FE]` | `text-[#2E90FA]` | `bg-[#F7FAFF]` | `BRAND.primary` |

- **Workshop status badges.** From `src/pages/TabHome.jsx:49-55`:
  - `LiveBadge`: `text-[9px] font-bold bg-success/10 text-success px-2 py-0.5 rounded-full uppercase tracking-wider`, text "Production Live".
  - `SimBadge`: the same class with `bg-warning/10 text-warning`.
- **Critical KPI background**: `bg-[#FFF8F7]` (`KPICard.jsx:76`).

### 1.7 Legacy and island colours (port only if the markup that uses them is ported)

- `tailwind.config.js:101-112` defines `brand.navy` and `brand.blue` as `#1677ff`, plus `success`/`positive #16A34A`,
  `negative #DC2626`, `bgApp #F5F7FA`, `bgContent #FFFFFF`, `dark #111827`, `muted #6B7280` and `border #E5E7EB`. These
  appear only on the DPD, outreach and negotiation pages.
- The `/field` island (`FieldAnalytics.css:138-147`) uses:

  | Token | Hex |
  |---|---|
  | brand | `#1677FF` |
  | success | `#16A34A` |
  | warning | `#D97706` |
  | danger | `#DC2626` |
  | text | `#1C1C1F` |
  | label | `#6B6D76` |
  | line | `#EAEBEF` |
  | link | `#0C66E4` |

  Agency colours (`src/pages/fieldOverviewData.js:481-487`):

  | Agency | Hex |
  |---|---|
  | ABC | `#2E86DE` |
  | XYZ | `#16A34A` |
  | PQR | `#D97706` |
  | LMN | `#8B5CF6` |
  | RST | `#DC2626` |

  Line colours: collection `#2E86DE`, PTP `#C7CFDB` (`:507-508`). Rate colours (`:564-567`): ≥90 `#16A34A`, ≥75 `#D97706`,
  otherwise `#DC2626`.

### 1.8 Typography

- **Family: the native system stack, with no webfont loaded.** No `<link>` and no `@font-face` exist anywhere; see
  `index.html` and `index.css:1-3`. From `tailwind.config.js:16-43`:
  - `sans`: `-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, Helvetica, Arial, sans-serif`.
  - `display` swaps in `"SF Pro Display"`.
  - `mono` is `ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, monospace`, but `font-mono` and `font-display`
    render as `inherit` (see §0).
  - On Windows this resolves to **Segoe UI**.
- **Size tokens** (`tailwind.config.js:45-53`). These differ from Tailwind defaults at `lg`, `xl` and `2xl`:

  | Class | Size / line-height |
  |---|---|
  | `2xs` | 10 / 14 |
  | `xs` | 12 / 16 |
  | `sm` | 14 / 20 |
  | `base` | 16 / 24 |
  | **`lg`** | **20 / 28** |
  | **`xl`** | **24 / 32** |
  | **`2xl`** | **32 / 40** |

- **Arbitrary sizes, as used across `src`** (count in brackets): 9px (121), 9.5 (34), **10 (344)**, 10.5 (10), 11 (137),
  11.5 (14), 12 (48), 12.5 (6), 13 (51), 14 (11), 15 (6), 16, 17, 18, 19, 22, 23, 24, 26, 36.
- **The type scale on the core surfaces:**

  | Element | Class | Renders |
  |---|---|---|
  | Page title | `text-2xl font-bold tracking-tight` | **32/40, 600, −0.025em** |
  | Tool or lifecycle page title | `text-xl font-bold` | 24/32, 600 |
  | Section heading (e.g. "Portfolio Analytics") | `text-[17px] font-bold tracking-tight` | 17, 600 |
  | Top-bar product name | `text-[15px] font-bold` | 15, 600 |
  | Drawer / modal title | `text-lg font-extrabold` | 20/28, 600 |
  | Card title (primitive) | `text-[19px] font-semibold leading-[1.35] tracking-tight` | 19, 600 |
  | Panel title | `text-[12.5px] font-semibold tracking-tight` | 12.5, 600 |
  | Eyebrow / section caption | `text-[11px] font-bold uppercase tracking-widest` | 11, 600, 0.1em |
  | KPI value | `text-[23px] font-bold leading-none tracking-tight tabular-nums` | 23, 600 |
  | Tile value | `text-[22px]` / drill stat `text-[18px]`, `font-bold` | 600 |
  | KPI label | `text-[11.5px] font-medium` | 500 |
  | Body copy / narrative | `text-[13px] font-normal leading-relaxed` | 400, lh 1.625 |
  | Table header | `uppercase text-[9.5px] tracking-wide` | 9.5, 600 (forced), 0.025em |
  | Table cell | `text-[11px]` | 11 |

- **Weights that exist:** 400, 500, 600. `bold`, `extrabold` and `black` all render as 600.
- **Tracking:** `tracking-wider` (274 uses), `tight` (49), `widest` (37), `wide` (32), `[0.06em]` (6). The body is 0.

### 1.9 Radii

| Token | Declared (`tailwind.config.js:114-129`) | **Rendered** | Why |
|---|---|---|---|
| `rounded-card` | 1.375rem (22px) | **16px** | `index.css:434` `!important` |
| `rounded-control` | 0.75rem (12px) | **10px** | `:436` `!important` |
| `rounded-modal` | 1.625rem (26px) | **16px** | `:437` `!important` |
| `rounded-inner` | — (not a Tailwind token) | **12px** | `:435`, a plain CSS class with `!important` |
| `rounded-pill` | 9999px | 9999px | |
| `rounded-lg` | `var(--radius)` | **16px** | `--radius: 1rem` |
| `rounded-md` / `rounded-sm` | 0.75rem / 0.5rem | 12px / 8px | |
| `rounded-xl` / `rounded-2xl` / `rounded` | Tailwind defaults | 12px / 16px / 4px | |
| `.panel`, `.card-base` | `var(--radius)` | **16px `!important`** (beats `rounded-[22px]` on the same element) | `:524-531` |

Inline radius overrides that win over the tokens:
- `WorkspaceModal` uses `borderRadius: '26px'` (`WorkspaceModal.jsx:118`).
- The Full Portfolio Matrix modal also uses `26px` (`RiskSimulator.jsx:872`).
- `RaiseQueryModal` uses `rounded-[22px]`.

### 1.10 Shadows

| Name | Value | Where defined | Used on |
|---|---|---|---|
| `shadow-resting` | `0 1px 2px rgba(17,24,39,0.03), 0 1px 3px rgba(17,24,39,0.04)` | `tailwind.config.js:132` | Sidebar, TopBar, `Card`, alert cards |
| `shadow-menu` | `0 8px 24px rgba(16, 24, 40, 0.10) !important` | `index.css:438` (plain class) | flyouts, search dropdown, drill drawer, co-pilot |
| `shadow-modal` | `0 20px 48px rgba(16, 24, 40, 0.16) !important` | `index.css:439` | `DialogContent`, Account 360 |
| `.panel` / `.card-base` | `0 1px 2px rgba(16, 24, 40, 0.04) !important` (the same on hover) | `index.css:524-538` | beats any `shadow-tint-*` on those elements |
| Analytics `Panel` | `shadow-[0_1px_2px_rgba(17,24,39,0.03)]` | `PortfolioAnalytics.jsx:60` | |
| Active segmented tab | `shadow-[0_1px_3px_rgba(0,0,0,0.04)]` | `PortfolioAnalytics.jsx:826` | |
| Workspace modal | `0 24px 48px -12px rgba(22,119,255,0.15), 0 0 0 1px rgba(0,0,0,0.04)` | `WorkspaceModal.jsx:118` | |
| Workspace icon circle | `0 4px 10px rgba(22,119,255,0.3)` | `:123` | |
| `shadow-hover` | `0 4px 16px rgba(22,119,255,0.12), 0 1px 3px rgba(17,24,39,0.05)` | config :133 | used once |
| `shadow-tint-{primary,warning,destructive,success}` (+`-primary-hover`) | e.g. `0 2px 8px rgba(220,38,38,0.08), 0 1px 3px rgba(220,38,38,0.04)` | config :140-144 | Inert on `.card-base`. `shadow-tint-destructive-hover` and `-warning-hover` are **undefined** |
| `shadow-sm` (Tailwind default) | `0 1px 2px 0 rgb(0 0 0 / 0.05)` | | KPI hover, active tab |

The blue-tinted `rgba(22,119,255,…)` values are leftovers from the `#1677FF` era. Keep them verbatim for parity.

### 1.11 Glass and backdrop blur, as rendered

There is **no frosted glass anywhere in the rendered app.** `.tiq-glass` is declared as `rgba(255,255,255,0.68)` +
`blur(20px)` (`index.css:144-149`), but it is overridden to `background:#FFFFFF; border-color:#ECEDF1;
backdrop-filter:none` (`:547-552`). The sidebar and top bar are opaque `bg-card`.

Blur appears only on overlay scrims:

| Overlay | Scrim |
|---|---|
| Drill drawer | `rgba(0,0,0,0.28)` + `blur(2px)` (`DrillPanel.jsx:65`) |
| Workspace modal | `rgba(0,0,0,0.4)` + `blur(4px)` (`WorkspaceModal.jsx:116`) |
| Matrix modal, Account 360, Raise a Query | `bg-black/40` or `bg-black/30` + `backdrop-blur-sm` (4px) |
| `Dialog` primitive | `bg-[#101828]/40`, no blur |

### 1.12 Spacing, grid and breakpoints

- **Breakpoints** are Tailwind defaults: sm 640, md 768, lg 1024, xl 1280, 2xl 1536. `container` is centred with 1rem
  padding and a 1400px 2xl cap (`tailwind.config.js:5-11`). The container is not used by the core pages. The only extra
  spacing token is `4.5` = 1.125rem.
- **Gaps in use:**

  | Context | Gap |
  |---|---|
  | KPI grid | `gap-3.5` (14px) |
  | Analytics grids and page sections | `gap-5` / `space-y-5` (20px) |
  | Tile rows | `gap-4` / `gap-3` |
  | Page root | `space-y-6` |
  | Card padding | `p-6` (24px); `.app-content .card-base:not([class*="p-"])` gets 24px, 20px at ≤640px (`index.css:502,521`) |

### 1.13 Motion

| Kind | Value | Source |
|---|---|---|
| Transition curve, effective | `cubic-bezier(0.2, 0, 0, 1)` on every element whose class contains `transition-` | `index.css:430-432` |
| Named `ease-spring` | `cubic-bezier(0.16, 1, 0.3, 1)` | `tailwind.config.js:151` |
| Keyframe animations | all `cubic-bezier(0.16, 1, 0.3, 1)` | config :193-199 |

The keyframe animations, from `tailwind.config.js`:

| Animation | Keyframe | Timing |
|---|---|---|
| `animate-slide-in` | opacity 0→1, translateY(8px)→0 | 300ms both |
| `animate-fade-in` | opacity 0→1 | 200ms both |
| `animate-scale-in` | scale(0.95) translateY(8px)→1 | 280ms both |
| `animate-zoom-in-95` | | 200ms |
| `animate-card-enter` | translateY(16px) | 400ms |
| `accordion-down` / `accordion-up` | | 200ms |

CSS utilities in `index.css`:

| Class | Animation | Source |
|---|---|---|
| `.page-fade-in` | `fadeIn 350ms` (translateY 6px) | `:269-275` |
| `.card-enter` | `slideUpCard 420ms` (18px) | `:277-283` |
| `.hero-fade-up` | 500ms (14px) | `:285-291` |
| `.stagger-item` | `animation-delay: calc(var(--stagger-i,0) * 50ms)` | `:297-300` |
| `.live-dot` | `liveRing 1.8s` (`rgba(52,211,153,0.55)` ring to 7px) | `:331-338` |
| Sidebar section collapse | 200ms | `:316-328` |

Other motion facts:
- **Durations in use:** `duration-150` (flyouts), `duration-200` (KPI cards, tabs), `duration-300` (sidebar width,
  chevron), `duration-500` (bar fills), `duration-700` (funnel).
- **Reduced motion.** At `index.css:628-636`, every animation drops to 1ms, and transitions drop to 1ms and are limited
  to opacity, color, background-color and border-color.
- **framer-motion** appears only in `AICoPilot.jsx` and `AIAssistant.jsx`:
  - Panel: `initial {opacity:0,y:20,scale:0.95}` → `animate {opacity:1,y:0,scale:1}`, `transition {type:"spring",stiffness:300,damping:30}` (`AICoPilot.jsx:318-321`).
  - Messages: `{opacity:0,y:10}` → `{opacity:1,y:0}`.
  - AIAssistant: `duration 0.3-0.35, ease [0.16,1,0.3,1]`, stagger `i*0.05`.
  - **None of the signature composites uses framer-motion.**
- **Count-up.** `KPICard.jsx:21-63` animates the numeric part of a formatted string over 850ms with an ease-out cubic
  (`1 - (1-p)^3`) via rAF. `PulseKpiFlow` does **not** count up.

---

## 2. App shell

### 2.1 Layout tree (`src/App.jsx:85-164`)

```
SidebarProvider defaultOpen={false}          ui/sidebar.jsx:20  sets --sidebar-width 252px / --sidebar-width-icon 76px
├─ ambient blobs (fixed, z:-1)                                  invisible (see §0.2); do not port
├─ div.flex.min-h-screen.w-full.bg-background.font-sans.text-foreground
│  ├─ <Sidebar/>  aside: fixed top-5 bottom-5 left-5 z-50       floating card, 20px inset from the viewport
│  └─ SidebarInset: "flex flex-col flex-1 h-screen overflow-hidden py-5 pr-5 gap-4"
│        + pl-[calc(var(--sidebar-width-icon)+2.5rem)]   → 116px (collapsed, the default)
│        + pl-[calc(var(--sidebar-width)+2.5rem)]        → 292px (expanded)
│        transition-[padding] duration-300
│     ├─ <TopBar/>  header, min-h-14 (56px) white card
│     └─ main.app-content: "flex-1 overflow-y-auto overflow-x-hidden p-4 md:p-5 page-fade-in bg-background w-full rounded-card"
│           └─ div.w-full.mx-auto → <Routes/>   (no max-width on the shell)
├─ <AICopilot/>  floating pill, bottom-right
├─ <AccountDrawer/>  portal
└─ <WorkspaceModal/> when a tool is active
```

- The sidebar-to-content gutter is 20px, and the top bar-to-main gap is 16px.
- `main` is the only scroll container. Its 16px corners clip the scrolling content, but it is the same colour as the
  page.
- There is **no mobile navigation**. The rail is always fixed.

### 2.2 Sidebar (`src/components/Sidebar.jsx`, `src/components/ui/sidebar.jsx`)

- **Root** (`ui/sidebar.jsx:98-104`):

  ```
  "fixed top-5 bottom-5 left-5 z-50 flex flex-col",
  "rounded-card border border-border bg-card text-foreground shadow-resting overflow-visible",
  "transition-[width] duration-300 ease-in-out",
  state === "expanded" ? "w-[var(--sidebar-width)]" : "w-[var(--sidebar-width-icon)]"
  ```

  Width is **252px expanded, 76px collapsed**. The collapsed state is the default; it persists in `localStorage`
  `sidebar:state:v2` (`"open"`/`"closed"`) and toggles with **Ctrl/Cmd+B**.
- **Header** (`ui/sidebar.jsx:120` + `Sidebar.jsx:134-148`): `h-16 flex items-center border-border shrink-0 px-3 gap-2`
  + `border-b border-border pb-3`.
  - Expanded: the logo `<img className="h-[2rem]" src={logo}>` (the TRANSORGIQ wordmark), plus a collapse button
    `text-muted-foreground hover:text-foreground hover:bg-muted rounded-control p-2 transition-colors` holding
    `PanelLeftClose size 16`.
  - Collapsed: a `<Link>` holding `<img className="h-7" src={IQ}>` (the lime "IQ" mark), then an expand button
    `mx-auto mt-2 … p-2` holding `PanelLeft size 16` (`:151-156`).
- **Persona card** (expanded only, `:159-165`): `mx-3 mt-3 mb-1 px-3 py-3 bg-muted rounded-inner`.
  - Role: `text-[11px] font-semibold text-muted-foreground uppercase tracking-[0.06em] mb-1`.
  - Product: `text-[14px] font-semibold text-foreground truncate`.
  - Geography: `text-[12px] text-muted-foreground mt-0.5`.
- **Content** (`:167`). Expanded: `pt-2 overflow-y-auto sidebar-scrollbar-hide`. Collapsed: `pt-3 overflow-visible
  gap-1.5`. Both merge with the base `flex-1 min-h-0 py-3 flex flex-col gap-3`.
- **Sections** (`Sidebar.jsx:32-75`), each with a lucide icon. The bank portal maps its §5.1 sections onto this structure:

  | Section (icon) | Items |
  |---|---|
  | Executive (`LayoutDashboard`) | Portfolio Overview `/`, AI Decision Center `/decision-center` |
  | AI Intelligence (`BrainCircuit`) | AI Co-Pilot, Risk Radar, Risk Simulator |
  | DPD Recovery Lifecycle (`Layers`) | Pre-Delinquency, 0–30, 30–60, 60–90, NPA, PTP Tracker |
  | Operations Pipeline (`Workflow`) | Comms Control Center, Digital & Tele-calling, Field Recovery, Manual Interventions |
  | Resolution (`Scale`) | Negotiations & OTS, Legal Actions |

- **Expanded section block**: `px-3 py-1`. The label is
  `text-[11px] font-semibold uppercase tracking-[0.06em] text-muted-foreground px-2 mb-1.5 mt-3`, and items sit in
  `space-y-0.5`.
- **Item** (`ItemLink`, `:77-91`):

  ```
  "relative flex items-center gap-2.5 pl-3 pr-2.5 py-2 rounded-control text-[12.5px] font-medium transition-colors group/i",
  active ? "bg-accent text-primary font-semibold" : "text-muted-foreground hover:bg-muted hover:text-foreground"
  icon: "size-[17px] shrink-0" + (active ? "text-primary" : "text-muted-foreground group-hover/i:text-foreground")
  ```

  Active renders `#EDEDFD` fill with `#5048E5` text at 600. Hover renders `#F6F7F9` fill.
- **Collapsed rail** (`SectionRail`, `:94-120`):
  - Each section is a 44×44 button: `relative flex items-center justify-center w-11 h-11 mx-auto rounded-control
    transition-colors`, with an icon `size-[19px]`. Active: `bg-accent text-primary`.
  - On hover a flyout appears:
    `absolute left-full top-0 pl-3 z-50 opacity-0 -translate-x-1 pointer-events-none group-hover:opacity-100 group-hover:translate-x-0 group-hover:pointer-events-auto transition-all duration-150 ease-out`.
  - The flyout panel is `w-60 rounded-inner border border-border bg-card shadow-menu p-2`, with the section label
    `text-[11px] font-semibold uppercase tracking-[0.06em] text-muted-foreground px-2.5 pt-1.5 pb-2`, then `ItemLink`s.
- **Footer** (`:180-202`): `border-t border-border pt-2 pb-2`.
  - Expanded sign-out: `flex min-h-10 items-center gap-2 w-full px-4 rounded-control text-[13px] font-medium text-muted-foreground hover:text-foreground hover:bg-muted transition-colors`, with `LogOut size-3.5`.
  - Collapsed: a 44×44 icon button with a flyout tooltip `px-3 py-2 rounded-inner border border-border shadow-menu text-[13px] font-medium`.

### 2.3 Top bar (`src/components/TopBar.jsx:21-70`)

```
header: "min-h-14 bg-card border border-border flex items-center justify-between px-5 py-2 shrink-0 gap-4 rounded-card shadow-resting z-30"
left:   h1 "text-[15px] font-bold text-foreground" = "Command Center"
        meta row "flex items-center gap-4 text-[12px] text-muted-foreground font-medium":
          live dot (h-2 w-2: bg-success animate-ping at opacity-75 + solid dot) "Live System"
          Package 11 + product (capitalize) · MapPin 11 + geography · (lg+) Calendar 11 + date en-GB "24 Sep 2026"
centre: SearchBar  "relative w-80" (320px)
          field: "flex min-h-10 items-center gap-2.5 bg-muted rounded-control px-3 transition-colors border"
                 + open ? "border-primary ring-2 ring-primary/20" : "border-transparent hover:border-input"
          input: "flex-1 text-[13px] bg-transparent outline-none text-foreground placeholder-muted-foreground min-w-0"
          kbd:   "hidden sm:flex items-center gap-0.5 text-[10px] text-muted-foreground bg-muted/50 border border-border/50 rounded px-1.5 py-0.5 font-semibold" (⌘K)
          dropdown: "absolute top-full left-0 right-0 mt-1.5 bg-card border border-border rounded-inner shadow-menu overflow-hidden z-50"
right:  "Raise a Query": "flex min-h-10 items-center gap-1.5 bg-card text-foreground border border-input rounded-control px-3 text-[13px] font-medium transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-primary/20" + LifeBuoy 12
        standing alert pill: "flex min-h-10 items-center gap-1.5 bg-warning/10 text-foreground rounded-pill px-3 text-[13px] font-medium"
          + dot "size-1.5 rounded-full bg-warning" + Activity 12 text-warning + "₹{slippageCr} Cr slipping to NPA"
```

Search result kind chips (`SearchBar.jsx:56-62`):

| Kind | Chip classes |
|---|---|
| account / tool | `text-primary bg-primary/10` |
| page | `text-secondary bg-secondary/10` |
| kpi | `text-success bg-success/10` |
| alert | `text-warning bg-warning/10` |

### 2.4 Floating AI Co-Pilot (`AICoPilot.jsx:306-322`)

- The launcher is the `Button` primitive with
  `fixed bottom-6 right-6 z-[80] rounded-full shadow-menu px-4 h-11 text-[13px] font-medium bg-card border-primary text-primary hover:bg-accent`,
  containing `Bot` 15 and the label "AI Co-Pilot".
- The panel is
  `fixed bottom-20 right-4 sm:right-6 z-[80] w-[390px] max-w-[calc(100vw-2rem)] h-[520px] max-h-[calc(100vh-7rem)] bg-card rounded-card shadow-menu border border-border`.

### 2.5 Login (`src/pages/Login.jsx`)

This is a split screen: `min-h-screen w-full flex bg-background`.

- **Left hero** (lg and up, `lg:w-[44%] p-12`, `:53-114`):
  - Background:
    `radial-gradient(circle at 42% 4%, rgba(112, 121, 255, 0.95) 0%, rgba(73, 84, 235, 0.62) 31%, transparent 56%), linear-gradient(155deg, #4655ef 0%, #2e3dd1 52%, #111b82 100%)`.
  - A sheen overlay: `linear-gradient(120deg, rgba(255,255,255,0.08) 0%, transparent 38%, rgba(8,16,112,0.18) 100%)`.
  - An SVG of five curves at `opacity-25`, with a gradient stroke from `white/0.56` through `#7782ff/0.28` to
    `#0b176f/0.8`.
  - The logo rendered white: `h-8 brightness-0 invert`.
  - Eyebrow: `text-[11px] font-medium uppercase tracking-[1.5px] text-white/70`.
  - H1: `text-[36px] font-semibold leading-[1.2] tracking-tight text-white`.
  - Three check rows: a `w-4 h-4 rounded-full bg-white/20` dot and `text-[13px] text-white/85`, staggered with
    `page-fade-in` at 60ms each.
  - Footer: `text-[11px] text-white/50`.
- **Right form** (`max-w-[400px]`):
  - H2: `text-[26px] font-semibold tracking-tight text-[#171717]`.
  - Help text: `text-[12px] leading-[1.45] text-[#888888]`.
  - Labels: `text-[12px] font-medium text-[#555555]`.
  - Inputs: `h-10 border-[#d8d8d8] bg-white pl-9 font-medium text-[#171717] placeholder:text-[#aaaaaa] focus-visible:border-[#171717] focus-visible:ring-[#171717]/10`, with `Mail` / `Lock` 14 in `#9a9a9a`.
  - Submit is a **black** button: inline `#000000`, class `mt-2 flex h-11 items-center justify-center gap-2 rounded-[12px] border text-[13px] font-semibold shadow-sm hover:opacity-90`, with `ArrowRight` 15.
  - Error: `text-[12px] text-destructive font-medium`.

---

## 3. Primitives (`src/components/ui/*`)

`cn` (`src/lib/utils.js:1-6`), verbatim:
`import { clsx } from "clsx"; import { twMerge } from "tailwind-merge"; export function cn(...inputs) { return twMerge(clsx(inputs)); }`

### 3.1 `button.jsx` (CVA, verbatim `:5-40`)

```js
const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-control border bg-card text-sm font-medium transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/25 focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "border-primary text-primary hover:bg-accent",
        destructive: "border-destructive text-destructive hover:bg-destructive/10",
        outline: "border-input text-foreground hover:bg-muted",
        secondary: "border-secondary text-secondary hover:bg-secondary/10",
        ghost: "border-transparent text-muted-foreground hover:bg-muted hover:text-foreground",
        link: "border-transparent text-primary underline-offset-4 hover:underline",
        success: "border-success text-[#067647] hover:bg-success/10",
        warning: "border-warning text-[#B54708] hover:bg-warning/10",
      },
      size: {
        default: "h-10 px-4 py-2 [&_svg]:size-4",
        sm: "h-9 px-3 text-[13px] [&_svg]:size-3.5",
        lg: "h-11 px-5 text-sm [&_svg]:size-4",
        xs: "h-8 px-2.5 text-[12px] [&_svg]:size-3",
        icon: "h-10 w-10 [&_svg]:size-4",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  }
);
```

Every variant is an **outlined, white-fill** button. There is no filled primary. `asChild` is accepted and discarded,
because `Comp` is always `"button"` (`:44`).

### 3.2 `badge.jsx` (CVA, verbatim `:5-32`). It renders a `<div>`

```js
const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-full border-0 px-2.5 py-1 text-[13px] font-medium text-foreground transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
  {
    variants: {
      variant: {
        default: "bg-accent",
        secondary: "bg-[#ECE9FE]",
        destructive: "bg-[#FDE7E6]",
        outline: "border border-input bg-card text-foreground",
        success: "bg-[#DCF5E9]",
        warning: "bg-[#FDF0DC]",
        softPrimary: "badge-premium-blue",
        softSuccess: "badge-premium-green",
        softDanger:  "badge-premium-red",
        softWarning: "badge-premium-amber",
      },
    },
    defaultVariants: { variant: "default" },
  }
);
```

The text is always ink. Status reads from the fill colour alone.

### 3.3 The other primitives, with verbatim class strings

| File | Parts and classes |
|---|---|
| `card.jsx` | `Card`: `rounded-card border border-border bg-card text-card-foreground shadow-resting` · `CardHeader`: `flex flex-col space-y-1.5 p-6` · `CardTitle` (h3): `text-[19px] font-semibold leading-[1.35] tracking-tight text-foreground` · `CardDescription`: `text-[13px] text-muted-foreground` · `CardContent`: `p-6 pt-0` · `CardFooter`: `flex items-center p-6 pt-0` |
| `dialog.jsx` | `Dialog` portals to `document.body`, locks `body` overflow, and closes on Esc. Wrapper: `fixed inset-0 z-[9999] flex items-center justify-center p-4`; scrim: `fixed inset-0 bg-[#101828]/40` · `DialogContent`: `relative z-50 bg-card border border-border rounded-modal shadow-modal w-full max-w-[560px] max-h-[88vh] flex flex-col overflow-hidden` · `DialogHeader`: `px-6 pt-6 pb-4 border-b border-border flex-shrink-0` · `DialogTitle` (h2): `text-[19px] font-semibold text-foreground tracking-tight` · `DialogDescription`: `text-[14px] text-muted-foreground mt-1` · `DialogFooter`: `px-6 py-4 border-t border-border flex-shrink-0 bg-card flex items-center justify-end gap-3` · `DialogClose`: `absolute top-4 right-4 w-10 h-10 rounded-control flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-muted transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/20`, with an inline 14px X svg (stroke 2.5) |
| `input.jsx` | `flex h-10 w-full rounded-control border border-input bg-card px-3 py-1 text-sm text-foreground transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:border-primary focus-visible:ring-2 focus-visible:ring-primary/20 disabled:cursor-not-allowed disabled:opacity-50` |
| `textarea.jsx` | `flex min-h-[96px] w-full rounded-control border border-input bg-card px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:border-primary focus-visible:ring-2 focus-visible:ring-primary/20 disabled:cursor-not-allowed disabled:opacity-50 transition-colors` |
| `label.jsx` | `text-[13px] font-normal text-muted-foreground` |
| `select.jsx` | A native `<select>` in `relative`: `flex h-10 w-full appearance-none items-center justify-between rounded-control border border-input bg-card px-3 pr-9 py-1 text-sm font-medium text-foreground transition-colors focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/20 disabled:cursor-not-allowed disabled:opacity-50`, plus `ChevronDown size 14` at `pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground` |
| `separator.jsx` | `shrink-0 bg-border` + `h-px w-full` or `h-full w-px` |
| `table.jsx` | `Table`: a `relative w-full overflow-auto` wrapper around `w-full caption-bottom text-sm border-collapse` · `TableHeader`: `bg-muted` · `TableBody`: `[&_tr:last-child]:border-0` · `TableFooter`: `border-t bg-muted/50 font-medium [&>tr]:last:border-b-0` · `TableRow`: `border-b border-border/60 transition-colors hover:bg-accent/40 data-[state=selected]:bg-muted even:bg-muted/30` · `TableHead`: `bg-muted text-left align-middle text-[10px] font-bold uppercase tracking-wider text-muted-foreground px-4 py-2.5 [&:has([role=checkbox])]:pr-0` · `TableCell`: `px-4 py-3 align-middle text-xs font-medium text-foreground [&:has([role=checkbox])]:pr-0` · `TableCaption`: `mt-4 text-sm text-muted-foreground`. **§0.3 applies:** padding-y and hover are overridden by the default table rules |
| `sidebar.jsx` | See §2.2. It also exports `SidebarGroup` (`flex flex-col gap-1 px-2`), `SidebarGroupLabel` (`text-[11px] font-semibold tracking-wider text-muted-foreground uppercase px-2 h-7 flex items-center transition-opacity duration-150`), `SidebarMenu` (`flex flex-col gap-0.5 list-none p-0 m-0`), `SidebarMenuButton` (`flex items-center gap-3 w-full rounded-control px-3 h-11 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring`; active `bg-accent text-primary font-semibold`, otherwise `text-muted-foreground font-medium hover:bg-muted hover:text-foreground`; collapsed tooltip `rounded-md bg-foreground text-background px-2 py-1 text-xs font-medium shadow-md`), `SidebarMenuBadge`, `SidebarInset` and `SidebarTrigger` (ghost icon `Button`, `h-8 w-8`). `Sidebar.jsx` uses only the Provider, Root, Header, Content, Footer, Inset and `useSidebar` |

### 3.4 Global component classes (`src/index.css`), with the effective result after the Soft Card overrides

```css
/* layered (:80-97) then overridden (:524-545) */
.panel      { @apply bg-card border border-border/60 p-6 transition-all; }   /* effective: */
.panel, .card-base { background:#FFFFFF; border:1px solid #ECEDF1; border-radius:16px !important;
                     box-shadow:0 1px 2px rgba(16,24,40,0.04) !important; transform:none !important; }
.panel:hover, .card-base:hover { border-color:#E1E3E9; box-shadow:0 1px 2px rgba(16,24,40,0.04) !important; transform:none !important; }
.panel-title { @apply text-[9.5px] font-semibold tracking-widest text-muted-foreground uppercase mb-5; } /* then: */
.panel-title { color:#98A2B3; font-size:11px; font-weight:600; letter-spacing:.06em; }   /* still uppercase, mb-5 */
.icon-circle { @apply w-10 h-10 rounded-full flex items-center justify-center text-white shrink-0;
               transition: transform 200ms cubic-bezier(0.16, 1, 0.3, 1); }
.card-base:hover .icon-circle, .panel:hover .icon-circle { transform: scale(1.08) rotate(-4deg); }
.chip-badge  { @apply px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider rounded-full inline-block; }
.tiq-glass   { background:#FFFFFF; border-color:#ECEDF1; backdrop-filter:none; }        /* effective */
.tiq-input   { height:40px; padding:0 12px; border:1px solid #E1E3E9; border-radius:10px; background:#FFFFFF; color:#101828; font-size:14px; }
.tiq-input:focus { @apply bg-card border-primary/50; box-shadow: 0 0 0 3px hsl(var(--primary) / 0.08); }
.tiq-label   { display:block; margin-bottom:.375rem; color:#98A2B3; font-size:13px; font-weight:400; letter-spacing:0; text-transform:none; }
.btn-primary, .btn-secondary { min-height:40px; padding:0 16px; border-radius:10px; background:#FFFFFF; font-size:14px; font-weight:500; box-shadow:none !important; }
.btn-primary   { color:#4F46E5; border:1px solid #4F46E5; }  .btn-primary:hover { background:#EEEDFD; }  .btn-primary:active { transform: scale(0.98); }
.btn-secondary { color:#101828; border:1px solid #E1E3E9; }  .btn-secondary:hover { background:#F7F8FA; }
.btn-ghost   { @apply inline-flex items-center gap-1.5 text-muted-foreground hover:text-foreground text-[12px] font-semibold; }
.tiq-table th { background:#F7F8FA; color:#98A2B3; font-size:11px; letter-spacing:.06em; /* + uppercase font-semibold px-5 py-3.5 border-b */ }
.tiq-table td { border-color:#ECEDF1; color:#101828; font-size:14px; font-weight:500; /* + px-5 py-4 border-b */ }
table:not(.tiq-table) th { padding-bottom:.75rem; font-weight:600; }          /* compiled; beats utilities */
table:not(.tiq-table) td { padding-top:.625rem; padding-bottom:.625rem; }
table:not(.tiq-table) tbody tr:hover { background-color: hsl(var(--muted) / 0.3); }
.badge-premium-{blue|green|red|amber} { border:0; color:#101828; font-size:13px; font-weight:500; letter-spacing:0;
    text-transform:none; padding:.125rem .625rem; border-radius:9999px; background:#E8F1FE|#DCF5E9|#FDE7E6|#FDF0DC; }
.rounded-card{border-radius:16px!important} .rounded-inner{border-radius:12px!important}
.rounded-control{border-radius:10px!important} .rounded-modal{border-radius:16px!important}
.shadow-menu{box-shadow:0 8px 24px rgba(16,24,40,.10)!important} .shadow-modal{box-shadow:0 20px 48px rgba(16,24,40,.16)!important}
.scrollbar-thin::-webkit-scrollbar { width:5px; height:5px; }  /* thumb hsl(var(--border)) radius 9999px; also applied globally at :367-369 */
input[type="range"] { appearance:none; background:transparent; }   /* track 4px hsl(var(--border)/.5); thumb 18px #FFF,
   border 1px rgba(0,0,0,.1), shadow 0 2px 5px rgba(0,0,0,.1), 0 1px 1px rgba(0,0,0,.05); hover scale 1.1, active .95 (:372-396) */
```

Other classes defined in `index.css`:
- `.agentic-panel` (dark `#0B0D14` with a violet shadow, `:112-120`). It is unused.
- `.hero .hf-l/.hf-v` (`:441-454`) and `.alert-detail` table (`:456-483`) have minor uses.
- `.hero-glow-*` and `.cursor-blink` exist.
- `.app-content` containment (`:487-522`): every grid or flex child gets `min-width:0`, and headings and cells get
  `overflow-wrap:anywhere`.

---

## 4. Signature composites

### 4.1 `PulseKpiFlow.jsx`: the KPI card flow (12 KPIs in 2 rows of 6)

The file's header comment says "ten", but `ROWS` lists 6 + 6 (`:16-27`).

```
section.mb-10
  header  "mb-5 flex items-baseline justify-between gap-4 px-1"
     h2   "text-[11px] font-bold uppercase tracking-widest text-muted-foreground"  "Portfolio Health"
     span "text-[11px] font-medium text-muted-foreground"  {frame.label}  e.g. "30 days to 22 Sep 2026"
  div.space-y-5  × 2 rows
     caption "mb-2.5 px-1 text-[11px] font-medium text-muted-foreground/70"   "Where the book stands" / "What came back, and what it cost"
     grid    "grid grid-cols-2 gap-3.5 sm:grid-cols-3 xl:grid-cols-6"
       KpiCard <button title={kpi.basis}>    ← the basis tooltip is the NATIVE title attribute
         "animate-slide-up flex h-full flex-col rounded-[16px] border border-border/50 bg-card px-4 py-4 text-left
          transition-all duration-200 hover:border-border hover:shadow-sm
          focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
         label  "text-[11.5px] font-medium leading-tight text-muted-foreground"
         value  "mt-3 text-[23px] font-bold leading-none tracking-tight text-foreground tabular-nums"
         trend  "mt-3 flex items-center gap-1.5 text-[11.5px] font-semibold ${trendClass}"
                icon TrendingUp | TrendingDown | Minus  size 13 strokeWidth 2.5; text "truncate"
         sub    "mt-2.5 text-[11px] font-normal leading-snug text-muted-foreground/80"
  narrative "mt-6 border-l-2 border-border pl-4 text-[13px] font-normal leading-relaxed text-muted-foreground"
```

- **The trend is plain coloured text with an icon, not a chip** (`:36-38`):
  - `flat = kpi.good == null || /^[+-]?0(\.0)?\s*(pp|%)/.test(kpi.trend)`.
  - The icon shows direction (`trendUp`). The colour shows whether that is good: `text-success`, `text-destructive`, or
    `text-muted-foreground` when flat.
- Clicking a card calls `onSelect(kpi.drill)`, which switches the analytics tab and scrolls to `#portfolio-analytics`
  (`PortfolioOverview.jsx:36-41`).
- The KPI ids are `at_risk, dpd_accounts, npa_accounts, roll_rate, cure_rate, ptp_keep` and
  `slippage, target, achieved, efficiency, coverage, cost`.
- The strings arrive pre-formatted from the server; see §4.12.

### 4.2 `PortfolioAnalytics.jsx`: the tab bar, sections, tables and heat grid

- **Section header** (`:814-819`):
  - h3: `text-[17px] font-bold tracking-tight text-foreground`.
  - Subtitle: `mt-1 text-[12px] text-muted-foreground`.
- **Segmented tab bar** (`:821-832`):
  - Container: `flex max-w-3xl overflow-x-auto rounded-2xl border border-border/40 bg-muted/25 p-1`.
  - Each tab: `flex-1 whitespace-nowrap rounded-xl px-3.5 py-2 text-center text-[11.5px] font-semibold transition-all duration-200`.
    - Active: `border border-border/40 bg-card text-foreground shadow-[0_1px_3px_rgba(0,0,0,0.04)]`.
    - Inactive: `text-muted-foreground hover:text-foreground`.
  - Tabs: Exposure, Migration, Recovery, Cost to Collect, Concentration. The bank portal needs eight tabs, and the bar
    scrolls horizontally.
- **Headline** (`:53-57`): `border-l-2 border-border pl-4 text-[13px] font-normal leading-relaxed text-muted-foreground`.
- **Panel** (`:59-67`): `rounded-card border border-border/60 bg-card p-6 shadow-[0_1px_2px_rgba(17,24,39,0.03)]`.
  - Header: `mb-5 flex flex-wrap items-baseline justify-between gap-3`.
  - Title: `text-[12.5px] font-semibold tracking-tight text-foreground`.
  - Hint: `text-[11px] text-muted-foreground`.
- **Tile** (`:69-75`): `rounded-[16px] border border-border/50 bg-card px-5 py-4`.
  - Label: `truncate text-[11.5px] font-medium leading-tight text-muted-foreground`.
  - Value: `mt-2.5 text-[22px] font-bold leading-none tracking-tight tabular-nums`, with an inline colour.
  - Sub: `mt-2 truncate text-[11px] text-muted-foreground/80`.
- **Bar100** (`:77-82`): the track is `bg-muted rounded-full overflow-hidden w-full` at height 6 (default) or 8; the fill
  is `h-full rounded-full transition-all duration-500` with `width: max(1.5, pct)%`.
- **DrillRow** (`:86-92`): `w-full text-left group transition-colors hover:bg-accent/50 rounded-inner focus-visible:ring-2 focus-visible:ring-primary/30`, usually with `block p-1.5 -m-1.5`.
- **Analytics table** (`:137-175`):

  | Element | Classes |
  |---|---|
  | `table` | `w-full text-[11px] text-left min-w-[620px]` |
  | header row | `border-b border-border` |
  | `th` | `pb-2.5 pr-3 font-medium text-muted-foreground uppercase text-[9.5px] tracking-wide`, rendered at pb 12px / weight 600 |
  | body row | `border-b border-border/30 cursor-pointer hover:bg-accent/50 transition-colors`, rendered hover muted/30 |
  | `td` | `py-3 pr-3`, rendered py 10px |
  | Figures | `font-bold text-foreground` |
  | Money in | `color: BRAND.success` |
  | Roll | `BRAND.destructive` |

- **Funnel** (`:108-133`):
  - Stage label: `text-[11px] font-bold w-40`.
  - Track: `h-7 bg-muted rounded-inner`.
  - Bar: `width max(pct,12)%`, bg `${color}22`, a 3px left border, and the value in colour at `text-[11px] font-semibold`.
  - Colours by stage: `[slate, primary, warning, destructive]`.
- **Heat grid, product × bucket** (`:180-223`):
  - Column headers are coloured by bucket: `text-[9px] font-bold uppercase tracking-wider`.
  - Cell: `rounded-inner py-1.5`, with background alpha `intensity*40+8`.
  - Cell value: `text-[10px] font-semibold`, with the count underneath at `text-[8.5px] text-muted-foreground`.
- **Transition matrix** (`:275-322`):
  - Each cell is a `<button>` with `w-full rounded-inner py-2 text-center transition-transform hover:scale-[1.06]`.
  - Background: `destructive` above the diagonal, `success` below it, `slate` on it. Alpha is
    `round(min(0.85, v/100*0.9)*255)`.
  - The diagonal is outlined with `inset 0 0 0 1.5px ${BRAND.ink}55`.
  - Text is white when v > 45 and ink otherwise.
- **Diverging cure/roll bar** (`:333-338`): `flex h-4 rounded-inner overflow-hidden border border-border/40`. Segments,
  in order: cure `success`, improve `success66`, hold `grid`, roll `destructive`.
- **Bridge rows** (`:374`): `bg-muted/20 border border-border/40 rounded-inner px-3.5 py-2.5`, with the value at
  `text-[14px] font-bold` in colour.
- **Loading** (`:47-51`): `Loader2` 14 `animate-spin` with "Loading portfolio data…" at `text-xs font-semibold text-muted-foreground`, `py-20`.
- **Error**: "This panel could not load." at `py-16 text-center text-xs font-medium`.

### 4.3 `DrillPanel.jsx`: the right-hand drill drawer

- Portals to `document.body`.
- **Scrim**: `fixed inset-0 z-[9998] flex justify-end animate-fade-in`, with `rgba(0,0,0,0.28)` and `blur(2px)`.
- **Sheet**: `w-full max-w-[560px] h-full bg-background border-l border-border shadow-menu flex flex-col animate-slide-in overflow-hidden`.
  `slide-in` moves it up 8px while fading. It does not slide in from the right.
- **Header** (`:71-85`): `px-6 py-5 border-b border-border/60 bg-card`.
  - Eyebrow: "Drill-down" at `text-[11px] font-medium text-muted-foreground`.
  - h3: `text-lg font-extrabold tracking-tight truncate mt-0.5`, which renders 20px / 600.
  - Subtitle: `text-[11px] font-semibold text-muted-foreground mt-1`.
  - Close: `w-8 h-8 rounded-full bg-muted/50 hover:bg-muted`, with `X` 16.
- **Body**: `flex-1 overflow-y-auto p-6 space-y-7`.
  - Eight stat tiles: `grid grid-cols-2 gap-3`, each `rounded-[16px] border border-border/50 bg-card px-4 py-3.5`, with
    the label at `text-[11px] font-medium` and the value at `mt-2 text-[18px] font-bold tracking-tight tabular-nums`.
  - Share callout: `bg-accent/40 border border-border/50 rounded-[16px] px-4 py-3 text-[11px] font-semibold`.
  - `SplitList` (by bucket, product and state):
    - Title: `text-[11px] font-medium text-muted-foreground mb-3`.
    - Row label: `text-[11px] font-bold w-24`.
    - Bar: `h-1.5 bg-muted rounded-full` with a fill of `max(2, pct)%`.
    - Value: `text-[10px] font-bold text-muted-foreground w-24 text-right`.
  - Top-accounts table: the same header style as §4.2, `td py-2 pr-3`, account in `font-mono text-[10px] font-bold`
    (rendered sans), bucket chips as §1.4, and paid amounts in `success` (or `muted` when zero).
- Esc closes the drawer.

### 4.4 `DecisionAlerts.jsx`: alert cards, and the AI Decision Center page around them

- **Header** (`:141-151`):
  - h2: `text-[11px] font-bold text-muted-foreground uppercase tracking-widest` "AI Alerts".
  - Subtext: `text-[11px] mt-1`.
  - Count pill: `text-[10px] text-muted-foreground font-semibold bg-muted px-2.5 py-1 rounded-full border border-border/50` "N active".
- **Grid**: `grid grid-cols-1 xl:grid-cols-2 gap-3`. An open card spans `xl:col-span-2`.
- **Card**: `animate-slide-up rounded-card border border-border overflow-hidden bg-card shadow-resting`.
  - Toggle: `w-full min-h-[92px] px-5 py-4 flex items-center justify-between gap-4 text-left focus-visible:ring-2 focus-visible:ring-primary/20`.
    Open: `cfg.bg`. Closed: `hover:bg-muted`.
  - Icon chip: `flex size-10 shrink-0 items-center justify-center rounded-inner ${cfg.soft}`, with the icon
    `size-[18px] ${cfg.iconColor}`. This is a **rounded square**, not a circle.
  - Title: `text-[14px] font-semibold leading-snug`.
  - Summary: `text-[11.5px] font-medium text-muted-foreground leading-snug mt-1`.
  - Chevron: `ChevronDown` 15, `transition-transform duration-300`, `rotate-180` when open.
- **Body** (`:32-117`): `border-t border-border bg-card animate-fade-in` › `p-5 space-y-5`.
  - Metric tiles: `grid grid-cols-2 md:grid-cols-4 gap-3`, styled like the drill stat tiles.
  - Distribution bars: `h-2 bg-muted rounded-full`, filled with the DPD colour or the severity accent.
  - A cohort table.
  - Action buttons: `flex min-h-9 items-center gap-1.5 text-[12px] font-semibold px-3 rounded-control bg-card hover:bg-accent text-primary border border-primary`,
    with `ArrowRight` 12 and `group-hover:translate-x-0.5`.
  - Basis note: `text-[9.5px] font-medium text-muted-foreground max-w-md`.
- **Around it** (`AIDecisionCenter.jsx`):
  - `ContextStrip` (`:19-39`): a 4-up `grid-cols-2 lg:grid-cols-4 gap-4 mb-10` of tiles
    (`rounded-[16px] border border-border/50 bg-card px-5 py-4`, value at `text-[24px] font-bold`).
  - `ToolCatalogue` (`:47-88`): `grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4`.
    - Category card: `flex h-full flex-col overflow-hidden rounded-[16px] border border-border/50 bg-card`.
    - Card header: `border-b border-border/40 px-5 py-3.5`, with a `Target` 13 icon in `text-primary/60`.
    - Tool rows: `divide-y divide-border/40`, each `px-5 py-4 hover:bg-accent/40`. The title is `text-[13px] font-semibold group-hover:text-primary`.

### 4.5 `WorkspaceModal.jsx`: the tool workspace

- Portals to `document.body`.
- **Scrim**: `fixed inset-0 z-[9999] flex items-center justify-center p-6 sm:p-12 animate-fade-in`, with `rgba(0,0,0,0.4)` and `blur(4px)`.
- **Frame** (`:117-118`): `w-full h-full max-w-screen-2xl max-h-[90vh] bg-background border border-border/60 flex flex-col overflow-hidden animate-scale-in`,
  plus inline `borderRadius: '26px'` and the shadow from §1.10. It fills the screen up to 1536px wide and 90vh high.
- **Header** (`:121-151`): `px-6 py-4 flex items-center justify-between border-b border-border/40 bg-white`.
  - `icon-circle bg-primary` (40px, white `Settings` 18, blue glow).
  - h2: `text-lg font-extrabold tracking-tight`.
  - Mode line: `text-[11px] font-semibold text-muted-foreground`, taken from `PANEL_MODE` (`:17-28`).
  - Actions:
    - `btn-secondary` "Save Scenario".
    - Icon-only `btn-secondary px-3` for Download and Share.
    - `btn-primary ml-2 min-w-[120px]` "Run Analysis", for budget, outreach, underwriting and scenario only.
    - A divider `w-[1px] h-6 bg-border mx-1`.
    - Close: `w-8 h-8 rounded-full bg-muted/40 hover:bg-muted`.
- **Body**: `flex-1 overflow-y-auto bg-muted/5 relative p-6`.
- **Toast**: `absolute bottom-5 right-5 bg-foreground text-primary-foreground text-xs font-bold px-4 py-2.5 rounded-lg shadow-2xl z-[200] flex items-center gap-2 border border-border animate-slide-in`,
  with a `CheckCircle` 14 in `text-success`. It dismisses after 3s.
- **Shared workshop chrome** (`TabHome.jsx`):
  - `PanelHeader` (`:58-67`): `flex items-center justify-between mb-5 gap-3 flex-wrap`, icon 16 `text-primary`, h3 `text-sm font-bold`, then a badge.
  - `PanelLoading` (`:70-74`): `RefreshCw` 13 spinning, at `text-xs text-muted-foreground font-semibold py-10`.
  - Workshop tooltip (`:29-38`): `fontSize 11px, borderRadius 8px, border 1px solid #111827, backgroundColor #111827, color #F9FAFB, boxShadow 0 4px 12px rgba(0,0,0,0.15), padding 8px 12px`, with the system font.

### 4.6 `BoardReportPanel` (`TabHome.jsx:1569-1757`), inside the workspace modal

- **Shell**: `bg-card border border-border rounded-lg p-5 animate-slide-in`, where `rounded-lg` renders 16px.
  - Header right: an audience `<select>` with `bg-background border border-border rounded px-2 py-1 text-[10px] font-bold`.
- **Cover block** (`:1667-1690`): `bg-muted/15 border border-border/60 rounded-lg p-4 mb-5`.
  - Eyebrow: `text-[10px] font-bold text-muted-foreground uppercase tracking-widest`.
  - Eight tiles in `grid grid-cols-2 md:grid-cols-4 gap-3`, each `bg-background border border-border/60 rounded-lg p-3`:
    - Label: `text-[8.5px] font-extrabold uppercase tracking-widest truncate`.
    - Value: `text-lg font-black tracking-tight mt-1`, which renders 20px / 600, coloured by tone
      (`good` success, `bad` destructive, `warning` warning).
    - Sub: `text-[9px] font-medium`.
  - Narrative: `text-[11px] font-medium leading-relaxed mt-3.5 pt-3.5 border-t border-border/50`.
- **Body**: `grid grid-cols-1 lg:grid-cols-5 gap-5` (3 + 2).
  - **Section list** (`:1697-1720`): `border border-border/60 rounded-lg divide-y divide-border/40`.
    - Row: `w-full flex items-center gap-3 px-3.5 py-2.5 hover:bg-muted/20`.
    - Checkbox: `w-4 h-4 rounded border`; checked is `bg-primary border-primary` with a white `Check` 11.
    - Index: `text-[9px] font-bold tabular-nums w-4`.
    - Title: `text-[12px] font-bold`.
    - "Always included": `text-[8.5px] font-bold uppercase tracking-wider`.
  - **Pack summary**: key/value rows `py-1.5 border-b border-border/30 last:border-0`.
  - **Info note**: `text-[10px] bg-primary/5 rounded-lg p-3.5 border border-primary/10` with a `CheckCircle` 14 in
    `text-primary`.
  - **CTA** (`:1747-1752`): `w-full bg-primary hover:bg-primary/90 disabled:opacity-50 text-primary-foreground font-bold py-3 rounded-lg flex items-center justify-center gap-2 transition-all hover:-translate-y-0.5 hover:shadow-lg hover:shadow-primary/20 text-[12px]`.
    **This is one of the few filled-primary buttons.**
  - Sections come from `BOARD_SECTIONS` (`lib/boardPdf.js:37-47`: nine sections; summary and appendix are always
    included). The PDF is built with `jspdf` using `BRAND`.

### 4.7 `RiskSimulator.jsx`: page chrome, sliders and the fan chart

- **Header** (`:208-231`):
  - h1: `text-2xl font-extrabold tracking-tight flex items-center gap-2`, which renders 32px / 600, with an
    `Activity size-6 text-primary` icon.
  - Description: `text-sm text-muted-foreground mt-1 max-w-2xl`.
  - Actions: `btn-ghost text-xs flex items-center gap-1.5 border border-border rounded-lg px-3 py-1.5 hover:bg-muted`
    for Export CSV and Print.
- **Tab bar** (`:234-244`), a different style from §4.2:
  - Container: `flex bg-muted p-1 rounded-xl w-fit flex-wrap gap-1`.
  - Tab: `px-3 py-1.5 text-xs font-bold rounded-lg transition-all flex items-center gap-1.5`, with an icon `size-3.5`.
    Active: `bg-card shadow-sm text-primary`. Inactive: `text-muted-foreground hover:text-foreground`.
- **Layout** (`:246`): `grid grid-cols-1 gap-5 xl:grid-cols-[340px_minmax(0,1fr)] xl:items-start`.
  - The rail is `space-y-4 xl:sticky xl:top-1 xl:max-h-[calc(100vh-9.5rem)] xl:overflow-y-auto xl:pr-1 scrollbar-thin`.
- **Rail cards**: `rounded-card border border-border/60 bg-card p-4`.
  - h3: `text-xs font-bold uppercase text-muted-foreground mb-3 flex items-center gap-1.5`, with an icon `size-4`
    (primary, destructive or secondary).
  - Selects: `w-full card-base border border-border rounded-md text-xs p-1.5 outline-none focus:border-primary`.
- **Presets** (`:286-302`), four chips in `grid grid-cols-2 gap-2`:
  - Chip: `text-left p-2 rounded-control border transition-all`.
    Active: `border-primary bg-primary/5 ring-1 ring-primary/30`. Inactive: `border-border/60 hover:border-border bg-muted/20`.
  - Content: a `size-2` dot in the preset colour, the label `text-[11px] font-bold`, and a description `text-[9px]`.
  - Presets: Baseline (success), Adverse (warning), Severely Adverse (destructive), Sector Shock (secondary).
- **Slider** (`:191-200`):
  - Label: `text-[10px] font-semibold text-muted-foreground`.
  - Value: `text-[10px] font-bold font-mono`.
  - Input: `w-full h-1.5 rounded-full appearance-none ${accent}`, where the accent is
    `accent-destructive bg-destructive/10` on the macro layer and `accent-secondary bg-secondary/10` on the strategy layer.
  - The thumb comes from the global range CSS (§3.4).
- **Run button** (`:340-344`): `btn-primary sticky bottom-0 flex w-full … py-2.5 shadow-lg shadow-primary/20`.
  The `btn-primary` rule `box-shadow:none !important` cancels the shadow.
- **Results strip** (`:351-377`): `card-base p-4 mb-4`.
  - Each metric: a `text-[10px] font-bold uppercase` label, a `text-sm font-black` value, and a `text-[10px] font-bold`
    delta with a trend icon.
- **Stat cards** (`:383-422`): `card-base shadow-tint-destructive p-4` (the tint is inert).
  - Icon: `w-9 h-9 rounded-full bg-destructive … text-white` (solid circle).
  - Label: `text-[10px] font-bold uppercase tracking-wider`.
  - Value: `text-2xl font-black`, which renders 32px.
  - `StatDelta` chip: `text-[10px] font-bold px-1.5 py-0.5 rounded ml-2` in `text-success bg-success/10` or
    `text-warning bg-warning/10`.
- **Fan chart** (`:425-451`): a `card-base lg:col-span-2` with a `panel-title` heading and a
  `text-[10px] font-medium bg-primary/10 text-primary px-2 py-0.5 rounded` tag reading "p10 · p50 · p90 bands".
  `ComposedChart` has height 256 (`h-64`) and margin `{top:10,right:10,left:-20,bottom:0}`. It layers three `Area`s:

  | Series | Stroke | Fill |
  |---|---|---|
  | `gnpa_p90` | none | `BRAND.destructive` at fillOpacity 0.1 |
  | `gnpa_p10` | none | `var(--card, #ffffff)` at fillOpacity 1 (masks the band floor) |
  | `gnpa_p50` | `BRAND.destructive`, width 3 | `url(#colorGnpa)` (destructive gradient, 0.4→0) |

  The X axis uses `tickFormatter={(v) => \`M${v}\`}`.
- **Local chart styles** (`:174-175`):
  - `axisTick = { fill: BRAND.axis, fontSize: 11 }`, with axes `stroke={BRAND.axis}`. **Axis lines are visible**, unlike
    in `chartTheme`.
  - `tooltipStyle = { borderRadius: "12px", fontSize: "12px", border: \`1px solid ${BRAND.grid}\`, boxShadow: "0 8px 24px rgba(0,0,0,.08)" }`.
    This is a **light** tooltip.
- **Heat grid** (sensitivity tab, `:684-687`):
  - Cell: `flex-1 aspect-square min-w-[52px] … m-0.5 rounded-lg text-white font-bold hover:scale-105 hover:ring-2 hover:ring-foreground/20`.
  - Background: `heatColor` (`:24-34`), an RGB interpolation success → warning → destructive.
  - Legend: `h-2 w-40 rounded-full`, `linear-gradient(90deg, success, warning, destructive)`.
- **Tornado chart**: a horizontal `BarChart` with `barSize` 16-18, `radius [0,4,4,0]`, and bars coloured destructive
  (up) or success (down).
- **Skeleton** (`:912-918`): `rounded-control bg-muted/30 animate-pulse`, at `h-80` or `min-h-[200px]`.

### 4.8 `FieldRecovery.jsx`: the agency leaderboard (the `.fo-analytics` island)

- **Root**: `fo-analytics space-y-5`. Blocks enter with `fo-enter 420ms cubic-bezier(0.16,1,0.3,1)`, staggered at
  60ms, 240ms and 300ms.
- **Header**:
  - h1: `text-xl font-bold`, inline `#1C1C1F`, letter-spacing −0.02em, with a `MapPin` 18 in `#1677FF`.
  - Subtitle: `text-sm` in `#6B6D76`.
- **KPI row**: `grid grid-cols-2 lg:grid-cols-4 gap-4`.
  - `KPICard` (`:315-345`) is a `.card`:

    ```
    background #fff; border 1px solid #EAEBEF; border-radius 16px; padding 16px;
    box-shadow 0 1px 2px rgba(16,24,40,.04), 0 1px 3px rgba(16,24,40,.06)
    ```

    On hover it lifts `translateY(-3px)` with shadow `0 6px 20px rgba(22,119,255,.12), 0 2px 6px rgba(17,24,39,.06)`
    (`FieldAnalytics.css:23-39`).
  - Label: `text-xs font-semibold uppercase tracking-wide` in `#6B6D76`.
  - Value: `text-2xl font-semibold mt-1.5` in a brand colour. Here `text-2xl` renders 32px.
  - Delta: "▲/▼ x.x%" at `text-xs font-semibold` in `#16a34a` or `#dc2626`, followed by "vs Previous Month".
  - Icon: a solid 40px `icon-circle` in `#1677FF`, `#D97706`, `#16A34A` or `#64748B`.
- **Mode toggle** (`.fo-mode-btn`, `FieldAnalytics.css:330-348`): `7px 16px`, 12px / 600. Active: `#1677FF` with white
  text. Container: `rounded-xl` with a 1px `#EAEBEF` border.
- **Agency pills** (`.fo-agency-pill`, `:242-275`): `7px 14px`, radius 999px, 1.5px `#EAEBEF` border, 12px / 600
  `#6B6D76`, and an 8px dot.
  - Selected: border and text in the agency colour, background `${color}1A`.
  - The Clear pill is `#0C66E4`.
- **Month buttons** (`.fo-month-btn`, `:280-303`): `flex: 1 1 0`, radius 12px. Active: border `rgba(22,119,255,.45)`,
  background `rgba(22,119,255,.08)`, text `#1677FF`.
- **Trend chart** (`:347-413`): height 300, margin `{top:10,right:30,left:0,bottom:0}`, grid `#EAEBEF`, ticks
  `{fontSize:10, fill:"#94a3b8", fontWeight:500}`. The Y axis is fixed at 50-100% with ticks every 10.
  - Lines: `strokeWidth` 2 (2.5 in overlay mode), `dot={false}`,
    `activeDot {r:5, fill, strokeWidth:2, stroke:"#fff"}`, 900ms ease-out, 100ms stagger.
  - Tooltip: `#111827`, radius 8px, padding `7px 11px`.
- **Leaderboard table** (`:519-592`): `w-full text-sm`.
  - `th`: `px-3 py-3 text-center`, with a title (`text-xs font-bold uppercase tracking-wide` in `#6B6D76`) over a
    subtitle (`text-xs` in `#94a3b8`, e.g. "(%) (MTD)").
  - Rows: `.fo-lb-row` with a `1px solid #EAEBEF` bottom border. Hover renders `rgba(22,119,255,.07)` plus
    `inset 3px 0 0 0 #1677ff`. The row hover is overridden by §0.3 only for the background.
  - The agency name is a link button (`#0C66E4`, 600).
  - Rates render in `rateColor`, with a `MiniTrend` delta. Amounts are 600 `#0B2A6F`.
- **Filter**: `.input w-auto` (radius 12px, 1px `#EAEBEF` border, padding `8px 12px`, 13px).

### 4.9 Shared `KPICard.jsx` (DPD, outreach and PTP pages)

- **Card**: `Card` plus `flex h-full flex-col px-5 py-4 transition-all`; `bg-[#FFF8F7]` when critical; clickable cards
  add `cursor-pointer hover:border-border hover:shadow-sm`.
- **Contents**: the same label, value, trend and sub classes as §4.1, but the value counts up (§1.13) and the trend
  comes from the `trend: 'up'|'down'` prop.
- **Used on DPD pages** as `grid grid-cols-1 md:grid-cols-3 lg:grid-cols-7 gap-3` (`DPD030.jsx:367`).

### 4.10 Filter and control bar (`RiskRadar.jsx:122-253`), the model for the bank's §5.2 filter bar

- **Bar**: `flex items-center gap-2.5 flex-wrap bg-card border border-border/60 rounded-card p-3`.
- **Search field**: `flex items-center gap-2 flex-1 min-w-[200px] bg-background border border-border/60 rounded-control px-3 h-9`,
  with a `Search` 14 icon and a `text-[13px]` input.
- **Select pill**: `flex items-center bg-background border border-border/60 rounded-control px-3 h-9`, wrapping
  `<select className="bg-transparent text-[13px] outline-none max-w-[150px]">`.
- **Reset**: `text-[12px] font-semibold text-muted-foreground hover:text-foreground px-2 h-9`.
- **Table card**: `bg-card border border-border/60 rounded-card overflow-hidden`.
  - Header row: `text-left text-[10.5px] uppercase tracking-wider text-muted-foreground border-b border-border/60 bg-muted/30`, with `th px-4 py-2.5 font-bold`.
  - Body rows: `border-b border-border/40 last:border-0 hover:bg-primary/[0.03] cursor-pointer`.
  - Risk chip: `text-[12px] font-medium px-2.5 py-1 rounded-full`, with background `RISK_SOFT_COLORS` and a
    `size-1.5` dot in `RISK_COLORS`.
- **Tier filter cards**: `rounded-card border p-4`, with background `RISK_SOFT_COLORS`. Active:
  `border-primary ring-2 ring-primary/15`.

### 4.11 Chart configuration

`src/lib/chartTheme.js` is the canonical file. Verbatim:

```js
const FONT = '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, Helvetica, Arial, sans-serif';
export const axisTick = { fontSize: 10, fill: BRAND.muted, fontWeight: 500, fontFamily: FONT };
export const gridProps = { strokeDasharray: '3 3', stroke: BRAND.grid, vertical: false };
export const axisProps = { tick: axisTick, axisLine: false, tickLine: false };
export const tooltipStyle = { fontSize: '11px', borderRadius: '10px', border: 'none', backgroundColor: BRAND.ink,
  color: '#F9FAFB', boxShadow: '0 8px 24px rgba(16,24,40,0.18)', padding: '8px 12px', fontFamily: FONT };
export const tooltipCursor = { stroke: BRAND.grid, strokeWidth: 1 };
export const tooltipBarCursor = { fill: BRAND.grid, opacity: 0.45 };
export const legendStyle = { fontSize: 10, fontWeight: 600, paddingTop: 8, fontFamily: FONT };
export const chartMargin = { top: 8, right: 8, left: -18, bottom: 0 };
export const seriesColor = (i) => CHART_SERIES[i % CHART_SERIES.length];
```

It is used by `components/Charts.jsx`. Three local variants are what the signature surfaces actually render:

| Surface | Tooltip | Ticks / axes | Legend |
|---|---|---|---|
| `PortfolioAnalytics` (`:33-38`) | `#111827` bg + 1px `#111827` border, **radius 8px**, `0 4px 12px rgba(0,0,0,0.15)`, 11px, pad `8px 12px`, no font set | `{fontSize:10, fill:BRAND.muted, fontWeight:500}`, `axisLine={false} tickLine={false}` | `iconSize={8} iconType="circle" wrapperStyle={{fontSize:10, paddingTop:6}}` |
| `RiskSimulator` (`:174-175`) | **light**: default white bg, 1px `#ECEDF1` border, radius 12px, 12px text, `0 8px 24px rgba(0,0,0,.08)` | `{fill:#98A2B3, fontSize:11}`, axis `stroke #98A2B3` (visible axis and tick lines) | `wrapperStyle={{fontSize:11}}` |
| `FieldRecovery` (`:62-71`) | `#111827`, radius 8px, pad `7px 11px`, `0 4px 12px rgba(0,0,0,0.18)` | `{fontSize:10, fill:"#94a3b8", fontWeight:500}` | custom HTML dots |

These settings are shared across charts:
- **Grid**: always `strokeDasharray "3 3"`, horizontal only, in `#ECEDF1` (or `#EAEBEF` on the field island).
- **Chart margin**: `left: -18` or `-20`.
- **Area fills**: `linearGradient` from 5% (opacity 0.25-0.4) to 95% (opacity 0).
- **Bars**: `radius [2,2,0,0]`, `[3,3,0,0]` or `[6,6,0,0]`, with `maxBarSize` 9 or 42.
- **Target pace line**: `BRAND.muted`, width 1.5, `strokeDasharray "5 5"`.
- **Chart heights**: 220-300px.

### 4.12 Number and currency formatters (verbatim)

On the client (`PortfolioAnalytics.jsx:40-43`, the same as `DrillPanel.jsx:15-16` and `DecisionAlerts.jsx:29-30`):

```js
const cr  = (v) => `₹${Number(v ?? 0).toFixed(2)} Cr`;     // "₹12.34 Cr" (input already in crores)
const cr1 = (v) => `₹${Number(v ?? 0).toFixed(1)} Cr`;
const rs  = (v) => `₹${Number(v ?? 0).toLocaleString('en-IN')}`;   // "₹1,23,456"
const n   = (v) => Number(v ?? 0).toLocaleString('en-IN');
```

The adaptive rupee formatter (`AccountDrawer.jsx:18-23`, `RiskRadar.jsx:18-23` and `PreDelinquency.jsx:19-24`) has
**no space** before the unit:

```js
const fmtINR = (n) => {
  const v = Number(n) || 0;
  if (v >= 1e7) return `₹${(v / 1e7).toFixed(2)}Cr`;
  if (v >= 1e5) return `₹${(v / 1e5).toFixed(2)}L`;
  return `₹${v.toLocaleString('en-IN')}`;
};
```

- `ManualIntervention.jsx:34-40` adds `if (amount == null) return '—'` and `Math.abs`.
- `Negotiations.jsx:6-10` uses one decimal and always falls back to L.

On the server, KPI values arrive pre-formatted (`backend/engines/portfolio_pulse.py:39-40, 299-305`):

```python
CR = 1e7; LAKH = 1e5
def _cr(v, dp=1):   return f"₹{v / CR:,.{dp}f} Cr"           # "₹1,234.5 Cr"
def _money(v):      return _cr(v) if abs(v) >= CR else f"₹{v / LAKH:,.1f} L"
```

The trend strings look like `f"{x:+.1f}% MoM"`, `f"{d:+,} MoM"`, `f"{d:+.1f} pp vs last month"` and
`f"{d:+.1f} pp vs prior 30d"`. The dates look like `"%d %b %Y"`, so the frame label reads `30 days to 22 Sep 2026`.

The chrome date is `new Date().toLocaleDateString('en-GB', { day:'numeric', month:'short', year:'numeric' })`
(`TopBar.jsx:18`). The field island uses `en-IN` with `{day:"2-digit", month:"short", year:"numeric"}`.

**Porting rule:** the bank's `kpi_catalog.py` must emit the same `value`, `trend`, `sub` and `basis` string shapes, and
the same `good` / `trendUp` semantics (`null` means neutral). `PulseKpiFlow` renders them as-is.

---

## 5. Page template

### 5.1 Annotated skeleton (the Overview pattern, which Command Center › Overview should copy)

```jsx
<div className="p-1 md:p-2 w-full mx-auto space-y-6">                 {/* page root: no max-width */}
  {/* HEADER BLOCK: title left, scope note right; no action buttons on the overview */}
  <div className="mb-8 flex flex-col justify-between gap-4 md:flex-row md:items-end">
    <div>
      <h2 className="text-2xl font-bold text-foreground tracking-tight">Portfolio Overview</h2>  {/* 32/40, 600 */}
      <div className="flex items-center gap-2 text-[11px] text-muted-foreground font-semibold mt-1 flex-wrap">
        <span className="w-1.5 h-1.5 rounded-full bg-success animate-pulse inline-block" />
        <span>Live book</span><span>·</span><span>{n} accounts</span><span>·</span><span>As of {asOf}</span>
      </div>
    </div>
    <p className="max-w-sm text-[11.5px] text-muted-foreground md:text-right">Scope note…</p>
  </div>

  <PulseKpiFlow pulse={pulse} onSelect={jumpToTab} />      {/* §4.1: 2 rows × 6 at xl, 3 at sm, 2 below */}

  <div id="portfolio-analytics" className="scroll-mt-4">
    <PortfolioAnalytics tab={tab} onTabChange={setTab} onDrill={…} />   {/* §4.2: title, segmented tabs, panels */}
  </div>
  {drill && <DrillPanel … />}                               {/* §4.3: portal */}
</div>
```

Loading state: `flex h-[60vh] items-center justify-center`, with "Loading portfolio…" at `text-sm font-semibold text-muted-foreground animate-pulse`.
Failure: `p-10 text-center`, `text-sm font-semibold text-muted-foreground`.

### 5.2 Header variants

| Variant | Used by | Title | Right side |
|---|---|---|---|
| **A: executive** | Overview, AI Decision Center | `text-2xl font-bold tracking-tight` + live-dot meta line | scope note `max-w-sm text-[11.5px] md:text-right` |
| **B: tool** | Risk Simulator, Risk Radar | `text-2xl font-extrabold` or `text-xl font-bold`, `flex items-center gap-2` with a primary icon (size 20-24) + `text-sm`/`text-[13px] text-muted-foreground mt-1 max-w-2xl` | bordered `btn-ghost` actions, or a big stat (`text-2xl font-bold` + `text-[11px]` caption) |
| **C: lifecycle** | DPD pages (`DPD030.jsx:344-365`) | root `p-6 max-w-[1400px] mx-auto space-y-6`; `text-xl font-bold tracking-tight` + `text-xs font-semibold mt-1` | status pills `text-[10px] bg-success/10 text-success border border-success/20 px-2.5 py-1 rounded-full font-bold uppercase tracking-wider` |

### 5.3 Tab styles

There are three tab styles. Pick by context, and do not mix them on one page.
1. Segmented pill bar (§4.2): analytics tabs.
2. Muted chip bar with icons (§4.7): tool pages.
3. Lifecycle tabs (`DPD030.jsx:375-386`): a `flex gap-1 border-b border-border/40 pb-px scrollbar-thin overflow-x-auto`
   container. Each tab is `px-4 py-3 text-xs font-bold whitespace-nowrap rounded-control transition-colors shrink-0`.
   Active: `bg-accent text-primary font-semibold`. Inactive: `text-muted-foreground hover:text-foreground hover:bg-muted`.

### 5.4 Grid compositions in use

| Composition | Classes |
|---|---|
| KPI flow | `grid-cols-2 sm:grid-cols-3 xl:grid-cols-6 gap-3.5` |
| Analytics 3/2 split | `grid-cols-1 lg:grid-cols-5 gap-5` + `lg:col-span-3` / `lg:col-span-2` |
| Analytics 2/1 split | `grid-cols-1 lg:grid-cols-3 gap-5` + `lg:col-span-2` |
| Halves | `grid-cols-1 lg:grid-cols-2 gap-5` |
| Tile rows | `grid-cols-2 md:grid-cols-4 gap-4` / `md:grid-cols-5` |
| Alerts | `grid-cols-1 xl:grid-cols-2 gap-3` |
| Tool catalogue | `grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4` |
| Config rail + results | `xl:grid-cols-[340px_minmax(0,1fr)] gap-5` |

---

## 6. Dependencies and assets

### 6.1 UI-relevant packages (`package.json`, with installed versions from `node_modules`)

| Package | Declared | Installed | Needed by the port? | Already in TIQCollect? |
|---|---|---|---|---|
| `tailwindcss` | ^3.4.19 | 3.4.19 | yes | yes, ^3.4.19 (locked 3.4.19) |
| `class-variance-authority` | ^0.7.1 | 0.7.1 | yes (button, badge) | **no, add it** |
| `clsx` / `tailwind-merge` | ^2.1.1 / ^3.6.0 | same | yes | yes |
| `recharts` | ^3.8.1 | 3.8.1 | yes | ^3.9.0 (minor drift, fine) |
| `lucide-react` | ^1.14.0 | 1.14.0 | yes | ^1.21.0 (glyphs may differ slightly; pin if the harness flags one) |
| `tailwindcss-animate` | ^1.0.7 | 1.0.7 | **no, see §7.4** | no |
| `framer-motion` | ^12.42.2 | 12.42.2 | only if the co-pilot or assistant is ported | no |
| `jspdf` | **undeclared** (transitive via `html2pdf.js` ^0.14.0) | 4.2.1 | board report PDF | no; declare it explicitly |
| `react` / `react-dom` | ^19.2.8 | 19.2.6 | | ^19.2.8 |
| `react-router` | ^8.3.0 | **7.15.0** (`node_modules` is stale against `package.json`) | | ^8.3.0 |

`postcss.config.js` is `{ tailwindcss: {}, autoprefixer: {} }`. `vite.config.js` is plain `@vitejs/plugin-react`.

### 6.2 Assets

| File | What | Port? |
|---|---|---|
| `src/assets/logo.png` | 1032×250 RGBA wordmark "TRANSORG" (navy slate) + "IQ" (lime green) | **yes**. Sidebar expanded (`h-[2rem]`); Login (white via `brightness-0 invert`) |
| `src/assets/IQ_Logo.png` | 250×250, lime "IQ" on white | **yes**. Collapsed rail (`h-7`) |
| `src/assets/hero.png` | 343×361, marketing page | no |
| `public/favicon.svg`, `public/icons.svg`, `react.svg`, `vite.svg` | the Vite template's purple bolt and social sprite | **no**. CC ships Vite's default favicon, and `<title>` is literally `frontend` |

- **Fonts:** none are shipped. **Icons:** `lucide-react` only; the design doc forbids emoji. The field island uses ▲ ▼
  as text glyphs.
- **Global CSS classes:** everything in §3.4, plus the utilities `.page-fade-in`, `.card-enter`, `.stagger-item`,
  `.live-dot`, `.scrollbar-thin` and `.sidebar-scrollbar-hide` (`index.css:267-364`), the global scrollbar
  (`:367-369`), the range sliders (`:372-396`) and reduced motion (`:628-636`).

---

## 7. Porting notes

### 7.1 Tailwind version: both apps are on 3.4.19, so CC's config ports directly

- CC declares `tailwindcss ^3.4.19` and has 3.4.19 installed.
- TIQCollect's `frontend/package.json` declares `^3.4.19`, and `package-lock.json` resolves 3.4.19. Its CSS entry
  (`frontend/src/index.css:4-6`) uses the same `@tailwind base; @tailwind components; @tailwind utilities;` and the
  same `@layer` blocks, with the same `postcss.config.js` shape.
- **No Tailwind 4 adaptation is needed.** There are no `@theme` tokens and no `@import "tailwindcss"`. The JS config
  object, `@apply`, `theme.extend` and the `hsl(var(--x))` colour mapping all carry over unchanged.

The problem is **collision**, not syntax. TIQCollect's CSS entry (`:9-41`) defines **the same shadcn variable names on
`:root`** with different values:

| Variable | TIQCollect `:root` | CC effective |
|---|---|---|
| `--primary` / `--ring` / `--accent-foreground` | `221 83% 53%` (#2563EB) | `243 75% 59%` (#4F46E5) |
| `--primary-dark` | `224 76% 48%` | `245 58% 51%` |
| `--secondary` | `251 91% 66%` | `255 91% 66%` |
| `--accent` | `214 100% 97%` | `242 80% 96%` |
| `--foreground` | `221 39% 11%` | `220 43% 11%` |
| `--card-foreground` / `--popover-foreground` | `221 39% 11%` | `240 10% 12%` |
| `--teal` | — | `175 84% 32%` |
| success, warning, destructive, background, muted, muted-fg, border, input, `--radius` | identical | identical |

TIQCollect's Tailwind theme also collides on these keys:

| Key | TIQCollect | CC |
|---|---|---|
| `fontSize` | only adds `2xs`, so `lg`/`xl`/`2xl` are Tailwind's 18 / 20 / 24px | 20 / 24 / 32px |
| `borderRadius.md` | 0.625rem | 0.75rem |
| `boxShadow.resting` / `hover` / `card` / `tint-*` | all `0 1px 2px rgba(16,24,40,0.04)` | distinct values |
| `@keyframes card-enter` | 18px | 16px |
| `brand` | a scale | flat aliases |
| `plugins` | `[]` | `tailwindcss-animate` |
| `screens` | adds `xs: 375px` | defaults |

Copied verbatim into TIQCollect's single build, CC's class strings would therefore render **wrong**. For example, the
page title `text-2xl` would render 24px instead of 32px, and `rounded-md` would render 10px instead of 12px.

### 7.2 Recommended approach: a second, scoped Tailwind entry for `/bank`

This approach was **verified** on 3.4.19 with CC's own `tailwindcss` and `postcss`, compiled in a scratch directory.

1. **`frontend/tailwind.bank.config.js`**: CC's `tailwind.config.js` verbatim, plus these changes:

   ```js
   content: ["./src/bank/**/*.{ts,tsx}", "./src/simulator/**/*.{ts,tsx}"],
   important: ".bank-root",                 // every utility becomes `.bank-root .x` (verified)
   corePlugins: { preflight: false },       // TIQ's preflight already applies; @tailwind base now emits only the --tw-* defaults (verified)
   plugins: [],                             // drop tailwindcss-animate (see 7.4)
   // keyframes renamed with a bank- prefix; the animation keys keep their CC names, so class strings copy verbatim (verified):
   keyframes: { "bank-fade-in": {…}, "bank-slide-in": {…}, "bank-scale-in": {…}, "bank-card-enter": {…}, … },
   animation: { "fade-in": "bank-fade-in 200ms cubic-bezier(0.16, 1, 0.3, 1) both", … },
   ```

2. **`frontend/src/bank/theme/bank.css`**, imported only by the lazy `/bank` and `/simulator` route modules:

   ```css
   @config "../../../tailwind.bank.config.js";   /* Tailwind ≥3.2: per-file config (verified) */
   @tailwind base; @tailwind components; @tailwind utilities;
   ```

   Then copy CC's `index.css` **in the same order**, with four adaptations:
   - Change `:root` to `.bank-root`. Declare **all** of CC's variables with their effective values (§1.1), not only
     the ones that differ.
   - Write `body {…}` as `.bank-root {…}`, adding `min-height: 100vh`.
   - Write `* {…}` as `:where(.bank-root) *, :where(.bank-root) ::before, :where(.bank-root) ::after {…}`. The
     `:where()` keeps specificity at 0, as in CC.
   - Prefix **every other** selector with exactly one `.bank-root`. This covers the `@layer components` classes, which
     `important` does **not** prefix (verified), and every unlayered Soft Card rule, including the `!important` ones.
     `@layer utilities` classes are prefixed automatically (verified).

   Rename the plain-CSS keyframes to `bank-fadeIn`, `bank-slideUpCard`, `bank-heroFadeUp`, `bank-liveRing` and
   `bank-cursorBlink`.

   Because every CC rule gains exactly one class of specificity, **relative specificity and order are unchanged, so the
   cascade resolves the same way**. That includes:
   - the §0.3 table overrides;
   - the `.font-bold → 600` rule;
   - the `[class*="transition-"]` curve;
   - variant rules landing last. With no `@tailwind variants`, Tailwind appends them at the end, as in CC.

3. **The route wrapper** is `<div className="bank-root">…shell…</div>`. Do not put utilities on the wrapper element
   itself: `.bank-root .x` only matches descendants.

4. **TIQCollect's own config** can stay as it is. It will also scan `src/bank/**`, but any TIQ utility it generates
   there has specificity (0,1,0) and loses to `.bank-root .x` at (0,2,0). Excluding `src/bank` from its `content` is
   optional tidiness.

This approach leaves the existing views untouched. Nothing is added to `:root`, and every rule is under `.bank-root`
except the prefixed keyframes and the `*` `--tw-*` defaults, which are identical to TIQ's.

The fallback, a single build with arbitrary values, rewrites every collided class: `text-2xl` becomes
`text-[2rem] leading-[2.5rem]`, `rounded-md` becomes `rounded-[12px]`, and each shadow token becomes an arbitrary shadow.
That breaks the "port, not reinterpret" rule and drifts the first time someone writes `text-2xl`. It is not recommended.

### 7.3 Portals must render inside `.bank-root`

`Dialog`, `DrillPanel`, `WorkspaceModal`, `AccountDrawer` and `RaiseQueryModal` all call
`createPortal(…, document.body)`. Outside the wrapper they would lose the CSS variables and every scoped rule. CC's own
`FieldAnalytics.css:194-195` hit exactly this.

Port them with a `getBankPortalRoot()` helper that lazily appends `<div class="bank-root" data-bank-portal>` to
`<body>`, and pass that to `createPortal`.

Keep CC's z-index ladder:

| Layer | z-index |
|---|---|
| TopBar | 30 |
| Sidebar | 50 |
| Co-pilot | 80 |
| Raise a Query | 100 |
| Account 360 | 120 |
| Drill drawer | 9998 |
| Dialog / Workspace | 9999 |
| Toast | 200 (inside the modal) |

### 7.4 Do not install `tailwindcss-animate` in the bank build

Its `animate-in` utility emits global `@keyframes enter` and `@keyframes exit`. TIQCollect's config already defines an
`enter` keyframe (`animate-enter`). Keyframes are global, so the later one wins, and TIQ's `animate-enter` animations
would silently change or stop.

None of the §4 composites uses the plugin. Only `AccountDrawer` (`animate-in slide-in-from-right duration-300`),
`RaiseQueryModal` and `Negotiations` do. If those are ported, replace those classes with a `bank-`prefixed keyframe
(`translateX(100%)→0`, 300ms).

### 7.5 Copy verbatim, or type first

- **Copy verbatim:**
  - `colors.js` and `chartTheme.js` → `bank/theme/{colors,chartTheme}.ts`, with `as const`, `Record<string,string>` for
    `DPD_COLORS` and friends, and `seriesColor(i: number)`.
  - `utils.ts` (`cn(...inputs: ClassValue[])`).
  - Every CVA string and every class string in §2-§4.
  - `index.css` → `bank.css` (§7.2).
  - Both logos.
- **Needs TypeScript typing** (TIQCollect uses TypeScript ~6.0, `strict`):
  - `Button`: `React.ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof buttonVariants> & { asChild?: boolean }`.
    Keep `asChild` accepted-and-ignored for parity.
  - `Badge`: `React.HTMLAttributes<HTMLDivElement> & VariantProps<typeof badgeVariants>`.
  - `ui/sidebar`: the context type `{ state: "expanded"|"collapsed"; open: boolean; setOpen(v: boolean | ((o: boolean) => boolean)): void; toggleSidebar(): void }`.
    The CSS custom properties in `style` need a `React.CSSProperties` cast.
  - `SidebarMenuButton`'s `cloneElement` ref typing.
  - `Dialog` (`open`, `onOpenChange?: (open: boolean) => void`).
  - lucide icons as `LucideIcon`.
  - Recharts `contentStyle` as `React.CSSProperties`, and the `formatter` signatures.
  - The KPI payload, typed from `_kpi(...)`: `{ id, label, value, sub, trend, trendUp: boolean|null, good: boolean|null, basis, drill, tone }`.
- **Lint.** Move `buttonVariants`, `badgeVariants` and `useSidebar` into their own non-component files.
  TIQCollect's `react-refresh/only-export-components` rule fails on files that export both a component and a
  non-component; CLAUDE.md records the same fix for `BeatContext`.

### 7.6 CC's quirks: keep them for parity, record them, fix them later on purpose

To keep screenshots identical, port these **as they render**:
- the inert `animate-slide-up`;
- the invisible blobs (these can simply be dropped, since they have no visible effect);
- the `!important` radius and shadow overrides;
- the forced 600 weight;
- the neutralised mono font;
- the §0.3 table overrides;
- the three tooltip styles;
- outlined primary buttons, with the board CTA and login as the filled exceptions.

Any "improvement", such as a KPI stagger, a tone colour on the KPI cards, or a working mono font, is a deliberate
divergence to decide after parity is green. It must not slip in during the port.

The stray `dark:` classes (`AICoPilot.jsx`) should be dropped. The bank portal is light-only, like CC.

---

## 8. Reference screenshot checklist (for the UI06 parity harness)

**Harness settings.**
- Chromium, `colorScheme: 'light'`, `reducedMotion: 'reduce'`. This triggers CC's own 1ms rule; also wait **≥ 1.0s**
  after network idle for the rAF count-up and the Recharts draw.
- Freeze `Date` so the top-bar date is stable.
- **Run CC and the bank portal on the same OS image.** The font is the system stack: Segoe UI on Windows,
  Roboto / Helvetica / Arial on Linux.
- Seed `localStorage`:
  - `cc_launched=true`
  - `cc_persona={"role":"collection_head","product":"all","geography":"All India"}`
  - `sidebar:state:v2` = `"closed"`, or `"open"` for the expanded variants.
- **Viewports:**
  - **1440×900** (primary);
  - **1280×800** (the `xl` boundary: 6-up KPIs and 2-up alerts);
  - **1024×768** (`lg`: split panels);
  - 768×1024 (`md`).

| # | CC route / state | How to reach it | Bank counterpart (plan §5.1) |
|---|---|---|---|
| S1 | Shell, sidebar **collapsed** (default), `/` | seed `closed` | every `/bank/*` page |
| S2 | Shell, sidebar **expanded** | seed `open`, or Ctrl+B | same |
| S3 | Collapsed-rail flyout | hover the Executive rail icon | same |
| S4 | Search dropdown: empty (Quick Access) and with results | focus search; type `npa` | global search |
| S5 | Raise a Query modal | top-bar button | — |
| S6 | AI Co-Pilot panel open | bottom-right pill | Tech Ops › AI Agents (chrome only) |
| O1 | `/` Portfolio Overview, top (header + 12 KPIs + narrative) | load | Command Center › Overview |
| O2 | KPI card hover and focus rings | hover / Tab to one card | Overview |
| O3-O7 | `/` analytics tabs: Exposure, Migration, Recovery, Cost to Collect, Concentration | click each tab; scroll to `#portfolio-analytics` | Analytics (8 tabs) |
| O8 | Drill drawer, loaded | click a DPD-ladder row (e.g. `90-180`) | Analytics drill (C05) |
| O9 | Drill drawer from a transition-matrix cell, and from a heat-grid row | Migration / Exposure tabs | drill |
| O10 | Overview loading and failure states | throttle / block `/api/portfolio/*` | Overview |
| D1 | `/decision-center`: context strip, tool catalogue, alerts collapsed | load | Command Center › Alerts |
| D2 | One alert of each severity expanded (critical, warning, info) | click cards | Alerts |
| W1 | Workspace › Board Intelligence Report | Decision Center › "Board Intelligence Report" | AI Strategy › Board Reports |
| W2 | Workspace › Budget Optimizer (with Run Analysis) and Portfolio Stress Test | catalogue | Scenario Lab |
| W3 | Workspace toast | click Save Scenario | — |
| R1-R5 | `/risk-simulator`: Dashboard (fan chart, tornado), Provisioning (IFRS-9), Sensitivity (heat grid), Compare Scenarios, Approval & Memo | tabs | AI Strategy › Monte Carlo Simulator |
| R6 | Risk Simulator: a preset chip active + a slider mid-drag state + the Full Portfolio Matrix modal | click Adverse; "View Full Portfolio Matrix" | Monte Carlo |
| F1 | `/field`: KPI row, Total trend, leaderboard | load | Agencies › Performance |
| F2 | `/field` Per Agency mode + one agency pill selected (spotlight) | toggle; click an agency | Agencies › Performance |
| F3 | `/field/analytics?agency=ABC%20Collections` and `/field/cases?agency=ABC%20Collections` | leaderboard link | Agencies › Directory / agency detail |
| L1 | `/risk-radar`: tier cards, filter bar, table | load | filter bar (§5.2), tables |
| L2 | `/dpd/0-30` (Variant C header, 7-up `KPICard`, lifecycle tabs) + `/dpd/npa` | load | tables / tabs reference |
| L3 | `/login` at 1440 (split hero) and at 900 (form only) | clear `cc_launched` | bank login |
| X1 | Account 360 drawer | search an account id and select it | account drill |
| X2 | Remaining routes, for completeness: `/ai-assistant`, `/dpd/pre-delinquency`, `/dpd/30-60`, `/dpd/60-90`, `/ptp-tracker`, `/comms-control`, `/outreach`, `/manual-intervention`, `/negotiations`, `/legal` | load | — |

**Pairing rule for diffs.** Compare the bank screen against the CC screen in the right-hand column at the same viewport
and sidebar state. Mask live numbers only where the bank data legitimately differs. **Never mask chrome, spacing or
colour.**

---

## 9. Deviations recorded in the port (UI02–UI05, 2026-09-24)

§7.6 says a divergence from what CC renders is a deliberate decision, never a
side effect of the port. This is the list of every one the port made, so a
parity diff (UI06) that shows one is an expected result, not a regression. The
code carries the same reasons at each site; in `src/bank/**`, search for "UI spec §9".

**None of these changes a pixel of CC's resting state unless marked VISIBLE.**

### Build and CSS

| Where | CC | Port | Why |
|---|---|---|---|
| `tailwind.bank.config.js` | the `container` core plugin is on | **off** | `important: ".bank-root"` does not wrap the components layer, so it came out as a bare, global `.container`. CC's pages do not use it (§1.12). This is a fifth config change beyond §7.2's four |
| `bank.css` | `.app-content > .p-6`, `.font-bold/-extrabold/-black` | written as `[class~="…"]`, and the 4 rules CC's `@apply` derives from them written out ("Derived") | Ported literally they trip Tailwind's circular-`@apply` check. The compiled result is CC's |
| `bank.css` | — | **TIQCollect → bank leak guards** | TIQ's global CSS (badge `::before` dots, `label`, `select`/`textarea`, focus rings, `.btn-ghost`, the (0,3,1) text-input rule) reaches inside `.bank-root`. The guards restore what CC renders |
| `bank.css` | — | `.bank-root[data-bank-portal] { display: contents }` | The portal container (§7.3) must not paint a 100vh block |
| `lib/cva.ts` | `class-variance-authority` 0.7.1 | a local equivalent of its `variants`/`defaultVariants` resolution | No new dependency; the variant tables and output are identical (tested) |

### Accessibility (added, and CC has none of it)

| Where | Port adds |
|---|---|
| `ui/dialog.tsx`, `DrillPanel`, `WorkspaceModal` | `role="dialog"`, `aria-modal`, `aria-labelledby` its title (`aria-describedby` on Dialog). One focus hook (`lib/useModalFocus.ts`): focus moves in, Tab is trapped, Escape closes, focus returns to the opener, page scroll locks, and overlays stack |
| `WorkspaceModal` | Escape and scroll lock (CC had neither); accessible names on the icon-only Download/Share; toast `role="status"` |
| `BankSidebar` | a `navigation` landmark, `aria-current="page"`, `aria-expanded` on the rail toggles. The collapsed-rail flyout is a disclosure: hover still opens it as in CC, and so do click, Enter and Space, with `aria-expanded`/`aria-controls`. Closed, it is `invisible`, so its links leave the tab order (CC leaves them focusable at opacity 0). Escape and focus-out close it. The rail stays collapsed by default (CC's default, state S1) |
| `DataTable`, `HeatGrid` | clickable `<tr>`s are focusable and open on Enter or Space, with a keyboard-only focus ring and an optional `aria-label` (`rowActivation.ts`) |
| `AnalyticsTabBar` | `role="tablist"` / `tab` / `aria-selected` |
| `DecisionAlerts` | `aria-expanded` on each card toggle |

### Honesty (the bank tree must not claim data it does not have)

| Where | CC | Port |
|---|---|---|
| `BankTopBar` | a hard-coded pulsing "Live System" | **VISIBLE**: absent unless an optional `systemStatus` is passed; it will be driven by real state (e.g. the last bank-feed ingest) |
| `ExecutiveHeader` | the green pulsing dot, always on | **VISIBLE**: opt-in (`live`), off on placeholder and sample pages |
| `DecisionAlerts` | title "AI Alerts"; subtitle "Derived live from the loan book…" | **VISIBLE**: default title "Alerts", because the bank's alerts are SQL rules and CLAUDE.md forbids a rule presenting itself as AI. No default subtitle: provenance is the caller's to state |
| `WorkspaceModal` share text | "Decision Center — …", "(live simulation, …)" | "Command Center — …", "(unsaved simulation, …)". The bank has no Decision Center, and nothing is live |
| `PulseKpiFlow`, `DecisionAlerts`, `DrillPanel`, `WorkspaceModal` | — | **VISIBLE when set**: a `sampleData` prop shows a "Sample data" badge (CC's warning colours). The gallery sets it on all four, and on its analytics heading |
| `WorkspaceModal` with `sampleData` | — | the CSV export opens with a one-cell "Sample data — …" line and is named `SAMPLE-<file>`; the clipboard summary ends with the same line. An export travels without the badge beside it (`components/sampleData.ts` holds the one wording) |
| `/bank/_gallery` | — | dev-only, like `/simulator` (`VITE_ENABLE_BANK_GALLERY=1` in production); off, the route, its links and its chunk are absent |

### Data-driven props (CC fetched; the port takes data)

`PulseKpiFlow` (rows as a prop, since the bank's twelve KPIs differ), `DecisionAlerts`, `DrillPanel` (`data`, `null` =
loading; splits as a list so agency/region/agent drills need no markup), `WorkspaceModal` (`children` instead of
CC's ten built-in workshops; `mode`, `onRunAnalysis`, `getExport`), `HeatGrid` (`rowHeader`; a missing cell reads 0),
`TransitionMatrix` (the observed count is optional; the "pooled across the 12-month account panel" phrase is dropped,
because it describes CC's data, not the component).

### Smaller behaviour differences

| Where | CC | Port |
|---|---|---|
| `BucketChip` | an unknown bucket renders `color: undefined`, background `"undefined15"` (inherited colour, no fill) | falls back to `BRAND.slate`, so an unmapped bucket is still a chip |
| `WorkspaceModal` CSV | headers joined bare; values `"${v}"` | headers quoted; `"` doubled (RFC 4180); a text cell starting `= + - @`, tab or CR is prefixed `'` (CSV injection). Numbers untouched |
| `WorkspaceModal` | storage key `decisionCenter:scenario:<panel>`; timers not cleared | `bankWorkspace:scenario:<panel>`; timers cleared on unmount |
| charts | fixed gradient ids (`delqFill`, `recFill`) | `useId`-based, so two charts on a page cannot share one gradient |
| `BankSearchBar` | indexes routes, tools, live KPIs/alerts and accounts; arrow keys on `window` | indexes the bank pages plus `extra` entries; arrow keys on the input (equivalent, as the list is open only while it has focus); placeholder "Search pages, KPIs, alerts…" |
| `BankTopBar` | date recomputed every render; slippage pill from its own fetch | date fixed at mount; the pill is the `standingAlert` prop |
| "Raise a Query" | `RaiseQueryModal` | a Dialog built from the primitives, with Submit disabled and labelled "not connected yet" |
