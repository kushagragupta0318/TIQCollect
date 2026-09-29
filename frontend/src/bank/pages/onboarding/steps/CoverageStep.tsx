// Step 2 — Coverage. A checklist over the bank's region hierarchy, plus a
// read-only map preview of the selected regions' points. No polygon drawing
// — that is explicitly deferred (STANDALONE-PRODUCT-PLAN §6.1); the map here
// only plots simple markers for whichever regions carry a latitude/longitude.
import { useMemo, useState } from "react";
import L from "leaflet";
import { useMutation } from "@tanstack/react-query";
import { toast } from "react-hot-toast";
import { Card, CardContent, CardFooter, CardHeader, CardTitle, CardDescription } from "../../../ui/card";
import { Button } from "../../../ui/button";
import { Badge } from "../../../ui/badge";
import { MapCanvas } from "@/components/map/MapCanvas";
import { DEFAULT_CENTRE } from "@/components/map/constants";
import { updateCoverageContract, type AgencyDetail, type Region } from "@/api/bank";
import { errorDetail } from "@/lib/apiError";

interface Props {
  agencyId: string;
  detail: AgencyDetail;
  regions: Region[];
  regionsLoading: boolean;
  onSaved: () => void;
}

/** Indent level from the materialised path ("/ncr/haryana/gurugram/" -> 2). */
function depthOf(path: string): number {
  return Math.max(0, (path.match(/\//g)?.length ?? 1) - 2);
}

export function CoverageStep({ agencyId, detail, regions, regionsLoading, onSaved }: Props) {
  const [selected, setSelected] = useState<Set<string>>(() => new Set(detail.region_ids));

  const sorted = useMemo(() => [...regions].sort((a, b) => a.path.localeCompare(b.path)), [regions]);
  const selectedRegions = useMemo(
    () => regions.filter((r) => selected.has(r.region_id) && r.latitude != null && r.longitude != null),
    [regions, selected],
  );

  const save = useMutation({
    mutationFn: () => updateCoverageContract(agencyId, { region_ids: Array.from(selected) }),
    onSuccess: () => { toast.success("Coverage saved"); onSaved(); },
    onError: (err) => toast.error(errorDetail(err, "Could not save coverage")),
  });

  function toggle(regionId: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(regionId)) next.delete(regionId); else next.add(regionId);
      return next;
    });
  }

  function drawMarkers(map: L.Map) {
    const layer = L.layerGroup().addTo(map);
    const bounds: L.LatLngExpression[] = [];
    for (const r of selectedRegions) {
      const point: L.LatLngExpression = [r.latitude as number, r.longitude as number];
      L.circleMarker(point, { radius: 7, color: "#1677FF", weight: 2, fillColor: "#1677FF", fillOpacity: 0.5 })
        .bindTooltip(`${r.name} (${r.level})`, { direction: "top" })
        .addTo(layer);
      bounds.push(point);
    }
    if (bounds.length) map.fitBounds(L.latLngBounds(bounds).pad(0.25), { maxZoom: 10 });
    return () => layer.remove();
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Coverage regions</CardTitle>
          <CardDescription>
            Which of the bank's regions this agency may be placed cases in. A polygon-drawn territory comes later —
            this is a checklist over the existing zone / region / state / city hierarchy.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {regionsLoading ? (
            <p className="text-[13px] text-muted-foreground">Loading regions…</p>
          ) : sorted.length === 0 ? (
            <p className="text-[13px] text-muted-foreground">This bank has no regions set up yet (Admin &gt; Regions).</p>
          ) : (
            <ul className="max-h-96 space-y-1 overflow-y-auto pr-1">
              {sorted.map((r) => (
                <li key={r.region_id}>
                  <label
                    className="flex cursor-pointer items-center gap-2.5 rounded-inner px-2 py-1.5 hover:bg-muted"
                    style={{ paddingLeft: `${depthOf(r.path) * 20 + 8}px` }}
                  >
                    <input
                      type="checkbox"
                      checked={selected.has(r.region_id)}
                      onChange={() => toggle(r.region_id)}
                      className="size-4 rounded border-input"
                    />
                    <span className="text-[13px] text-foreground">{r.name}</span>
                    <Badge variant="outline" className="text-[9.5px] uppercase">{r.level}</Badge>
                  </label>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
        <CardFooter className="justify-between">
          <p className="text-[12px] text-muted-foreground">{selected.size} region{selected.size === 1 ? "" : "s"} selected</p>
          <Button onClick={() => save.mutate()} disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save & continue"}
          </Button>
        </CardFooter>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Coverage preview</CardTitle>
          <CardDescription>
            Read-only — plots the selected regions that carry a location. {selectedRegions.length} of {selected.size} selected region(s) have one.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <MapCanvas
            onReady={drawMarkers}
            deps={[selectedRegions]}
            centre={DEFAULT_CENTRE}
            className="h-72 w-full rounded-inner overflow-hidden"
          />
        </CardContent>
      </Card>
    </div>
  );
}
