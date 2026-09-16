"""Operational tables: escalations, Coach briefings, Copilot log, onboarding."""

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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, PlantScopedMixin, TimestampMixin, uuid_pk
from .types import EnumString


class EscalationChannel(str, Enum):
    PUSH = "push"
    WHATSAPP = "whatsapp"
    SMS = "sms"  # §16.4 fallback while WATI approval is pending
    IN_APP = "in_app"


class EscalationStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"  # the event closed before this rung came due


class Escalation(Base, PlantScopedMixin, TimestampMixin):
    """The 5/15/30-minute ladder (§3 Layer 2).

    "Every escalation is timestamped. Nobody can later say they didn't know."
    That sentence is the reason this is a table and not a log line: the record
    has to survive to the next morning meeting, and to the month after that.
    """

    __tablename__ = "escalations"
    __table_args__ = (
        Index("ix_escalations_due", "plant_id", "status", "due_at"),
        Index("ix_escalations_event", "downtime_event_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    downtime_event_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("downtime_events.id", ondelete="CASCADE")
    )
    level: Mapped[int] = mapped_column(Integer, nullable=False)  # 1 | 2 | 3
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    channel: Mapped[EscalationChannel] = mapped_column(
        EnumString(EscalationChannel, 12), default=EscalationChannel.IN_APP
    )
    status: Mapped[EscalationStatus] = mapped_column(
        EnumString(EscalationStatus, 12), default=EscalationStatus.PENDING, nullable=False
    )
    # Who the rung was aimed at, as a role — not just as a name. §3 Layer 2:
    # "the system picks the recipient from the reason code". Storing only the
    # person means the maintenance in-charge's inbox cannot tell a breakdown
    # addressed to him from a material wait addressed to Stores, and the
    # routing that the reason code performed is lost the moment staff change.
    route: Mapped[str | None] = mapped_column(String(20), index=True)
    recipient_name: Mapped[str | None] = mapped_column(String(200))
    recipient_number: Mapped[str | None] = mapped_column(String(20))
    message: Mapped[str | None] = mapped_column(Text)
    # The running cost at the moment the rung fired — what made it urgent.
    cost_at_send: Mapped[int] = mapped_column(default=0)
    provider_response: Mapped[dict | None] = mapped_column(JSONB)
    # Someone said "on it" — the ladder has been heard.
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_escalations_acknowledged_by"),
    )
    # The machine restarted — there is nothing left to escalate.
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CoachBriefing(Base, PlantScopedMixin, TimestampMixin):
    """§3 Layer 2 / §14.6 — the 5:45 AM briefing. Exactly three priorities.

    Each names a machine, cites the plant's own history, gives a concrete
    action, a person, a deadline and a rupee value. "It never gives generic
    advice; if it has nothing specific to say, it says less."
    """

    __tablename__ = "coach_briefings"
    __table_args__ = (Index("ix_coach_date", "plant_id", "business_date"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    shift_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("shifts.id", ondelete="SET NULL")
    )
    recipient_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    # The tier the Coach was entitled to speak at, and the evidence behind it.
    # Stored so a briefing can always be audited against what it could know.
    tier: Mapped[int] = mapped_column(Integer, default=0)
    data_basis: Mapped[dict | None] = mapped_column(JSONB)

    # [{priority, station, headline, evidence, action, assignee, deadline, value_paise}]
    priorities: Mapped[list | None] = mapped_column(JSONB)
    # The facts the kernel computed. The LLM only phrased them (C5).
    fact_bundle: Mapped[dict | None] = mapped_column(JSONB)
    rendered_text: Mapped[str | None] = mapped_column(Text)
    generated_by: Mapped[str] = mapped_column(String(16), default="rules")  # rules|llm
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CopilotQuery(Base, PlantScopedMixin, TimestampMixin):
    """Layer 3 — every question asked and the facts used to answer it.

    Logged in full because the failure mode that matters is a confidently wrong
    answer: when one surfaces, we need to see exactly which facts produced it.
    """

    __tablename__ = "copilot_queries"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    channel: Mapped[str] = mapped_column(String(16), default="web")  # web | whatsapp
    question: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(8), default="en")

    intent: Mapped[str | None] = mapped_column(String(64))
    # 'factual' ships from day one; 'pattern' unlocks with history (§3 Layer 3).
    tier_required: Mapped[str] = mapped_column(String(16), default="factual")
    tier_available: Mapped[bool] = mapped_column(Boolean, default=True)

    fact_bundle: Mapped[dict | None] = mapped_column(JSONB)
    answer: Mapped[str | None] = mapped_column(Text)
    answered_by: Mapped[str] = mapped_column(String(16), default="deterministic")
    latency_ms: Mapped[int | None] = mapped_column(Integer)


class ReadinessAssessment(Base, TimestampMixin):
    """§4.1 Step 0 — the Digital Readiness Questionnaire.

    Answered on the owner's phone before we ever visit. Cheap to build,
    disproportionate sales value: it qualifies the plant, tailors the
    onboarding, and is itself a powerful sales artefact.

    Deliberately NOT plant-scoped: the whole point is that it is filled in
    before the plant exists in the system. It is linked to one once the plant
    signs up.
    """

    __tablename__ = "readiness_assessments"

    id: Mapped[uuid.UUID] = uuid_pk()
    plant_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("plants.id", ondelete="SET NULL"), index=True
    )
    plant_name: Mapped[str | None] = mapped_column(String(200))
    respondent_name: Mapped[str | None] = mapped_column(String(200))
    respondent_phone: Mapped[str | None] = mapped_column(String(20))

    answers: Mapped[dict | None] = mapped_column(JSONB)
    score: Mapped[int] = mapped_column(Integer, default=0)  # out of 100
    band: Mapped[str | None] = mapped_column(String(32))
    priority_gaps: Mapped[list | None] = mapped_column(JSONB)
    estimated_onboarding_hours: Mapped[int | None] = mapped_column(Integer)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TemplateUpload(Base, PlantScopedMixin, TimestampMixin):
    """§4.1 Step 1 — "never type what can be uploaded".

    A 30-60 minute verification replaces days of typing. The extraction result
    is kept alongside the file so the confirm screen ('Found: 132 part numbers,
    19 machines, 41 operators, 3 shifts. Confirm?') can be re-shown and audited.
    """

    __tablename__ = "template_uploads"

    id: Mapped[uuid.UUID] = uuid_pk()
    template: Mapped[str] = mapped_column(String(8), nullable=False)  # T1..T7
    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    storage_url: Mapped[str | None] = mapped_column(String(500))
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    status: Mapped[str] = mapped_column(String(16), default="parsed")
    rows_found: Mapped[int] = mapped_column(Integer, default=0)
    rows_imported: Mapped[int] = mapped_column(Integer, default=0)
    preview: Mapped[list | None] = mapped_column(JSONB)
    errors: Mapped[list | None] = mapped_column(JSONB)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
