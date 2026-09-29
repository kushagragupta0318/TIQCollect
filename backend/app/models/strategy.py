"""The `strategy` schema: what the bank's own levers cost (DATA-MODEL-V2 §4.8).

`cost_rates` is the ONE cost table: the scorecard's field_cost, the scenario
simulator and the activity ledger all read unit costs from here. Agency
commission is not a cost rate; it comes from agency_contract_terms.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import CheckConstraint, Date, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPrimaryKey, uuid_fk

SCHEMA = "strategy"

COST_CHANNELS = ("FIELD_VISIT", "CALL", "SMS", "WHATSAPP", "EMAIL", "IVR", "LEGAL_NOTICE")
COST_UNITS = ("PER_ATTEMPT", "PER_CONTACT", "PER_KM", "PER_MESSAGE", "PER_CASE")


class CostRate(Base, UUIDPrimaryKey, CreatedAtMixin):
    __tablename__ = "cost_rates"

    bank_id: Mapped[str] = uuid_fk("tenancy.banks.id")
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    unit: Mapped[str] = mapped_column(String(12), nullable=False)
    rate_inr: Mapped[float] = mapped_column(Numeric(12, 4, asdecimal=False), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)          # NULL = open-ended
    source: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = uuid_fk("tenancy.users.id", nullable=True)

    __table_args__ = (
        UniqueConstraint("bank_id", "channel", "unit", "valid_from"),
        CheckConstraint("channel IN (" + ", ".join(repr(c) for c in COST_CHANNELS) + ")", name="channel"),
        CheckConstraint("unit IN (" + ", ".join(repr(u) for u in COST_UNITS) + ")", name="unit"),
        CheckConstraint("rate_inr >= 0", name="rate_non_negative"),
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="valid_range"),
        {"schema": SCHEMA},
    )
