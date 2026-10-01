// The Command Center's global filter bar (plan §5.2, task C02). One row above
// the charts; every choice lives in the URL (kpiFilter.ts) so a view can be
// shared by link. The options come from GET /bank/filters, the caller's bank only.
import { useQuery } from "@tanstack/react-query";
import api from "@/api/axios";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Select } from "../ui/select";
import { activeCount, type KpiFilter, type Period } from "./kpiFilter";
import { useKpiFilter } from "./useKpiFilter";

interface Option {
  value: string;
  label: string;
  level?: string | null;
  parent?: string | null;
}

export interface FilterOptions {
  periods: Option[];
  geography: Option[];
  agencies: Option[];
  products: Option[];
  buckets: Option[];
  security: Option[];
}

const LEVEL_INDENT: Record<string, string> = { ZONE: "", REGION: "  ", STATE: "    ", CITY: "      " };

function Choice({ label, value, options, onChange, all }: {
  label: string; value?: string; options: Option[]; onChange: (v: string | undefined) => void; all: string;
}) {
  return (
    <label className="flex min-w-[9.5rem] flex-col gap-1">
      <span className="text-[10.5px] font-semibold uppercase tracking-wide text-muted-foreground">{label}</span>
      <Select value={value ?? ""} onChange={(e) => onChange(e.target.value || undefined)} aria-label={label}>
        <option value="">{all}</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>{(o.level && LEVEL_INDENT[o.level]) || ""}{o.label}</option>
        ))}
      </Select>
    </label>
  );
}

export function FilterBar() {
  const [f, setF] = useKpiFilter();
  const q = useQuery({
    queryKey: ["bank", "filters"],
    queryFn: async () => (await api.get<FilterOptions>("/bank/filters")).data,
    staleTime: 10 * 60_000,
  });
  const o = q.data;
  const set = (patch: Partial<KpiFilter>) => setF({ ...f, ...patch });
  // An agency that isn't ACTIVE yet (PENDING onboarding, SUSPENDED) has no
  // book — filtering to it blanks the whole page with an honest but
  // confusing "no reading for this selection." Say so in the option itself,
  // so picking it is an informed choice, not a trap (owner hit this on
  // Hooghly, still mid-onboarding).
  const agencies = (o?.agencies ?? []).map((a) =>
    a.level && a.level !== "ACTIVE" ? { ...a, label: `${a.label} (${a.level.toLowerCase()})` } : a);

  return (
    <div className="flex flex-wrap items-end gap-3 rounded-[16px] border border-border/50 bg-card px-4 py-3">
      <label className="flex min-w-[10rem] flex-col gap-1">
        <span className="text-[10.5px] font-semibold uppercase tracking-wide text-muted-foreground">Period</span>
        <Select value={f.period} aria-label="Period"
                onChange={(e) => set({ period: e.target.value as Period, start: undefined, end: undefined })}>
          {(o?.periods ?? [{ value: "mtd", label: "Month to date" }]).map((p) => (
            <option key={p.value} value={p.value}>{p.label}</option>
          ))}
        </Select>
      </label>
      {f.period === "custom" && (
        <>
          <label className="flex flex-col gap-1">
            <span className="text-[10.5px] font-semibold uppercase tracking-wide text-muted-foreground">From</span>
            <Input type="date" value={f.start ?? ""} onChange={(e) => set({ start: e.target.value || undefined })} />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[10.5px] font-semibold uppercase tracking-wide text-muted-foreground">To</span>
            <Input type="date" value={f.end ?? ""} onChange={(e) => set({ end: e.target.value || undefined })} />
          </label>
        </>
      )}
      <Choice label="Geography" value={f.geo} options={o?.geography ?? []} all="All India" onChange={(geo) => set({ geo })} />
      <Choice label="Agency" value={f.agency} options={agencies} all="All agencies" onChange={(agency) => set({ agency })} />
      <Choice label="Product" value={f.product} options={o?.products ?? []} all="All products" onChange={(product) => set({ product })} />
      <Choice label="DPD bucket" value={f.bucket} options={o?.buckets ?? []} all="All buckets" onChange={(bucket) => set({ bucket })} />
      <Choice label="Security" value={f.security} options={o?.security ?? []} all="Secured and unsecured"
              onChange={(s) => set({ security: s as KpiFilter["security"] })} />
      {activeCount(f) > 0 && (
        <Button variant="ghost" size="sm" onClick={() => setF({ period: f.period, start: f.start, end: f.end })}>
          Clear {activeCount(f)} filter{activeCount(f) > 1 ? "s" : ""}
        </Button>
      )}
    </div>
  );
}
