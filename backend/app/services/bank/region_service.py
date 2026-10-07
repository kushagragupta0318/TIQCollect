"""The bank's region tree (Admin > Regions): zone -> region -> state -> city
-> branch, each node carrying a roll-up of its own branches plus every
descendant's (loans, exposure, distinct agencies with an ACTIVE placement
there).

Region-limited the same way the placement screens are (scope.region_limit_path
+ placement_read_service.region_subtree_clause, by segment, NOT a naive
startswith): a BANK_ANALYST with scope_region_id set sees only that subtree,
with branches outside it absent rather than zeroed, and the unassigned bucket
(below) suppressed entirely rather than shown as 0 — an unassigned branch
belongs to no region, so it is never inside *or* outside a subtree the limited
caller is allowed to reason about.

Branches with no region yet are never folded into the tree as if they had
one; they are the `unassigned` summary instead (ADR 0005: abstain, don't
impute), and only for a bank-wide caller.
"""
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.loan import Loan
from app.models.placement import Placement
from app.models.tenancy import Branch, Region
from app.services.placement_read_service import region_subtree_clause
from app.services.scope import REGION_LIMIT_UNRESOLVED, region_limit_path

LEVEL_DEPTH = {"ZONE": 0, "REGION": 1, "STATE": 2, "CITY": 3}


def _per_branch_metrics(db: Session, bank_id: str) -> dict[str, dict]:
    """branch_code -> {loan_count, exposure}, one row per branch that has at
    least one loan."""
    rows = (db.query(Loan.branch_code, func.count(Loan.id), func.coalesce(func.sum(Loan.total_outstanding), 0.0))
           .filter(Loan.bank_id == bank_id).group_by(Loan.branch_code).all())
    return {code: {"loan_count": int(n), "exposure": float(exposure)} for code, n, exposure in rows}


def _per_branch_agencies(db: Session, bank_id: str) -> dict[str, set[str]]:
    """branch_code -> the set of agency ids with an ACTIVE placement on a loan
    at that branch. Sets, not counts: a parent node's agency_count is the size
    of the UNION of its children's sets, which a pre-summed count cannot give."""
    rows = (db.query(Loan.branch_code, Placement.agency_id)
           .join(Placement, Placement.loan_id == Loan.id)
           .filter(Loan.bank_id == bank_id, Placement.status == "ACTIVE")
           .distinct().all())
    out: dict[str, set[str]] = {}
    for code, agency_id in rows:
        out.setdefault(code, set()).add(agency_id)
    return out


def region_tree(db: Session, principal) -> dict:
    bank_id = principal.bank_id
    limit = region_limit_path(db, principal)
    if limit is REGION_LIMIT_UNRESOLVED:
        return {"roots": [], "unassigned": {"branch_count": 0, "loan_count": 0, "exposure": 0.0}}

    regions_q = db.query(Region).filter(Region.bank_id == bank_id, Region.is_active.is_(True))
    if limit is not None:
        regions_q = regions_q.filter(region_subtree_clause(limit, Region.path))
    regions = regions_q.order_by(Region.path).all()
    region_ids = {r.id for r in regions}

    branches_q = db.query(Branch).filter(Branch.bank_id == bank_id, Branch.region_id.isnot(None))
    if limit is not None:
        branches_q = branches_q.filter(Branch.region_id.in_(region_ids))
    branches = branches_q.all()
    branch_metrics = _per_branch_metrics(db, bank_id)
    branch_agencies = _per_branch_agencies(db, bank_id)

    # Seed each region's own totals from its direct branches, then roll
    # deepest-first (CITY before STATE before REGION before ZONE) so a
    # parent's accumulator already holds every descendant's contribution by
    # the time IT is added to ITS parent.
    totals: dict[str, dict] = {r.id: {"loan_count": 0, "exposure": 0.0, "agency_ids": set()} for r in regions}
    branch_nodes: dict[str, list[dict]] = {r.id: [] for r in regions}
    for b in branches:
        if b.region_id not in totals:
            # Points at a region this query didn't load — archived
            # (is_active False) or, for a limited caller, outside their
            # subtree. Either way there is no node to hang it on; drop it
            # from the tree rather than crash the whole response.
            continue
        m = branch_metrics.get(b.branch_code, {"loan_count": 0, "exposure": 0.0})
        agencies = branch_agencies.get(b.branch_code, set())
        branch_nodes[b.region_id].append({
            "id": b.id, "level": "BRANCH", "code": b.branch_code, "name": b.name or b.branch_code,
            "loan_count": m["loan_count"], "exposure": m["exposure"], "agency_count": len(agencies),
            "children": [],
        })
        t = totals[b.region_id]
        t["loan_count"] += m["loan_count"]
        t["exposure"] += m["exposure"]
        t["agency_ids"] |= agencies

    by_parent: dict[str | None, list[Region]] = {}
    for r in regions:
        by_parent.setdefault(r.parent_id, []).append(r)

    for r in sorted(regions, key=lambda r: -LEVEL_DEPTH[r.level]):
        if r.parent_id is not None and r.parent_id in totals:
            parent = totals[r.parent_id]
            parent["loan_count"] += totals[r.id]["loan_count"]
            parent["exposure"] += totals[r.id]["exposure"]
            parent["agency_ids"] |= totals[r.id]["agency_ids"]

    def build(r: Region) -> dict:
        t = totals[r.id]
        children = [build(c) for c in by_parent.get(r.id, [])] + branch_nodes.get(r.id, [])
        return {
            "id": r.id, "level": r.level, "code": r.code, "name": r.name,
            "loan_count": t["loan_count"], "exposure": t["exposure"], "agency_count": len(t["agency_ids"]),
            "children": children,
        }

    # A region-limited caller's subtree root is not necessarily a ZONE: its
    # true parent just isn't in `regions` (filtered out), which is exactly
    # the condition that also picks out real ZONEs (parent_id None) for a
    # bank-wide caller.
    roots = [build(r) for r in regions if r.parent_id not in region_ids]

    if limit is not None:
        unassigned = {"branch_count": 0, "loan_count": 0, "exposure": 0.0}
    else:
        unassigned_branches = (db.query(Branch)
                               .filter(Branch.bank_id == bank_id, Branch.region_id.is_(None)).all())
        u_loans = sum(branch_metrics.get(b.branch_code, {"loan_count": 0})["loan_count"]
                     for b in unassigned_branches)
        u_exposure = sum(branch_metrics.get(b.branch_code, {"exposure": 0.0})["exposure"]
                        for b in unassigned_branches)
        unassigned = {"branch_count": len(unassigned_branches), "loan_count": u_loans, "exposure": u_exposure}

    return {"roots": roots, "unassigned": unassigned}
