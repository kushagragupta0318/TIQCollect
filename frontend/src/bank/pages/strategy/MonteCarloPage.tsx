// AI Strategy › Monte Carlo Simulator (plan §7, task E05). Runs the bank's own
// book forward under a macro scenario and a set of levers, and shows what the
// simulated paths did — as bands, never as a single number.
//
// Two things this page refuses to do:
//   · call a simulated figure a forecast. The engine is UNCALIBRATED on this
//     book (ADR 0014), so the caveat it returns is permanent furniture here,
//     printed from the response's own stamp — this file writes no caption.
//   · re-decide anything. Precision, bands, units and lever bounds all come
//     from simulatorModel.ts, which mirrors the engine; the engine refuses an
//     impossible lever itself.
import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { FlaskConical, Info, Play, TriangleAlert } from "lucide-react";
import { runSimulation, type SimulationRun } from "@/api/bankStrategy";
import { errorCode, errorDetail, errorStatus } from "@/lib/apiError";
import { AnalyticsLoading, Panel, Tile } from "../../components/analytics";
import { BRAND } from "../../theme/chartTheme";
import { cr1 } from "../../theme/format";
import { PageRoot, ToolHeader } from "../../components/PageTemplate";
import { Button } from "../../ui/button";
import { Input } from "../../ui/input";
import { Label } from "../../ui/label";
import { Select } from "../../ui/select";
import {
  BAND_NOTE, DEFAULT_FORM, HORIZONS, LEVER_FIELDS, PATH_COUNTS, PRESETS, STAGING_NOTE,
  cashChartRows, changedLevers, gnpaChartRows, headlineTiles, ifrs9ChartRows, ifrs9Rows, leverErrors,
  requestFor, runSummary, scenarioChanges, stateShareChartRows, type SimulatorForm,
} from "./simulatorModel";

/** The run's own caveat, rendered verbatim. The wording lives in the engine
 *  (strategy/honesty.py); this is the frame around it. */
function HonestyNote({ run }: { run: SimulationRun }) {
  return (
    <div
      role="note"
      className="flex gap-3 rounded-card border border-[#F79009]/40 bg-[#FDF0DC] px-5 py-4 text-[12.5px] leading-relaxed text-[#7A3E05]"
    >
      <TriangleAlert className="mt-0.5 size-4 shrink-0 text-[#B54708]" aria-hidden="true" />
      <p>{run.text}</p>
    </div>
  );
}

