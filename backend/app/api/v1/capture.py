"""Screens 2-5 — the machine grid and the three capture actions.

Everything here is budgeted in seconds, because §4.3's fifteen-minute daily
entry budget is sacred: "if the burden creeps above that, adoption dies."
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Identity, get_identity, get_plant, get_session, require
from app.api.scoping import check_event_time, in_plant
from app.kernel import format_inr
from app.models import (
    DefectCode,
    Disposition,
    DowntimeEvent,
    DowntimeStatus,
    JobOrder,
    JobOrderStatus,
    JobSession,
    Lot,
    Operator,
    Part,
    Plant,
    ReasonCode,
    Role,
    ShiftSession,
    Station,
)
from app.schemas import (
    ChangeJobRequest,
    CostBreakdownOut,
    DowntimeCloseRequest,
    DowntimeEventOut,
    DowntimeStartRequest,
    ProductionOut,
    ProductionRequest,
    QualityOut,
    QualityRequest,
    StationTileOut,
)
from app.services import capture as capture_service
from app.services import downtime as downtime_service
from app.services import notifications
from app.services import reports as report_service
from app.services import shifts as shift_service
from app.services.costing import resolve_routing_operation
from app.services.lots import ensure_lot
from app.ws.manager import (
    MACHINE_STARTED,
    MACHINE_STOPPED,
    PRODUCTION_RECORDED,
    QUALITY_RECORDED,
    Event,
    manager,
)

router = APIRouter(tags=["capture"])


# ---------------------------------------------------------------------------
# Screen 2 — the machine grid
# ---------------------------------------------------------------------------


@router.get("/grid", response_model=list[StationTileOut])
async def machine_grid(
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """One tile per machine. Green running, red stopped, live ₹ on any open stop."""
    shift_session = await shift_service.current_open_session(session, plant.id)
    tiles = await report_service.machine_grid(
        session, plant=plant, shift_session=shift_session
    )
    return [
        StationTileOut(
            station_id=t.station_id,
            code=t.code,
            name=t.name,
            line=t.line,
            status=t.status,
            count=t.count,
            target=t.target,
            job_number=t.job_number,
            part_number=t.part_number,
            open_event_id=t.open_event_id,
            stopped_since=t.stopped_since,
            stopped_minutes=float(t.stopped_minutes),
            reason_label=t.reason_label,
            live_cost_paise=t.live_cost,
            live_cost_display=format_inr(t.live_cost),
            ticker_rate_per_minute=float(t.ticker_rate_per_minute),
            shift_cost_paise=t.shift_cost,
        )
        for t in tiles
    ]


# ---------------------------------------------------------------------------
# Screen 3 — downtime log
# ---------------------------------------------------------------------------


@router.post(
    "/downtime/start",
    response_model=DowntimeEventOut,
    dependencies=[Depends(require(Role.SUPERVISOR, Role.MAINTENANCE, Role.OWNER))],
)
async def start_downtime(
    payload: DowntimeStartRequest,
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """'Machine Stopped'. Twenty seconds, and the rupee ticker starts."""
    shift_session = await shift_service.current_open_session(session, plant.id)
    if shift_session is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "No shift is open — start the shift first"
        )
    return await start_stop(
        session,
        identity,
        plant,
        station_id=payload.station_id,
        reason_code_id=payload.reason_code_id,
        note=payload.note,
        shift_session=shift_session,
        started_at=payload.started_at,
        client_ref=payload.client_ref,
    )


@router.post(
    "/downtime/{event_id}/close",
    response_model=DowntimeEventOut,
    dependencies=[Depends(require(Role.SUPERVISOR, Role.MAINTENANCE, Role.OWNER))],
)
async def close_downtime(
    event_id: uuid.UUID,
    payload: DowntimeCloseRequest,
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """'Running' — one tap. The final cost is frozen here."""
    event = await in_plant(session, DowntimeEvent, event_id, plant.id, "Event")
    if event.status is DowntimeStatus.CLOSED:
        if payload.client_ref:
            # A retried tap from a tablet that lost its connection mid-request.
            return await _event_out(session, event)
        raise HTTPException(status.HTTP_409_CONFLICT, "Event is already closed")
    await _reject_if_locked(session, event.shift_session_id)
    return await end_stop(
        session,
        identity,
        plant,
        event=event,
        reason_code_id=payload.reason_code_id,
        ended_at=payload.ended_at,
    )


async def start_stop(
    session: AsyncSession,
    identity: Identity,
    plant: Plant,
    *,
    station_id: uuid.UUID,
    reason_code_id: uuid.UUID | None,
    note: str | None,
    shift_session: ShiftSession,
    started_at: datetime | None,
    client_ref: str | None,
) -> DowntimeEventOut:
    """Record a stop — shared by the screen and the offline sync."""
    station = await _station(session, identity, station_id)
    reason = await in_plant(session, ReasonCode, reason_code_id, plant.id, "Reason code")
    event = await downtime_service.open_downtime(
        session,
        plant=plant,
        station=station,
        reason_code=reason,
        shift_session=shift_session,
        started_at=check_event_time(started_at, shift_session, label="Stop time"),
        note=note,
        logged_by_id=identity.user.id,
        client_ref=client_ref,
    )
    out = await _event_out(session, event)
    await manager.publish(plant.id, Event(MACHINE_STOPPED, out.model_dump(mode="json")))
    return out


async def end_stop(
    session: AsyncSession,
    identity: Identity,
    plant: Plant,
    *,
    event: DowntimeEvent,
    reason_code_id: uuid.UUID | None,
    ended_at: datetime | None,
) -> DowntimeEventOut:
    """Record a restart — shared by the screen and the offline sync."""
    reason = await in_plant(session, ReasonCode, reason_code_id, plant.id, "Reason code")
    if ended_at is not None and event.shift_session_id:
        event_shift = await session.get(ShiftSession, event.shift_session_id)
        if event_shift is not None:
            ended_at = check_event_time(ended_at, event_shift, label="Restart time")
    try:
        event = await downtime_service.close_downtime(
            session, event=event, ended_at=ended_at, reason_code=reason
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    out = await _event_out(session, event)
    await manager.publish(plant.id, Event(MACHINE_STARTED, out.model_dump(mode="json")))
    return out


@router.get("/downtime/open", response_model=list[DowntimeEventOut])
async def open_downtime(
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    events = (
        await session.scalars(
            select(DowntimeEvent)
            .where(
                DowntimeEvent.plant_id == identity.plant_id,
                DowntimeEvent.status == DowntimeStatus.OPEN,
            )
            .order_by(DowntimeEvent.started_at)
        )
    ).all()
    return [await _event_out(session, event) for event in events]


# ---------------------------------------------------------------------------
# Screen 4 — production entry
# ---------------------------------------------------------------------------


@router.post(
    "/production",
    response_model=ProductionOut,
    dependencies=[Depends(require(Role.SUPERVISOR, Role.OWNER))],
)
async def record_production(
    payload: ProductionRequest,
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Count since the last entry. Number pad, fifteen seconds."""
    shift_session = await shift_service.current_open_session(session, plant.id)
    if shift_session is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No shift is open")
    return await count_entry(
        session,
        identity,
        plant,
        station_id=payload.station_id,
        count=payload.count,
        shift_session=shift_session,
        recorded_at=payload.recorded_at,
        client_ref=payload.client_ref,
    )


