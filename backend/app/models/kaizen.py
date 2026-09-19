"""Kaizen AI — Layer 5, Plugin Edition (Plugin Spec v2).

    "Operators submit improvement ideas from a phone. The plugin converts each
     into a structured, costed proposal using Core's own cost engine, ranks it
     against everything else in the plant, routes it into Core's Actions engine
     for implementation, and tracks the realised saving through Core's Recovered
     Profit ledger. Nothing is a separate product; nothing is a separate login;
     nothing is a second source of truth."  — §1

THE CODE-REVIEW TEST (§3) governs every table here:

    "If any Kaizen query reads a cost rate, a machine attribute, a bottleneck
     flag or an employee record from a Kaizen-owned table, the plugin is already
     broken."

So there is no machine list, no part list, no employee list, no cost rate and no
bottleneck flag in this module. Every one of those is a foreign key into Core's
masters. What Kaizen *owns* is the submission and its lifecycle, the voice and
photo capture, the AI interview, the challenge, the hard/soft classification
layer built on Core's numbers, the approval templates, and the points.

Two columns deserve their own note because they encode hard product rules:

* ``vision_findings`` holds only findings the operator **accepted**. §Step 2's
  Rule 1 is that a rejected finding "is deleted and never reaches a supervisor".
  That is a technical rule, not a policy note — the moment operators believe the
  camera reports them to management, they stop taking photos, and Step 2 dies.
* ``credited_operator_id`` is separate from ``submitted_by_user_id``. A
  supervisor submitting on behalf of an operator is normal in month one; the
  points and the recognition go to the operator. Get this wrong once and
  participation on that line never recovers.
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
    Numeric,
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
# Enumerations
# ===========================================================================


class KaizenTrack(str, Enum):
    """Two buttons on the home screen, not one (Standalone "TWO TRACKS").

    At ~10 submissions per employee per month, most of what an operator notices
    is small — a loose guard, an unlabelled bin. Running the full AI interview,
    cost engine, challenge and impact card on a ₹200 observation is wasted
    processing and, worse, buries the four ideas a month worth ₹4 lakh.
    """

    QUICK = "quick"  # 30 seconds — photo + voice, no interview, no cost engine
    FULL = "full"  # 3 minutes — the whole journey, Steps 1-9


class KaizenStatus(str, Enum):
    """The operator only ever sees the simple progression (§8.3).

    Submitted → Approved → In progress → Done → Verified. The states below that
    map onto Core Action states are driven *by* the Action, never duplicated.
    """

    DRAFT = "draft"  # journey started, not yet submitted
    SUBMITTED = "submitted"
    SCORED = "scored"  # costed and ranked, waiting for a decision
    APPROVED = "approved"  # approved but below Core's Action threshold
    ACCEPTED = "accepted"  # approved and promoted to a Core Action
    REJECTED = "rejected"
    IMPLEMENTED = "implemented"
    VERIFIED = "verified"  # Core's Recovered Profit sustained (§9)
    DUPLICATE = "duplicate"  # an open Action or earlier Kaizen already covers it


#: Statuses that still need somebody's decision.
OPEN_STATUSES = (KaizenStatus.SUBMITTED, KaizenStatus.SCORED)
#: Statuses where the idea is alive and on its way.
LIVE_STATUSES = (
    KaizenStatus.APPROVED,
    KaizenStatus.ACCEPTED,
    KaizenStatus.IMPLEMENTED,
    KaizenStatus.VERIFIED,
)


class KaizenCategory(str, Enum):
    """Step 3's list, exactly. AI classifies first; the operator confirms.

    *"Asking a shop-floor operator to pick the right waste category before
    explaining the problem is asking them to do abstract classification. That's
    exactly the form-filling this product exists to kill."*
    """

    MOTION = "motion"
    WAITING = "waiting"
    TRANSPORT = "transport"
    INVENTORY = "inventory"
    QUALITY = "quality"
    SAFETY = "safety"
    ENERGY = "energy"
    PRODUCTIVITY = "productivity"
    COST = "cost"
    FIVE_S = "five_s"
    MAINTENANCE = "maintenance"
    OTHER = "other"


CATEGORY_LABELS: dict[KaizenCategory, str] = {
    KaizenCategory.MOTION: "Motion waste",
    KaizenCategory.WAITING: "Waiting",
    KaizenCategory.TRANSPORT: "Transport",
    KaizenCategory.INVENTORY: "Inventory",
    KaizenCategory.QUALITY: "Quality",
    KaizenCategory.SAFETY: "Safety",
    KaizenCategory.ENERGY: "Energy",
    KaizenCategory.PRODUCTIVITY: "Productivity",
    KaizenCategory.COST: "Cost",
    KaizenCategory.FIVE_S: "5S / housekeeping",
    KaizenCategory.MAINTENANCE: "Maintenance",
    KaizenCategory.OTHER: "Other",
}

#: The same labels keyed by the stored string, for the API layer.
CATEGORY_LABELS_BY_VALUE: dict[str, str] = {
    category.value: label for category, label in CATEGORY_LABELS.items()
}


class VisionCategory(str, Enum):
    """Step 2's Rule 3 — only five categories of finding are ever shown.

    *"Not 'everything AI can see.' A list of 15 observations makes the operator
    scroll past all of them."*
    """

    HOUSEKEEPING = "housekeeping"  # 5S
    SAFETY_HAZARD = "safety_hazard"
    LEAK_OR_SPILL = "leak_or_spill"
    MISSING_LABEL = "missing_label"
    OBSTRUCTION = "obstruction"


class ApprovalTemplate(str, Enum):
    """§10 — three presets and an editable step list, not a workflow builder.

    *"'Fully configurable' sounds good in a sales pitch and becomes a six-month
    engineering project with a drag-and-drop rules engine nobody uses correctly.
    Ship 3 presets, an editable step list, and threshold rules. That covers ~95%
    of real plants."*
    """

    SIMPLE = "simple"  # Operator → Supervisor → Closed
    STANDARD = "standard"  # Operator → Supervisor → Plant Head → Maintenance → Closed
    OPEN = "open"  # Operator → Auto-approved → Implemented


#: The step lists behind each template. Editable per plant; these are the seeds.
TEMPLATE_STEPS: dict[ApprovalTemplate, tuple[str, ...]] = {
    ApprovalTemplate.SIMPLE: ("supervisor",),
    ApprovalTemplate.STANDARD: ("supervisor", "owner", "maintenance"),
    ApprovalTemplate.OPEN: (),
}


# ===========================================================================
# The submission
# ===========================================================================


class KaizenIdea(Base, PlantScopedMixin, TimestampMixin):
    """One improvement, from the operator's 30 seconds to the verified rupee.

    Most columns are written by the AI, not by a person. The operator's total
    input is a voice note, a photo, one category confirmation and four to six
    tapped answers — the three-minute rule that every field here had to survive.
    """

    __tablename__ = "kaizen_ideas"
    __table_args__ = (
        UniqueConstraint("plant_id", "reference"),
        Index("ix_kaizen_status", "plant_id", "status"),
        Index("ix_kaizen_priority", "plant_id", "status", "priority_score"),
        Index("ix_kaizen_month", "plant_id", "submitted_on"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    reference: Mapped[str] = mapped_column(String(32), nullable=False)  # "KZN-0042"
    track: Mapped[KaizenTrack] = mapped_column(
        EnumString(KaizenTrack, 8), default=KaizenTrack.FULL, nullable=False
    )

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    #: Step 1 — what the operator actually said, before AI cleaned it up. Kept
    #: verbatim so a disputed report can always be traced to the original words.
    voice_transcript: Mapped[str | None] = mapped_column(Text)
    voice_language: Mapped[str | None] = mapped_column(String(8))  # "ta", "en"

    # --- who ---------------------------------------------------------------
    submitted_by_name: Mapped[str] = mapped_column(String(200), nullable=False)
    submitted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    #: Submit-on-behalf: points and recognition go HERE, never to the submitter.
    credited_operator_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operators.id", ondelete="SET NULL"), index=True
    )
    credited_name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Which leaderboard this person competes on — shopfloor or support/staff.
    board: Mapped[str] = mapped_column(String(16), default="shopfloor", nullable=False)
    department: Mapped[str | None] = mapped_column(String(64))
    submitted_on: Mapped[date | None] = mapped_column(Date, index=True)

    # --- Core context, inherited not asked (§5.1) ---------------------------
    station_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("stations.id", ondelete="SET NULL"), index=True
    )
    job_order_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("job_orders.id", ondelete="SET NULL")
    )
    part_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("parts.id", ondelete="SET NULL"), index=True
    )
    reason_code_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("reason_codes.id", ondelete="SET NULL"), index=True
    )
    defect_code_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("defect_codes.id", ondelete="SET NULL")
    )
    #: Inherited values are shown for one-tap confirmation, never silently
    #: assumed — an operator may be standing at a different machine from the one
    #: they are logged into.
    context_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    #: §4.2 — a Kaizen on a station Core does not know about is accepted and
    #: flagged for the Lean engineer. Kaizen never creates master data.
    unknown_station_note: Mapped[str | None] = mapped_column(String(300))

    # --- what the AI worked out --------------------------------------------
    category: Mapped[KaizenCategory | None] = mapped_column(EnumString(KaizenCategory, 20))
    saving_type: Mapped[str | None] = mapped_column(String(24))  # kernel SavingType
    #: Step 2 — ONLY findings the operator accepted. Rejected ones are deleted.
    vision_findings: Mapped[list | None] = mapped_column(JSONB)
    vision_ran: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Step 4 — [{key, question, answer, unit, source}] where source is
    #: "operator" or "core" (inherited, so never asked).
    interview: Mapped[list | None] = mapped_column(JSONB)
    #: Step 5A — the Core History Check snapshot, frozen at submission so the
    #: evidence an approver saw can be reproduced months later.
    history: Mapped[dict | None] = mapped_column(JSONB)
    #: Step 5 — similar past Kaizens found in the plant's own memory.
    similar: Mapped[list | None] = mapped_column(JSONB)
    #: Step 7 — one alternative, never two. {alternative, investment, rationale,
    #: chosen, dismissed_reason}
    challenge: Mapped[dict | None] = mapped_column(JSONB)
    #: Step 9 — {probability, expected_days, obstacles: [...]}
    forecast: Mapped[dict | None] = mapped_column(JSONB)
    #: Semantic fingerprint for pattern detection (§10). Tokens always; the
    #: embedding only when a local model produced one.
    fingerprint: Mapped[list[str] | None] = mapped_column(ARRAY(String(48)))
    embedding: Mapped[list[float] | None] = mapped_column(ARRAY(Numeric(12, 8)))

    # --- the money, classified (§7.2) --------------------------------------
    #: What Core's formula module returned, before classification. Kept so the
    #: §7.1 invariant can be asserted against Core's own screens.
    core_value_paise: Mapped[int] = mapped_column(default=0)
    hard_saving_paise: Mapped[int] = mapped_column(default=0)
    soft_saving_paise: Mapped[int] = mapped_column(default=0)
    saving_basis: Mapped[str | None] = mapped_column(Text)
    #: {safety, quality, delivery, morale, compliance} — 0-5 each.
    intangible_stars: Mapped[dict | None] = mapped_column(JSONB)
    #: The operator's own guess at what the fix costs. Marked as an estimate
    #: until Maintenance or Purchase replaces it at approval.
    investment_paise: Mapped[int] = mapped_column(default=0)
    investment_is_estimate: Mapped[bool] = mapped_column(Boolean, default=True)
    needs_shutdown: Mapped[bool] = mapped_column(Boolean, default=False)
    #: 1 (very hard) to 5 (very easy).
    effort_score: Mapped[int] = mapped_column(Integer, default=3)
    impact_score: Mapped[int] = mapped_column(Integer, default=1)  # 1-5, for display
    #: The hidden 0-100 rank. Never the headline; powers the queue and Quick Wins.
    priority_score: Mapped[int] = mapped_column(Integer, default=0)
    priority_verdict: Mapped[str | None] = mapped_column(String(8))  # HIGH|MEDIUM|LOW
    score_components: Mapped[dict | None] = mapped_column(JSONB)
    score_rationale: Mapped[str | None] = mapped_column(Text)
    #: Core-verified rupees this problem has already cost (§5A). Not a saving.
    linked_loss_paise: Mapped[int] = mapped_column(default=0)
    #: §7.3 — calculated on a Core default, so excluded from verified totals
    #: and from the owner's digest until the rate is confirmed.
    provisional: Mapped[bool] = mapped_column(Boolean, default=False)
    provisional_note: Mapped[str | None] = mapped_column(Text)
    safety_flagged: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- lifecycle ---------------------------------------------------------
    status: Mapped[KaizenStatus] = mapped_column(
        EnumString(KaizenStatus, 20), default=KaizenStatus.SUBMITTED, nullable=False
    )
    #: Which step of the plant's approval template it is waiting on.
    approval_step: Mapped[int] = mapped_column(Integer, default=0)
    approval_route: Mapped[list | None] = mapped_column(JSONB)  # the steps, resolved
    approval_trail: Mapped[list | None] = mapped_column(JSONB)  # who decided what, when
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)

    #: §4.3 — a Quick Observation never creates an Action; a Full Kaizen does
    #: only above Core's existing threshold, or when safety-flagged, or when it
    #: is part of a detected pattern.
    action_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("actions.id", ondelete="SET NULL"), index=True
    )
    #: §6.4 — an open Action already covers this problem.
    duplicate_action_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("actions.id", ondelete="SET NULL")
    )
    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("kaizen_ideas.id", ondelete="SET NULL")
    )
    #: A closed Action covered this and the problem has come back. A verified
    #: saving that quietly stops holding is the most dangerous number in the
    #: platform, and an operator noticing it is the fastest detection there is.
    is_recurrence: Mapped[bool] = mapped_column(Boolean, default=False)
    #: A Quick Observation that AI promoted to a Full Kaizen.
    promoted_from_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("kaizen_ideas.id", ondelete="SET NULL")
    )

    implemented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: §9 — written only when Core's Recovered Profit ledger says the fix held.
    #: The rupee is counted once, in Core; this is the attributed copy for the
    #: operator's credit, never added to the plant total a second time.
    verified_saving_paise: Mapped[int] = mapped_column(default=0)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    points_awarded: Mapped[int] = mapped_column(Integer, default=0)
    #: Cross-plugin signals (§10.3): "skill_gap", "compliance", "maintenance".
    signals: Mapped[list[str] | None] = mapped_column(ARRAY(String(32)))


# ===========================================================================
# Configuration — K4 (approval) and K5 (categories). About ten minutes of setup.
# ===========================================================================


class KaizenSettings(Base, PlantScopedMixin, TimestampMixin):
    """The plugin's whole configuration surface, one row per plant.

    §2.1 removed the standalone Cost Master (Core owns every rate), the manual
    bottleneck list (Core owns BF) and onboarding templates K1-K3 (Core's T1-T7
    carry people, stations and cost data). What is left is K4 and K5.
    """

    __tablename__ = "kaizen_settings"
    __table_args__ = (UniqueConstraint("plant_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()

    # --- K4: approval ------------------------------------------------------
    template: Mapped[ApprovalTemplate] = mapped_column(
        EnumString(ApprovalTemplate, 12), default=ApprovalTemplate.SIMPLE, nullable=False
    )
    #: The editable step list. Roles, in order, e.g. ["supervisor", "owner"].
    steps: Mapped[list | None] = mapped_column(JSONB)
    #: Threshold rules the plant sets itself (§10).
    auto_approve_below_paise: Mapped[int] = mapped_column(default=500_000)  # ₹5,000
    auto_approve_min_score: Mapped[int] = mapped_column(Integer, default=80)
    safety_always_to_owner: Mapped[bool] = mapped_column(Boolean, default=True)
    finance_above_paise: Mapped[int] = mapped_column(default=2_500_000)  # ₹25,000

    # --- §6.1: the History Check ------------------------------------------
    history_window_days: Mapped[int] = mapped_column(Integer, default=180)

    # --- §11: scoring ------------------------------------------------------
    #: Weights live in config, never in code — tune after 20+ real Kaizens.
    score_weights: Mapped[dict | None] = mapped_column(JSONB)

    # --- §10: pattern detection -------------------------------------------
    pattern_min_items: Mapped[int] = mapped_column(Integer, default=5)
    pattern_min_value_paise: Mapped[int] = mapped_column(default=5_000_000)  # ₹50,000
    #: Quick Observations carry no cost data but often carry the clearest
    #: description of a recurring nuisance — so: yes for clustering, no for
    #: value thresholds (§13.4).
    patterns_include_quick: Mapped[bool] = mapped_column(Boolean, default=True)

    # --- Two tracks and the monthly floor ---------------------------------
    #: A Quick Observation above this estimated value gets offered promotion.
    promote_above_paise: Mapped[int] = mapped_column(default=2_000_000)  # ₹20,000
    #: "Minimum one submission per employee per month". Deliberately low and
    #: easy — one 30-second observation clears it. Do not raise the default.
    monthly_minimum: Mapped[int] = mapped_column(Integer, default=1)
    #: Quick Wins filter: investment ceiling for "low cost".
    quick_win_investment_paise: Mapped[int] = mapped_column(default=1_000_000)  # ₹10,000
    #: K5 — which categories this plant shows.
    enabled_categories: Mapped[list[str] | None] = mapped_column(ARRAY(String(20)))


class KaizenCategoryCode(Base, PlantScopedMixin, TimestampMixin):
    """§6.1's one piece of configuration: category ↔ Core reason/defect code.

    *"This mapping ships with defaults built from Core's standard T6 code
    library and is adjusted per plant in about fifteen minutes during
    onboarding. Where no code matches, fall back to semantic matching on the
    event description — never show nothing."*
    """

    __tablename__ = "kaizen_category_codes"
    __table_args__ = (
        UniqueConstraint("plant_id", "category", "reason_code_id", "defect_code_id"),
        Index("ix_kaizen_category_codes_cat", "plant_id", "category"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    category: Mapped[KaizenCategory] = mapped_column(
        EnumString(KaizenCategory, 20), nullable=False
    )
    reason_code_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("reason_codes.id", ondelete="CASCADE")
    )
    defect_code_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("defect_codes.id", ondelete="CASCADE")
    )


# ===========================================================================
# Points ledger and detected patterns
# ===========================================================================


class KaizenPoint(Base, PlantScopedMixin, TimestampMixin):
    """One points award, with the outcome that earned it.

    A ledger rather than a counter because the biggest award — verification —
    lands two to six weeks after implementation and must be reversible if the
    fix stops holding. A single integer column could not be audited or undone.
    """

    __tablename__ = "kaizen_points"
    __table_args__ = (
        UniqueConstraint("plant_id", "idea_id", "kind"),
        Index("ix_kaizen_points_person", "plant_id", "board", "person_name"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    idea_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("kaizen_ideas.id", ondelete="CASCADE"), index=True
    )
    operator_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operators.id", ondelete="SET NULL")
    )
    #: Denormalised on purpose: an operator record can be deactivated, and last
    #: quarter's leaderboard must still say who earned it.
    person_name: Mapped[str] = mapped_column(String(200), nullable=False)
    board: Mapped[str] = mapped_column(String(16), default="shopfloor", nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)  # PointsEvent
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    awarded_on: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(String(300))


class KaizenPattern(Base, PlantScopedMixin, TimestampMixin):
    """§10 — one root cause behind many Kaizens and many Core events.

    *"This is the highest-value feature in the product, and it is impossible
    without the plant's own accumulated history — which means it gets better the
    longer they stay, and a competitor starting fresh cannot replicate it."*

    A plant-wide project created from this alert is a SINGLE Core Action with
    the constituent Kaizens and events linked as children — not seventeen
    separate Actions.
    """

    __tablename__ = "kaizen_patterns"
    __table_args__ = (Index("ix_kaizen_patterns_open", "plant_id", "dismissed"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    label: Mapped[str] = mapped_column(String(300), nullable=False)
    #: What the cluster is keyed on, so the same pattern is recognised again.
    signature: Mapped[list[str] | None] = mapped_column(ARRAY(String(48)))

    idea_ids: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(PGUUID(as_uuid=True)))
    downtime_event_ids: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(PGUUID(as_uuid=True))
    )
    departments: Mapped[list[str] | None] = mapped_column(ARRAY(String(64)))

    kaizen_count: Mapped[int] = mapped_column(Integer, default=0)
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    #: Core-verified: recorded, not estimated. Only Core events contribute here.
    verified_cost_paise: Mapped[int] = mapped_column(default=0)
    lost_minutes: Mapped[int] = mapped_column(Integer, default=0)
    recommendation: Mapped[str | None] = mapped_column(Text)

    action_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("actions.id", ondelete="SET NULL")
    )
    dismissed: Mapped[bool] = mapped_column(Boolean, default=False)
    detected_on: Mapped[date] = mapped_column(Date, nullable=False)
