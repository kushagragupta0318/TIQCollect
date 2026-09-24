# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-24 (B03) — New file. The tenant spine of the standalone product:
#   Bank → Region hierarchy → Agency (+ contract, commission terms, coverage,
#   documents), and the capability catalog. docs/DATA-MODEL-V2.md §4.1.
#
#   v1 had none of this: `agents.agency_id` was a free String(50) read only by
#   /verify-agent, and "ABC Bank" was a literal on every loan and in seven
#   files of SMS text. Tenancy is now data, and the tenant columns that every
#   other table carries point here.
# ────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey,
    ForeignKeyConstraint, Index, Integer, SmallInteger, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base, CreatedAtMixin, JsonDoc, Money, Rate, TimestampMixin, UUIDPrimaryKey,
    UUIDType, uuid_fk,
)
from app.models.loan import DPD_BUCKET_SQL, LOAN_TYPE_SQL, DPDBucket, LoanType
from app.models.user import USER_ROLE_SQL, UserRole

SCHEMA = "tenancy"


def _check_in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


# ── Banks ────────────────────────────────────────────────────────────────────

BANK_STATUSES = ("ACTIVE", "SUSPENDED", "ARCHIVED")


class Bank(Base, UUIDPrimaryKey, TimestampMixin):
    """The tenant root. The name that used to be hardcoded in SMS, receipts and
    the UPI QR is `display_name`; the rest of the brand lives in `brand`."""
    __tablename__ = "banks"

    code: Mapped[str] = mapped_column(String(20), nullable=False)
    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    rbi_entity_code: Mapped[str | None] = mapped_column(String(40))
    # The business-day definition (design §2.3): a "day" is the bank's calendar day.
    timezone: Mapped[str] = mapped_column(String(40), nullable=False, default="Asia/Kolkata")
    # logo_key, sms_sender_id, upi_vpa, upi_payee_name, support_phone, receipt_footer
    brand: Mapped[dict] = mapped_column(JsonDoc, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="ACTIVE")
    # Demo tenants: invented, never contacted (outbound messaging suppressed).
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("code"),
        CheckConstraint(_check_in("status", BANK_STATUSES), name="status"),
        {"schema": SCHEMA},
    )

    def __repr__(self) -> str:
        return f"<Bank {self.code}>"


# ── Region hierarchy: ZONE → REGION → STATE → CITY ───────────────────────────

REGION_LEVELS = ("ZONE", "REGION", "STATE", "CITY")


class Region(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "regions"

    bank_id: Mapped[str] = uuid_fk("tenancy.banks.id")
    parent_id: Mapped[str | None] = mapped_column(UUIDType)
    level: Mapped[str] = mapped_column(String(8), nullable=False)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Materialised "/zone/region/state/city/" of codes, written by the region
    # service, so "everything under NCR" is one prefix filter.
    path: Mapped[str] = mapped_column(Text, nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    coverage_geojson: Mapped[dict | None] = mapped_column(JsonDoc)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("bank_id", "level", "code"),
        UniqueConstraint("id", "bank_id"),
        ForeignKeyConstraint(["parent_id", "bank_id"], ["tenancy.regions.id", "tenancy.regions.bank_id"],
                             ondelete="RESTRICT"),
        CheckConstraint(_check_in("level", REGION_LEVELS), name="level"),
        CheckConstraint("(level = 'ZONE') = (parent_id IS NULL)", name="zone_is_root"),
        Index(None, "bank_id", "level"),
        Index(None, "parent_id"),
        {"schema": SCHEMA},
    )


class Branch(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "branches"

    bank_id: Mapped[str] = uuid_fk("tenancy.banks.id")
    region_id: Mapped[str | None] = mapped_column(UUIDType)
    branch_code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str | None] = mapped_column(String(100))
    address: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("bank_id", "branch_code"),
        UniqueConstraint("id", "bank_id"),
        ForeignKeyConstraint(["region_id", "bank_id"], ["tenancy.regions.id", "tenancy.regions.bank_id"],
                             ondelete="RESTRICT"),
        Index(None, "region_id"),
        {"schema": SCHEMA},
    )


# ── Agencies ─────────────────────────────────────────────────────────────────

AGENCY_STATUSES = ("PENDING", "ACTIVE", "SUSPENDED", "OFFBOARDED")