async def count_entry(
    session: AsyncSession,
    identity: Identity,
    plant: Plant,
    *,
    station_id: uuid.UUID,
    count: int,
    shift_session: ShiftSession,
    recorded_at: datetime | None,
    client_ref: str | None,
):
    station = await _station(session, identity, station_id)
    row = await capture_service.record_production(
        session,
        plant=plant,
        station=station,
        shift_session=shift_session,
        count=count,
        recorded_at=check_event_time(recorded_at, shift_session, label="Count time"),
        logged_by_id=identity.user.id,
        client_ref=client_ref,
    )
    await manager.publish(
        plant.id,
        Event(PRODUCTION_RECORDED, {"station_id": str(station.id), "count": row.count}),
    )
    return row


# ---------------------------------------------------------------------------
# Screen 5 — quality entry (independent login, by design)
# ---------------------------------------------------------------------------


@router.post(
    "/quality",
    response_model=QualityOut,
    dependencies=[Depends(require(Role.QUALITY, Role.OWNER))],
)
async def record_quality(
    payload: QualityRequest,
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """The inspector's count, entered independently of the supervisor's.

    Restricted to the quality role on purpose: the cross-check is only worth
    anything if one person cannot enter both sides of it (§2).
    """
    shift_session = await shift_service.current_open_session(session, plant.id)
    if shift_session is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No shift is open")
    return await quality_entry(
        session,
        identity,
        plant,
        payload=payload,
        shift_session=shift_session,
        recorded_at=None,
    )


async def quality_entry(
    session: AsyncSession,
    identity: Identity,
    plant: Plant,
    *,
    payload: QualityRequest,
    shift_session: ShiftSession,
    recorded_at: datetime | None,
) -> QualityOut:
    """Record an inspection — shared by the screen and the offline sync."""
    part = await in_plant(session, Part, payload.part_id, plant.id, "Part")
    station = (
        await _station(session, identity, payload.station_id)
        if payload.station_id
        else None
    )
    await in_plant(session, Lot, payload.lot_id, plant.id, "Lot")

    good_count = payload.good_count
    reject_count, rework_count = payload.reject_count, payload.rework_count
    disposition = Disposition(payload.disposition)
    if disposition is Disposition.REWORK:
        # Older clients sent one disposition for the whole batch: every rejected
        # part was reworked to good.
        good_count += reject_count
        rework_count += reject_count
        reject_count = 0
        disposition = Disposition.SCRAP
    if rework_count > good_count:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Reworked parts are counted within the good parts, so they cannot exceed them",
        )

    await _check_defects(session, plant.id, payload.defects, reject_count + rework_count)

    record = await capture_service.record_quality(
        session,
        plant=plant,
        part=part,
        shift_session=shift_session,
        good_count=good_count,
        reject_count=reject_count,
        station=station,
        lot_id=payload.lot_id,
        rework_count=rework_count,
        rework_minutes=(
            Decimal(str(payload.rework_minutes)) if payload.rework_minutes else None
        ),
        disposition=disposition,
        defects=[d.model_dump() for d in payload.defects],
        inspector_id=identity.user.id,
        recorded_at=check_event_time(recorded_at, shift_session, label="Inspection time"),
        client_ref=payload.client_ref,
    )

    message = None
    if record.reconciliation_flagged:
        # §2: "If the two don't reconcile, the system flags it to the owner
        # automatically. Two people would have to collude to fake the numbers."
        queued = await notifications.enqueue(
            session,
            plant=plant,
            kind="reconciliation",
            recipients=await notifications.owners(session, plant.id),
            title="Counts do not reconcile",
            body=(
                f"{station.code if station else 'A station'}, {part.part_number}: the inspector's "
                f"count and the supervisor's production entries differ by "
                f"{record.reconciliation_gap} parts this shift."
            ),
            url="/quality",
            subject_type="quality_record",
            subject_id=record.id,
            dedupe_key=f"reconciliation:{record.id}",
        )
        message = (
            f"Count mismatch: parts inspected so far differ from the supervisor's "
            f"production entries by {record.reconciliation_gap}. "
            + (
                "The owner is being told."
                if notifications.reached_anyone(queued)
                else "Flagged for the owner's review."
            )
        )

    out = quality_out(record, message)
    await manager.publish(plant.id, Event(QUALITY_RECORDED, out.model_dump(mode="json")))
    return out


