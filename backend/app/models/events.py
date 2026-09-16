"""Capture rows — append-only facts from the shop floor (Layer 1).

Two properties every table here shares:

**The data-source seam (§2, §9, §16.3).** Every capture row records *how* it
arrived — ``source``. A production count typed by a supervisor and one emitted
by a CT clamp enter through the same contract and land in the same column.
That is what makes the hardware phase a data-source swap rather than a rebuild:

    "the data layer must treat 'who produced this data point' as
     interchangeable ... Get this right now and the hardware phase is a
     data-source swap, not a rebuild."

**The frozen cost snapshot (ARCHITECTURE.md C3).** At the moment an event is
costed, the kernel's resolved *inputs* are written onto the row beside the
outputs. A locked shift is arithmetically frozen and can be explained years
later; a rate correction in week 6 never restates week 1.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, PlantScopedMixin, TimestampMixin, uuid_pk
from .types import EnumString


class DataSource(str, Enum):
    """The seam. Today only MANUAL exists; the rest are already representable."""

    MANUAL = "manual"  # a supervisor's thumb on a tablet
    SENSOR = "sensor"  # CT clamp, pulse counter, controller tap (via MQTT)
    IMPORT = "import"  # backfill from a spreadsheet during onboarding
    SYSTEM = "system"  # derived by the platform, e.g. a changeover gap


class DowntimeStatus(str, Enum):
    OPEN = "open"  # the rupee ticker is running right now
    CLOSED = "closed"


class Disposition(str, Enum):
    """§8.1 — a part is scrapped or reworked, never both."""

    SCRAP = "scrap"
    REWORK = "rework"
    USE_AS_IS = "use_as_is"


class CostedMixin:
    """The frozen snapshot written onto every costed row."""

    cost_total: Mapped[int] = mapped_column(default=0)  # paise
    cost_breakdown: Mapped[dict | None] = mapped_column(JSONB)
    # Resolved kernel inputs — the 'why' behind cost_total, kept forever.
    snapshot_machine_rate: Mapped[int] = mapped_column(default=0)
    snapshot_labour_rate: Mapped[int] = mapped_column(default=0)
    snapshot_overhead_rate: Mapped[int] = mapped_column(default=0)
    snapshot_cycle_time_min: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    snapshot_contribution_margin: Mapped[int] = mapped_column(default=0)
    snapshot_bottleneck_factor: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), default=Decimal("1.00")
    )
    snapshot_costing_mode: Mapped[str] = mapped_column(String(16), default="basic")
    formula_version: Mapped[int] = mapped_column(Integer, default=1)


class DowntimeEvent(Base, PlantScopedMixin, TimestampMixin, CostedMixin):
    """Screen 3. Two taps minimum: 'Stopped' + reason, then 'Running'.

    Event-driven, so the rupee ticker on a stopped machine is genuinely live —
    unlike production counts, which are hourly. §3: "owners do not need
    second-by-second data to make better decisions; they need timely-enough data
    that drives action today."
    """

    __tablename__ = "downtime_events"
    __table_args__ = (
        Index("ix_downtime_open", "plant_id", "status", "started_at"),
        Index("ix_downtime_station_time", "plant_id", "station_id", "started_at"),
        Index("ix_downtime_business_date", "plant_id", "business_date"),
        CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at", name="ck_downtime_time_order"
        ),
        Index(
            "uq_downtime_events_client_ref",
            "plant_id",
            "client_ref",
            unique=True,
            postgresql_where=text("client_ref IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    #: Made by the device that recorded it, so a retried offline sync is a no-op.
    client_ref: Mapped[str | None] = mapped_column(String(64))
    station_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("stations.id", ondelete="RESTRICT"), index=True
    )
    job_session_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("job_sessions.id", ondelete="SET NULL")
    )
    shift_session_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("shift_sessions.id", ondelete="SET NULL"),
        index=True,
    )
    business_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Nullable *only* while the event is open. The Shift Closure Lock makes it
    # impossible to go home with this column empty (§2).
    reason_code_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("reason_codes.id", ondelete="RESTRICT"), index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_minutes: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    status: Mapped[DowntimeStatus] = mapped_column(
        EnumString(DowntimeStatus, 12), default=DowntimeStatus.OPEN, nullable=False
    )

    note: Mapped[str | None] = mapped_column(Text)
    source: Mapped[DataSource] = mapped_column(
        EnumString(DataSource, 12), default=DataSource.MANUAL
    )
    source_ref: Mapped[str | None] = mapped_column(String(128))  # device id, later
    logged_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    escalation_level: Mapped[int] = mapped_column(Integer, default=0)

    # §1, Maintenance In-charge: "Logs what was done to fix breakdowns."
    # The reason code says WHAT stopped (§5.5); this says what was actually
    # done about it, by whom, at the machine, while the memory is fresh — and
    # it is the raw material Factory Memory answers 'what usually fixes this?'
    # from. Kept on the event rather than in a table of its own because one
    # breakdown is one event: a second row could only ever disagree with it.
    repair_note: Mapped[str | None] = mapped_column(Text)
    repair_part_changed: Mapped[str | None] = mapped_column(String(200))
    repaired_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_downtime_repaired_by"),
    )
    repaired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProductionCount(Base, PlantScopedMixin, TimestampMixin):
    """Screen 4. Count since last entry, hourly or per batch. 15 seconds.

    Deliberately *not* cumulative: a supervisor types what happened since they
    last looked, which is the number they actually know.
    """

    __tablename__ = "production_counts"
    __table_args__ = (
        Index("ix_production_station_time", "plant_id", "station_id", "recorded_at"),
        Index("ix_production_business_date", "plant_id", "business_date"),
        CheckConstraint("count >= 0", name="ck_production_count_nonneg"),
        Index(
            "uq_production_counts_client_ref",
            "plant_id",
            "client_ref",
            unique=True,
            postgresql_where=text("client_ref IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    client_ref: Mapped[str | None] = mapped_column(String(64))
    station_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("stations.id", ondelete="RESTRICT"), index=True
    )
    job_session_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("job_sessions.id", ondelete="SET NULL"), index=True
    )
    shift_session_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("shift_sessions.id", ondelete="SET NULL"),
        index=True,
    )
    business_date: Mapped[date] = mapped_column(Date, nullable=False)

    count: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    covers_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    source: Mapped[DataSource] = mapped_column(
        EnumString(DataSource, 12), default=DataSource.MANUAL
    )
    source_ref: Mapped[str | None] = mapped_column(String(128))
    logged_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class QualityRecord(Base, PlantScopedMixin, TimestampMixin, CostedMixin):
    """Screen 5. Entered by the inspector, independently of the supervisor.

    §2's second honesty mechanism: "Two people would have to collude to fake the
    numbers." The reconciliation check runs on save and alerts the owner when
    the two counts disagree beyond tolerance.
    """

    __tablename__ = "quality_records"
    __table_args__ = (
        Index("ix_quality_business_date", "plant_id", "business_date"),
        Index("ix_quality_part", "plant_id", "part_id", "recorded_at"),
        CheckConstraint("good_count >= 0 AND reject_count >= 0", name="ck_quality_nonneg"),
        CheckConstraint("rework_count >= 0", name="ck_quality_rework_nonneg"),
        Index(
            "uq_quality_records_client_ref",
            "plant_id",
            "client_ref",
            unique=True,
            postgresql_where=text("client_ref IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    client_ref: Mapped[str | None] = mapped_column(String(64))
    station_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("stations.id", ondelete="SET NULL"), index=True
    )
    job_session_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("job_sessions.id", ondelete="SET NULL")
    )
    shift_session_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("shift_sessions.id", ondelete="SET NULL"),
        index=True,
    )
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("lots.id", ondelete="SET NULL"), index=True
    )
    part_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("parts.id", ondelete="RESTRICT"), index=True
    )
    business_date: Mapped[date] = mapped_column(Date, nullable=False)

    good_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reject_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rework_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rework_minutes: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    disposition: Mapped[Disposition] = mapped_column(
        EnumString(Disposition, 12), default=Disposition.SCRAP
    )
    # [{defect_code_id, count}] — a batch can fail several ways at once.
    defects: Mapped[list | None] = mapped_column(JSONB)

    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[DataSource] = mapped_column(
        EnumString(DataSource, 12), default=DataSource.MANUAL
    )
    inspector_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    # Cross-verification outcome (§6, 2:30 PM).
    reconciliation_gap: Mapped[int | None] = mapped_column(Integer)
    reconciliation_flagged: Mapped[bool] = mapped_column(Boolean, default=False)


class StageCompletion(Base, PlantScopedMixin, TimestampMixin):
    """§14.2 — quantity through each operation, so the system always knows where
    every lot physically sits.

    §3's worked example: lot #4471, 500 pieces — 500 completed Op10, 430 through
    Op20 (70 in WIP buffer), 380 through Op30, 20 rejected at Op30. The owner
    sees exactly where the lot is stuck and what the rejections cost.
    """

    __tablename__ = "stage_completions"
    __table_args__ = (Index("ix_stage_lot", "plant_id", "lot_id", "completed_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    lot_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("lots.id", ondelete="CASCADE"), index=True
    )
    routing_operation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("routing_operations.id", ondelete="RESTRICT")
    )
    station_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("stations.id", ondelete="SET NULL")
    )
    business_date: Mapped[date] = mapped_column(Date, nullable=False)

    quantity_completed: Mapped[int] = mapped_column(Integer, default=0)
    quantity_rejected: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[DataSource] = mapped_column(
        EnumString(DataSource, 12), default=DataSource.MANUAL
    )


class DispatchRecord(Base, PlantScopedMixin, TimestampMixin):
    """§14.2 Op40 — deliberately the final operation.

    Confirming quantity despatched against quantity produced closes the loop and
    catches the losses between QC and the gate, which is where parts silently
    disappear in most plants today.
    """

    __tablename__ = "dispatch_records"

    id: Mapped[uuid.UUID] = uuid_pk()
    lot_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("lots.id", ondelete="CASCADE"), index=True
    )
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    vehicle_reference: Mapped[str | None] = mapped_column(String(128))
    despatched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[DataSource] = mapped_column(
        EnumString(DataSource, 12), default=DataSource.MANUAL
    )


class StagingStatus(str, Enum):
    PENDING = "pending"
    STAGED = "staged"
    SHORT = "short"


class MaterialStaging(Base, PlantScopedMixin, TimestampMixin):
    """§1, Stores In-charge: "Confirms material staging."

    §6's 5:45 AM briefing ends its second priority with "confirm with Ramesh in
    Stores that full quantity is at Line 2 before 8:00" — this is the row that
    confirmation writes, and the only place the plant can later answer whether
    the material was actually there before the shift started.

    No row is created for Stores to fill: the queue is today's job orders, and
    a row appears when someone taps. Confirming is one tap plus a number when
    it is short, which is why this never spends any of the 15-minute daily
    entry budget §4.3 protects.
    """

    __tablename__ = "material_stagings"
    __table_args__ = (
        # One standing answer per job order per day. Stores correcting itself
        # ("it was short, now it is staged") must update that answer, never add
        # a second one beside it.
        Index(
            "uq_material_staging_job_date",
            "plant_id",
            "job_order_id",
            "business_date",
            unique=True,
        ),
        Index("ix_material_staging_date", "plant_id", "business_date", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    job_order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("job_orders.id", ondelete="CASCADE"), index=True
    )
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[StagingStatus] = mapped_column(
        EnumString(StagingStatus, 12), default=StagingStatus.PENDING, nullable=False
    )
    #: What the job needs, frozen when Stores answered — so a later change to
    #: the job's plan never rewrites what Stores was asked for that morning.
    quantity_required: Mapped[int | None] = mapped_column(Integer)
    quantity_staged: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)
    confirmed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