class Agency(Base, UUIDPrimaryKey, TimestampMixin):
    """One row per (bank, agency): a real firm working for two banks is two
    rows, one per tenant (design Q3)."""
    __tablename__ = "agencies"

    bank_id: Mapped[str] = uuid_fk("tenancy.banks.id")
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    trade_name: Mapped[str | None] = mapped_column(String(200))
    entity_type: Mapped[str | None] = mapped_column(String(20))        # PVT_LTD / LLP / PARTNERSHIP / PROPRIETORSHIP
    cin: Mapped[str | None] = mapped_column(String(30))                 # CIN or LLPIN
    rbi_registration_no: Mapped[str | None] = mapped_column(String(50))
    pan: Mapped[str | None] = mapped_column(String(10))
    gstin: Mapped[str | None] = mapped_column(String(15))
    registered_address: Mapped[dict | None] = mapped_column(JsonDoc)
    hq_city: Mapped[str | None] = mapped_column(String(100))
    website: Mapped[str | None] = mapped_column(String(200))
    # Named people: [{role, name, email, phone}] — Director, Operations Head,
    # Compliance Officer. The master login is a user, not a contact.
    contacts: Mapped[list] = mapped_column(JsonDoc, nullable=False, default=list)
    contact_name: Mapped[str | None] = mapped_column(String(200))
    contact_email: Mapped[str | None] = mapped_column(String(255))
    contact_phone: Mapped[str | None] = mapped_column(String(15))
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="PENDING")
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspended_reason: Mapped[str | None] = mapped_column(Text)
    offboarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    offboard_requested_by: Mapped[str | None] = mapped_column(UUIDType)
    offboard_approved_by: Mapped[str | None] = mapped_column(UUIDType)
    created_by: Mapped[str | None] = mapped_column(UUIDType)
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("bank_id", "code"),
        UniqueConstraint("id", "bank_id"),
        CheckConstraint(_check_in("status", AGENCY_STATUSES), name="status"),
        CheckConstraint(
            "offboard_approved_by IS NULL OR offboard_requested_by IS NULL "
            "OR offboard_approved_by <> offboard_requested_by",
            name="four_eyes_offboard",
        ),
        # users ↔ agencies point at each other; these three are created after
        # both tables exist (use_alter).
        ForeignKeyConstraint(["created_by"], ["tenancy.users.id"], ondelete="RESTRICT", use_alter=True),
        ForeignKeyConstraint(["offboard_requested_by"], ["tenancy.users.id"], ondelete="RESTRICT", use_alter=True),
        ForeignKeyConstraint(["offboard_approved_by"], ["tenancy.users.id"], ondelete="RESTRICT", use_alter=True),
        Index(None, "bank_id", "status"),
        {"schema": SCHEMA},
    )

    def __repr__(self) -> str:
        return f"<Agency {self.code} {self.status}>"


CONTRACT_STATUSES = ("DRAFT", "ACTIVE", "EXPIRED", "TERMINATED")


class AgencyContract(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "agency_contracts"

    bank_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    agency_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    contract_no: Mapped[str] = mapped_column(String(40), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="DRAFT")
    max_placed_cases: Mapped[int | None] = mapped_column(Integer)
    max_agents: Mapped[int | None] = mapped_column(Integer)                 # the seat limit
    max_visits_per_month: Mapped[int | None] = mapped_column(Integer)       # Monte Carlo capacity lever
    sla_first_visit_days: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=7)
    recall_no_activity_days: Mapped[int | None] = mapped_column(SmallInteger)
    recall_on_sla_breach: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    recall_at_contract_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    performance_bonus_pct: Mapped[float | None] = mapped_column(Rate)
    performance_target_pct: Mapped[float | None] = mapped_column(Rate)
    security_deposit: Mapped[float | None] = mapped_column(Money)
    renewal_of_id: Mapped[str | None] = uuid_fk("tenancy.agency_contracts.id", nullable=True)
    agreement_document_id: Mapped[str | None] = mapped_column(UUIDType)
    created_by: Mapped[str | None] = uuid_fk("tenancy.users.id", nullable=True)
    approved_by: Mapped[str | None] = uuid_fk("tenancy.users.id", nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("bank_id", "contract_no"),
        UniqueConstraint("id", "agency_id"),
        ForeignKeyConstraint(["agency_id", "bank_id"], ["tenancy.agencies.id", "tenancy.agencies.bank_id"],
                             ondelete="RESTRICT"),
        ForeignKeyConstraint(["agreement_document_id"], ["tenancy.agency_documents.id"],
                             ondelete="RESTRICT", use_alter=True),
        CheckConstraint(_check_in("status", CONTRACT_STATUSES), name="status"),
        CheckConstraint("end_date >= start_date", name="dates"),
        Index(None, "agency_id", "status", "end_date"),
        {"schema": SCHEMA},
    )


