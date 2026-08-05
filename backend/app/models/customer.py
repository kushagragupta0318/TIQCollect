import enum
from sqlalchemy import String, Float, Integer, Boolean, Enum as SAEnum, Index, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin, UUIDPrimaryKey


class RiskCategory(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Customer(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "customers"

    # Core identifiers
    customer_ref: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    date_of_birth: Mapped[str] = mapped_column(String(10), nullable=False)
    gender: Mapped[str] = mapped_column(String(10), nullable=False)

    # Masked PII (stored masked — last 4 digits only shown)
    pan_masked: Mapped[str] = mapped_column(String(10), nullable=False)        # "XXXXX1234X"
    aadhaar_masked: Mapped[str] = mapped_column(String(12), nullable=False)    # "XXXXXXXX5678"

    # Contact
    phone_primary: Mapped[str] = mapped_column(String(15), nullable=False, index=True)
    phone_alternate: Mapped[str | None] = mapped_column(String(15), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Address
    address_line1: Mapped[str] = mapped_column(String(300), nullable=False)
    address_line2: Mapped[str | None] = mapped_column(String(300), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(100), nullable=False)
    pincode: Mapped[str] = mapped_column(String(6), nullable=False, index=True)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)

    # Risk
    risk_category: Mapped[RiskCategory] = mapped_column(
        SAEnum(RiskCategory, name="risk_category_enum"), default=RiskCategory.MEDIUM, nullable=False
    )
    risk_score: Mapped[float] = mapped_column(Float, default=50.0, nullable=False)
    cibil_score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Preferred contact window
    preferred_contact_start: Mapped[int] = mapped_column(Integer, default=9, nullable=False)
    preferred_contact_end: Mapped[int] = mapped_column(Integer, default=18, nullable=False)
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

    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    loans: Mapped[list["Loan"]] = relationship("Loan", back_populates="customer", lazy="noload")  # type: ignore[name-defined]  # noqa: F821
    cases: Mapped[list["Case"]] = relationship("Case", back_populates="customer", lazy="noload")  # type: ignore[name-defined]  # noqa: F821

    __table_args__ = (
        Index("ix_customer_risk_city", "risk_category", "city"),
        Index("ix_customer_location", "latitude", "longitude"),
    )