def quality_out(record, message: str | None = None) -> QualityOut:
    breakdown = record.cost_breakdown or {}
    rework_cost = int(breakdown.get("rework", 0))
    scrap_cost = (record.cost_total or 0) - rework_cost
    return QualityOut(
        id=record.id,
        part_id=record.part_id,
        good_count=record.good_count,
        reject_count=record.reject_count,
        rework_count=record.rework_count,
        scrap_cost_paise=scrap_cost,
        scrap_cost_display=format_inr(scrap_cost),
        rework_cost_paise=rework_cost,
        rework_cost_display=format_inr(rework_cost),
        reconciliation_gap=record.reconciliation_gap,
        reconciliation_flagged=record.reconciliation_flagged,
        reconciliation_message=message,
    )


# ---------------------------------------------------------------------------
# §14.3 — the mid-job part change
# ---------------------------------------------------------------------------


@router.post(
    "/change-job",
    dependencies=[Depends(require(Role.SUPERVISOR, Role.OWNER))],
)
async def change_job(
    payload: ChangeJobRequest,
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Switch a station to a different job mid-shift, without corrupting either.

    Job A pauses — its quantities, downtime and quality records to this second
    stay intact and locked to Job A. The gap until the first entry on Job B is
    recorded as Setup/Changeover downtime and costed like any other stop, which
    is how the plant gets changeover time measured for free.

    From the instant Job B starts, every count, stop and rejection on this
    station tags to Job B. Nobody can mis-record a part, because nobody types
    the part number — it is inherited from whichever job is active.
    """
    station = await _station(session, identity, payload.station_id)
    shift_session = await shift_service.current_open_session(session, plant.id)
    if shift_session is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No shift is open")

    # Everything is checked before Job A is touched: a refused change must
    # leave the station exactly as it was.
    new_job = await in_plant(session, JobOrder, payload.new_job_order_id, plant.id, "Job order")
    await in_plant(session, Operator, payload.operator_id, plant.id, "Operator")
    if new_job.status in (JobOrderStatus.COMPLETED, JobOrderStatus.CANCELLED):
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Job {new_job.job_number} is {new_job.status.value}"
        )

    now = datetime.now(UTC)
    previous = await session.scalar(
        select(JobSession)
        .where(
            JobSession.plant_id == plant.id,
            JobSession.station_id == station.id,
            JobSession.ended_at.is_(None),
        )
        .order_by(JobSession.started_at.desc())
        .limit(1)
    )
    if previous is not None:
        previous.ended_at = now
        previous.ended_by_change_job = True

    changeover_reason = await session.scalar(
        select(ReasonCode).where(
            ReasonCode.plant_id == plant.id, ReasonCode.code == "SETUP_CHANGEOVER"
        )
    )
    changeover = await downtime_service.open_downtime(
        session,
        plant=plant,
        station=station,
        reason_code=changeover_reason,
        shift_session=shift_session,
        started_at=now,
        note="Automatic changeover clock (Change Job)",
        logged_by_id=identity.user.id,
        source_ref=capture_service.CHANGEOVER_REF,
    )

    # Job B carries its own lot and routing step, exactly as if it had been
    # assigned at shift start — split quantities stay under one lot lineage.
    lot = await ensure_lot(
        session, plant=plant, job_order=new_job, lot_code=None,
        business_date=shift_session.business_date,
    )
    operation = await resolve_routing_operation(
        session, plant_id=plant.id, part_id=new_job.part_id, station=station
    )
    job_session = JobSession(
        plant_id=plant.id,
        job_order_id=new_job.id,
        shift_session_id=shift_session.id,
        station_id=station.id,
        lot_id=lot.id if lot else None,
        routing_operation_id=operation.id if operation else None,
        operator_id=payload.operator_id or (previous.operator_id if previous else None),
        started_at=now,
        previous_session_id=previous.id if previous else None,
    )
    session.add(job_session)
    if new_job.status is JobOrderStatus.PLANNED:
        new_job.status = JobOrderStatus.RUNNING
        new_job.opened_at = new_job.opened_at or now
    await session.flush()

    return {
        "job_session_id": str(job_session.id),
        "paused_session_id": str(previous.id) if previous else None,
        "changeover_event_id": str(changeover.id),
        "message": (
            "Changeover clock running. It stops by itself at the first count "
            "entered on the new job."
        ),
    }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _station(
    session: AsyncSession, identity: Identity, station_id: uuid.UUID
) -> Station:
    station = await session.get(Station, station_id)
    if station is None or station.plant_id != identity.plant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Station not found")
    return station


async def _check_defects(
    session: AsyncSession, plant_id, defects, defective_parts: int
) -> None:
    """Defect codes come from the plant's list, never free text (§4.1, T6)."""
    if not defects:
        return
    known = set(
        (
            await session.scalars(
                select(DefectCode.code).where(
                    DefectCode.plant_id == plant_id, DefectCode.is_active.is_(True)
                )
            )
        ).all()
    )
    unknown = sorted({d.code for d in defects} - known)
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Unknown defect code(s): {', '.join(unknown)}",
        )
    if sum(d.count for d in defects) > defective_parts:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Defect counts add up to more than the rejected and reworked parts",
        )


