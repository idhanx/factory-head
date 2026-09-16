"""The Job Order spine (§3) — the design decision everything hangs on.

    "The system does not revolve around machines. It revolves around Job
     Orders. From that moment, every event recorded — every production count,
     every downtime, every rejection — automatically inherits all of those
     tags. Nobody types a customer name or a lot number during the shift, ever."

Two structural consequences:

* A Job Order **spans shifts, days and machines** (ARCHITECTURE.md C4).
  §14.3: "300 pcs Monday on CNC-3, 200 pcs Tuesday on CNC-5 — under one job
  order, one lot lineage." Attaching a job order to a shift makes split jobs,
  the Change Job event and multi-day throughput time unrepresentable.
* The shift-scoped entity is therefore a **JobSession**: this station ran this
  job from t1 to t2. Events attach to the session, and inherit the job's tags
  through it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import Enum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, PlantScopedMixin, TimestampMixin, uuid_pk
from .types import EnumString


class JobOrderStatus(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    PAUSED = "paused"  # §14.3 — a Change Job pauses, it never deletes
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class LotGranularity(str, Enum):
    """§3 — the plant climbs this ladder at its own pace.

    A plant with no lot discipline starts coarse: everything Line 2 produced on
    14 June is lot L2-140626. One rule, implementable on day one, and already
    better traceability than most tier-2 plants have.
    """

    DAY_BATCH = "day_batch"
    SHIFT_BATCH = "shift_batch"
    JOB_LOT = "job_lot"


class ShiftSessionStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"  # data locks permanently at close
    REOPENED = "reopened"  # owner override only, always logged


class JobOrder(Base, PlantScopedMixin, TimestampMixin):
    """Carries the tags every downstream event inherits for free."""

    __tablename__ = "job_orders"
    __table_args__ = (
        UniqueConstraint("plant_id", "job_number"),
        Index("ix_job_orders_status", "plant_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    job_number: Mapped[str] = mapped_column(String(64), nullable=False)  # "2231"
    part_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("parts.id", ondelete="RESTRICT"), index=True
    )
    customer_name: Mapped[str | None] = mapped_column(String(200))
    planned_quantity: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[JobOrderStatus] = mapped_column(
        EnumString(JobOrderStatus, 16), default=JobOrderStatus.PLANNED, nullable=False
    )
    due_date: Mapped[date | None] = mapped_column(Date)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)

    lots: Mapped[list[Lot]] = relationship(back_populates="job_order")
    sessions: Mapped[list[JobSession]] = relationship(back_populates="job_order")


class Lot(Base, PlantScopedMixin, TimestampMixin):
    """The identity that travels physically on a printed QR / bin card.

    §3: printed paper, zero hardware, zero cost. If the plant already runs
    kanban, the kanban card already IS a lot identity moving with material —
    print the QR on it and a card movement becomes a stage-completion event.
    The plant's physical pull system stays untouched.
    """

    __tablename__ = "lots"
    __table_args__ = (UniqueConstraint("plant_id", "lot_code"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    lot_code: Mapped[str] = mapped_column(String(64), nullable=False)  # "4471", "L2-140626"
    job_order_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("job_orders.id", ondelete="SET NULL"), index=True
    )
    granularity: Mapped[LotGranularity] = mapped_column(
        EnumString(LotGranularity, 16), default=LotGranularity.JOB_LOT
    )
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job_order: Mapped[JobOrder | None] = relationship(back_populates="lots")


class ShiftSession(Base, PlantScopedMixin, TimestampMixin):
    """One shift on one plant-day. Screens 1 and 6 bracket this row.

    ``business_date`` is resolved once, at shift start, using the plant's
    configured rule — never recomputed from a timestamp later. Every "yesterday"
    number the owner reads depends on this column being stable.
    """

    __tablename__ = "shift_sessions"
    __table_args__ = (
        UniqueConstraint("plant_id", "shift_id", "business_date"),
        Index("ix_shift_sessions_date", "plant_id", "business_date"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    shift_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("shifts.id", ondelete="RESTRICT"), index=True
    )
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    supervisor_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    status: Mapped[ShiftSessionStatus] = mapped_column(
        EnumString(ShiftSessionStatus, 16), default=ShiftSessionStatus.OPEN, nullable=False
    )

    # The lock. Once set, nothing inside this shift can be edited — that is the
    # whole point (§2: "data locks permanently at shift close").
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    # A supervisor may request an owner override rather than fake a reason code.
    # Overriding is allowed; overriding silently is not.
    override_reason: Mapped[str | None] = mapped_column(Text)
    override_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    planned_minutes: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict | None] = mapped_column(JSONB)  # frozen KPI snapshot

    sessions: Mapped[list[JobSession]] = relationship(back_populates="shift_session")


class JobSession(Base, PlantScopedMixin, TimestampMixin):
    """This station ran this job from t1 to t2.

    The shift-scoped join between a long-lived Job Order and a station. Events
    attach here, which is how a production count typed by a supervisor knows its
    part number, customer, lot, operator and shift without anyone typing them.
    """

    __tablename__ = "job_sessions"
    __table_args__ = (
        Index("ix_job_sessions_station_open", "plant_id", "station_id", "ended_at"),
        Index("ix_job_sessions_shift", "shift_session_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    job_order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("job_orders.id", ondelete="CASCADE"), index=True
    )
    shift_session_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("shift_sessions.id", ondelete="CASCADE")
    )
    station_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("stations.id", ondelete="RESTRICT"), index=True
    )
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("lots.id", ondelete="SET NULL")
    )
    routing_operation_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("routing_operations.id", ondelete="SET NULL")
    )
    operator_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operators.id", ondelete="SET NULL")
    )

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # §14.3 — when a station switches jobs mid-shift, the gap between 'Job A
    # paused' and the first entry on Job B is recorded as Setup/Changeover
    # downtime and costed like any other stop. Changeover time is a loss most
    # plants never measure; here it is captured for free.
    ended_by_change_job: Mapped[bool] = mapped_column(Boolean, default=False)
    previous_session_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("job_sessions.id", ondelete="SET NULL")
    )

    job_order: Mapped[JobOrder] = relationship(back_populates="sessions")
    shift_session: Mapped[ShiftSession] = relationship(back_populates="sessions")
