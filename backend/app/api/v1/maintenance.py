"""The maintenance in-charge's desk (§1).

    Maintenance In-charge | Receives escalation alerts. Logs what was done to
    fix breakdowns. | When events occur.

One GET that answers "what is mine right now", and one POST that is §1's second
duty. Nothing else: a role whose cadence is "when events occur" does not need a
reporting surface, and every KPI this person could want already exists on the
Maintenance tab of `/kpis` (§8's "one executive page, drill-down per role").
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Identity, get_identity, get_plant, get_session, require
from app.api.scoping import in_plant
from app.kernel import format_inr
from app.models import DowntimeEvent, Plant, Role, Station, User
from app.schemas import (
    ClosedBreakdownOut,
    MaintenanceDeskOut,
    OpenStopOut,
    RepairLogRequest,
)
from app.services import escalations as escalation_service
from app.services import maintenance as service

router = APIRouter(prefix="/maintenance", tags=["maintenance"])

#: Who may open the desk. The supervisor is here because on a small plant the
#: same person is often both, and the owner because §7's drill-down has to end
#: somewhere real. The consultant sees it during week-1 training.
DESK = (Role.MAINTENANCE, Role.SUPERVISOR, Role.OWNER, Role.CONSULTANT)


@router.get("/desk", response_model=MaintenanceDeskOut, dependencies=[Depends(require(*DESK))])
async def desk(
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Alerts, live stops and unwritten repairs — ranked by rupees, not by time."""
    alerts = await escalation_service.open_for_route(session, plant.id, route="maintenance")

    stops = await service.open_stops(session, plant.id)
    repairers = await escalation_service.names_for(
        session, {stop.event.repaired_by_id for stop in stops}
    )

    breakdowns = [
        _stop_out(stop, repairers) for stop in stops if stop.reason and stop.reason.is_breakdown
    ]
    others = [
        _stop_out(stop, repairers)
        for stop in stops
        if not (stop.reason and stop.reason.is_breakdown)
    ]

    outstanding = await service.repairs_outstanding(session, plant)
    open_cost = sum(stop.cost_paise for stop in stops)

    return MaintenanceDeskOut(
        alerts=alerts,
        open_breakdowns=breakdowns,
        other_open_stops=others,
        awaiting_repair_note=[
            ClosedBreakdownOut(
                event_id=event.id,
                station_code=station.code,
                station_name=station.name,
                reason_label=reason.label if reason else None,
                started_at=event.started_at,
                ended_at=event.ended_at,
                minutes=int(event.duration_minutes or 0),
                cost_paise=event.cost_total or 0,
                cost_display=format_inr(event.cost_total or 0),
            )
            for event, station, reason in outstanding
        ],
        open_cost_paise=open_cost,
        open_cost_display=format_inr(open_cost),
    )


@router.post(
    "/breakdowns/{event_id}/repair",
    response_model=OpenStopOut,
    dependencies=[Depends(require(Role.MAINTENANCE, Role.SUPERVISOR, Role.OWNER))],
)
async def log_repair(
    event_id: uuid.UUID,
    payload: RepairLogRequest,
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """§1 — "Logs what was done to fix breakdowns."

    Note the one thing this is *not* gated on: whether the stop is still open.
    A fitter records the fix at the machine, and forcing them to wait for the
    supervisor's 'Running' tap is how the note stops being written at all.

    It is gated on the shift still being open. §2's Shift Closure Lock means a
    closed shift is arithmetically frozen — and although a repair note changes
    no number, letting text be appended to a locked shift would make "locked"
    mean two different things, which is exactly the crack tamper-proofing dies
    through. After close, the record is amended through the corrections trail
    like every other post-lock change.
    """
    event = await in_plant(session, DowntimeEvent, event_id, plant.id, "Event")
    await _reject_if_locked(session, event)

    try:
        updated = await service.log_repair(
            session,
            event=event,
            note=payload.note,
            part_changed=payload.part_changed,
            user=identity.user,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    station = await session.get(Station, updated.station_id)
    reason = await session.get(ReasonCode, updated.reason_code_id) if updated.reason_code_id else None

    # A stop that is still running keeps its live ticker; a closed one reports
    # the frozen figures the kernel computed at close, never a recomputation.
    if updated.status is DowntimeStatus.OPEN:
        minutes, cost = service.live_cost(updated)
    else:
        minutes, cost = updated.duration_minutes or 0, updated.cost_total or 0

    return OpenStopOut(
        event_id=updated.id,
        station_id=station.id,
        station_code=station.code,
        station_name=station.name,
        reason_code=reason.code if reason else None,
        reason_label=reason.label if reason else None,
        is_breakdown=bool(reason and reason.is_breakdown),
        started_at=updated.started_at,
        minutes=int(minutes),
        cost_paise=int(cost),
        cost_display=format_inr(int(cost)),
        note=updated.note,
        repair_note=updated.repair_note,
        repair_part_changed=updated.repair_part_changed,
        repaired_by_name=identity.display_name,
        repaired_at=updated.repaired_at,
        escalation_level=updated.escalation_level,
    )


async def _reject_if_locked(session: AsyncSession, event: DowntimeEvent) -> None:
    from app.models import ShiftSession, ShiftSessionStatus

    if event.shift_session_id is None:
        return
    shift_session = await session.get(ShiftSession, event.shift_session_id)
    if shift_session is not None and shift_session.status is ShiftSessionStatus.CLOSED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "That shift is closed — raise a correction instead",
        )


def _stop_out(stop: service.OpenStop, repairers: dict[uuid.UUID, str]) -> OpenStopOut:
    event = stop.event
    return OpenStopOut(
        event_id=event.id,
        station_id=stop.station.id,
        station_code=stop.station.code,
        station_name=stop.station.name,
        reason_code=stop.reason.code if stop.reason else None,
        reason_label=stop.reason.label if stop.reason else None,
        is_breakdown=bool(stop.reason and stop.reason.is_breakdown),
        started_at=event.started_at,
        minutes=int(stop.minutes),
        cost_paise=stop.cost_paise,
        cost_display=format_inr(stop.cost_paise),
        note=event.note,
        repair_note=event.repair_note,
        repair_part_changed=event.repair_part_changed,
        repaired_by_name=repairers.get(event.repaired_by_id),
        repaired_at=event.repaired_at,
        escalation_level=event.escalation_level,
    )


__all__ = ["router", "User"]
