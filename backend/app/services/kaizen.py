"""Kaizen AI — the operator journey and the loop back to Core (Plugin Spec v2).

    "An operator talks to their phone for 30 seconds and takes one photo. The
     system already knows which machine they are standing at, which part is
     running and what that machine costs per hour — so it asks four questions
     instead of ten. Then it does the thing only a plugin can do: it looks up
     what the plant has already recorded about this exact problem."

This module is the spine of that. What it deliberately does NOT do is as
important as what it does — §2.1 removed a great deal from the standalone
product, and none of it may creep back:

    no cost master           Core's T4 holds every rate
    no bottleneck list       Core's BF per machine
    no savings formulas      Core's formula module, called once
    no PDCA object           an approved Kaizen becomes a Core Action
    no verification rule     Core's Recovered Profit ledger, unchanged
    no separate digest       a block inside Core's weekly owner digest

Related modules: :mod:`app.services.kaizen_history` (Step 5A),
:mod:`app.services.kaizen_costing` (§7), :mod:`app.services.kaizen_patterns`
(§10), :mod:`app.services.kaizen_reporting` (§12).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import kaizen_ai
from app.kernel import (
    PLUGIN_WEIGHTS,
    STANDALONE_WEIGHTS,
    Board,
    PointsEvent,
    PriorityScore,
    SavingType,
    ScoreInputs,
    ScoreWeights,
    format_inr,
    is_quick_win,
    points_for,
    priority_score,
    verdict_for,
)
from app.models import (
    Action,
    ActionTrigger,
    ApprovalTemplate,
    DefectCode,
    JobOrder,
    JobOrderStatus,
    JobSession,
    KaizenCategory,
    KaizenCategoryCode,
    KaizenIdea,
    KaizenPoint,
    KaizenSettings,
    KaizenStatus,
    KaizenTrack,
    Operator,
    Part,
    PdcaState,
    Plant,
    ReasonCode,
    Role,
    RoutingOperation,
    Station,
    User,
    UserPlantRole,
)
from app.services import actions as action_service
from app.services import costing as costing_service
from app.services import kaizen_costing, kaizen_history, notifications
from app.services.clock import local_date

# ---------------------------------------------------------------------------
# Configuration — K4 and K5, about ten minutes of setup
# ---------------------------------------------------------------------------

#: §6.1's default mapping, expressed against Core's standard T6 code library.
#: Matched on the reason code's own ``code``, so a plant that renamed its codes
#: keeps whatever the consultant maps during onboarding instead.
DEFAULT_REASON_MAP: dict[KaizenCategory, tuple[str, ...]] = {
    KaizenCategory.WAITING: ("MATERIAL_WAIT", "NO_OPERATOR", "NO_POWER", "PLANNED_WAIT"),
    KaizenCategory.MAINTENANCE: (
        "MACHINE_BREAKDOWN",
        "TOOL_BREAKAGE",
        "FIXTURE_ISSUE",
        "COOLANT_ISSUE",
    ),
    KaizenCategory.PRODUCTIVITY: ("SETUP_CHANGEOVER", "PROGRAM_ISSUE"),
    KaizenCategory.QUALITY: ("QUALITY_HOLD",),
    KaizenCategory.SAFETY: ("SAFETY_STOP",),
    KaizenCategory.ENERGY: ("NO_POWER",),
    KaizenCategory.TRANSPORT: ("MATERIAL_WAIT",),
    KaizenCategory.MOTION: ("SETUP_CHANGEOVER",),
}


async def settings_for(session: AsyncSession, *, plant: Plant) -> KaizenSettings:
    """This plant's Kaizen configuration, seeded on first use.

    Seeded rather than required: a plugin that will not run until somebody fills
    a setup screen does not get switched on in week one.
    """
    row = await session.scalar(
        select(KaizenSettings).where(KaizenSettings.plant_id == plant.id)
    )
    if row is not None:
        return row

    row = KaizenSettings(
        plant_id=plant.id,
        template=ApprovalTemplate.SIMPLE,
        steps=[Role.SUPERVISOR.value],
        enabled_categories=[c.value for c in KaizenCategory],
    )
    session.add(row)
    await session.flush()
    await seed_category_map(session, plant=plant)
    return row


async def seed_category_map(session: AsyncSession, *, plant: Plant) -> int:
    """Wire Kaizen categories to whichever of Core's T6 codes this plant has.

    Idempotent, and additive only — a consultant's fifteen minutes of mapping
    during onboarding is never overwritten by a later reseed.
    """
    reasons = {
        row.code: row.id
        for row in (
            await session.scalars(select(ReasonCode).where(ReasonCode.plant_id == plant.id))
        ).all()
    }
    existing = {
        (row.category, row.reason_code_id)
        for row in (
            await session.scalars(
                select(KaizenCategoryCode).where(KaizenCategoryCode.plant_id == plant.id)
            )
        ).all()
    }

    added = 0
    for category, codes in DEFAULT_REASON_MAP.items():
        for code in codes:
            reason_id = reasons.get(code)
            if reason_id is None or (category, reason_id) in existing:
                continue
            session.add(
                KaizenCategoryCode(
                    plant_id=plant.id, category=category, reason_code_id=reason_id
                )
            )
            existing.add((category, reason_id))
            added += 1

    if added:
        await session.flush()
    return added


def weights_for(
    config: KaizenSettings, history: kaizen_history.CoreHistory
) -> ScoreWeights:
    """§11's weights — the plugin set when Core has history, standalone when not.

    A plant in its first weeks on Core has nothing to weight verified cost
    against, and scoring a Kaizen at 0 for "no verified history" would rank
    genuine ideas below noise. So the standalone weights apply until there is
    something real to measure.
    """
    if config.score_weights:
        return ScoreWeights.from_dict(config.score_weights)
    return PLUGIN_WEIGHTS if history.found else STANDALONE_WEIGHTS


# ---------------------------------------------------------------------------
# §5.1 — context inherited from Core, shown for one-tap confirmation
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Context:
    """What Core already knows about where this operator is standing.

    *"Inherited values are shown for one-tap confirmation, never silently
    assumed. An operator may be standing at a different machine from the one
    they are logged into."*
    """

    station: Station | None = None
    job_order: JobOrder | None = None
    part: Part | None = None
    operation: RoutingOperation | None = None
    line: str | None = None
    department: str | None = None
    bottleneck_factor: float | None = None
    is_bottleneck: bool | None = None
    #: Question keys Core has answered, so Step 4 never asks them.
    known: set[str] = field(default_factory=set)
    prompt: str = ""

    def as_dict(self) -> dict:
        return {
            "station_id": str(self.station.id) if self.station else None,
            "station_code": self.station.code if self.station else None,
            "station_name": self.station.name if self.station else None,
            "line": self.line,
            "department": self.department,
            "job_order_id": str(self.job_order.id) if self.job_order else None,
            "job_number": self.job_order.job_number if self.job_order else None,
            "part_id": str(self.part.id) if self.part else None,
            "part_number": self.part.part_number if self.part else None,
            "operation_id": str(self.operation.id) if self.operation else None,
            "operation_code": self.operation.operation_code if self.operation else None,
            "bottleneck_factor": self.bottleneck_factor,
            "is_bottleneck": self.is_bottleneck,
            "known": sorted(self.known),
            "prompt": self.prompt,
        }


async def station_named_in(
    session: AsyncSession, *, plant: Plant, text: str
) -> uuid.UUID | None:
    """The one station code mentioned in free text, if exactly one is.

    An operator who says "CNC-3" has named the machine, whether or not they
    picked it from a list — and on a phone, mid-shift, they often will not.
    Ambiguity resolves to ``None`` rather than a guess: an idea mentioning both
    CNC-3 and VMC-1 is about the process between them, not about either.
    """
    if not text.strip():
        return None
    stations = (
        await session.execute(
            select(Station.id, Station.code).where(
                Station.plant_id == plant.id, Station.is_active.is_(True)
            )
        )
    ).all()
    found = {
        station_id
        for station_id, code in stations
        if re.search(rf"(?<![\w-]){re.escape(code)}(?![\w-])", text, re.IGNORECASE)
    }
    return found.pop() if len(found) == 1 else None


async def inherit_context(
    session: AsyncSession, *, plant: Plant, station_id: uuid.UUID | None = None
) -> Context:
    """Read machine, line, part and operation off Core's Job Order spine (§5.1).

    Saves the operator about 45 seconds of typing and, on the bottleneck
    question, gives a far more accurate answer than they could — which is the
    whole point: the Full Kaizen gets *faster* in the plugin, not slower,
    despite doing considerably more.
    """
    context = Context()
    if station_id is None:
        return context

    station = await session.get(Station, station_id)
    if station is None or station.plant_id != plant.id:
        return context

    context.station = station
    context.line = station.line
    context.department = station.section.value if station.section else None
    context.bottleneck_factor = float(station.bottleneck_factor or 1)
    context.is_bottleneck = bool(station.is_bottleneck)
    # Core carries a Bottleneck Factor per machine, so §Step 4's bottleneck
    # question is deleted from the interview rather than asked.
    context.known.add("bottleneck_factor")

    session_row = await session.scalar(
        select(JobSession)
        .where(JobSession.plant_id == plant.id, JobSession.station_id == station.id)
        .order_by(JobSession.started_at.desc())
        .limit(1)
    )
    job = None
    if session_row is not None and session_row.ended_at is None:
        job = await session.get(JobOrder, session_row.job_order_id)
    if job is None:
        job = await session.scalar(
            select(JobOrder)
            .where(
                JobOrder.plant_id == plant.id,
                JobOrder.status == JobOrderStatus.RUNNING,
            )
            .order_by(JobOrder.opened_at.desc().nullslast())
            .limit(1)
        )

    if job is not None:
        context.job_order = job
        context.part = await session.get(Part, job.part_id)
        if context.part is not None:
            context.known.add("part")
            context.operation = await costing_service.resolve_routing_operation(
                session, plant_id=plant.id, part_id=context.part.id, station=station
            )
            if context.operation is not None:
                context.known.add("operation")

    bits = [station.code]
    if context.part is not None:
        bits.append(f"Part {context.part.part_number}")
    context.prompt = f"{', '.join(bits)} — correct?"
    return context


# ---------------------------------------------------------------------------
# Who gets the credit, and on which board
# ---------------------------------------------------------------------------

#: The two rankings, always shown side by side. A quality engineer competing
#: with a machine operator on one board wins every month — better vocabulary,
#: more context, more time at a desk — and the operator stops submitting.
_SUPPORT_ROLES = frozenset(
    {Role.OWNER, Role.QUALITY, Role.MAINTENANCE, Role.STORES, Role.CONSULTANT, Role.ADMIN}
)


async def board_for(
    session: AsyncSession, *, plant: Plant, user: User | None, operator_id: uuid.UUID | None
) -> Board:
    """Shopfloor or Support & Staff.

    A named operator is always shopfloor — that is what "credited to the
    operator" means. Everyone else is placed by the roles they hold.
    """
    if operator_id is not None:
        return Board.SHOPFLOOR
    if user is None:
        return Board.SHOPFLOOR
    roles = {
        link.role
        for link in (
            await session.scalars(
                select(UserPlantRole).where(
                    UserPlantRole.user_id == user.id, UserPlantRole.plant_id == plant.id
                )
            )
        ).all()
    }
    return Board.SUPPORT if roles & _SUPPORT_ROLES else Board.SHOPFLOOR


# ---------------------------------------------------------------------------
# The submission
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Submission:
    """Everything the phone collected, ready to be turned into a Kaizen."""

    title: str
    description: str
    track: KaizenTrack = KaizenTrack.FULL
    voice_transcript: str | None = None
    voice_language: str | None = None
    station_id: uuid.UUID | None = None
    category: KaizenCategory | None = None
    #: Only the findings the operator accepted ever arrive here.
    vision_findings: list[dict] = field(default_factory=list)
    vision_ran: bool = False
    interview: list[dict] = field(default_factory=list)
    investment_paise: int = 0
    needs_shutdown: bool = False
    proposed_fix: str | None = None
    #: Submit-on-behalf. Points and recognition go to this operator.
    credited_operator_id: uuid.UUID | None = None
    context_confirmed: bool = False
    unknown_station_note: str | None = None


async def next_reference(session: AsyncSession, *, plant: Plant) -> str:
    """``KZN-0042`` — per plant, gapless enough to be quoted on the floor."""
    count = await session.scalar(
        select(func.count(KaizenIdea.id)).where(KaizenIdea.plant_id == plant.id)
    )
    return f"KZN-{(count or 0) + 1:04d}"


async def submit(
    session: AsyncSession,
    *,
    plant: Plant,
    submission: Submission,
    user: User | None = None,
    submitted_by_name: str | None = None,
    as_of: date | None = None,
) -> KaizenIdea:
    """Steps 3-9 in one call, so the operator gets an answer before they walk off.

    A Quick Observation stops after classification: no interview, no cost
    engine, no challenge, no impact card. At ~80% of volume, running the full
    journey on a ₹200 observation is wasted processing and buries the four ideas
    a month that are worth ₹4 lakh.
    """
    as_of = as_of or local_date(plant, datetime.now(UTC))
    config = await settings_for(session, plant=plant)

    station_id = submission.station_id
    if station_id is None:
        # Not every submission comes from a station the phone knows about, but
        # an operator who typed "CNC-3" has named it all the same. Without this
        # the idea is priced against the whole plant.
        station_id = await station_named_in(
            session,
            plant=plant,
            text=f"{submission.title} {submission.description}",
        )
    context = await inherit_context(session, plant=plant, station_id=station_id)
    vision = [f for f in submission.vision_findings if f.get("accepted")]

    category = submission.category or kaizen_ai.classify(
        submission.title, submission.description, vision=vision
    )
    saving_type = kaizen_ai.saving_type_for(
        category, submission.title, submission.description
    )
    safety = category is KaizenCategory.SAFETY or kaizen_ai.looks_safety_related(
        submission.title, submission.description, vision
    )

    credited_name = submitted_by_name or (user.full_name if user else "Unknown")
    if submission.credited_operator_id:
        operator = await session.get(Operator, submission.credited_operator_id)
        if operator is not None and operator.plant_id == plant.id:
            credited_name = operator.name

    idea = KaizenIdea(
        plant_id=plant.id,
        reference=await next_reference(session, plant=plant),
        track=submission.track,
        title=submission.title,
        description=submission.description,
        voice_transcript=submission.voice_transcript,
        voice_language=submission.voice_language,
        submitted_by_name=submitted_by_name or (user.full_name if user else "Unknown"),
        submitted_by_user_id=user.id if user else None,
        credited_operator_id=submission.credited_operator_id,
        credited_name=credited_name,
        board=(
            await board_for(
                session,
                plant=plant,
                user=user,
                operator_id=submission.credited_operator_id,
            )
        ).value,
        department=context.department,
        submitted_on=as_of,
        station_id=context.station.id if context.station else None,
        job_order_id=context.job_order.id if context.job_order else None,
        part_id=context.part.id if context.part else None,
        context_confirmed=submission.context_confirmed,
        unknown_station_note=submission.unknown_station_note,
        category=category,
        saving_type=saving_type.value,
        vision_findings=vision or None,
        vision_ran=submission.vision_ran,
        interview=submission.interview or None,
        investment_paise=submission.investment_paise,
        needs_shutdown=submission.needs_shutdown,
        safety_flagged=safety,
        signals=kaizen_ai.cross_plugin_signals(submission.title, submission.description)
        or None,
        fingerprint=kaizen_ai.fingerprint(
            submission.title, submission.description, submission.voice_transcript or ""
        ),
        status=KaizenStatus.SUBMITTED,
    )
    session.add(idea)
    await session.flush()

    await _tag_codes(session, plant=plant, idea=idea, category=category)
    await enrich(
        session,
        plant=plant,
        idea=idea,
        config=config,
        context=context,
        proposed_fix=submission.proposed_fix,
        as_of=as_of,
    )

    # Step 5 — the plant's own memory, so six people do not submit one idea and
    # five get rejected, which is how operators lose faith in the scheme.
    await _attach_similar(session, plant=plant, idea=idea)

    await award(session, plant=plant, idea=idea, event=PointsEvent.SUBMITTED, on=as_of)
    await _apply_thresholds(session, plant=plant, idea=idea, config=config)
    await session.flush()
    return idea


async def _tag_codes(
    session: AsyncSession, *, plant: Plant, idea: KaizenIdea, category: KaizenCategory
) -> None:
    """Attach the first Core reason/defect code this category maps to.

    One code, for the join key. The History Check searches every mapped code, so
    nothing is lost by storing a single representative here.
    """
    reason_ids, defect_ids = await kaizen_history.resolve_codes(
        session, plant=plant, category=category
    )
    if reason_ids:
        idea.reason_code_id = reason_ids[0]
    if defect_ids and category is KaizenCategory.QUALITY:
        idea.defect_code_id = defect_ids[0]


async def enrich(
    session: AsyncSession,
    *,
    plant: Plant,
    idea: KaizenIdea,
    config: KaizenSettings | None = None,
    context: Context | None = None,
    proposed_fix: str | None = None,
    as_of: date | None = None,
) -> KaizenIdea:
    """Steps 5A-9: history, money, challenge, forecast, score.

    Split out from :func:`submit` because it re-runs — when a Quick Observation
    is promoted, when an approver supplies a missing rate, or when a nightly job
    refreshes a queue that has been sitting for a fortnight while the cost of
    doing nothing ticked upwards.
    """
    as_of = as_of or local_date(plant, datetime.now(UTC))
    config = config or await settings_for(session, plant=plant)
    if context is None:
        context = await inherit_context(session, plant=plant, station_id=idea.station_id)

    category = idea.category or KaizenCategory.OTHER
    saving_type = SavingType(idea.saving_type or SavingType.OTHER.value)
    days_waiting = max(0, (as_of - (idea.submitted_on or as_of)).days)

    # --- Step 5A: what the plant already knows ---------------------------
    history = await kaizen_history.check(
        session,
        plant=plant,
        category=category,
        title=idea.title,
        description=idea.description,
        station=context.station,
        part_id=idea.part_id,
        reason_code_id=idea.reason_code_id,
        defect_code_id=idea.defect_code_id,
        window_days=config.history_window_days,
        as_of=as_of,
    )
    idea.history = history.as_dict()
    idea.linked_loss_paise = history.cost_paise
    idea.duplicate_action_id = (
        history.open_action.action_id if history.open_action else None
    )
    idea.is_recurrence = history.recurrence_of is not None

    # A Quick Observation is logged, routed and counted — and that is all. No
    # cost engine, no challenge, no impact card (§4.3).
    if idea.track is KaizenTrack.QUICK:
        idea.status = (
            KaizenStatus.DUPLICATE
            if idea.duplicate_action_id
            else KaizenStatus.SCORED
            if idea.status is KaizenStatus.SUBMITTED
            else idea.status
        )
        idea.priority_verdict = verdict_for(score=0, plant_rank=history.rank)
        await session.flush()
        return idea

    # --- Steps 6-7: Core prices it, Kaizen classifies it ------------------
    priced = await kaizen_costing.quote(
        session,
        plant=plant,
        saving_type=saving_type,
        category=category,
        interview=idea.interview or [],
        station_id=idea.station_id,
        part_id=idea.part_id,
        operation_id=context.operation.id if context.operation else None,
        vision=idea.vision_findings,
        investment_paise=idea.investment_paise,
        days_waiting=days_waiting,
        on_date=as_of,
    )
    idea.core_value_paise = priced.core.subject_value
    idea.hard_saving_paise = priced.hard
    idea.soft_saving_paise = priced.soft
    idea.saving_basis = f"{priced.core.working}\n{priced.split.basis}".strip()
    idea.intangible_stars = priced.stars.as_dict()
    idea.provisional = priced.provisional
    idea.provisional_note = priced.provisional_note

    if priced.hard + priced.soft > 0:
        total = priced.hard + priced.soft
        idea.impact_score = (
            5
            if total >= 50_00_000
            else 4
            if total >= 10_00_000
            else 3
            if total >= 2_00_000
            else 2
        )
    else:
        idea.impact_score = 1

    # --- Step 7: one cheaper alternative, never two -----------------------
    if not idea.challenge:
        challenge = await kaizen_ai.propose_alternative(
            problem=idea.description,
            proposed_fix=proposed_fix or idea.title,
            investment_paise=idea.investment_paise,
            station_code=context.station.code if context.station else None,
        )
        idea.challenge = challenge.as_dict() if challenge.offered else None

    # --- Step 9: what will go wrong, from Core's own Action history -------
    prediction = await kaizen_history.forecast(
        session,
        plant=plant,
        investment_paise=idea.investment_paise,
        needs_shutdown=idea.needs_shutdown,
        safety=idea.safety_flagged,
        as_of=as_of,
    )
    idea.forecast = prediction.as_dict()
    idea.effort_score = _effort_from(prediction, idea)

    # --- §11: the hidden rank --------------------------------------------
    score = rank(idea, history=history, weights=weights_for(config, history))
    idea.priority_score = score.score
    idea.priority_verdict = score.verdict
    idea.score_components = {k: float(v) for k, v in score.components.items()}
    idea.score_rationale = _rationale(idea, history, priced)

    if idea.status is KaizenStatus.SUBMITTED:
        idea.status = (
            KaizenStatus.DUPLICATE if idea.duplicate_action_id else KaizenStatus.SCORED
        )
    await session.flush()
    return idea


def _effort_from(prediction: kaizen_history.Forecast, idea: KaizenIdea) -> int:
    """1 (very hard) to 5 (very easy), from the obstacles Core actually predicts."""
    high = sum(1 for o in prediction.obstacles if o["risk"] == "high")
    medium = sum(1 for o in prediction.obstacles if o["risk"] == "medium")
    if high >= 2:
        return 1
    if high == 1:
        return 2
    if medium:
        return 3
    return 5 if idea.investment_paise == 0 else 4


def rank(
    idea: KaizenIdea,
    *,
    history: kaizen_history.CoreHistory,
    weights: ScoreWeights,
) -> PriorityScore:
    """§11's 0-100 score. Never a headline — it ranks the queue, nothing else."""
    stars = idea.intangible_stars or {}
    return priority_score(
        ScoreInputs(
            verified_cost=history.cost_paise,
            verified_events=history.events,
            events_recent_half=history.events_recent,
            events_prior_half=history.events_prior,
            hard_saving=idea.hard_saving_paise,
            ease=idea.effort_score,
            safety_stars=int(stars.get("safety", 0)),
            quality_stars=int(stars.get("quality", 0)),
            compliance_stars=int(stars.get("compliance", 0)),
            cost_reference=history.cost_reference,
            frequency_reference=history.frequency_reference,
            plant_rank=history.rank,
            # Rank projected savings against the plant's own recurring losses
            # too, so one optimistic estimate cannot top the queue on its own.
            saving_reference=history.cost_reference or max(idea.hard_saving_paise, 1),
        ),
        weights,
    )


