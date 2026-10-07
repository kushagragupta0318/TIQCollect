// Admin → Settings (K01): the bank's one config surface.
//
// Two kinds of value, shown apart so they cannot be confused:
//  · EDITABLE — this bank's stored policy (contact hours, geo-fence, SLA). A
//    bank admin edits these; a PATCH persists and audits the change.
//  · READ-ONLY — engine constants (exploration rate, retention) and the DPD
//    bucket thresholds. They come from the engine's own config; the form shows
//    them but never lets them be edited.
//
// Written against GET/PATCH /bank/settings (@/api/bankSettings). Figures are
// rendered from the server's values, never restated here.
import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Clock, Lock, MapPin, SlidersHorizontal, Target } from "lucide-react";
import { toast } from "react-hot-toast";
import { ExecutiveHeader, PageFailure, PageLoading, PageRoot } from "../../components/PageTemplate";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "../../ui/card";
import { Badge } from "../../ui/badge";
import { Button } from "../../ui/button";
import { Input } from "../../ui/input";
import { Label } from "../../ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../ui/table";
import {
  getBankSettings, updateBankSettings, type BankSettings, type UpdateBankSettings,
} from "@/api/bankSettings";
import { errorDetail } from "@/lib/apiError";

const SETTINGS_KEY = ["bank-settings"];

function OverrideBadge({ on }: { on: boolean }) {
  return on
    ? <Badge variant="softPrimary">Overridden</Badge>
    : <Badge variant="outline">Engine default</Badge>;
}

/** A signature of the saved editable values. The parent uses it as this form's
 * `key`, so a successful save (which rewrites these values) remounts the form
 * and re-seeds the inputs — no setState-in-effect needed. */
function policyFormKey(data: BankSettings): string {
  return [data.contact_hours.start, data.contact_hours.end,
          data.geofence_metres.value, data.sla_first_visit_days.value].join("-");
}

/** The editable form. Seeded once from the loaded settings (its useState
 * initializers); the parent remounts it via `key` when a save changes them. */
function PolicyForm({ data }: { data: BankSettings }) {
  const qc = useQueryClient();
  const [start, setStart] = useState(() => String(data.contact_hours.start));
  const [end, setEnd] = useState(() => String(data.contact_hours.end));
  const [geofence, setGeofence] = useState(() => String(data.geofence_metres.value));
  const [sla, setSla] = useState(() => String(data.sla_first_visit_days.value));

  const save = useMutation({
    mutationFn: (body: UpdateBankSettings) => updateBankSettings(body),
    onSuccess: (fresh) => {
      qc.setQueryData(SETTINGS_KEY, fresh);
      toast.success("Settings saved");
    },
    onError: (e) => toast.error(errorDetail(e, "Could not save settings")),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const body: UpdateBankSettings = {};
    const s = Number(start), en = Number(end), g = Number(geofence), sd = Number(sla);
    if (s !== data.contact_hours.start) body.contact_hour_start = s;
    if (en !== data.contact_hours.end) body.contact_hour_end = en;
    if (g !== data.geofence_metres.value) body.geofence_metres = g;
    if (sd !== data.sla_first_visit_days.value) body.sla_first_visit_days = sd;
    if (Object.keys(body).length === 0) {
      toast("Nothing changed");
      return;
    }
    save.mutate(body);
  }

  return (
    <form onSubmit={onSubmit} className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Clock className="size-5 text-primary" />Contact hours</CardTitle>
          <CardDescription>
            The window collections contact is allowed (RBI). Engine default {data.contact_hours.default_start}:00–{data.contact_hours.default_end}:00.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="contact-start">Start hour (0–23, IST)</Label>
            <Input id="contact-start" type="number" min={0} max={23} value={start}
                   onChange={(e) => setStart(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="contact-end">End hour (1–24, IST)</Label>
            <Input id="contact-end" type="number" min={1} max={24} value={end}
                   onChange={(e) => setEnd(e.target.value)} />
          </div>
          <div className="sm:col-span-2"><OverrideBadge on={data.contact_hours.is_override} /></div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><MapPin className="size-5 text-primary" />Geo-fence radius</CardTitle>
          <CardDescription>
            How close an agent must be to record a visit. Engine default {data.geofence_metres.default} m.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="geofence">Radius (metres, 10–5000)</Label>
            <Input id="geofence" type="number" min={10} max={5000} value={geofence}
                   onChange={(e) => setGeofence(e.target.value)} />
          </div>
          <div className="flex items-end"><OverrideBadge on={data.geofence_metres.is_override} /></div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Target className="size-5 text-primary" />SLA target</CardTitle>
          <CardDescription>
            The bank-wide first-visit target for new agency contracts. Default {data.sla_first_visit_days.default} days.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="sla">First visit within (days, 1–90)</Label>
            <Input id="sla" type="number" min={1} max={90} value={sla}
                   onChange={(e) => setSla(e.target.value)} />
          </div>
          <div className="flex items-end"><OverrideBadge on={data.sla_first_visit_days.is_override} /></div>
        </CardContent>
        <CardFooter className="justify-end">
          <Button type="submit" disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save settings"}
          </Button>
        </CardFooter>
      </Card>
    </form>
  );
}

/** Engine constants and DPD thresholds — read-only. */
function ReadOnlyCard({ data }: { data: BankSettings }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Lock className="size-5 text-muted-foreground" />Engine constants</CardTitle>
        <CardDescription>
          Set by the deployment's configuration, not editable here. {data.note}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Setting</TableHead>
              <TableHead className="text-right">Value</TableHead>
              <TableHead>Notes</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.engine_constants.map((c) => (
              <TableRow key={c.key}>
                <TableCell className="font-medium text-foreground">{c.label}</TableCell>
                <TableCell className="text-right tabular-nums">{c.value} {c.unit === "fraction" ? "" : c.unit}</TableCell>
                <TableCell className="text-[12px] text-muted-foreground">{c.description}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>

        <div>
          <p className="mb-2 flex items-center gap-2 text-[13px] font-semibold text-foreground">
            <SlidersHorizontal className="size-4 text-muted-foreground" />DPD bucket thresholds
          </p>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Bucket</TableHead>
                <TableHead>Days past due</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.dpd_buckets.map((b) => (
                <TableRow key={b.bucket}>
                  <TableCell className="font-medium text-foreground">{b.bucket}</TableCell>
                  <TableCell className="tabular-nums text-muted-foreground">{b.label}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  );
}

export default function SettingsPage() {
  const { data, isLoading, isError } = useQuery({ queryKey: SETTINGS_KEY, queryFn: getBankSettings });

  if (isLoading) return <PageLoading label="Loading settings…" />;
  if (isError || !data) return <PageFailure>Settings could not load.</PageFailure>;

  return (
    <PageRoot>
      <ExecutiveHeader
        title="Settings"
        meta={[data.bank.display_name, `Timezone ${data.bank.timezone}`]}
        scopeNote="Bank-wide policy and the engine constants the platform runs on. Changes are audited."
      />
      <div className="grid gap-6 lg:grid-cols-2">
        <PolicyForm key={policyFormKey(data)} data={data} />
        <ReadOnlyCard data={data} />
      </div>
    </PageRoot>
  );
}
