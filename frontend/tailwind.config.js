/** @type {import('tailwindcss').Config} */
export default {
  // src/bank has its own scoped build (tailwind.bank.config.js via
  // src/bank/bank.css). Excluding it here keeps this bundle byte-identical to
  // what it was before the bank portal existed — bank class strings would
  // otherwise add TIQCollect-themed copies of CC's classes to it (spec §7.2.4).
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}", "!./src/bank/**"],
  theme: {
    container: {
      center: true,
      padding: "1rem",
      screens: { "2xl": "1400px" },
    },
    extend: {
      colors: {
        border:     "hsl(var(--border) / <alpha-value>)",
        input:      "hsl(var(--input) / <alpha-value>)",
        ring:       "hsl(var(--ring) / <alpha-value>)",
        background: "hsl(var(--background) / <alpha-value>)",
        foreground: "hsl(var(--foreground) / <alpha-value>)",
        primary: {
          DEFAULT:    "hsl(var(--primary) / <alpha-value>)",
          foreground: "hsl(var(--primary-foreground) / <alpha-value>)",
          dark:       "hsl(var(--primary-dark) / <alpha-value>)",
        },
        secondary: {
          DEFAULT:    "hsl(var(--secondary) / <alpha-value>)",
          foreground: "hsl(var(--secondary-foreground) / <alpha-value>)",
        },
        destructive: {
          DEFAULT:    "hsl(var(--destructive) / <alpha-value>)",
          foreground: "hsl(var(--destructive-foreground) / <alpha-value>)",
        },
        muted: {
          DEFAULT:    "hsl(var(--muted) / <alpha-value>)",
          foreground: "hsl(var(--muted-foreground) / <alpha-value>)",
        },
        accent: {
          DEFAULT:    "hsl(var(--accent) / <alpha-value>)",
          foreground: "hsl(var(--accent-foreground) / <alpha-value>)",
        },
        popover: {
          DEFAULT:    "hsl(var(--popover) / <alpha-value>)",
          foreground: "hsl(var(--popover-foreground) / <alpha-value>)",
        },
        card: {
          DEFAULT:    "hsl(var(--card) / <alpha-value>)",
          foreground: "hsl(var(--card-foreground) / <alpha-value>)",
        },
        brand: {
          50:  "#EFF6FF",
          100: "#DBEAFE",
          200: "#BFDBFE",
          500: "#3B82F6",
          600: "#2563EB",
          700: "#1D4ED8",
          900: "#1E3A8A",
        },
        success: {
          DEFAULT:    "hsl(var(--success) / <alpha-value>)",
          foreground: "hsl(var(--success-foreground) / <alpha-value>)",
          50:  "#F3FBF7",
          100: "#DCF5E9",
          200: "#B7EBD3",
          400: "#32C880",
          500: "#12B76A",
          600: "#079455",
          700: "#067647",
        },
        warning: {
          DEFAULT:    "hsl(var(--warning) / <alpha-value>)",
          foreground: "hsl(var(--warning-foreground) / <alpha-value>)",
          50:  "#FEF8EF",
          100: "#FDF0DC",
          200: "#FBE0B9",
          500: "#F79009",
          600: "#DC6803",
          700: "#B54708",
        },
        danger: {
          50:  "#FEF4F3",
          100: "#FDE7E6",
          200: "#FBCBC8",
          500: "#F04438",
          600: "#D92D20",
          700: "#B42318",
        },
        bucket: {
          current: "#12B76A",
          b1: "#2E90FA",
          b2: "#F79009",
          b3: "#F04438",
          npa: "#7A5AF8",
        },
      },
      borderRadius: {
        card:    "1rem",
        control: "0.625rem",
        modal:   "1rem",
        pill:    "9999px",
        lg: "var(--radius)",
        md: "0.625rem",
        sm: "0.5rem",
      },
      fontFamily: {
        sans: [
          "-apple-system",
          "BlinkMacSystemFont",
          '"SF Pro Text"',
          '"Segoe UI"',
          "Roboto",
          "Helvetica",
          "Arial",
          "sans-serif",
        ],
        display: [
          "-apple-system",
          "BlinkMacSystemFont",
          '"SF Pro Display"',
          '"Segoe UI"',
          "Roboto",
          "Helvetica",
          "Arial",
          "sans-serif",
        ],
        mono: [
          "ui-monospace",
          '"SF Mono"',
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "monospace",
        ],
      },
      fontSize: {
        "2xs": ["0.625rem", { lineHeight: "0.875rem" }],
      },
      boxShadow: {
        resting:              "0 1px 2px rgba(16,24,40,0.04)",
        hover:                "0 1px 2px rgba(16,24,40,0.04)",
        premium:              "0 8px 24px rgba(16,24,40,0.10)",
        glow:                 "0 0 0 3px rgba(79,70,229,0.12)",
        card:                 "0 1px 2px rgba(16,24,40,0.04)",
        "tint-primary":       "0 1px 2px rgba(16,24,40,0.04)",
        "tint-primary-hover": "0 1px 2px rgba(16,24,40,0.04)",
        "tint-warning":       "0 1px 2px rgba(16,24,40,0.04)",
        "tint-destructive":   "0 1px 2px rgba(16,24,40,0.04)",
        "tint-success":       "0 1px 2px rgba(16,24,40,0.04)",
      },
      spacing: {
        "4.5": "1.125rem",
      },
      transitionTimingFunction: {
        spring: "cubic-bezier(0.2, 0, 0, 1)",
      },
      screens: {
        xs: "375px",
      },
      keyframes: {
        slideInRight: {
          "0%":   { transform: "translateX(100%)" },
          "100%": { transform: "translateX(0)" },
        },
        enter: {
          "0%":   { opacity: "0", transform: "translateY(16px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "card-enter": {
          from: { opacity: "0", transform: "translateY(18px)" },
          to:   { opacity: "1", transform: "translateY(0)" },
        },
        "fade-in": {
          from: { opacity: "0" },
          to:   { opacity: "1" },
        },
        "scale-in": {
          from: { opacity: "0", transform: "scale(0.95) translateY(8px)" },
          to:   { opacity: "1", transform: "scale(1) translateY(0)" },
        },
        "slide-in": {
          from: { opacity: "0", transform: "translateY(8px)" },
          to:   { opacity: "1", transform: "translateY(0)" },
        },
        "zoom-in-95": {
          from: { opacity: "0", transform: "scale(0.95)" },
          to:   { opacity: "1", transform: "scale(1)" },
        },
        "accordion-down": {
          from: { height: "0" },
          to:   { height: "var(--radix-accordion-content-height)" },
        },
        "accordion-up": {
          from: { height: "var(--radix-accordion-content-height)" },
          to:   { height: "0" },
        },
      },
      animation: {
        slideInRight:     "slideInRight 0.22s ease-out",
        enter:            "enter 0.4s cubic-bezier(0.16,1,0.3,1) both",
        "card-enter":     "card-enter 400ms cubic-bezier(0.16,1,0.3,1) both",
        "fade-in":        "fade-in 200ms cubic-bezier(0.16,1,0.3,1) both",
        "scale-in":       "scale-in 280ms cubic-bezier(0.16,1,0.3,1) both",
        "slide-in":       "slide-in 300ms cubic-bezier(0.16,1,0.3,1) both",
        "zoom-in-95":     "zoom-in-95 200ms cubic-bezier(0.16,1,0.3,1) both",
        "accordion-down": "accordion-down 200ms cubic-bezier(0.16,1,0.3,1)",
        "accordion-up":   "accordion-up 200ms cubic-bezier(0.16,1,0.3,1)",
      },
    },
  },
  plugins: [],
};
