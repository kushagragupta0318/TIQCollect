# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-24 (B05) — lending.customers (docs/DATA-MODEL-V2.md §4.2).
#   - bank_id: a borrower belongs to one bank; customer_ref is unique per bank
#     (it was global, which two banks' feeds would collide on).
#   - date_of_birth is a DATE (was String(10); 1,378/1,378 fixture rows ISO).
#   - tags is JSONB.
#   - DEFERRED, not done: the design moves the address and contact columns to
#     customer_addresses / customer_contacts. ~50 readers use them directly, and
#     creating the child tables before those readers move would leave two
#     sources of truth for one address. Tracked as task B23.
# ────────────────────────────────────────────────────────────────────────────
import enum
from datetime import date

from sqlalchemy import (
    Boolean, CheckConstraint, Date, Enum as SAEnum, Float, Index, Integer, SmallInteger, String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import PUBLIC, Base, JsonDoc, TimestampMixin, UUIDPrimaryKey, uuid_fk


class RiskCategory(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


RISK_CATEGORY_SQL = SAEnum(RiskCategory, name="risk_category_enum", schema=PUBLIC, metadata=Base.metadata)


class Customer(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "customers"

    bank_id: Mapped[str] = uuid_fk("tenancy.banks.id")

    # Core identifiers
    customer_ref: Mapped[str] = mapped_column(String(30), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    gender: Mapped[str] = mapped_column(String(10), nullable=False)

    # Masked PII (stored masked — last 4 digits only shown)
    pan_masked: Mapped[str] = mapped_column(String(10), nullable=False)        # "XXXXX1234X"
    aadhaar_masked: Mapped[str] = mapped_column(String(12), nullable=False)    # "XXXXXXXX5678"

    # Contact (moves to customer_contacts in B23)
    phone_primary: Mapped[str] = mapped_column(String(15), nullable=False)
    phone_alternate: Mapped[str | None] = mapped_column(String(15))
    email: Mapped[str | None] = mapped_column(String(255))

    # Address (moves to customer_addresses in B23)
    address_line1: Mapped[str] = mapped_column(String(300), nullable=False)
    address_line2: Mapped[str | None] = mapped_column(String(300))
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(100), nullable=False)
    pincode: Mapped[str] = mapped_column(String(6), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)

    # Risk
    risk_category: Mapped[RiskCategory] = mapped_column(RISK_CATEGORY_SQL, default=RiskCategory.MEDIUM, nullable=False)
    risk_score: Mapped[float] = mapped_column(Float, default=50.0, nullable=False)
    cibil_score: Mapped[int | None] = mapped_column(Integer)

    # Preferred contact window
    preferred_contact_start: Mapped[int] = mapped_column(SmallInteger, default=9, nullable=False)
    preferred_contact_end: Mapped[int] = mapped_column(SmallInteger, default=18, nullable=False)
    language_preference: Mapped[str] = mapped_column(String(20), default="HINDI", nullable=False)

    # Employment / demographic segment (from bank KYC)
    customer_segment: Mapped[str] = mapped_column(
        String(30), default="SALARIED", nullable=False
    )  # SALARIED / SELF_EMPLOYED / BUSINESS_OWNER / RETIRED / HOMEMAKER / STUDENT

    # Flags
    is_hostile: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    requires_female_agent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    do_not_contact: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fraud_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    complaints_raised: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    tags: Mapped[list] = mapped_column(JsonDoc, default=list, nullable=False)

    loans: Mapped[list["Loan"]] = relationship("Loan", back_populates="customer", lazy="noload", primaryjoin="Customer.id == Loan.customer_id", foreign_keys="[Loan.customer_id]")  # type: ignore[name-defined]  # noqa: F821
    cases: Mapped[list["Case"]] = relationship("Case", back_populates="customer", lazy="noload", primaryjoin="Customer.id == Case.customer_id", foreign_keys="[Case.customer_id]")  # type: ignore[name-defined]  # noqa: F821

    __table_args__ = (
        UniqueConstraint("bank_id", "customer_ref"),
        UniqueConstraint("id", "bank_id"),
        CheckConstraint("preferred_contact_start >= 0 AND preferred_contact_end <= 24", name="contact_window"),
        Index(None, "bank_id", "risk_category", "city"),
        Index(None, "bank_id", "phone_primary"),
        Index(None, "bank_id", "pincode"),
        Index(None, "latitude", "longitude"),
        {"schema": "lending"},
    )
