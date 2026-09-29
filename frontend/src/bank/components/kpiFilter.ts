// The Command Center's global filter (plan §5.2, task C02), as it lives in the
// URL. One object; every bank endpoint takes it as query parameters (backend
// app/services/bank/kpi_filter.py validates the same names). Pure, so a link
// can be shared and the round trip tested without a browser.

export const PERIODS = ["mtd", "l30", "qtd", "fytd", "custom"] as const;
export type Period = (typeof PERIODS)[number];

export interface KpiFilter {
  period: Period;
  start?: string; // ISO date, custom only
  end?: string; // ISO date, custom only
  geo?: string; // a region id at any level
  agency?: string;
  product?: string;
  bucket?: string;
  security?: "SECURED" | "UNSECURED";
}

const KEYS = ["period", "start", "end", "geo", "agency", "product", "bucket", "security"] as const;
const ISO = /^\d{4}-\d{2}-\d{2}$/;

/** Read the filter from a URL's search params. Anything malformed is dropped, never guessed. */
export function filterFromParams(params: URLSearchParams): KpiFilter {
  const period = (PERIODS as readonly string[]).includes(params.get("period") ?? "")
    ? (params.get("period") as Period)
    : "mtd";
  const f: KpiFilter = { period };
  const start = params.get("start");
  const end = params.get("end");
  if (period === "custom") {
    if (start && ISO.test(start) && end && ISO.test(end) && start <= end) {
      f.start = start;
      f.end = end;
    } else {
      f.period = "mtd"; // an incomplete custom range falls back rather than sending a request the API refuses
    }
  }
  for (const k of ["geo", "agency", "product", "bucket"] as const) {
    const v = params.get(k);
    if (v) f[k] = v;
  }
  const sec = params.get("security");
  if (sec === "SECURED" || sec === "UNSECURED") f.security = sec;
  return f;
}

/** The filter as search params: only what differs from the default, in a stable order. */
export function filterToParams(f: KpiFilter): URLSearchParams {
  const out = new URLSearchParams();
  for (const k of KEYS) {
    const v = f[k];
    if (v === undefined || v === "" || (k === "period" && v === "mtd")) continue;
    if ((k === "start" || k === "end") && f.period !== "custom") continue;
    out.set(k, String(v));
  }
  return out;
}

/** How many filters narrow the view (the period is always set, so it does not count). */
export function activeCount(f: KpiFilter): number {
  return (["geo", "agency", "product", "bucket", "security"] as const).filter((k) => f[k]).length;
}
