"""Layer 5 — Factory Plugins (§3 Layer 5, §9 parallel stream).

Three plugins, built in parallel with the core rather than deferred behind it:
Compliance/Audit AI, Kaizen AI and Skill Matrix AI.

One rule governs every table in this module (§3 Layer 5):

    "It must feed on existing Core data, not demand new daily data-entry
     burden. If a plugin needs the plant to do more daily work, it is designed
     wrong. The 15-minute daily entry budget is sacred and applies across the
     whole platform, plugins included."

So nothing here adds a daily capture screen. Compliance reads the downtime,
quality and Action history the plant already produces; Skill Matrix reads the
operator list and the 'No Operator' events already in Core; Kaizen is the one
with an input, and it is an *occasional* idea from an operator, not a shift task.

The second rule is that plugins are never separate products: they feed the same
Actions engine, the same PDCA lifecycle, and the same Recovered-Profit ledger.
There is no second definition of "done" and no second definition of "saved".
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
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, PlantScopedMixin, TimestampMixin, uuid_pk
from .types import EnumString

# ===========================================================================
# Compliance / Audit AI
# ===========================================================================


class Standard(str, Enum):
    """The standards an Indian auto-ancillary plant actually gets audited against."""

    ISO_9001 = "iso_9001"
    IATF_16949 = "iatf_16949"
    ZED = "zed"  # MSME-specific, Govt of India
    CUSTOMER = "customer"  # OEM / tier-1 specific formats


class ClauseStatus(str, Enum):
    READY = "ready"  # evidence exists and is current
    PARTIAL = "partial"  # some evidence, gaps remain
    GAP = "gap"  # nothing to show an auditor
    NOT_APPLICABLE = "not_applicable"


class ComplianceClause(Base, PlantScopedMixin, TimestampMixin):
    """One clause of one standard, and how this plant satisfies it.

    §3 Layer 5: *"A downtime log with corrective actions IS audit evidence — the
    plugin formats it against the right clause and tracks CAPA to closure
    through the same Actions engine."*

    Cross-standard mapping is why `evidence_source` matters: one captured fact
    (a costed downtime event with a closed Action behind it) satisfies a clause
    in ISO 9001, one in IATF 16949 and one in ZED simultaneously. The plant does
    the work once.
    """

    __tablename__ = "compliance_clauses"
    __table_args__ = (
        UniqueConstraint("plant_id", "standard", "clause_ref"),
        Index("ix_compliance_status", "plant_id", "standard", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    standard: Mapped[Standard] = mapped_column(EnumString(Standard, 20), nullable=False)
    clause_ref: Mapped[str] = mapped_column(String(32), nullable=False)  # "8.5.1"
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    requirement: Mapped[str | None] = mapped_column(Text)

    # Which Core data proves this clause. The scorer reads it to decide status,
    # so a clause is never marked ready by anyone's opinion.
    evidence_source: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[ClauseStatus] = mapped_column(
        EnumString(ClauseStatus, 20), default=ClauseStatus.GAP, nullable=False
    )
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    last_evidence_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gap_note: Mapped[str | None] = mapped_column(Text)

    # The same clause satisfied under other standards — cross-standard mapping.
    also_satisfies: Mapped[list[str] | None] = mapped_column(ARRAY(String(64)))
    display_order: Mapped[int] = mapped_column(Integer, default=100)


class AuditPack(Base, PlantScopedMixin, TimestampMixin):
    """A generated evidence pack, frozen at the moment it was produced.

    Frozen on purpose: an auditor asked for evidence on a date, and the plant
    must be able to show exactly what was handed over, not a view that has since
    moved on.
    """

    __tablename__ = "audit_packs"

    id: Mapped[uuid.UUID] = uuid_pk()
    standard: Mapped[Standard] = mapped_column(EnumString(Standard, 20), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    readiness_score: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    clause_summary: Mapped[list | None] = mapped_column(JSONB)
    evidence: Mapped[dict | None] = mapped_column(JSONB)
    generated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


# ===========================================================================
# Kaizen AI
# ===========================================================================


class KaizenStatus(str, Enum):
    SUBMITTED = "submitted"
    SCORED = "scored"
    ACCEPTED = "accepted"  # promoted to an Action
    REJECTED = "rejected"
    IMPLEMENTED = "implemented"


class KaizenIdea(Base, PlantScopedMixin, TimestampMixin):
    """An operator's improvement idea, converted into a costed proposal.

    §3 Layer 5: *"Operators submit improvement ideas (eventually by voice, in
    Tamil); the system converts each into a structured cost-saving proposal,
    scores it, routes it into the Actions engine for implementation, and tracks
    the realised saving through the same Recovered-Profit accounting the core
    product already uses."*

    The scoring is deliberately transparent — an operator whose idea is declined
    can see why, and one whose idea is accepted can later see what it saved with
    their name on it. That is most of what makes a suggestion scheme survive
    past month two.

    Voice capture in Tamil is explicitly deferred (§9 NOT-building list) until
    the typed flows are proven.
    """

    __tablename__ = "kaizen_ideas"
    __table_args__ = (Index("ix_kaizen_status", "plant_id", "status"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    reference: Mapped[str] = mapped_column(String(32), nullable=False)  # "KZN-0042"
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    submitted_by_name: Mapped[str] = mapped_column(String(200), nullable=False)
    submitted_by_operator_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operators.id", ondelete="SET NULL")
    )
    station_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("stations.id", ondelete="SET NULL"), index=True
    )
    reason_code_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("reason_codes.id", ondelete="SET NULL")
    )

    status: Mapped[KaizenStatus] = mapped_column(
        EnumString(KaizenStatus, 20), default=KaizenStatus.SUBMITTED, nullable=False
    )

    # Scored against the plant's own loss history, never against a generic
    # rubric: an idea is worth what the losses it addresses actually cost here.
    estimated_annual_saving: Mapped[int] = mapped_column(default=0)  # paise
    effort_score: Mapped[int] = mapped_column(Integer, default=0)  # 1-5
    impact_score: Mapped[int] = mapped_column(Integer, default=0)  # 1-5
    score_rationale: Mapped[str | None] = mapped_column(Text)
    linked_loss_paise: Mapped[int] = mapped_column(default=0)

    # Promotion into the core Actions engine — never a parallel workflow.
    action_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("actions.id", ondelete="SET NULL"), index=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)


# ===========================================================================
# Skill Matrix AI
# ===========================================================================


class SkillLevel(int, Enum):
    """The four-level convention most Indian auto plants already use on the wall."""

    NONE = 0
    TRAINING = 1  # can operate under supervision
    COMPETENT = 2  # can operate alone
    EXPERT = 3  # can train others


class OperatorSkill(Base, PlantScopedMixin, TimestampMixin):
    """Who is trained on what.

    §3 Layer 5: *"Tracks which operator is trained on which machine/operation,
    and connects that matrix directly to downtime data — 'No Operator' events
    point at exactly where a skill gap is costing money, and the plugin ranks
    training needs by the rupee loss they would remove. It reuses the operator
    list and event stream already in Core; no new registers."*
    """

    __tablename__ = "operator_skills"
    __table_args__ = (UniqueConstraint("plant_id", "operator_id", "station_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    operator_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operators.id", ondelete="CASCADE"), index=True
    )
    station_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE"), index=True
    )
    level: Mapped[int] = mapped_column(Integer, default=0)
    certified_on: Mapped[date | None] = mapped_column(Date)
    expires_on: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)


class TrainingNeed(Base, PlantScopedMixin, TimestampMixin):
    """A skill gap, ranked by the rupee loss it would remove.

    Generated, not entered. The plugin derives these from 'No Operator' downtime
    and from stations whose coverage is too thin to survive one absence — both
    of which are already in Core data.
    """

    __tablename__ = "training_needs"
    __table_args__ = (Index("ix_training_value", "plant_id", "annual_loss_paise"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    station_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("stations.id", ondelete="CASCADE"), index=True
    )
    # Null when the gap is coverage-wide rather than one person's.
    operator_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operators.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # coverage | gap
    annual_loss_paise: Mapped[int] = mapped_column(default=0)
    evidence: Mapped[dict | None] = mapped_column(JSONB)
    recommendation: Mapped[str | None] = mapped_column(Text)
    action_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("actions.id", ondelete="SET NULL")
    )
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