class AgencyContractTerm(Base, UUIDPrimaryKey, TimestampMixin):
    """The commission slab and the (product, bucket) authorisation, as rows so
    SQL — the Cost to Collect KPI and the placement hard gate — can read it."""
    __tablename__ = "agency_contract_terms"

    bank_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    agency_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    contract_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    loan_type: Mapped[LoanType] = mapped_column(LOAN_TYPE_SQL, nullable=False)
    dpd_bucket: Mapped[DPDBucket] = mapped_column(DPD_BUCKET_SQL, nullable=False)
    commission_pct: Mapped[float] = mapped_column(Rate, nullable=False)
    fixed_fee_per_resolution: Mapped[float | None] = mapped_column(Money)
    is_authorised: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("contract_id", "loan_type", "dpd_bucket"),
        ForeignKeyConstraint(["contract_id", "agency_id"],
                             ["tenancy.agency_contracts.id", "tenancy.agency_contracts.agency_id"],
                             ondelete="CASCADE"),
        CheckConstraint("commission_pct >= 0 AND commission_pct <= 100", name="commission_range"),
        {"schema": SCHEMA},
    )


class AgencyRegion(Base, UUIDPrimaryKey, TimestampMixin):
    """Coverage is contractual: covering a node covers its subtree (via path)."""
    __tablename__ = "agency_regions"

    bank_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    agency_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    contract_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    region_id: Mapped[str] = mapped_column(UUIDType, nullable=False)

    __table_args__ = (
        UniqueConstraint("contract_id", "region_id"),
        ForeignKeyConstraint(["contract_id", "agency_id"],
                             ["tenancy.agency_contracts.id", "tenancy.agency_contracts.agency_id"],
                             ondelete="CASCADE"),
        ForeignKeyConstraint(["region_id", "bank_id"], ["tenancy.regions.id", "tenancy.regions.bank_id"],
                             ondelete="RESTRICT"),
        Index(None, "region_id"),
        {"schema": SCHEMA},
    )


DOC_TYPES = (
    "INCORPORATION_CERT", "REGISTRATION_CERT", "AGREEMENT", "INSURANCE",
    "POLICE_VERIFICATION_POLICY", "PAN", "GST", "DRA_REGISTER", "OTHER",
)
DOC_STATUSES = ("UPLOADED", "VERIFIED", "REJECTED", "EXPIRED", "SUPERSEDED")
SCAN_STATUSES = ("PENDING", "CLEAN", "INFECTED", "ERROR")


class AgencyDocument(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "agency_documents"

    bank_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    agency_id: Mapped[str] = mapped_column(UUIDType, nullable=False)
    doc_type: Mapped[str] = mapped_column(String(30), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    file_name: Mapped[str | None] = mapped_column(String(255))
    content_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    scan_status: Mapped[str] = mapped_column(String(10), nullable=False, default="PENDING")
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="UPLOADED")
    issued_on: Mapped[date | None] = mapped_column(Date)
    expires_on: Mapped[date | None] = mapped_column(Date)
    uploaded_by: Mapped[str | None] = uuid_fk("tenancy.users.id", nullable=True)
    verified_by: Mapped[str | None] = uuid_fk("tenancy.users.id", nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        ForeignKeyConstraint(["agency_id", "bank_id"], ["tenancy.agencies.id", "tenancy.agencies.bank_id"],
                             ondelete="RESTRICT"),
        CheckConstraint(_check_in("doc_type", DOC_TYPES), name="doc_type"),
        CheckConstraint(_check_in("status", DOC_STATUSES), name="status"),
        CheckConstraint(_check_in("scan_status", SCAN_STATUSES), name="scan_status"),
        Index(None, "agency_id", "doc_type", "status"),
        Index("ix_agency_documents_expiring", "bank_id", "expires_on",
              postgresql_where="status = 'VERIFIED'", sqlite_where="status = 'VERIFIED'"),
        {"schema": SCHEMA},
    )


# ── Capabilities ─────────────────────────────────────────────────────────────

class Permission(Base, CreatedAtMixin):
    """The capability catalog. The registry in code (core/permissions.py) is
    the one definition; this table is seeded from it and a test asserts the
    two agree."""
    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    requires_second_person: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = ({"schema": SCHEMA},)


class RolePermission(Base, CreatedAtMixin):
    """Global, not per bank: capabilities are granted to a role in one table."""
    __tablename__ = "role_permissions"

    role: Mapped[UserRole] = mapped_column(USER_ROLE_SQL, primary_key=True)
    permission_code: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenancy.permissions.code", ondelete="CASCADE"), primary_key=True)

    __table_args__ = ({"schema": SCHEMA},)
