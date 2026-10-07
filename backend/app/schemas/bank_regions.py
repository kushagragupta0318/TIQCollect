"""Response shapes for the bank's region tree (endpoints/bank_agencies_admin.py,
services/bank/region_service.py).

Every node's loan/exposure/agency figures are a roll-up of its own branches
plus every descendant's, computed once in the service and carried here
unchanged — this file only shapes what already exists, nothing is derived
in a route or in the frontend.
"""
from __future__ import annotations

from pydantic import BaseModel


class RegionTreeNode(BaseModel):
    id: str
    #: ZONE / REGION / STATE / CITY (tenancy.regions) or BRANCH (a leaf, not
    #: itself a region row).
    level: str
    code: str
    name: str
    loan_count: int
    exposure: float
    #: Agencies with an ACTIVE placement on a loan anywhere in this node's
    #: subtree — a count, not a list, since who they are isn't this page's
    #: question (that's the Directory).
    agency_count: int
    children: list["RegionTreeNode"] = []


RegionTreeNode.model_rebuild()


class UnassignedSummary(BaseModel):
    """Branches with no region yet — never folded into the tree as if they
    had one; surfaced instead, same honesty rule as every other abstain in
    this codebase (ADR 0005)."""
    branch_count: int
    loan_count: int
    exposure: float


class RegionTreeResponse(BaseModel):
    roots: list[RegionTreeNode]
    unassigned: UnassignedSummary
