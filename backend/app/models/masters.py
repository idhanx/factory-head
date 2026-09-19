"""Setup data — entered once during onboarding (§4.1), from templates T1-T7 (§15).

Two rules shape this module:

* **Never type what can be uploaded.** Every table here maps to a template
  column, so a plant that hands over T1 and T2 can go live the same day.
* **Never block on what can be estimated.** Missing cycle times, unknown
  margins and absent cost data all have documented fallbacks (§4.1), because
  onboarding that takes three days does not scale.
"""

from __future__ import annotations

import uuid
from datetime import date, time
from decimal import Decimal
from enum import Enum

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, PlantScopedMixin, TimestampMixin, uuid_pk
from .types import EnumString


class Section(str, Enum):
    """§14.2 — the four stages every part passes through."""

    MACHINING = "machining"
    ASSEMBLY = "assembly"
    QUALITY = "quality"
    DISPATCH = "dispatch"


class Recoverability(str, Enum):
    RECOVERABLE = "R"
    STRUCTURAL = "S"


class OeeTreatment(str, Enum):
    PLANNED = "planned"
    UNPLANNED = "unplanned"


class EscalationRoute(str, Enum):
    """The system picks the recipient from the reason code (§3 Layer 2)."""

    MAINTENANCE = "maintenance"
    STORES = "stores"
    QUALITY = "quality"
    PRODUCTION = "production"
    NONE = "none"


class MarginBand(str, Enum):
    LOW = "L"
    MEDIUM = "M"
    HIGH = "H"


class RateSource(str, Enum):
    """§14.4 — the three-rung ladder when data doesn't exist.

    Storing which rung a number came from is what lets the plant see its own
    accuracy improve, and lets us say '±30%' honestly instead of implying
    precision the input never had.
    """

    DEFAULT = "default"  # pre-loaded library, ±30%, day 1
    DERIVED = "derived"  # reconstructed from wage/electricity bills, ±15-20%
    MEASURED = "measured"  # time study / accountant session, ±5-10%


# ---------------------------------------------------------------------------
# T1 — Machines & Stations
# ---------------------------------------------------------------------------


class CostFamily(Base, PlantScopedMixin, TimestampMixin):
    """§14.4 — 100 machines collapse to 6-8 rates by grouping into families.

    100 individual rates is an onboarding death march; one plant-wide rate
    distorts the loss ranking. Families are the answer.
    """

    __tablename__ = "cost_families"
    __table_args__ = (UniqueConstraint("plant_id", "code"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(64), nullable=False)  # "CNC-family"
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    stations: Mapped[list[Station]] = relationship(back_populates="cost_family")


class Station(Base, PlantScopedMixin, TimestampMixin):
    """T1 — one row per machine/station."""

    __tablename__ = "stations"
    __table_args__ = (UniqueConstraint("plant_id", "code"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(64), nullable=False)  # "CNC-3"
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    station_type: Mapped[str | None] = mapped_column(String(100))  # "CNC turning"
    section: Mapped[Section] = mapped_column(
        EnumString(Section, 20), default=Section.MACHINING
    )

    # §8.2 — the correction that protects credibility. Set on the floor walk by
    # whoever has the Lean judgment, not guessed by the software.
    is_bottleneck: Mapped[bool] = mapped_column(Boolean, default=False)
    bottleneck_factor: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), default=Decimal("1.00")
    )

    cost_family_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("cost_families.id", ondelete="SET NULL")
    )
    line: Mapped[str | None] = mapped_column(String(64))  # "Line 2"
    zone: Mapped[str | None] = mapped_column(String(64))  # §14.5 device topology
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    cost_family: Mapped[CostFamily | None] = relationship(back_populates="stations")


# ---------------------------------------------------------------------------
# T2 / T3 — Part master, routing, cycle times
# ---------------------------------------------------------------------------