function Controls({ form, setForm, onRun, running }: {
  form: SimulatorForm;
  setForm: (f: SimulatorForm) => void;
  onRun: () => void;
  running: boolean;
}) {
  const errors = leverErrors(form.levers);
  const moved = changedLevers(form);
  const set = (patch: Partial<SimulatorForm>) => setForm({ ...form, ...patch });
  const setLever = (key: keyof SimulatorForm["levers"], value: number | string) =>
    set({ levers: { ...form.levers, [key]: value } });

  return (
    <Panel title="What to simulate" hint="Levers left alone keep the book as observed">
      <div className="space-y-6">
        <div>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <Label htmlFor="mc-preset">Macro scenario</Label>
              <Select id="mc-preset" value={form.preset} onChange={(e) => set({ preset: e.target.value })}>
                {PRESETS.map((p) => (
                  <option key={p.value} value={p.value}>{p.label} — {p.note}</option>
                ))}
              </Select>
            </div>
            <div>
              <Label htmlFor="mc-horizon">Horizon</Label>
              <Select
                id="mc-horizon"
                value={String(form.horizon_months)}
                onChange={(e) => set({ horizon_months: Number(e.target.value) })}
              >
                {HORIZONS.map((h) => <option key={h} value={h}>{h} months</option>)}
              </Select>
            </div>
            <div>
              <Label htmlFor="mc-paths">Paths</Label>
              <Select
                id="mc-paths"
                value={String(form.n_paths)}
                onChange={(e) => set({ n_paths: Number(e.target.value) })}
              >
                {PATH_COUNTS.map((p) => <option key={p} value={p}>{p.toLocaleString("en-IN")} paths</option>)}
              </Select>
            </div>
            <div>
              <Label htmlFor="mc-seed">Seed</Label>
              <Input
                id="mc-seed"
                type="number"
                min={0}
                value={form.seed}
                onChange={(e) => set({ seed: Number(e.target.value) })}
              />
            </div>
          </div>
          <p className="mt-2 text-[11px] text-muted-foreground">
            The same seed, scenario and levers reproduce the same paths.
          </p>
        </div>

        <div className="grid gap-x-8 gap-y-5 border-t border-border/50 pt-5 sm:grid-cols-2 xl:grid-cols-3">
          {LEVER_FIELDS.map((f) => (
            <div key={f.key}>
              <div className="flex items-baseline justify-between gap-3">
                <Label htmlFor={`mc-${f.key}`}>{f.label}</Label>
                <span className="text-[12px] font-semibold tabular-nums text-foreground">
                  {f.format(form.levers[f.key])}
                </span>
              </div>
              {/* The theme's slider track is a hairline that disappears on a
                  white card, and the rule setting it lives in the shared
                  bank.css. So the visible track is drawn BEHIND the input
                  instead, and nothing shared is overridden. */}
              <div className="relative mt-3 flex h-4 items-center">
                <span aria-hidden="true" className="absolute inset-x-0 h-1 rounded-full bg-muted-foreground/25" />
                <input
                  id={`mc-${f.key}`}
                  type="range"
                  min={f.min}
                  max={f.max}
                  step={f.step}
                  value={form.levers[f.key]}
                  onChange={(e) => setLever(f.key, Number(e.target.value))}
                  className="relative h-4 w-full"
                />
              </div>
              <p className="mt-1 text-[11px] text-muted-foreground">{f.help}</p>
            </div>
          ))}
          <div>
            <Label htmlFor="mc-writeoff">Write off NPAs at (months)</Label>
            <Input
              id="mc-writeoff"
              type="number"
              min={1}
              placeholder="Keep the observed write-off pattern"
              value={form.levers.writeoff_policy_months}
              onChange={(e) => setLever("writeoff_policy_months", e.target.value)}
            />
          </div>
        </div>
      </div>

      {errors.length > 0 && (
        <ul className="mt-5 space-y-1 text-[12px] text-destructive">
          {errors.map((e) => <li key={e}>{e}</li>)}
        </ul>
      )}

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <Button onClick={onRun} disabled={running || errors.length > 0}>
          <Play className="size-4" />
          {running ? "Simulating…" : "Run simulation"}
        </Button>
        <span className="text-[11.5px] text-muted-foreground">
          {moved.length ? `Changed: ${moved.join(" · ")}` : "Levers at the book as observed"}
        </span>
      </div>
    </Panel>
  );
}

/** A distribution metric as a card: the p50 is the headline, with a labelled
 *  p5–p95 band + median tick below. Replaces the thin box-plot bars, which read
 *  as sparse and need explaining — the number people want is p50, and the band
 *  is the uncertainty around it. Mini-bars share one scale per group so they
 *  compare. */
function RangeCards({ rows, color, format, cols = "sm:grid-cols-2 lg:grid-cols-4" }: {
  rows: { name: string; p5: number; p50: number; p95: number }[];
  color: string;
  format: (v: number) => string;
  cols?: string;
}) {
  const lo = Math.min(...rows.map((r) => r.p5), 0);
  const hi = Math.max(...rows.map((r) => r.p95));
  const span = hi - lo || 1;
  const at = (v: number) => ((v - lo) / span) * 100;
  return (
    <div className={`grid gap-3 ${cols}`}>
      {rows.map((r) => (
        <div key={r.name} className="rounded-[14px] border border-border/60 bg-card p-3.5">
          <div className="truncate text-[11px] text-muted-foreground" title={r.name}>{r.name}</div>
          <div className="mt-0.5 text-[17px] font-bold tabular-nums text-foreground">{format(r.p50)}</div>
          <div className="relative mt-3 h-2 rounded-full bg-muted/40">
            <div className="absolute inset-y-0 rounded-full"
                 style={{ left: `${at(r.p5)}%`, width: `${Math.max(2, at(r.p95) - at(r.p5))}%`, background: color, opacity: 0.4 }} />
            <div className="absolute inset-y-0 w-[2px] rounded-full"
                 style={{ left: `calc(${at(r.p50)}% - 1px)`, background: color }} />
          </div>
          <div className="mt-1.5 text-[10.5px] tabular-nums text-muted-foreground">{format(r.p5)} – {format(r.p95)}</div>
        </div>
      ))}
    </div>
  );
}

