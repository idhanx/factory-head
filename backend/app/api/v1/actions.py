"""Screens 9 and 10 — the Actions Board and the RCA/closure prompt."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Identity, get_identity, get_plant, get_session, require
from app.api.scoping import in_plant, require_member
from app.kernel import CLOSABLE_STATES, MANUAL_TRANSITIONS, PDCA_PHASE, format_inr
from app.kernel import PdcaState as KernelPdcaState
from app.models import (
    Action,
    ActionTrigger,
    Attachment,
    MemoryEntry,
    PdcaState,
    Plant,
    ReasonCode,
    Role,
    RootCauseAnalysis,
    Station,
)
from app.schemas import (
    ActionCloseRequest,
    ActionCreateRequest,
    ActionOut,
    ActionUpdateRequest,
    MemoryEntryOut,
    RcaUpdateRequest,
)
from app.services import actions as action_service
from app.services.clock import plant_today
from app.ws.manager import ACTION_CREATED, Event, manager

router = APIRouter(prefix="/actions", tags=["actions"])

OPEN_STATES = [
    PdcaState.OPEN,
    PdcaState.ROOT_CAUSE,
    PdcaState.PLANNED,
    PdcaState.IN_PROGRESS,
    PdcaState.REOPENED,
]

#: Cancelling an Action, or bringing a cancelled one back, removes or restores
#: work on someone's list — a decision for whoever runs the plant or the line,
#: not for any one assignee.
LEAD_ROLES = (Role.OWNER, Role.SUPERVISOR, Role.CONSULTANT, Role.ADMIN)
LEAD_ONLY_TARGETS = {KernelPdcaState.CANCELLED, KernelPdcaState.OPEN}


@router.get("", response_model=list[ActionOut])
async def board(
    state: str | None = None,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    """Open / In-progress / Monitoring / Verified — ranked by money.

    Ranking by ₹ rather than by age is deliberate: the board should answer
    "what is costing us most that we haven't fixed", not "what is oldest".
    """
    query = select(Action).where(Action.plant_id == identity.plant_id)
    if state == "open":
        query = query.where(Action.state.in_(OPEN_STATES))
    elif state:
        query = query.where(Action.state == state)

    rows = (
        await session.scalars(query.order_by(Action.linked_cost.desc()).limit(500))
    ).all()
    return [await _out(session, action, identity) for action in rows]


@router.post("", response_model=ActionOut, dependencies=[Depends(require(*list(Role)))])
async def create(
    payload: ActionCreateRequest,
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """§5.2 third trigger — anyone can raise an Action from any observation.

    This is also where Kaizen ideas enter: a near-miss a supervisor noticed is
    worth exactly as much tracking as one the threshold rules found.
    """
    await in_plant(session, Station, payload.station_id, plant.id, "Station")
    await in_plant(session, ReasonCode, payload.reason_code_id, plant.id, "Reason code")

    action = Action(
        plant_id=plant.id,
        reference=await action_service._next_reference(session, plant.id),
        title=payload.title,
        description=payload.description,
        trigger=ActionTrigger.MANUAL,
        trigger_rationale=f"Raised manually by {identity.display_name}",
        state=PdcaState.OPEN,
        station_id=payload.station_id,
        reason_code_id=payload.reason_code_id,
        owner_name=payload.owner_name,
        due_date=payload.due_date,
        surfaced_in_meeting_on=plant_today(plant),
    )
    session.add(action)
    await session.flush()

    action.baseline_weekly_loss = await action_service.compute_baseline(
        session,
        plant_id=plant.id,
        station_id=action.station_id,
        reason_code_id=action.reason_code_id,
        as_of=plant_today(plant),
    )
    action.baseline_frozen_at = datetime.now(UTC)

    out = await _out(session, action, identity)
    await manager.publish(plant.id, Event(ACTION_CREATED, out.model_dump(mode="json")))
    return out


@router.patch("/{action_id}", response_model=ActionOut)
async def update(
    action_id: uuid.UUID,
    payload: ActionUpdateRequest,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    """§4.3 — whoever owns the Action updates it as work happens.

    The state moves only along :data:`app.kernel.MANUAL_TRANSITIONS`. CHECK is
    entered by closing with the countermeasure and the Factory Memory answers;
    Verified and Reopened are set by the system after measuring what happened.
    """
    action = await _load(session, identity, action_id)

    if payload.meeting_status == "delayed" and not (payload.delay_reason or "").strip():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "A delayed action needs a reason — that is the whole discipline.",
        )

    if payload.state and payload.state != action.state.value:
        try:
            target = KernelPdcaState(payload.state)
        except ValueError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"Unknown state {payload.state!r}"
            ) from None
        if target.value not in _allowed_transitions(action, identity):
            raise HTTPException(
                status.HTTP_409_CONFLICT, _transition_refusal(action, target)
            )
        action.state = PdcaState(target.value)

    await require_member(session, payload.owner_id, identity.plant_id)
    for field in ("owner_id", "owner_name", "due_date", "root_cause", "countermeasure"):
        value = getattr(payload, field)
        if value is not None:
            setattr(action, field, value)

    # §5.3 gate — the morning meeting cannot advance while yesterday's escalated
    # Actions sit unaddressed. "Delayed" is a legitimate answer; silence is not.
    if payload.meeting_status:
        plant = await session.get(Plant, identity.plant_id)
        action.meeting_status = payload.meeting_status
        action.meeting_status_on = plant_today(plant)
        action.delay_reason = payload.delay_reason

    await session.flush()
    return await _out(session, action, identity)


@router.post("/{action_id}/close", response_model=ActionOut)
async def close(
    action_id: uuid.UUID,
    payload: ActionCloseRequest,
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Closure moves the Action to CHECK — monitoring — never straight to done.

    The four Factory Memory questions are mandatory here. Sixty seconds from the
    person who actually fixed it, once, while they still remember.
    """
    action = await _load(session, identity, action_id)
    if action.closed_at is not None and action.state is not PdcaState.REOPENED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Action is already closed")
    if KernelPdcaState(action.state.value) not in CLOSABLE_STATES:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"A {action.state.value} action cannot be closed"
        )
    # §5.5 — reason codes are not root causes. An Action routed to RCA stays
    # open, visibly, until the root cause is known.
    if action.routed_to_rca and not ((payload.root_cause or "").strip() or action.root_cause):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Record the root cause (or finish the RCA) before closing.",
        )

    await action_service.close_action(
        session,
        plant=plant,
        action=action,
        root_cause=payload.root_cause,
        countermeasure=payload.countermeasure,
        memory=action_service.MemoryCapture(
            what_fixed_it=payload.memory.what_fixed_it,
            what_was_replaced=payload.memory.what_was_replaced,
            photo_url=payload.memory.photo_url,
            check_first_next_time=payload.memory.check_first_next_time,
        ),
        closed_by_name=identity.display_name,
    )
    return await _out(session, action, identity)