class Part(Base, PlantScopedMixin, TimestampMixin):
    """T2 — one row per part number.

    Customer is a NAME and nothing more (§14.1): no addresses, no contacts. It
    exists solely so traceability can answer 'which customer received this lot'.
    """

    __tablename__ = "parts"
    __table_args__ = (UniqueConstraint("plant_id", "part_number"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    part_number: Mapped[str] = mapped_column(String(64), nullable=False)  # "AK-447"
    description: Mapped[str | None] = mapped_column(String(255))
    customer_name: Mapped[str | None] = mapped_column(String(200))
    part_family: Mapped[str | None] = mapped_column(String(100))  # "Brackets"

    # §4.1 don't-block rule: an owner who won't disclose exact margins picks a
    # band per family. The loss ranking stays correct; precision comes when
    # trust does.
    contribution_margin: Mapped[int | None] = mapped_column()  # paise, exact
    margin_band: Mapped[MarginBand | None] = mapped_column(String(2))

    material_cost: Mapped[int] = mapped_column(default=0)  # paise, for scrap
    # Scrap is sold back. Ignoring that recovery overstates every scrap saving
    # by 20-30%, so the weight sits beside the material cost: recovery is
    # ``weight_kg x scrap recovery rate`` and is deducted, never assumed away.
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    operations: Mapped[list[RoutingOperation]] = relationship(
        back_populates="part",
        cascade="all, delete-orphan",
        order_by="RoutingOperation.sequence",
    )


class RoutingOperation(Base, PlantScopedMixin, TimestampMixin):
    """T2 routing + T3 cycle time — the ordered operations a part passes through.

    §9: the model supports operations from day one even though the multi-stage
    UI follows later. "Retrofitting a routing later is a rebuild."
    """

    __tablename__ = "routing_operations"
    __table_args__ = (
        UniqueConstraint("plant_id", "part_id", "sequence"),
        Index("ix_routing_part_seq", "part_id", "sequence"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    part_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("parts.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)  # 10, 20, 30, 40
    operation_code: Mapped[str] = mapped_column(String(32), nullable=False)  # "Op10"
    name: Mapped[str | None] = mapped_column(String(200))
    section: Mapped[Section] = mapped_column(
        EnumString(Section, 20), default=Section.MACHINING
    )

    cost_family_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("cost_families.id", ondelete="SET NULL")
    )
    cycle_time_min: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    cycle_time_source: Mapped[RateSource] = mapped_column(
        EnumString(RateSource, 16), default=RateSource.DEFAULT
    )

    part: Mapped[Part] = relationship(back_populates="operations")


# ---------------------------------------------------------------------------
# T4 — Cost inputs, effective-dated
# ---------------------------------------------------------------------------


class CostRate(Base, PlantScopedMixin, TimestampMixin):
    """Effective-dated rates. History never restates (ARCHITECTURE.md C3).

    Correcting a machine rate in week 6 must not silently turn yesterday's
    ₹9,380 into ₹11,200 — and must not shift a Recovered Profit baseline out
    from under its own Action. New rates apply forward from ``effective_from``.
    """

    __tablename__ = "cost_rates"
    __table_args__ = (
        Index("ix_cost_rates_lookup", "plant_id", "scope", "scope_ref", "effective_from"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    # scope: 'cost_family' (machine rate) | 'section' (labour) | 'plant' (overhead)
    #      | 'utility' (a per-unit rate — see ``unit_rate`` below)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    scope_ref: Mapped[str] = mapped_column(String(64), nullable=False)

    machine_rate: Mapped[int] = mapped_column(default=0)  # MCR, paise/hour
    labour_rate: Mapped[int] = mapped_column(default=0)  # LCR, paise/hour
    overhead_rate: Mapped[int] = mapped_column(default=0)  # OHR, paise/hour
    # Rates that are per *unit* rather than per hour: electricity per kWh,
    # compressed air per m3, scrap sold back per kg. They belong in the cost
    # master with everything else — a plant's energy cost is a Core cost, not a
    # Kaizen one — and this is the column the Kaizen approval card writes
    # through when the plant supplies a missing rate for the first time
    # (Plugin Spec 7.3, "the one place Kaizen writes a cost value").
    unit_rate: Mapped[int] = mapped_column(default=0, server_default="0")

    source: Mapped[RateSource] = mapped_column(
        EnumString(RateSource, 16), default=RateSource.DEFAULT
    )
    derivation: Mapped[dict | None] = mapped_column(JSONB)  # the owner's own numbers
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)


class MarginBandValue(Base, PlantScopedMixin, TimestampMixin):
    """T4.D — L/M/H rupee values agreed per part family."""

    __tablename__ = "margin_band_values"
    __table_args__ = (UniqueConstraint("plant_id", "part_family", "band"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    part_family: Mapped[str] = mapped_column(String(100), nullable=False)
    band: Mapped[MarginBand] = mapped_column(EnumString(MarginBand, 2), nullable=False)
    value: Mapped[int] = mapped_column(nullable=False)  # paise per part


# ---------------------------------------------------------------------------
# T5 — People
# ---------------------------------------------------------------------------


class Operator(Base, PlantScopedMixin, TimestampMixin):
    """T5.B — names only, for job-tagging dropdowns. No logins at launch."""

    __tablename__ = "operators"
    __table_args__ = (UniqueConstraint("plant_id", "name"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    employee_code: Mapped[str | None] = mapped_column(String(64))
    # Skill Matrix AI (Layer 5) reads this; it adds no new daily entry burden.
    certified_station_ids: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(PGUUID(as_uuid=True))
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class AlertRecipient(Base, PlantScopedMixin, TimestampMixin):
    """T5.A — the 5-8 people who receive escalations and reports."""

    __tablename__ = "alert_recipients"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role_tag: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # maintenance|stores|...
    whatsapp_number: Mapped[str | None] = mapped_column(String(20))
    escalation_level: Mapped[int] = mapped_column(Integer, default=2)  # L1|L2|L3
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# ---------------------------------------------------------------------------
# T6 — Reason and defect codes
# ---------------------------------------------------------------------------


class ReasonCode(Base, PlantScopedMixin, TimestampMixin):
    """T6 — downtime reasons. A fixed dropdown, never free text.

    Carries FOUR independent attributes, not one. The spec's T6 gives only
    ``recoverability``, but three different formulas consume this row and they
    need different answers (ARCHITECTURE.md §2.3):

    * ``recoverability``  -> the money report and Action triggers
    * ``oee_treatment``   -> Availability. A power cut is Structural but
      unplanned; mapping S to 'planned' would silently improve OEE on every
      power cut, and a plant manager would notice.
    * ``is_breakdown``    -> MTBF/MTTR, so those never string-match a label
    * ``escalation_route``-> who gets the 15-minute WhatsApp
    """

    __tablename__ = "reason_codes"
    __table_args__ = (UniqueConstraint("plant_id", "code"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    label_ta: Mapped[str | None] = mapped_column(String(200))  # Tamil, via info icon

    recoverability: Mapped[Recoverability] = mapped_column(
        EnumString(Recoverability, 2), nullable=False
    )
    oee_treatment: Mapped[OeeTreatment] = mapped_column(
        EnumString(OeeTreatment, 16), default=OeeTreatment.UNPLANNED, nullable=False
    )
    is_breakdown: Mapped[bool] = mapped_column(Boolean, default=False)
    escalation_route: Mapped[EscalationRoute] = mapped_column(
        EnumString(EscalationRoute, 20), default=EscalationRoute.PRODUCTION
    )
    safety_flag: Mapped[bool] = mapped_column(Boolean, default=False)

    # Screen 3: "biggest buttons for the commonest reasons" — ordering matters
    # when a supervisor is logging a stop in 20 seconds with oily hands.
    display_order: Mapped[int] = mapped_column(Integer, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class DefectCode(Base, PlantScopedMixin, TimestampMixin):
    """T6 — 10-15 defect codes per part family."""

    __tablename__ = "defect_codes"
    __table_args__ = (UniqueConstraint("plant_id", "code"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    label_ta: Mapped[str | None] = mapped_column(String(200))
    part_family: Mapped[str | None] = mapped_column(String(100))
    display_order: Mapped[int] = mapped_column(Integer, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# ---------------------------------------------------------------------------
# T7 — Shifts and targets
# ---------------------------------------------------------------------------


class Shift(Base, PlantScopedMixin, TimestampMixin):
    """T7 — shift pattern. Breaks are planned downtime for the OEE denominator."""

    __tablename__ = "shifts"
    __table_args__ = (UniqueConstraint("plant_id", "name"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(64), nullable=False)  # "Shift A"
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    break_minutes: Mapped[int] = mapped_column(Integer, default=0)
    weekly_off: Mapped[str | None] = mapped_column(String(32))  # "Sun"
    display_order: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    @property
    def crosses_midnight(self) -> bool:
        return self.end_time <= self.start_time


class ProductionTarget(Base, PlantScopedMixin, TimestampMixin):
    """T7 — daily production target per line or machine group."""

    __tablename__ = "production_targets"

    id: Mapped[uuid.UUID] = uuid_pk()
    line: Mapped[str | None] = mapped_column(String(64))
    station_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE")
    )
    shift_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("shifts.id", ondelete="CASCADE")
    )
    target_parts: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
