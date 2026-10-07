// Admin > Regions: the zone/region/state/city/branch tree with each node's
// roll-up (loans, exposure, agencies with an ACTIVE placement there).
// Read-only — editing the hierarchy is bank.regions.manage's other half, not
// built here. GET /bank/regions/tree (services/bank/region_service.py)
// already does every roll-up; this page only renders what it's given.
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, MapPin } from "lucide-react";
import { ExecutiveHeader, PageFailure, PageLoading, PageRoot } from "../../components/PageTemplate";
import { Card, CardContent, CardHeader, CardTitle } from "../../ui/card";
import { Badge } from "../../ui/badge";
import { getRegionTree, type RegionTreeNode } from "@/api/bank";
import { errorDetail } from "@/lib/apiError";
import { n, rs } from "../../theme/format";

const LEVEL_LABEL: Record<string, string> = {
  ZONE: "Zone", REGION: "Region", STATE: "State", CITY: "City", BRANCH: "Branch",
};

function TreeRow({ node, depth }: { node: RegionTreeNode; depth: number }) {
  const hasChildren = node.children.length > 0;
  const [open, setOpen] = useState(depth < 1);

  return (
    <div>
      <div
        className="grid grid-cols-[1fr_auto_auto_auto] items-center gap-3 border-b border-border/60 py-2 pr-2 text-sm last:border-0"
        style={{ paddingLeft: depth * 20 }}
      >
        <button
          type="button"
          onClick={() => hasChildren && setOpen((o) => !o)}
          className="flex items-center gap-1.5 text-left disabled:cursor-default"
          disabled={!hasChildren}
        >
          {hasChildren ? (
            open ? <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />
                 : <ChevronRight className="size-3.5 shrink-0 text-muted-foreground" />
          ) : <span className="inline-block size-3.5" />}
          <Badge variant="outline" className="shrink-0 text-[10px]">{LEVEL_LABEL[node.level] ?? node.level}</Badge>
          <span className="font-medium text-foreground">{node.name}</span>
        </button>
        <span className="text-right text-muted-foreground">{n(node.loan_count)} loans</span>
        <span className="text-right text-muted-foreground">{rs(node.exposure)}</span>
        <span className="text-right text-muted-foreground">{n(node.agency_count)} agencies</span>
      </div>
      {open && node.children.map((c) => <TreeRow key={c.id} node={c} depth={depth + 1} />)}
    </div>
  );
}

export default function RegionsPage() {
  const q = useQuery({ queryKey: ["bank", "regions-tree"], queryFn: getRegionTree });

  if (q.isLoading) return <PageLoading label="Loading regions…" />;
  if (q.isError) return <PageFailure>{errorDetail(q.error, "The region tree could not load.")}</PageFailure>;
  if (!q.data) return <PageFailure>No region data.</PageFailure>;

  const { roots, unassigned } = q.data;

  return (
    <PageRoot>
      <ExecutiveHeader
        title="Regions"
        meta={["Admin", "Regions"]}
        scopeNote="Zone to branch, each node rolled up over its own branches and every branch below it: loans, exposure, and agencies currently placed there."
      />

      {unassigned.branch_count > 0 && (
        <Card>
          <CardContent className="flex items-center justify-between py-3 text-sm">
            <span className="flex items-center gap-2 text-muted-foreground">
              <MapPin className="size-4" />
              {n(unassigned.branch_count)} branch{unassigned.branch_count === 1 ? "" : "es"} with no region
              assigned yet — not shown in the tree below.
            </span>
            <span className="font-medium text-foreground">{n(unassigned.loan_count)} loans · {rs(unassigned.exposure)}</span>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader><CardTitle>Region hierarchy</CardTitle></CardHeader>
        <CardContent>
          {roots.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">No regions set up yet.</p>
          ) : (
            roots.map((r) => <TreeRow key={r.id} node={r} depth={0} />)
          )}
        </CardContent>
      </Card>
    </PageRoot>
  );
}
