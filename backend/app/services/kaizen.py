"""Kaizen AI (§3 Layer 5).

    "Operators submit improvement ideas (eventually by voice, in Tamil); the
     system converts each into a structured cost-saving proposal, scores it,
     routes it into the Actions engine for implementation, and tracks the
     realised saving through the same Recovered-Profit accounting the core
     product already uses. Reliable pattern detection needs roughly 60-90 days
     of core data first, so early Kaizen runs on operator submissions and simple
     rules, deepening as data accumulates."

The scoring is grounded in the plant's own loss history rather than a generic
rubric: an idea is worth what the losses it addresses actually cost *here*. That
also makes a rejection explainable, which is most of what keeps a suggestion
scheme alive past month two.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.kernel import format_inr
from app.models import (
    Action,
    ActionTrigger,
    DowntimeEvent,
    KaizenIdea,
    KaizenStatus,
    PdcaState,
    Plant,
    ReasonCode,
    Recoverability,
    Station,
)
from app.services import actions as action_service

# Words an operator actually uses, mapped to the reason codes they point at.
# Deliberately plain keyword matching, not a classifier: a wrong guess here
# should produce a slightly-off saving estimate, never a wrong decision, and the
# rationale shown to the operator makes the link visible either way.
#
# Two rules learned the hard way, both enforced by tests:
#
#  * Keywords match at a word boundary. As bare substrings, "oil" matched "soil",
#    "tool" matched "stool" and "kit" matched "kitchen".
#  * No keyword that names a *place* rather than a *problem*. "Inspection" was
#    here once, and "put a fan near the inspection bench" scored as the plant's
#    most valuable idea — priced against every Quality Hold in the plant.
_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("tool", "insert", "cutter", "drill bit", "tool life"), "TOOL_BREAKAGE"),
    (("material", "stock", "stores", "staging", "trolley", "line feed"), "MATERIAL_WAIT"),
    (("setup", "set-up", "changeover", "change over", "shadow board"), "SETUP_CHANGEOVER"),
    (("breakdown", "motor", "bearing", "spindle", "hydraulic"), "MACHINE_BREAKDOWN"),
    (("coolant", "lubricant", "lubrication", "consumable"), "COOLANT_ISSUE"),
    (("fixture", "jig", "clamp", "locator", "locating pin"), "FIXTURE_ISSUE"),
    (
        ("operator", "manpower", "absent", "absence", "cross-train", "cross train"),
        "NO_OPERATOR",
    ),
    (
        ("defect", "reject", "rejection", "burr", "rework", "scrap", "quality hold"),
        "QUALITY_HOLD",
    ),
    (("program", "cnc code", "offset", "parameter"), "PROGRAM_ISSUE"),
)

#: Share of the annualised loss credited to an idea that names a station.
SCOPED_SHARE = 0.25
#: Share credited when the idea names no station. An idea that does not say
#: where it applies cannot plausibly remove the loss on every machine in the
#: plant, so it is credited far more cautiously.
UNSCOPED_SHARE = 0.10


def match_reason(title: str, description: str) -> str | None:
    """The reason code an idea addresses, or None.

    The title is searched before the description: an operator's title says what
    the idea *is*, while the description often mentions incidental things.
    """
    for text in (title.lower(), description.lower()):
        for keywords, code in _HINTS:
            if any(re.search(rf"\b{re.escape(k)}", text) for k in keywords):
                return code
    return None


@dataclass(slots=True)
class Score:
    impact: int  # 1-5
    effort: int  # 1-5
    annual_saving: int  # paise
    linked_loss: int  # paise observed in the window
    rationale: str
    reason_code: str | None


async def score_idea(
    session: AsyncSession,
    *,
    plant: Plant,
    title: str,
    description: str,
    station_id: uuid.UUID | None = None,
    window_days: int = 90,
) -> Score:
    """Turn a sentence from an operator into a costed proposal.

    The estimate is deliberately conservative — a quarter of the observed annual
    loss for the reason the idea addresses. Over-promising here would poison the
    same Recovered-Profit ledger the core product depends on, and §5.6 already
    sets the standard: savings are measured, not claimed.
    """
    reason_code = match_reason(title, description)

    # An operator who writes "CNC-3" in the text has named the machine, whether
    # or not they picked it from the dropdown. Scope to it — otherwise the idea
    # is priced against the whole plant, and an idea about one machine can
    # outrank every idea that correctly named its station.
    if station_id is None:
        station_id = await _station_named_in(session, plant, f"{title} {description}")

    since = date.today() - timedelta(days=window_days)
    observed = 0
    occurrences = 0
    scope_label = "the plant"

    if reason_code:
        query = (
            select(
                func.coalesce(func.sum(DowntimeEvent.cost_total), 0),
                func.count(DowntimeEvent.id),
            )
            .join(ReasonCode, ReasonCode.id == DowntimeEvent.reason_code_id)
            .where(
                DowntimeEvent.plant_id == plant.id,
                DowntimeEvent.business_date >= since,
                ReasonCode.code == reason_code,
                ReasonCode.recoverability == Recoverability.RECOVERABLE,
            )
        )
        if station_id:
            query = query.where(DowntimeEvent.station_id == station_id)
            station = await session.get(Station, station_id)
            scope_label = station.code if station else scope_label

        observed, occurrences = (await session.execute(query)).one()
        observed = int(observed or 0)
        occurrences = int(occurrences or 0)

    # Annualise the window, then credit a share. A good idea rarely eliminates a
    # loss outright; it reduces it — and an idea that names no machine gets a
    # smaller share still.
    share = SCOPED_SHARE if station_id else UNSCOPED_SHARE
    annualised = int(observed * (365 / window_days)) if observed else 0
    annual_saving = int(annualised * share)

    impact = (
        5
        if annual_saving >= 50_00_000
        else 4
        if annual_saving >= 10_00_000
        else 3
        if annual_saving >= 2_00_000
        else 2
        if annual_saving > 0
        else 1
    )
    # Effort is a genuine unknown at submission time. Start in the middle and
    # let whoever reviews it adjust — guessing precisely would be false comfort.
    effort = 3

    if reason_code and occurrences:
        rationale = (
            f"Matched to '{reason_code.replace('_', ' ').title()}' on {scope_label}: "
            f"{occurrences} event{'' if occurrences == 1 else 's'} costing "
            f"{format_inr(observed)} in the last "
            f"{window_days} days. Annualised that is {format_inr(annualised)}; the "
            + (
                "estimate credits a quarter of it, because ideas usually reduce a "
                "loss rather than remove it."
                if station_id
                else "estimate credits a tenth of it, because the idea names no "
                "machine and cannot plausibly remove that loss everywhere. Naming "
                "the station would sharpen the estimate."
            )
        )
    elif reason_code:
        rationale = (
            f"Matched to '{reason_code.replace('_', ' ').title()}', but no losses "
            f"recorded against it in the last {window_days} days — so there is no "
            f"measured saving to claim yet."
        )
    else:
        rationale = (
            "No downtime reason matched this idea, so it carries no estimated "
            "saving. Worth reviewing on its merits — safety, effort and quality "
            "improvements are real even when the system cannot price them."
        )

    return Score(
        impact=impact,
        effort=effort,
        annual_saving=annual_saving,
        linked_loss=observed,
        rationale=rationale,
        reason_code=reason_code,
    )


async def _station_named_in(
    session: AsyncSession, plant: Plant, text: str
) -> uuid.UUID | None:
    """The one station code mentioned in free text, if exactly one is.

    Ambiguity resolves to None rather than a guess: an idea mentioning both
    CNC-3 and VMC-1 is about the process between them, not about either.
    """
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


async def submit(
    session: AsyncSession,
    *,
    plant: Plant,
    title: str,
    description: str,
    submitted_by_name: str,
    station_id: uuid.UUID | None = None,
    operator_id: uuid.UUID | None = None,
) -> KaizenIdea:
    """Record and score an idea in one step, so the operator gets an immediate answer."""
    if station_id is None:
        station_id = await _station_named_in(session, plant, f"{title} {description}")

    score = await score_idea(
        session,
        plant=plant,
        title=title,
        description=description,
        station_id=station_id,
    )

    reason = None
    if score.reason_code:
        reason = await session.scalar(
            select(ReasonCode).where(
                ReasonCode.plant_id == plant.id, ReasonCode.code == score.reason_code
            )
        )

    count = await session.scalar(
        select(func.count(KaizenIdea.id)).where(KaizenIdea.plant_id == plant.id)
    )

    idea = KaizenIdea(
        plant_id=plant.id,
        reference=f"KZN-{(count or 0) + 1:04d}",
        title=title,
        description=description,
        submitted_by_name=submitted_by_name,
        submitted_by_operator_id=operator_id,
        station_id=station_id,
        reason_code_id=reason.id if reason else None,
        status=KaizenStatus.SCORED,
        estimated_annual_saving=score.annual_saving,
        effort_score=score.effort,
        impact_score=score.impact,
        score_rationale=score.rationale,
        linked_loss_paise=score.linked_loss,
    )
    session.add(idea)
    await session.flush()
    return idea


async def accept(
    session: AsyncSession,
    *,
    plant: Plant,
    idea: KaizenIdea,
    owner_name: str | None = None,
    due_date: date | None = None,
    note: str | None = None,
) -> Action:
    """Promote an idea into the core Actions engine.

    §3 Layer 5's build rule: *"One Actions engine. Compliance CAPAs, Kaizen
    implementations and Skill-gap fixes are all Actions — same lifecycle, same
    PDCA, same closure prompt into Factory Memory."* So this creates a real
    Action, not a parallel Kaizen workflow, and the saving will be measured by
    the same Recovered-Profit rules as everything else.
    """
    reference = await action_service._next_reference(session, plant.id)

    action = Action(
        plant_id=plant.id,
        reference=reference,
        title=idea.title,
        description=idea.description,
        trigger=ActionTrigger.KAIZEN,
        trigger_rationale=f"Kaizen idea {idea.reference} from {idea.submitted_by_name}",
        state=PdcaState.OPEN,
        station_id=idea.station_id,
        reason_code_id=idea.reason_code_id,
        linked_cost=idea.linked_loss_paise,
        owner_name=owner_name or idea.submitted_by_name,
        due_date=due_date or (date.today() + timedelta(days=14)),
        surfaced_in_meeting_on=date.today(),
    )
    session.add(action)
    await session.flush()

    # The baseline is frozen now, exactly as for a threshold-triggered Action —
    # so the saving this idea earns is measured the same conservative way.
    action.baseline_weekly_loss = await action_service.compute_baseline(
        session,
        plant_id=plant.id,
        station_id=action.station_id,
        reason_code_id=action.reason_code_id,
        as_of=date.today(),
    )
    action.baseline_frozen_at = datetime.now(UTC)

    idea.status = KaizenStatus.ACCEPTED
    idea.action_id = action.id
    idea.decided_at = datetime.now(UTC)
    idea.decision_note = note

    await session.flush()
    return action


async def reject(session: AsyncSession, *, idea: KaizenIdea, note: str) -> KaizenIdea:
    """Decline an idea — with a reason the operator can read.

    A suggestion scheme dies the first time someone's idea disappears without an
    answer, so the note is required by the API rather than optional.
    """
    idea.status = KaizenStatus.REJECTED
    idea.decided_at = datetime.now(UTC)
    idea.decision_note = note
    await session.flush()
    return idea


async def leaderboard(
    session: AsyncSession, *, plant: Plant, limit: int = 10
) -> list[dict]:
    """Who is contributing, and what it has been worth.

    §10's no-blame rule targets *reports about problems*; recognising people for
    improvements they proposed is the opposite of blame, and it is the main
    reason operators keep submitting.
    """
    rows = (
        await session.execute(
            select(
                KaizenIdea.submitted_by_name,
                func.count(KaizenIdea.id),
                func.coalesce(
                    func.sum(func.coalesce(KaizenIdea.estimated_annual_saving, 0)),
                    0,
                ),
                func.count(KaizenIdea.action_id),
            )
            .where(KaizenIdea.plant_id == plant.id)
            .group_by(KaizenIdea.submitted_by_name)
            .order_by(func.count(KaizenIdea.id).desc())
            .limit(limit)
        )
    ).all()

    return [
        {
            "name": name,
            "ideas": int(ideas),
            "estimated_annual_saving_paise": int(saving or 0),
            "accepted": int(accepted or 0),
        }
        for name, ideas, saving, accepted in rows
    ]
