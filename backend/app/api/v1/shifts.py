"""Screens 1 and 6 — shift start and the closure lock."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Identity, get_identity, get_plant, get_session, require
from app.api.scoping import in_plant
from app.models import (
    AuditLog,
    JobOrder,
    JobOrderStatus,
    JobSession,
    Operator,
    Plant,
    Role,
    Shift,
    ShiftSession,
    Station,
)
from app.schemas import (
    BlockerOut,
    ClosureCheckOut,
    ShiftCloseRequest,
    ShiftSessionOut,
    ShiftStartRequest,
)
from app.services import actions as action_service
from app.services import notifications
from app.services import reports as report_service
from app.services import shifts as shift_service
from app.services.costing import resolve_routing_operation
from app.services.lots import ensure_lot
from app.ws.manager import SHIFT_CLOSED, Event, manager

router = APIRouter(prefix="/shifts", tags=["shifts"])


@router.get("/current", response_model=ShiftSessionOut | None)
async def current(
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    return await shift_service.current_open_session(session, identity.plant_id)


@router.post(
    "/start",
    response_model=ShiftSessionOut,
    dependencies=[Depends(require(Role.SUPERVISOR, Role.OWNER))],
)
async def start(
    payload: ShiftStartRequest,
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Screen 1 — under 60 seconds.

    Job orders are assigned to stations here, which is the moment every later
    event in the shift acquires its part, customer, lot and operator for free.
    """
    shift = await session.get(Shift, payload.shift_id)
    if shift is None or shift.plant_id != identity.plant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shift not found")

    shift_session = await shift_service.start_shift(
        session, plant=plant, shift=shift, supervisor_id=identity.user.id
    )

    for assignment in payload.assignments:
        await _assign_job(
            session,
            plant=plant,
            shift_session=shift_session,
            assignment=assignment,
        )

    return shift_session


@router.get("/{shift_session_id}/closure-check", response_model=ClosureCheckOut)
async def closure_check(
    shift_session_id: uuid.UUID,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    """The checklist standing between the supervisor and going home."""
    shift_session = await _load(session, identity, shift_session_id)
    check = await shift_service.closure_blockers(session, shift_session)
    return ClosureCheckOut(
        can_close=check.can_close,
        blockers=[
            BlockerOut(kind=b.kind, detail=b.detail, reference_id=b.reference_id)
            for b in check.blockers
        ],
    )


@router.post(
    "/{shift_session_id}/close",
    response_model=ShiftSessionOut,
    dependencies=[Depends(require(Role.SUPERVISOR, Role.OWNER))],
)
async def close(
    shift_session_id: uuid.UUID,
    payload: ShiftCloseRequest,
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Lock the shift, then evaluate which Actions it earned.

    Blockers are enforced here, not in the UI: an offline tablet that skipped
    the check must still be refused. An owner may override, and the override is
    written to the audit log with its reason — visible in the monthly report.
    """
    shift_session = await _load(session, identity, shift_session_id)
    if shift_session.locked_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Shift is already closed")

    check = await shift_service.closure_blockers(session, shift_session)
    if not check.can_close:
        if not payload.override_reason:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {
                    "message": "Resolve these before closing, or request an owner override.",
                    "blockers": [
                        {"kind": b.kind, "detail": b.detail, "reference_id": b.reference_id}
                        for b in check.blockers
                    ],
                },
            )
        if not identity.has(Role.OWNER, Role.ADMIN):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Only the owner can override the shift closure lock",
            )
        session.add(
            AuditLog(
                plant_id=plant.id,
                actor_id=identity.user.id,
                entity_type="shift_session",
                entity_id=shift_session.id,
                action="override",
                reason=payload.override_reason,
                after={
                    "blockers": [
                        {"kind": b.kind, "detail": b.detail} for b in check.blockers
                    ]
                },
                occurred_at=datetime.now(UTC),
            )
        )

    await shift_service.close_shift(
        session,
        shift_session=shift_session,
        closed_by_id=identity.user.id,
        override_reason=payload.override_reason,
        override_by_id=identity.user.id if payload.override_reason else None,
    )

    created = await action_service.evaluate_shift_close(
        session, plant=plant, shift_session=shift_session
    )

    # §4.4 — "End-of-shift cost report: ranked losses in ₹ … owner's phone, at shift close."
    report = await report_service.shift_report_text(
        session, plant=plant, shift_session=shift_session
    )
    await notifications.enqueue(
        session,
        plant=plant,
        kind="shift_report",
        recipients=await notifications.owners(session, plant.id),
        title=f"{report['shift_name']} closed",
        body=report["text"],
        url="/owner",
        subject_type="shift_session",
        subject_id=shift_session.id,
        dedupe_key=f"shift:{shift_session.id}",
    )

    await manager.publish(
        plant.id,
        Event(
            SHIFT_CLOSED,
            {
                "shift_session_id": str(shift_session.id),
                "business_date": shift_session.business_date.isoformat(),
                "actions_created": len(created),
            },
        ),
    )
    return shift_session


@router.get("/history", response_model=list[ShiftSessionOut])
async def history(
    since: date | None = None,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    query = select(ShiftSession).where(ShiftSession.plant_id == identity.plant_id)
    if since:
        query = query.where(ShiftSession.business_date >= since)
    return (
        await session.scalars(query.order_by(ShiftSession.business_date.desc()).limit(60))
    ).all()


async def _assign_job(
    session: AsyncSession, *, plant: Plant, shift_session: ShiftSession, assignment
) -> JobSession:
    job_order = await in_plant(session, JobOrder, assignment.job_order_id, plant.id, "Job order")
    station = await in_plant(session, Station, assignment.station_id, plant.id, "Station")
    await in_plant(session, Operator, assignment.operator_id, plant.id, "Operator")
    if job_order.status in (JobOrderStatus.COMPLETED, JobOrderStatus.CANCELLED):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Job {job_order.job_number} is {job_order.status.value}",
        )

    lot = await ensure_lot(
        session,
        plant=plant,
        job_order=job_order,
        lot_code=assignment.lot_code,
        business_date=shift_session.business_date,
    )

    # The routing step this station performs is what OEE's Performance term
    # measures the station against; without it a shift reads 0% OEE.
    operation = await resolve_routing_operation(
        session, plant_id=plant.id, part_id=job_order.part_id, station=station
    )
    job_session = JobSession(
        plant_id=plant.id,
        job_order_id=job_order.id,
        shift_session_id=shift_session.id,
        station_id=station.id,
        lot_id=lot.id if lot else None,
        routing_operation_id=operation.id if operation else None,
        operator_id=assignment.operator_id,
        started_at=datetime.now(UTC),
    )
    session.add(job_session)

    if job_order.status is JobOrderStatus.PLANNED:
        job_order.status = JobOrderStatus.RUNNING
        job_order.opened_at = job_order.opened_at or datetime.now(UTC)

    await session.flush()
    return job_session


async def _load(
    session: AsyncSession, identity: Identity, shift_session_id: uuid.UUID
) -> ShiftSession:
    shift_session = await session.get(ShiftSession, shift_session_id)
    if shift_session is None or shift_session.plant_id != identity.plant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shift session not found")
    return shift_session