function Results({ run }: { run: SimulationRun }) {
  const tiles = headlineTiles(run);
  const stages = ifrs9Rows(run);
  const gnpaBand = gnpaChartRows(run);
  const cashBand = cashChartRows(run);
  const stateBand = stateShareChartRows(run);
  const stageChart = ifrs9ChartRows(run);
  const pct = (v: number) => `${v.toFixed(1)}%`;

  return (
    <>
      <HonestyNote run={run} />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-5">
        {tiles.map((t) => <Tile key={t.key} label={t.label} value={t.value} sub={t.sub} />)}
      </div>
      <p className="flex items-center gap-1.5 text-[11.5px] text-muted-foreground">
        <Info className="size-3.5" aria-hidden="true" />
        {BAND_NOTE}
      </p>

      <div className="grid gap-5 xl:grid-cols-2">
        <Panel title="Gross NPA at the horizon" hint="median, with the p5–p95 band">
          <RangeCards rows={gnpaBand} color={BRAND.destructive} format={pct} cols="grid-cols-1" />
        </Panel>
        <Panel title="Cash at the horizon" hint="median ₹ crore, with the p5–p95 band">
          <RangeCards rows={cashBand} color={BRAND.success} format={cr1} cols="grid-cols-2" />
        </Panel>
      </div>

      <div className="grid gap-5">
        <Panel title="The book at the horizon" hint={`Share of accounts after ${run.horizon_months} months, median with the p5–p95 band`}>
          <RangeCards rows={stateBand} color={BRAND.primary} format={pct} cols="grid-cols-2 sm:grid-cols-3 lg:grid-cols-4" />
        </Panel>

        <Panel title="The scenario it ran" hint={run.scenario.name}>
          <ul className="space-y-2 text-[12.5px] text-foreground">
            {scenarioChanges(run).map((c) => <li key={c}>{c}</li>)}
          </ul>
          <p className="mt-4 text-[11px] leading-relaxed text-muted-foreground">{run.basis}</p>
        </Panel>
      </div>

      <Panel title="IFRS-9 staging at the horizon" hint={STAGING_NOTE}>
        {stageChart.length > 0 && (
          // Stage cards, not a bar+line chart: Stage-1 exposure dwarfs 2 and 3
          // (a performing book is mostly Stage 1), so on one ₹-axis the other
          // bars vanish, and a dual axis over three categories reads as noise.
          // Cards compare cleanly and the coverage bar is the headline per stage.
          <div className="mb-5 grid gap-3 sm:grid-cols-3">
            {stageChart.map((s, i) => {
              const tone = [BRAND.success, BRAND.warning, BRAND.destructive][i] ?? BRAND.slate;
              const totalEad = stageChart.reduce((a, r) => a + (r.ead as number), 0) || 1;
              const share = ((s.ead as number) / totalEad) * 100;
              const cov = Math.min(100, Math.max(0, s.coverage as number));
              return (
                <div key={s.stage} className="rounded-[14px] border border-border/60 bg-card p-4">
                  <div className="flex items-center justify-between">
                    <span className="inline-flex items-center gap-2 text-[13px] font-semibold text-foreground">
                      <span className="size-2.5 rounded-full" style={{ background: tone }} />
                      {s.stage}
                    </span>
                    <span className="text-[11px] text-muted-foreground">{share.toFixed(0)}% of book</span>
                  </div>
                  <div className="mt-3 flex items-end justify-between">
                    <div>
                      <div className="text-[11px] text-muted-foreground">Exposure</div>
                      <div className="text-[15px] font-bold tabular-nums text-foreground">{cr1(s.ead as number)}</div>
                    </div>
                    <div className="text-right">
                      <div className="text-[11px] text-muted-foreground">ECL</div>
                      <div className="text-[15px] font-bold tabular-nums text-foreground">{cr1(s.ecl as number)}</div>
                    </div>
                  </div>
                  <div className="mt-3">
                    <div className="mb-1 flex items-center justify-between text-[11px]">
                      <span className="text-muted-foreground">Coverage</span>
                      <span className="font-semibold tabular-nums" style={{ color: tone }}>{cov.toFixed(1)}%</span>
                    </div>
                    <div className="h-2 w-full overflow-hidden rounded-full bg-muted/50">
                      <div className="h-full rounded-full" style={{ width: `${cov}%`, background: tone }} />
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
        <div className="overflow-x-auto">
          <table className="w-full text-[12.5px]">
            <thead>
              <tr className="border-b border-border/60 text-left text-[11px] uppercase tracking-wide text-muted-foreground">
                <th className="py-2 pr-4 font-semibold">Stage</th>
                <th className="py-2 pr-4 text-right font-semibold">Exposure</th>
                <th className="py-2 pr-4 text-right font-semibold">ECL</th>
                <th className="py-2 pr-4 text-right font-semibold">Coverage</th>
                <th className="py-2 pr-4 text-right font-semibold">Implied PD</th>
              </tr>
            </thead>
            <tbody>
              {stages.map((s) => (
                <tr key={s.stage} className="border-b border-border/40 last:border-0">
                  <td className="py-2.5 pr-4">
                    <span className="text-foreground">{s.label}</span>
                    <span className="block text-[11px] text-muted-foreground">{s.note}</span>
                  </td>
                  <td className="py-2.5 pr-4 text-right tabular-nums">{s.ead}</td>
                  <td className="py-2.5 pr-4 text-right tabular-nums">{s.ecl}</td>
                  <td className="py-2.5 pr-4 text-right tabular-nums">{s.coverage}</td>
                  <td className="py-2.5 pr-4 text-right tabular-nums">{s.pd}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title="What this run assumes" hint={`${run.assumptions.length} assumptions`}>
        <ul className="space-y-2 text-[12px] leading-relaxed text-muted-foreground">
          {run.assumptions.map((a) => <li key={a}>· {a}</li>)}
        </ul>
      </Panel>

      <p className="text-[11px] text-muted-foreground">
        {runSummary(run)}
        {run.numpy_version ? ` · numpy ${run.numpy_version}` : ""} · seed {run.seed}
      </p>
    </>
  );
}

export default function MonteCarloPage() {
  const [form, setForm] = useState<SimulatorForm>(DEFAULT_FORM);
  // The request that has actually been RUN, which is what the results belong to:
  // moving a slider must not quietly restate the figures on screen as if they
  // had been simulated. A run is deterministic in (scenario, levers, seed), so
  // asking for the same one twice is served from the cache.
  const [submitted, setSubmitted] = useState(() => requestFor(DEFAULT_FORM));
  const sim = useQuery({
    queryKey: ["bank", "strategy", "simulate", submitted],
    queryFn: () => runSimulation(submitted),
    placeholderData: keepPreviousData,
    retry: false,
    staleTime: Infinity,
  });
  const run = sim.data;

  const code = sim.isError ? errorCode(sim.error) : undefined;
  const status = sim.isError ? errorStatus(sim.error) : undefined;
  return (
    <PageRoot>
      <ToolHeader
        title="Monte Carlo Simulator"
        icon={FlaskConical}
        description="Roll every account in the book forward, month by month, under a macro scenario and a set of collection levers. Each figure is read across the simulated paths, so it arrives as a range."
      />

      <Controls
        form={form}
        setForm={setForm}
        onRun={() => setSubmitted(requestFor(form))}
        running={sim.isFetching}
      />

      {sim.isError && (
        <div role="alert" className="rounded-card border border-destructive/40 bg-destructive/5 px-5 py-4 text-[12.5px] text-destructive">
          {code === "INSUFFICIENT_HISTORY"
            ? "Not enough history to simulate this book yet. The engine needs several month-ends of loan history to count transitions from, and refuses rather than inventing them."
            : status === 403
              // The capability matrix lives in core/permissions.py; naming the
              // roles here would be a second copy of it that could drift.
              ? "Running a simulation needs the strategy-simulation capability, which this role does not hold."
              : errorDetail(sim.error, "The simulation could not be run.")}
        </div>
      )}

      {sim.isFetching && !run && <AnalyticsLoading label="Simulating the book forward…" />}

      {run && <Results run={run} />}
    </PageRoot>
  );
}