async def _reject_if_locked(session: AsyncSession, shift_session_id) -> None:
    """A locked shift is arithmetically frozen — §2, and the whole reason the
    numbers can be trusted."""
    if await shift_service.is_locked(session, shift_session_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "That shift is closed and locked. Ask the owner to log a correction.",
        )


async def _event_out(session: AsyncSession, event: DowntimeEvent) -> DowntimeEventOut:
    station = await session.get(Station, event.station_id)
    reason = (
        await session.get(ReasonCode, event.reason_code_id)
        if event.reason_code_id
        else None
    )
    breakdown = None
    if event.cost_breakdown:
        breakdown = CostBreakdownOut(
            machine_paise=event.cost_breakdown.get("machine", 0),
            labour_paise=event.cost_breakdown.get("labour", 0),
            overhead_paise=event.cost_breakdown.get("overhead", 0),
            lost_contribution_paise=event.cost_breakdown.get("lost_contribution", 0),
            parts_not_made=event.cost_breakdown.get("parts_not_made", 0),
        )

    return DowntimeEventOut(
        id=event.id,
        station_id=event.station_id,
        station_code=station.code if station else None,
        reason_code_id=event.reason_code_id,
        reason_label=reason.label if reason else None,
        started_at=event.started_at,
        ended_at=event.ended_at,
        duration_minutes=(
            float(event.duration_minutes) if event.duration_minutes else None
        ),
        status=event.status.value,
        note=event.note,
        cost_paise=event.cost_total,
        cost_display=format_inr(event.cost_total),
        breakdown=breakdown,
        source=event.source.value,
    )
