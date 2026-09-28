# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-24 (B10) — New file. Lookup tables for the extensible business
#   vocabularies that v1 kept as free String(30) columns with the allowed
#   values in a comment (docs/DATA-MODEL-V2.md §2.4). Python still sees plain
#   strings — the column stays a VARCHAR with an FK to `code` — so no call site
#   changes, but Postgres now refuses a value nobody declared.
#
#   Why not more native enums: `ALTER TYPE … ADD VALUE` has already been needed
#   three times (5 values) in this repo's migrations, and it cannot run inside
#   a transaction on older servers. A lookup row is an ordinary INSERT.
#
#   LOOKUP_SEEDS is the one definition of the seed rows: the migration inserts
#   them and tests/pg seeds from the same dict.
# ────────────────────────────────────────────────────────────────────────────
from sqlalchemy import Boolean, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.allocation_decision import AllocationOutcome
from app.models.allocation_setting import AllocationObjective
from app.models.base import Base


class _LookupMixin:
    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    is_terminal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class LegalStatusLookup(Base, _LookupMixin):
    __tablename__ = "legal_statuses"
    __table_args__ = {"schema": "lending"}


class SettlementStatusLookup(Base, _LookupMixin):
    __tablename__ = "settlement_statuses"
    __table_args__ = {"schema": "lending"}


class BankActionTypeLookup(Base, _LookupMixin):
    __tablename__ = "bank_action_types"
    __table_args__ = {"schema": "lending"}


class CollectionStageLookup(Base, _LookupMixin):
    __tablename__ = "collection_stages"
    __table_args__ = {"schema": "collections"}


class AllocationOutcomeLookup(Base, _LookupMixin):
    __tablename__ = "allocation_outcomes"
    __table_args__ = {"schema": "planning"}


class AllocationObjectiveLookup(Base, _LookupMixin):
    __tablename__ = "allocation_objectives"
    __table_args__ = {"schema": "planning"}


class PlacementOutcomeLookup(Base, _LookupMixin):
    __tablename__ = "placement_outcomes"
    __table_args__ = {"schema": "planning"}


def _rows(*codes: str, terminal: tuple[str, ...] = ()) -> list[dict]:
    return [
        {"code": c, "label": c.replace("_", " ").title(), "sort_order": i,
         "is_terminal": c in terminal}
        for i, c in enumerate(codes)
    ]


# table name → rows. Sources are cited in the design (§4.2, §4.3, §4.5).
LOOKUP_SEEDS: dict[str, list[dict]] = {
    "legal_statuses": _rows("NONE", "NOTICE_SENT", "SARFAESI", "SUIT_FILED", "DRT", "ARBITRATION"),
    "settlement_statuses": _rows("NONE", "OFFERED", "NEGOTIATING", "ACCEPTED", "REJECTED",
                                 terminal=("ACCEPTED", "REJECTED")),
    "bank_action_types": _rows("PAID_DIRECT", "RECALL", "SETTLED", "WRITTEN_OFF", "DECEASED"),
    "collection_stages": _rows("SOFT_CALL", "FIELD", "PRE_LEGAL", "LEGAL", "NPA_RECOVERY",
                               "WRITTEN_OFF_RECOVERY"),
    # Derived from the Python enums, never restated: a hand-typed copy of
    # these was wrong on the first draft of this file (it invented values and
    # missed DEFERRED_PTP / DEFERRED_VISIT_CAP / MIN_DISTANCE).
    "allocation_outcomes": _rows(*(o.value for o in AllocationOutcome)),
    "allocation_objectives": _rows(*(o.value for o in AllocationObjective)),
    "placement_outcomes": _rows("PLACED", "KEPT", "BLOCKED", "DEFERRED", "RECALLED"),
}

LOOKUP_MODELS = {
    m.__tablename__: m for m in (
        LegalStatusLookup, SettlementStatusLookup, BankActionTypeLookup, CollectionStageLookup,
        AllocationOutcomeLookup, AllocationObjectiveLookup, PlacementOutcomeLookup,
    )
}
