"""The Actions & PDCA engine (§5), Factory Memory (Layer 4) and the profit ledger.

    "Everything before this section makes problems visible. This section makes
     them SOLVED — and proves it in rupees. Without it, the product is an
     expensive mirror."

An Action is never free text in a meeting minute; it is a living object the
system tracks until closed and verified.
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
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, PlantScopedMixin, TimestampMixin, uuid_pk
from .types import EnumString


class PdcaState(str, Enum):
    """§5.4 — the Action lifecycle IS the PDCA cycle, so nothing feels foreign."""

    OPEN = "open"
    ROOT_CAUSE = "root_cause"
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    MONITORING = "monitoring"
    VERIFIED = "verified"
    REOPENED = "reopened"
    CANCELLED = "cancelled"


class ActionTrigger(str, Enum):
    BIG_SINGLE_EVENT = "big_single_event"
    RECURRING_PATTERN = "recurring_pattern"
    MANUAL = "manual"
    COMPLIANCE_CAPA = "compliance_capa"  # Layer 5 plugins feed the same engine
    KAIZEN = "kaizen"
    SKILL_GAP = "skill_gap"


class Action(Base, PlantScopedMixin, TimestampMixin):
    """One tracked commitment to fix something, born from an event."""

    __tablename__ = "actions"
    __table_args__ = (
        Index("ix_actions_state", "plant_id", "state"),
        Index("ix_actions_due", "plant_id", "due_date"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    reference: Mapped[str] = mapped_column(String(32), nullable=False)  # "ACT-0042"
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    trigger: Mapped[ActionTrigger] = mapped_column(
        EnumString(ActionTrigger, 24), nullable=False
    )
    trigger_rationale: Mapped[str | None] = mapped_column(Text)
    state: Mapped[PdcaState] = mapped_column(
        EnumString(PdcaState, 16), default=PdcaState.OPEN, nullable=False
    )

    station_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("stations.id", ondelete="SET NULL"), index=True
    )
    reason_code_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("reason_codes.id", ondelete="SET NULL"), index=True
    )

    # Money is attached from birth — an Action without a ₹ figure cannot be
    # ranked, and ranking is the whole point.
    linked_cost: Mapped[int] = mapped_column(default=0)  # paise
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)

    # §5.3 — did this issue earn a place in the RCA queue?
    routed_to_rca: Mapped[bool] = mapped_column(Boolean, default=False)
    severity_reasons: Mapped[list | None] = mapped_column(JSONB)

    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    owner_name: Mapped[str | None] = mapped_column(String(200))  # named person, always
    due_date: Mapped[date | None] = mapped_column(Date)

    root_cause: Mapped[str | None] = mapped_column(Text)
    countermeasure: Mapped[str | None] = mapped_column(Text)
    evidence_photo_url: Mapped[str | None] = mapped_column(String(500))

    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopen_count: Mapped[int] = mapped_column(Integer, default=0)

    # §5.6 — frozen at creation, never recomputed. If the plant's mix changes,
    # the baseline this Action is measured against does not move.
    baseline_weekly_loss: Mapped[int] = mapped_column(default=0)
    baseline_frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recovered_profit: Mapped[int] = mapped_column(default=0)
    #: Credit earned before the Action last reopened. A reopen stops accrual;
    #: it does not take back weeks the fix genuinely held (§8.1).
    recovered_profit_prior: Mapped[int] = mapped_column(default=0, server_default="0")
    #: What the loss is measured in: downtime minutes on a reason, or scrap on a part.
    loss_source: Mapped[str] = mapped_column(
        String(12), default="downtime", server_default="downtime"
    )
    part_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("parts.id", ondelete="SET NULL", name="fk_actions_part")
    )
    sustained_weeks: Mapped[int] = mapped_column(Integer, default=0)

    # §6, 3:27 PM — an Action sits in the morning meeting until it is closed.
    surfaced_in_meeting_on: Mapped[date | None] = mapped_column(Date)
    meeting_status: Mapped[str | None] = mapped_column(String(16))  # done|delayed
    #: The plant day the status was given — the gate asks again each morning.
    meeting_status_on: Mapped[date | None] = mapped_column(Date)
    delay_reason: Mapped[str | None] = mapped_column(Text)

    events: Mapped[list[ActionEvent]] = relationship(
        back_populates="action", cascade="all, delete-orphan"
    )
    rca: Mapped[RootCauseAnalysis | None] = relationship(
        back_populates="action", uselist=False, cascade="all, delete-orphan"
    )


class ActionEvent(Base, PlantScopedMixin):
    """Which downtime events this Action covers — the evidence behind the ₹."""

    __tablename__ = "action_events"
    __table_args__ = (UniqueConstraint("action_id", "downtime_event_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    action_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("actions.id", ondelete="CASCADE"), index=True
    )
    downtime_event_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("downtime_events.id", ondelete="CASCADE")
    )
    action: Mapped[Action] = relationship(back_populates="events")


class RootCauseAnalysis(Base, PlantScopedMixin, TimestampMixin):
    """§5.5 — reason codes are not root causes.

    'Machine Breakdown' is logged in 20 seconds during the shift and says WHAT
    happened. 'Bearing seized — wrong grease grade used at last PM' says WHY,
    and is established later, at Action level, never on the shopfloor mid-shift.
    """

    __tablename__ = "root_cause_analyses"

    id: Mapped[uuid.UUID] = uuid_pk()
    action_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("actions.id", ondelete="CASCADE"), unique=True
    )
    assigned_to_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    assigned_team: Mapped[str | None] = mapped_column(String(64))
    due_date: Mapped[date | None] = mapped_column(Date)

    # Pre-filled with the event history — dates, frequency, cost so far — so
    # the team starts from evidence rather than a blank form.
    five_whys: Mapped[list | None] = mapped_column(JSONB)
    prefilled_context: Mapped[dict | None] = mapped_column(JSONB)

    # §5.5 AI assist: a suggestion to accept or reject, never an automatic
    # verdict. The plant's own past, not a model's opinion.
    ai_suggested_causes: Mapped[list | None] = mapped_column(JSONB)
    ai_suggestion_accepted: Mapped[bool | None] = mapped_column(Boolean)

    conclusion: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    action: Mapped[Action] = relationship(back_populates="rca")


class MemoryEntry(Base, PlantScopedMixin, TimestampMixin):
    """Factory Memory (Layer 4) — the four closure questions, kept forever.

    Sixty seconds per significant fix. Fifty closures later the plant owns a
    private repair playbook that survives any resignation — and two years of a
    plant's own root causes cannot be copied at any price.
    """

    __tablename__ = "memory_entries"
    __table_args__ = (Index("ix_memory_search", "plant_id", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    action_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("actions.id", ondelete="SET NULL"), index=True
    )
    station_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("stations.id", ondelete="SET NULL"), index=True
    )
    part_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("parts.id", ondelete="SET NULL")
    )
    reason_code_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("reason_codes.id", ondelete="SET NULL")
    )

    # The standard capture set (§3 Layer 4, §5.3) — exactly four questions.
    what_fixed_it: Mapped[str] = mapped_column(Text, nullable=False)
    what_was_replaced: Mapped[str | None] = mapped_column(Text)
    photo_url: Mapped[str | None] = mapped_column(String(500))
    check_first_next_time: Mapped[str | None] = mapped_column(Text)

    problem_summary: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String(64)))
    fixed_by_name: Mapped[str | None] = mapped_column(String(200))
    occurred_on: Mapped[date | None] = mapped_column(Date)

    # Populated by the AI worker for semantic recall (pgvector). Postgres FTS
    # carries retrieval until the plant has enough history to need embeddings.
    embedding_status: Mapped[str] = mapped_column(String(16), default="pending")


class RecoveredProfitEntry(Base, PlantScopedMixin, TimestampMixin):
    """One week of measured savings for one verified Action.

    Weekly rows, not a running total: the owner can see which weeks held and
    which slipped, which is what makes the number defensible in a renewal
    conversation.
    """

    __tablename__ = "recovered_profit_entries"
    __table_args__ = (UniqueConstraint("action_id", "week_start"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    action_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("actions.id", ondelete="CASCADE"), index=True
    )
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    baseline_weekly_loss: Mapped[int] = mapped_column(default=0)
    actual_weekly_loss: Mapped[int] = mapped_column(default=0)
    saving: Mapped[int] = mapped_column(default=0)  # max(0, baseline - actual)
    counted: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text)
