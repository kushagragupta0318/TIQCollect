"""Reads behind the bank's placement screens (D08): loans to pick from, the
agencies to place with and their room, and placements made or received.

Every read is filtered by a tenant id the CALLER passes from RequestContext
(bank, or bank + agency); nothing here widens past it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import lru_cache

from sqlalchemy import and_, exists, false, or_
from sqlalchemy.orm import Session, aliased

from app.models.customer import Customer
from app.models.loan import DPDBucket, Loan, LoanType, dpd_bucket_for
from app.models.placement import Placement
from app.models.tenancy import Agency, AgencyRegion, Branch, Region
from app.services.placement_service import PLACEABLE_LOAN_STATUSES, PlacementService
from app.services.scope import REGION_LIMIT_UNRESOLVED

MAX_PAGE_SIZE = 200


_DPD_SCAN = 10_000


@lru_cache(maxsize=None)
def bucket_dpd_range(bucket: DPDBucket) -> tuple[int | None, int | None]:
    """The DPD interval `dpd_bucket_for` maps to `bucket`, read off the one
    rule rather than restated, so the filter and the placement gate agree
    even where loans.dpd_bucket (a stored column) is stale. None = open end."""
    hits = [d for d in range(0, _DPD_SCAN) if dpd_bucket_for(d) == bucket]
    if not hits:
        return (0, -1)                                   # matches nothing
    lo = None if hits[0] == 0 else hits[0]               # CURRENT also holds dpd <= 0
    hi = None if hits[-1] == _DPD_SCAN - 1 else hits[-1]
    return lo, hi


@lru_cache(maxsize=64)
def artifact_synthetic_warning(model: str, version: str) -> str | None:
    """SYNTHETIC_WARNING from ml/artifacts/<model>/<version>/metadata.json.
    A version whose metadata cannot be read is reported as unverified, never
    as real."""
    import json
    from app.ml.pipeline.registry import ARTIFACT_ROOT
    root = ARTIFACT_ROOT
    path = (root / model / version / "metadata.json").resolve()
    if root.resolve() not in path.parents:
        return f"UNVERIFIED: model {model} {version} has no readable artifact metadata."
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("SYNTHETIC_WARNING") or None
    except (OSError, ValueError):
        return f"UNVERIFIED: model {model} {version} has no readable artifact metadata."


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def region_subtree_clause(path: str, column=None):
    """`column` (Region.path) is `path` or below it, by segment (NORTH.HR never matches NORTH.HRX)."""
    col = Region.path if column is None else column
    return or_(col == path, col.like(_like_escape(path) + ".%", escape="\\"))


def apply_region_limit(q, region_limit):
    """Narrow a query that selects Loan to the caller's region limit
    (scope.region_limit_path): None = unlimited, unresolved = nothing."""
    if region_limit is None:
        return q
    if region_limit is REGION_LIMIT_UNRESOLVED:
        return q.filter(false())
    # Aliased: a caller's query may already join Branch/Region (loans() does),
    # and EXISTS would otherwise correlate to those and lose its FROM.
    b, r = aliased(Branch), aliased(Region)
    return q.filter(exists().where(
        b.bank_id == Loan.bank_id, b.branch_code == Loan.branch_code,
        r.id == b.region_id, r.bank_id == Loan.bank_id, region_subtree_clause(region_limit, r.path)))


@dataclass(frozen=True)
class LoanFilter:
    region_id: str | None = None
    branch_code: str | None = None
    loan_type: LoanType | None = None
    dpd_bucket: DPDBucket | None = None
    dpd_min: int | None = None
    dpd_max: int | None = None
    outstanding_min: float | None = None
    outstanding_max: float | None = None
    placed: bool | None = False          # False: unplaced only; True: placed only; None: both
    search: str | None = None            # loan account number prefix


class PlacementReadService:
    def __init__(self, db: Session):
        self.db = db

    # ── loans to pick from ──────────────────────────────────────────────────

    def loans(self, bank_id: str, f: LoanFilter, *, region_limit=None, page: int = 1, page_size: int = 50) -> dict:
        page_size = max(1, min(int(page_size), MAX_PAGE_SIZE))
        page = max(1, int(page))
        active = (self.db.query(Placement.loan_id.label("loan_id"), Placement.agency_id.label("agency_id"),
                                Placement.id.label("placement_id"))
                  .filter(Placement.bank_id == bank_id, Placement.status == "ACTIVE").subquery())
        q = (self.db.query(Loan, Customer.full_name, Region.id, Region.name, Region.path,
                           active.c.agency_id, active.c.placement_id, Agency.trade_name, Agency.legal_name)
             .join(Customer, and_(Customer.id == Loan.customer_id, Customer.bank_id == Loan.bank_id))
             .outerjoin(Branch, and_(Branch.bank_id == Loan.bank_id, Branch.branch_code == Loan.branch_code))
             .outerjoin(Region, and_(Region.id == Branch.region_id, Region.bank_id == Loan.bank_id))
             .outerjoin(active, active.c.loan_id == Loan.id)
             .outerjoin(Agency, and_(Agency.id == active.c.agency_id, Agency.bank_id == Loan.bank_id))
             .filter(Loan.bank_id == bank_id, Loan.status.in_(PLACEABLE_LOAN_STATUSES)))
        q = apply_region_limit(q, region_limit)
        if f.region_id:
            root = self.db.query(Region.path).filter(Region.id == f.region_id, Region.bank_id == bank_id).first()
            if root is None:
                return {"items": [], "total": 0, "page": page, "page_size": page_size}
            q = q.filter(region_subtree_clause(root.path))
        if f.branch_code:
            q = q.filter(Loan.branch_code == f.branch_code)
        if f.loan_type is not None:
            q = q.filter(Loan.loan_type == f.loan_type)
        if f.dpd_min is not None:
            q = q.filter(Loan.dpd >= f.dpd_min)
        if f.dpd_max is not None:
            q = q.filter(Loan.dpd <= f.dpd_max)
        if f.dpd_bucket is not None:
            lo, hi = bucket_dpd_range(f.dpd_bucket)
            if lo is not None:
                q = q.filter(Loan.dpd >= lo)
            if hi is not None:
                q = q.filter(Loan.dpd <= hi)
        if f.outstanding_min is not None:
            q = q.filter(Loan.total_outstanding >= f.outstanding_min)
        if f.outstanding_max is not None:
            q = q.filter(Loan.total_outstanding <= f.outstanding_max)
        if f.placed is False:
            q = q.filter(active.c.placement_id.is_(None))
        elif f.placed is True:
            q = q.filter(active.c.placement_id.isnot(None))
        if f.search:
            q = q.filter(Loan.loan_account_number.like(_like_escape(f.search.strip()) + "%", escape="\\"))
        total = q.count()
        rows = (q.order_by(Loan.dpd.desc(), Loan.loan_account_number)
                .offset((page - 1) * page_size).limit(page_size).all())
        items = [{
            "loan_id": loan.id, "loan_account_number": loan.loan_account_number, "customer_name": name,
            "loan_type": loan.loan_type.value, "dpd": int(loan.dpd or 0),
            "dpd_bucket": dpd_bucket_for(loan.dpd).value,
            "total_outstanding": float(loan.total_outstanding or 0.0),
            "overdue_amount": float(loan.overdue_amount or 0.0),
            "branch_code": loan.branch_code, "region_id": rid, "region_name": rname, "region_path": rpath,
            "placement_id": pid, "placed_with_agency_id": aid,
            "placed_with_agency_name": (trade or legal) if aid else None,
        } for loan, name, rid, rname, rpath, aid, pid, trade, legal in rows]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    # ── agencies and their room ─────────────────────────────────────────────

    def agencies(self, bank_id: str, on: date) -> list[dict]:
        rules = PlacementService(self.db)
        out = []
        for agency in (self.db.query(Agency).filter(Agency.bank_id == bank_id)
                       .order_by(Agency.trade_name, Agency.legal_name)):
            contract = rules.contract_in_force(agency.id, on) if agency.status == "ACTIVE" else None
            coverage = []
            if contract is not None:
                coverage = [{"region_id": r.id, "name": r.name, "path": r.path} for r in
                            (self.db.query(Region)
                             .join(AgencyRegion, AgencyRegion.region_id == Region.id)
                             .filter(AgencyRegion.contract_id == contract.id, Region.bank_id == bank_id)
                             .order_by(Region.path))]
            out.append({
                "agency_id": agency.id, "code": agency.code, "name": agency.trade_name or agency.legal_name,
                "status": agency.status, "placeable": agency.status == "ACTIVE" and contract is not None,
                "contract_no": contract.contract_no if contract else None,
                "contract_end": contract.end_date.isoformat() if contract else None,
                "max_placed_cases": contract.max_placed_cases if contract else None,
                "active_placements": rules.placed_count(contract) if contract else 0,
                "headroom": rules.headroom(contract) if contract else 0,
                "coverage": coverage,
            })
        return out

    # ── placements made (bank) or received (agency) ─────────────────────────

    def placements(self, *, bank_id: str, agency_id: str | None, status: str | None = None,
                   filter_agency_id: str | None = None, region_limit=None,
                   page: int = 1, page_size: int = 50) -> dict:
        """`agency_id` is the caller's own agency (AGENCY scope) and
        `region_limit` their region limit (scope.region_limit_path); both
        always apply. `filter_agency_id` is a bank user's filter."""
        page_size = max(1, min(int(page_size), MAX_PAGE_SIZE))
        page = max(1, int(page))
        q = (self.db.query(Placement, Loan.loan_account_number, Agency.trade_name, Agency.legal_name)
             .join(Loan, and_(Loan.id == Placement.loan_id, Loan.bank_id == Placement.bank_id))
             .join(Agency, and_(Agency.id == Placement.agency_id, Agency.bank_id == Placement.bank_id))
             .filter(Placement.bank_id == bank_id))
        if agency_id is not None:
            q = q.filter(Placement.agency_id == agency_id)
        q = apply_region_limit(q, region_limit)
        if filter_agency_id:
            q = q.filter(Placement.agency_id == filter_agency_id)
        if status:
            q = q.filter(Placement.status == status)
        total = q.count()
        rows = (q.order_by(Placement.placed_on.desc(), Loan.loan_account_number)
                .offset((page - 1) * page_size).limit(page_size).all())
        items = [{
            "placement_id": p.id, "loan_id": p.loan_id, "loan_account_number": lan,
            "agency_id": p.agency_id, "agency_name": trade or legal, "source": p.source, "status": p.status,
            "placed_on": p.placed_on.isoformat(), "ended_on": p.ended_on.isoformat() if p.ended_on else None,
            "end_reason": p.end_reason, "dpd_at_placement": p.dpd_at_placement,
            "dpd_bucket_at_placement": p.dpd_bucket_at_placement.value,
            "exposure_at_placement": float(p.exposure_at_placement),
            "expected_recovery_prob": p.expected_recovery_prob,
            "sla_first_visit_due": p.sla_first_visit_due.isoformat() if p.sla_first_visit_due else None,
            "placement_run_id": p.placement_run_id,
        } for p, lan, trade, legal in rows]
        return {"items": items, "total": total, "page": page, "page_size": page_size,
                "synthetic_warning": self._synthetic_warning([p.model_prediction_id for p, *_ in rows])}

    def _synthetic_warning(self, prediction_ids: list[str | None]) -> str | None:
        """The SYNTHETIC_WARNING of the model artifact(s) behind the shown
        expected_recovery_prob values, read from the artifact's own metadata
        (the ml/pipeline convention), so the page cannot show a synthetic
        model's number without saying so."""
        from app.ml.pipeline.config import RECOVERY_RISK
        from app.models.model_prediction import ModelPrediction
        ids = [i for i in prediction_ids if i]
        if not ids:
            return None
        versions = sorted({v for (v,) in self.db.query(ModelPrediction.model_version)
                           .filter(ModelPrediction.id.in_(ids)).distinct()})
        warnings = [w for v in versions if (w := artifact_synthetic_warning(RECOVERY_RISK.name, v))]
        return " ".join(dict.fromkeys(warnings)) or None