def _rationale(idea: KaizenIdea, history: kaizen_history.CoreHistory, priced) -> str:
    """Why this number, in words the operator who raised it can read."""
    lines = [history.message]
    if priced.hard or priced.soft:
        lines.append(
            f"Hard saving {format_inr(priced.hard)}/yr · "
            f"capacity released {format_inr(priced.soft)}/yr. {priced.split.basis}"
        )
    elif idea.safety_flagged:
        lines.append(
            "No rupee figure: the value here is risk removed, and it is shown "
            "in stars rather than costed."
        )
    if priced.provisional_note:
        lines.append(priced.provisional_note)
    return "\n".join(lines)


async def _attach_similar(
    session: AsyncSession, *, plant: Plant, idea: KaizenIdea, limit: int = 3
) -> None:
    """Step 5 — past Kaizens in this plant that describe the same thing."""
    rows = (
        await session.scalars(
            select(KaizenIdea)
            .where(
                KaizenIdea.plant_id == plant.id,
                KaizenIdea.id != idea.id,
                KaizenIdea.fingerprint.is_not(None),
            )
            .order_by(KaizenIdea.created_at.desc())
            .limit(400)
        )
    ).all()

    scored = sorted(
        (
            (kaizen_ai.jaccard(idea.fingerprint or [], row.fingerprint or []), row)
            for row in rows
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )
    matches = [
        {
            "id": str(row.id),
            "reference": row.reference,
            "title": row.title,
            "status": row.status.value,
            "similarity": round(score, 2),
            "verified_saving_display": format_inr(row.verified_saving_paise),
            "action_id": str(row.action_id) if row.action_id else None,
        }
        for score, row in scored[:limit]
        if score >= kaizen_history.SEMANTIC_THRESHOLD
    ]
    idea.similar = matches or None

    # A near-identical repeat of something already in the plant's history scores
    # nothing and does not clear the month's requirement (anti-gaming rule 3).
    if matches and matches[0]["similarity"] >= 0.72:
        idea.duplicate_of_id = uuid.UUID(matches[0]["id"])
        idea.status = KaizenStatus.DUPLICATE


# ---------------------------------------------------------------------------
# §10 — the approval workflow (Kaizen-owned; Core has no equivalent)
# ---------------------------------------------------------------------------


def resolve_route(config: KaizenSettings, idea: KaizenIdea) -> list[str]:
    """The approval steps this Kaizen must clear, after the threshold rules.

    Three presets and threshold rules, not a drag-and-drop builder — that covers
    ~95% of real plants and ships this quarter rather than in six months.
    """
    # §4.3 — a Quick Observation needs no approval at all. It is logged, routed
    # to the owning department, counted for participation and worth 5 points.
    # At ~80% of volume, putting these in front of an approver would flood the
    # queue on day one: a supervisor with 40 operators facing 400 items a month
    # cannot review 400, and the four ideas worth ₹4 lakh get buried under them.
    if idea.track is KaizenTrack.QUICK:
        return []

    steps = [str(s) for s in (config.steps or [])]
    if config.template is ApprovalTemplate.OPEN and not idea.safety_flagged:
        return []

    total = idea.hard_saving_paise + idea.soft_saving_paise
    if (
        idea.track is KaizenTrack.FULL
        and not idea.safety_flagged
        and idea.investment_paise < config.auto_approve_below_paise
        and idea.priority_score >= config.auto_approve_min_score
        and total > 0
    ):
        return []

    # "IF safety-flagged → always route to Plant Head" and
    # "IF cost > threshold → add Finance" — the plant's own threshold rules.
    escalate = (idea.safety_flagged and config.safety_always_to_owner) or (
        idea.investment_paise > config.finance_above_paise
    )
    if escalate and Role.OWNER.value not in steps:
        steps = [*steps, Role.OWNER.value]
    return steps


async def _apply_thresholds(
    session: AsyncSession, *, plant: Plant, idea: KaizenIdea, config: KaizenSettings
) -> None:
    """Set the route, and auto-approve where the plant's own rules say to."""
    if idea.status is KaizenStatus.DUPLICATE:
        idea.approval_route = []
        return

    route = resolve_route(config, idea)
    idea.approval_route = route
    idea.approval_step = 0

    if not route and idea.track is KaizenTrack.FULL:
        await approve(
            session,
            plant=plant,
            idea=idea,
            decided_by=None,
            note="Auto-approved by the plant's threshold rule.",
            config=config,
        )


async def decide(
    session: AsyncSession,
    *,
    plant: Plant,
    idea: KaizenIdea,
    accept: bool,
    user: User | None,
    note: str | None = None,
    owner_name: str | None = None,
    due_date: date | None = None,
    investment_paise: int | None = None,
    config: KaizenSettings | None = None,
) -> KaizenIdea:
    """One approval step. Declining always carries a reason the operator can read."""
    config = config or await settings_for(session, plant=plant)
    now = datetime.now(UTC)

    route = list(idea.approval_route or [])
    trail = list(idea.approval_trail or [])
    trail.append(
        {
            "step": idea.approval_step,
            "role": route[idea.approval_step] if idea.approval_step < len(route) else None,
            "by": user.full_name if user else "system",
            "by_user_id": str(user.id) if user else None,
            "accepted": accept,
            "note": note,
            "at": now.isoformat(),
        }
    )
    idea.approval_trail = trail

    if not accept:
        # A suggestion scheme dies the first time someone's idea disappears
        # without an answer, so the reason is required by the API, not optional.
        idea.status = KaizenStatus.REJECTED
        idea.decided_at = now
        idea.decision_note = note
        await _notify_operator(
            session,
            plant=plant,
            idea=idea,
            body=f"Your Kaizen {idea.reference} was not taken forward. {note or ''}".strip(),
        )
        await session.flush()
        return idea

    # An approver who supplied the real cost replaces the operator's guess, and
    # payback and ROI recalculate automatically when the real number lands.
    if investment_paise is not None and investment_paise != idea.investment_paise:
        idea.investment_paise = investment_paise
        idea.investment_is_estimate = False
        await enrich(session, plant=plant, idea=idea, config=config)

    idea.approval_step += 1
    if idea.approval_step < len(route):
        await session.flush()
        return idea

    return await approve(
        session,
        plant=plant,
        idea=idea,
        decided_by=user,
        note=note,
        owner_name=owner_name,
        due_date=due_date,
        config=config,
    )


async def approve(
    session: AsyncSession,
    *,
    plant: Plant,
    idea: KaizenIdea,
    decided_by: User | None,
    note: str | None = None,
    owner_name: str | None = None,
    due_date: date | None = None,
    config: KaizenSettings | None = None,
) -> KaizenIdea:
    """Final approval — and, above Core's threshold, the handoff to an Action."""
    config = config or await settings_for(session, plant=plant)
    idea.decided_at = datetime.now(UTC)
    idea.decision_note = note
    idea.status = KaizenStatus.APPROVED

    if creates_action(plant=plant, idea=idea):
        await create_action(
            session, plant=plant, idea=idea, owner_name=owner_name, due_date=due_date
        )

    await award(session, plant=plant, idea=idea, event=PointsEvent.APPROVED)
    tracked = (
        " It is now on the plant's action board with an owner and a date."
        if idea.action_id
        else ""
    )
    await _notify_operator(
        session,
        plant=plant,
        idea=idea,
        body=f"Your Kaizen {idea.reference} has been approved.{tracked}",
    )
    await session.flush()
    return idea


def creates_action(*, plant: Plant, idea: KaizenIdea) -> bool:
    """§4.3 — which Kaizens become Core Actions, and which stay in Kaizen.

    *"Core Spec 5.2 is explicit: not every event becomes an Action, because a
    system that spawns forty open actions a week dies of action fatigue within a
    month. At ~80% of volume, Quick Observations would flood the Actions Board
    on day one."*

    The threshold is the plant's existing Core threshold. Kaizen does not
    introduce a second one, and Kaizen-sourced Actions are not exempt from it.
    """
    if idea.track is KaizenTrack.QUICK or idea.action_id is not None:
        return False
    if idea.duplicate_action_id is not None:
        # Somebody is already on it. Adding a second Action for one problem is
        # exactly the duplication §6.4 exists to prevent.
        return False
    if idea.safety_flagged or idea.is_recurrence:
        return True
    value = max(idea.hard_saving_paise, idea.linked_loss_paise)
    return value >= plant.action_cost_threshold


async def create_action(
    session: AsyncSession,
    *,
    plant: Plant,
    idea: KaizenIdea,
    owner_name: str | None = None,
    due_date: date | None = None,
) -> Action:
    """§8.1 — the handoff. From here the Action follows CORE's lifecycle.

    Everything Core's Actions Board, morning-meeting gate, RCA rules and
    four-question closure already do, it now does for this Kaizen. There is no
    Kaizen PDCA object, no Kaizen status chips and no second Actions Board — a
    supervisor who has to check two lists checks neither.
    """
    reference = await action_service._next_reference(session, plant.id)
    history = idea.history or {}

    rationale = [
        f"Kaizen {idea.reference}, raised by {idea.credited_name}.",
    ]
    if history.get("found"):
        rationale.append(
            f"Core-verified history: {history.get('events')} events over "
            f"{history.get('window_days')} days costing {history.get('cost_display')}."
        )
    if idea.is_recurrence and history.get("recurrence_of"):
        rationale.append(
            f"RECURRENCE — {history['recurrence_of']['reference']} was closed for "
            "this and the problem has returned."
        )
    # A projection is never a saving, and must be labelled as one wherever it
    # appears. Only Core's measured post-implementation figure ever enters the
    # Recovered Profit ledger.
    if idea.hard_saving_paise:
        rationale.append(
            f"PROJECTED (not a saving): {format_inr(idea.hard_saving_paise)}/yr hard, "
            f"{format_inr(idea.soft_saving_paise)}/yr capacity."
        )

    action = Action(
        plant_id=plant.id,
        reference=reference,
        title=idea.title,
        description=idea.description,
        trigger=ActionTrigger.KAIZEN,
        trigger_rationale=" ".join(rationale),
        state=PdcaState.OPEN,
        station_id=idea.station_id,
        reason_code_id=idea.reason_code_id,
        part_id=idea.part_id,
        loss_source=(
            action_service.LOSS_QUALITY
            if idea.category is KaizenCategory.QUALITY
            else action_service.LOSS_DOWNTIME
        ),
        linked_cost=idea.linked_loss_paise,
        countermeasure=_chosen_countermeasure(idea),
        owner_name=owner_name or idea.credited_name,
        due_date=due_date or (date.today() + timedelta(days=plant.action_due_days or 3)),
        surfaced_in_meeting_on=date.today(),
    )
    session.add(action)
    await session.flush()

    # The baseline is frozen now, exactly as for a threshold-triggered Action,
    # so this Kaizen's saving is measured by the same conservative rule as
    # everything else in the plant (§9).
    action.baseline_weekly_loss = await action_service.compute_baseline(
        session,
        plant_id=plant.id,
        station_id=action.station_id,
        reason_code_id=action.reason_code_id,
        as_of=date.today(),
        loss_source=action.loss_source,
        part_id=action.part_id,
    )
    action.baseline_frozen_at = datetime.now(UTC)

    idea.action_id = action.id
    idea.status = KaizenStatus.ACCEPTED
    await session.flush()
    return action


def _chosen_countermeasure(idea: KaizenIdea) -> str:
    """The operator's fix, or the AI alternative if that is what was chosen."""
    challenge = idea.challenge or {}
    if challenge.get("chosen") == "alternative" and challenge.get("alternative"):
        return str(challenge["alternative"])
    return idea.title


# ---------------------------------------------------------------------------
# Quick Observation → Full Kaizen (auto-promotion)
# ---------------------------------------------------------------------------


async def should_promote(
    session: AsyncSession, *, plant: Plant, idea: KaizenIdea, config: KaizenSettings
) -> bool:
    """*"AI auto-promotes when it matters."* Recurring, safety-related, or big."""
    if idea.track is not KaizenTrack.QUICK or idea.promoted_from_id:
        return False
    if idea.safety_flagged:
        return True
    history = idea.history or {}
    if history.get("rank") and int(history["rank"]) <= 5:
        return True
    return int(history.get("cost_paise") or 0) >= config.promote_above_paise


async def promote(
    session: AsyncSession,
    *,
    plant: Plant,
    idea: KaizenIdea,
    interview: list[dict],
    investment_paise: int = 0,
    needs_shutdown: bool = False,
    proposed_fix: str | None = None,
) -> KaizenIdea:
    """*"This one looks worth a proper analysis. 4 quick questions?"*

    Promotion creates a NEW Full Kaizen linked to the observation rather than
    mutating it: the observation already earned its 5 points and cleared the
    operator's month, and rewriting history to take that away would be the
    fastest way to teach people not to report small things.
    """
    config = await settings_for(session, plant=plant)
    full = KaizenIdea(
        plant_id=plant.id,
        reference=await next_reference(session, plant=plant),
        track=KaizenTrack.FULL,
        title=idea.title,
        description=idea.description,
        voice_transcript=idea.voice_transcript,
        voice_language=idea.voice_language,
        submitted_by_name=idea.submitted_by_name,
        submitted_by_user_id=idea.submitted_by_user_id,
        credited_operator_id=idea.credited_operator_id,
        credited_name=idea.credited_name,
        board=idea.board,
        department=idea.department,
        submitted_on=date.today(),
        station_id=idea.station_id,
        job_order_id=idea.job_order_id,
        part_id=idea.part_id,
        reason_code_id=idea.reason_code_id,
        defect_code_id=idea.defect_code_id,
        context_confirmed=idea.context_confirmed,
        category=idea.category,
        saving_type=idea.saving_type,
        vision_findings=idea.vision_findings,
        vision_ran=idea.vision_ran,
        interview=interview or None,
        investment_paise=investment_paise,
        needs_shutdown=needs_shutdown,
        safety_flagged=idea.safety_flagged,
        signals=idea.signals,
        fingerprint=idea.fingerprint,
        promoted_from_id=idea.id,
        status=KaizenStatus.SUBMITTED,
    )
    session.add(full)
    await session.flush()

    await enrich(session, plant=plant, idea=full, config=config, proposed_fix=proposed_fix)
    await _attach_similar(session, plant=plant, idea=full)
    await _apply_thresholds(session, plant=plant, idea=full, config=config)
    await session.flush()
    return full


# ---------------------------------------------------------------------------
# Points — awarded on outcome, never on submission count
# ---------------------------------------------------------------------------


async def award(
    session: AsyncSession,
    *,
    plant: Plant,
    idea: KaizenIdea,
    event: PointsEvent,
    on: date | None = None,
    note: str | None = None,
) -> int:
    """Record one points award. Idempotent per Kaizen per outcome.

    *"If you award points for submitting, you will get 500 junk submissions a
    month... Weighting points towards verified outcomes means the operator
    chasing Sensei is chasing exactly what the Plant Head wants."*
    """
    # A near-identical repeat of something already in the plant's history scores
    # nothing at all — not even the submission point.
    if idea.status is KaizenStatus.DUPLICATE:
        event = PointsEvent.DUPLICATE

    existing = await session.scalar(
        select(KaizenPoint).where(
            KaizenPoint.plant_id == plant.id,
            KaizenPoint.idea_id == idea.id,
            KaizenPoint.kind == event.value,
        )
    )
    if existing is not None:
        return existing.points

    points = points_for(event, verified_annual_saving=idea.verified_saving_paise)
    session.add(
        KaizenPoint(
            plant_id=plant.id,
            idea_id=idea.id,
            operator_id=idea.credited_operator_id,
            person_name=idea.credited_name,
            board=idea.board,
            kind=event.value,
            points=points,
            awarded_on=on or date.today(),
            note=note,
        )
    )
    idea.points_awarded = (idea.points_awarded or 0) + points
    await session.flush()
    return points


# ---------------------------------------------------------------------------
# §9 — verification through Core's Recovered Profit ledger
# ---------------------------------------------------------------------------


async def sync_with_actions(
    session: AsyncSession, *, plant: Plant, as_of: date | None = None
) -> int:
    """Pull Kaizen status from the Core Actions its approvals created (§8.3, §9).

    The operator sees Submitted → Approved → In progress → Done → Verified, and
    nothing else: no Actions Board, no RCA template, no PDCA grid. They
    submitted an idea; they want to know what happened to it.

    THE RUPEE IS COUNTED ONCE, THE CREDIT IS SHARED. ``verified_saving_paise``
    here is an attributed copy for the operator's recognition. The plant's total
    stays Core's Recovered Profit ledger, with this Action's entry tagged
    ``source = Kaizen`` — never added a second time.
    """
    as_of = as_of or local_date(plant, datetime.now(UTC))
    rows = (
        await session.execute(
            select(KaizenIdea, Action)
            .join(Action, Action.id == KaizenIdea.action_id)
            .where(
                KaizenIdea.plant_id == plant.id,
                KaizenIdea.status.in_(
                    [KaizenStatus.ACCEPTED, KaizenStatus.APPROVED, KaizenStatus.IMPLEMENTED]
                ),
            )
        )
    ).all()

    changed = 0
    for idea, action in rows:
        before = idea.status

        if action.closed_at is not None and idea.implemented_at is None:
            idea.implemented_at = action.closed_at
            idea.status = KaizenStatus.IMPLEMENTED
            await award(
                session, plant=plant, idea=idea, event=PointsEvent.IMPLEMENTED, on=as_of
            )
            if idea.safety_flagged:
                await award(
                    session,
                    plant=plant,
                    idea=idea,
                    event=PointsEvent.SAFETY_IMPLEMENTED,
                    on=as_of,
                )
            await _notify_operator(
                session,
                plant=plant,
                idea=idea,
                body=f"Your Kaizen {idea.reference} has been implemented. "
                "We will check in two weeks whether the saving held.",
            )

        # Points on verification fire when Core marks the profit SUSTAINED, not
        # when the Action is marked complete. That is 2-6 weeks later, and it is
        # correct: the product pays for improvements that held.
        if action.state is PdcaState.VERIFIED and idea.verified_at is None:
            idea.verified_saving_paise = action.recovered_profit or 0
            idea.verified_at = action.verified_at or datetime.now(UTC)
            idea.status = KaizenStatus.VERIFIED
            await award(
                session, plant=plant, idea=idea, event=PointsEvent.VERIFIED, on=as_of
            )
            await _notify_operator(
                session,
                plant=plant,
                idea=idea,
                body=(
                    f"Verified: {idea.reference} saved "
                    f"{format_inr(idea.verified_saving_paise)} so far, and it held."
                ),
            )

        if idea.status is not before:
            changed += 1

    if changed:
        await session.flush()
    return changed


async def _notify_operator(
    session: AsyncSession, *, plant: Plant, idea: KaizenIdea, body: str
) -> None:
    """One message per status change — the cheapest trust mechanism in the product.

    *"The single most common reason suggestion schemes collapse is operators
    believing their idea vanished into a drawer."*
    """
    if idea.submitted_by_user_id is None:
        return
    user = await session.get(User, idea.submitted_by_user_id)
    if user is None:
        return
    await notifications.enqueue(
        session,
        plant=plant,
        kind="kaizen_status",
        recipients=[
            notifications.Recipient(name=user.full_name, number=user.phone, user_id=user.id)
        ],
        title=f"Kaizen {idea.reference}",
        body=body,
        url=f"/kaizen?idea={idea.id}",
        subject_type="kaizen",
        subject_id=idea.id,
        dedupe_key=f"kaizen:{idea.id}:{idea.status.value}",
    )


# ---------------------------------------------------------------------------
# §7.3 — the one place Kaizen writes a cost value
# ---------------------------------------------------------------------------


async def supply_rate(
    session: AsyncSession,
    *,
    plant: Plant,
    idea: KaizenIdea,
    key: str,
    paise: int,
    user: User | None,
    effective_from: date | None = None,
) -> KaizenIdea:
    """Write a missing rate the approver supplied into CORE's cost master.

    *"It must write through Core's master-data API with full audit trail — who
    supplied it, when, from which Kaizen — and must be visible and editable in
    Core's own cost screens immediately."*

    Never blocks approval: the approver can skip the question and approve
    anyway, and the Kaizen simply stays Provisional.
    """
    await costing_service.set_utility_rate(
        session,
        plant_id=plant.id,
        key=key,
        paise=paise,
        effective_from=effective_from or date.today(),
        derivation={
            "supplied_by": user.full_name if user else "unknown",
            "supplied_by_user_id": str(user.id) if user else None,
            "from_kaizen": idea.reference,
            "at": datetime.now(UTC).isoformat(),
        },
    )
    return await enrich(session, plant=plant, idea=idea)


# ---------------------------------------------------------------------------
# Views the dashboard is built from (§12.2)
# ---------------------------------------------------------------------------


def quick_win(idea: KaizenIdea, config: KaizenSettings) -> bool:
    """*"Quick Wins is the view that gets used in week 1. Build it first."*"""
    return is_quick_win(
        score=idea.priority_score,
        investment=idea.investment_paise,
        needs_shutdown=idea.needs_shutdown,
        threshold=config.quick_win_investment_paise,
    )


async def open_queue(
    session: AsyncSession, *, plant: Plant, limit: int = 200
) -> list[KaizenIdea]:
    """Everything waiting on a decision, ranked by the hidden score."""
    return list(
        (
            await session.scalars(
                select(KaizenIdea)
                .where(
                    KaizenIdea.plant_id == plant.id,
                    KaizenIdea.status.in_([KaizenStatus.SUBMITTED, KaizenStatus.SCORED]),
                    # Observations are not decisions. Keeping them out of this
                    # queue is what keeps the queue survivable (§4.3).
                    KaizenIdea.track == KaizenTrack.FULL,
                )
                .order_by(KaizenIdea.priority_score.desc(), KaizenIdea.created_at)
                .limit(limit)
            )
        ).all()
    )


async def delayed(
    session: AsyncSession, *, plant: Plant, days: int = 14, limit: int = 100
) -> list[KaizenIdea]:
    """Anything sitting in one status for more than a fortnight."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    return list(
        (
            await session.scalars(
                select(KaizenIdea)
                .where(
                    KaizenIdea.plant_id == plant.id,
                    KaizenIdea.status.notin_(
                        [
                            KaizenStatus.VERIFIED,
                            KaizenStatus.REJECTED,
                            KaizenStatus.DUPLICATE,
                        ]
                    ),
                    # An observation nobody actioned is not "delayed" — nothing
                    # was ever owed on it.
                    KaizenIdea.track == KaizenTrack.FULL,
                    KaizenIdea.updated_at < cutoff,
                )
                .order_by(KaizenIdea.updated_at)
                .limit(limit)
            )
        ).all()
    )


async def mine(
    session: AsyncSession, *, plant: Plant, user: User, operator_id: uuid.UUID | None = None
) -> list[KaizenIdea]:
    """The operator's own list — the only Kaizen screen most people ever open."""
    scope = [KaizenIdea.submitted_by_user_id == user.id]
    if operator_id is not None:
        scope.append(KaizenIdea.credited_operator_id == operator_id)
    return list(
        (
            await session.scalars(
                select(KaizenIdea)
                .where(KaizenIdea.plant_id == plant.id, or_(*scope))
                .order_by(KaizenIdea.created_at.desc())
                .limit(100)
            )
        ).all()
    )


async def label_for(session: AsyncSession, idea: KaizenIdea) -> dict:
    """Display names for the Core records a Kaizen points at."""
    station = await session.get(Station, idea.station_id) if idea.station_id else None
    part = await session.get(Part, idea.part_id) if idea.part_id else None
    reason = (
        await session.get(ReasonCode, idea.reason_code_id) if idea.reason_code_id else None
    )
    defect = (
        await session.get(DefectCode, idea.defect_code_id) if idea.defect_code_id else None
    )
    return {
        "station_code": station.code if station else None,
        "line": station.line if station else None,
        "part_number": part.part_number if part else None,
        "reason_label": reason.label if reason else None,
        "defect_label": defect.label if defect else None,
    }