@router.get("/{action_id}/rca")
async def get_rca(
    action_id: uuid.UUID,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    """The 5-Why template, pre-filled with the event history.

    Pre-filling matters: a team that opens a blank form at 8 AM closes it again.
    A form that already says "3 occurrences, ₹4,760, last three Tuesdays" gets
    filled in.
    """
    await _load(session, identity, action_id)
    rca = await session.scalar(
        select(RootCauseAnalysis).where(RootCauseAnalysis.action_id == action_id)
    )
    if rca is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "This action did not meet the RCA severity bar"
        )
    return {
        "id": str(rca.id),
        "assigned_team": rca.assigned_team,
        "due_date": rca.due_date,
        "five_whys": rca.five_whys,
        "prefilled_context": rca.prefilled_context,
        "ai_suggested_causes": rca.ai_suggested_causes,
        "conclusion": rca.conclusion,
        "completed_at": rca.completed_at,
    }


@router.patch("/{action_id}/rca")
async def update_rca(
    action_id: uuid.UUID,
    payload: RcaUpdateRequest,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    action = await _load(session, identity, action_id)
    rca = await session.scalar(
        select(RootCauseAnalysis).where(RootCauseAnalysis.action_id == action_id)
    )
    if rca is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No RCA for this action")

    rca.five_whys = payload.five_whys
    if payload.assigned_team:
        rca.assigned_team = payload.assigned_team
    if payload.due_date:
        rca.due_date = payload.due_date
    if payload.conclusion:
        rca.conclusion = payload.conclusion
        rca.completed_at = datetime.now(UTC)
        action.root_cause = payload.conclusion
        # Root cause found: PLAN moves on to the countermeasure — unless the work
        # is already under way or closed, which a late RCA must not rewind.
        if action.state in (PdcaState.OPEN, PdcaState.ROOT_CAUSE, PdcaState.REOPENED):
            action.state = PdcaState.PLANNED

    await session.flush()
    return {"status": "saved"}


@router.get("/memory", response_model=list[MemoryEntryOut])
async def memory(
    q: str | None = None,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    """Factory Memory — 'have we seen this before?'

    A problem that would have taken two days of asking around is answered in
    thirty seconds, by anyone, including someone who joined last week.
    """
    query = select(MemoryEntry).where(MemoryEntry.plant_id == identity.plant_id)
    if q:
        pattern = f"%{q}%"
        query = query.where(
            MemoryEntry.what_fixed_it.ilike(pattern)
            | MemoryEntry.problem_summary.ilike(pattern)
            | MemoryEntry.check_first_next_time.ilike(pattern)
        )

    rows = (
        await session.scalars(query.order_by(MemoryEntry.occurred_on.desc()).limit(50))
    ).all()

    stations = {
        s.id: s.code
        for s in (
            await session.scalars(
                select(Station).where(Station.plant_id == identity.plant_id)
            )
        ).all()
    }
    return [
        MemoryEntryOut(
            id=row.id,
            problem_summary=row.problem_summary,
            what_fixed_it=row.what_fixed_it,
            what_was_replaced=row.what_was_replaced,
            check_first_next_time=row.check_first_next_time,
            fixed_by_name=row.fixed_by_name,
            occurred_on=row.occurred_on,
            station_code=stations.get(row.station_id),
        )
        for row in rows
    ]


@router.get("/{action_id}", response_model=ActionOut)
async def get_action(
    action_id: uuid.UUID,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    """One Action — declared after ``/memory`` so that path is not read as an id."""
    return await _out(
        session, await _load(session, identity, action_id), identity, with_photos=True
    )


async def _load(session: AsyncSession, identity: Identity, action_id: uuid.UUID) -> Action:
    action = await session.get(Action, action_id)
    if action is None or action.plant_id != identity.plant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Action not found")
    return action


async def _out(
    session: AsyncSession,
    action: Action,
    identity: Identity | None = None,
    *,
    with_photos: bool = False,
) -> ActionOut:
    station = await session.get(Station, action.station_id) if action.station_id else None
    reason = (
        await session.get(ReasonCode, action.reason_code_id)
        if action.reason_code_id
        else None
    )
    state = KernelPdcaState(action.state.value)
    plant = await session.get(Plant, action.plant_id)
    today = plant_today(plant)
    photos = [] if not with_photos else (
        await session.scalars(
            select(Attachment)
            .where(
                Attachment.plant_id == action.plant_id,
                Attachment.entity_type == "action",
                Attachment.entity_id == action.id,
            )
            .order_by(Attachment.created_at)
        )
    ).all()
    return ActionOut(
        id=action.id,
        reference=action.reference,
        title=action.title,
        description=action.description,
        trigger=action.trigger.value,
        state=action.state.value,
        pdca_phase=PDCA_PHASE.get(state, ""),
        station_code=station.code if station else None,
        reason_label=reason.label if reason else None,
        linked_cost_paise=action.linked_cost,
        linked_cost_display=format_inr(action.linked_cost),
        occurrence_count=action.occurrence_count,
        routed_to_rca=action.routed_to_rca,
        severity_reasons=list(action.severity_reasons or []),
        owner_name=action.owner_name,
        due_date=action.due_date,
        root_cause=action.root_cause,
        countermeasure=action.countermeasure,
        evidence_photo_url=action.evidence_photo_url,
        baseline_weekly_loss_paise=action.baseline_weekly_loss,
        recovered_profit_paise=action.recovered_profit,
        recovered_profit_display=format_inr(action.recovered_profit),
        sustained_weeks=action.sustained_weeks,
        is_overdue=bool(
            action.due_date and action.due_date < today and action.state in OPEN_STATES
        ),
        closed_at=action.closed_at,
        meeting_status_on=action.meeting_status_on,
        photos=[
            {"id": str(photo.id), "url": f"/api/v1/attachments/{photo.id}"} for photo in photos
        ],
        allowed_transitions=_allowed_transitions(action, identity) if identity else [],
        can_close=state in CLOSABLE_STATES,
        created_at=action.created_at,
    )


def _allowed_transitions(action: Action, identity: Identity) -> list[str]:
    state = KernelPdcaState(action.state.value)
    lead = identity.has(*LEAD_ROLES)
    return sorted(
        target.value
        for target in MANUAL_TRANSITIONS.get(state, frozenset())
        if lead or target not in LEAD_ONLY_TARGETS
    )


def _transition_refusal(action: Action, target: KernelPdcaState) -> str:
    if target in (KernelPdcaState.MONITORING,):
        return "Close the action with its countermeasure to start monitoring."
    if target in (KernelPdcaState.VERIFIED, KernelPdcaState.REOPENED):
        return (
            "Verified and reopened are set by the system after it measures "
            "whether the losses actually stopped."
        )
    if target in LEAD_ONLY_TARGETS:
        return "Only the owner or a supervisor can cancel or restore an action."
    return f"An action cannot move from {action.state.value} to {target.value}."
