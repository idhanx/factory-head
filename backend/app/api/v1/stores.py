"""The stores in-charge's desk (§1).

    Stores In-charge | Receives material-shortage alerts. Confirms material
    staging. | When events occur.

One GET that answers "which line is waiting on me, and what is it costing", and
one POST that is §1's second duty. §6 is the argument for both: at 5:45 AM the
Coach asks Stores to confirm Job #4471's material is at Line 2 before 8:00, and
at 10:40 AM Press-2 is stopped on a material wait with a rupee counter climbing
because nobody did.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Identity, get_identity, get_plant, get_session, require
from app.api.scoping import in_plant
from app.kernel import format_inr
from app.models import JobOrder, Plant, Role, StagingStatus
from app.schemas import (
    OpenStopOut,
    StagingConfirmRequest,
    StagingRowOut,
    StoresDeskOut,
)
from app.services import escalations as escalation_service
from app.services import maintenance as maintenance_service
from app.services import stores as service
from app.services.clock import plant_today

router = APIRouter(prefix="/stores", tags=["stores"])

#: The supervisor is here because on a 20-machine plant the same person often
#: stages the material; the owner and consultant because §7's drill-down and
#: week-1 training both have to reach a real screen.
DESK = (Role.STORES, Role.SUPERVISOR, Role.OWNER, Role.CONSULTANT)


@router.get("/desk", response_model=StoresDeskOut, dependencies=[Depends(require(*DESK))])
async def desk(
    business_date: date | None = None,
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Alerts, lines stopped on material now, and today's staging queue."""
    day = business_date or plant_today(plant)

    alerts = await escalation_service.open_for_route(session, plant.id, route="stores")

    waits = await service.open_material_waits(session, plant.id)
    open_waits: list[OpenStopOut] = []
    for event, station, reason in waits:
        minutes, cost = maintenance_service.live_cost(event)
        open_waits.append(
            OpenStopOut(
                event_id=event.id,
                station_id=station.id,
                station_code=station.code,
                station_name=station.name,
                reason_code=reason.code,
                reason_label=reason.label,
                is_breakdown=reason.is_breakdown,
                started_at=event.started_at,
                minutes=int(minutes),
                cost_paise=cost,
                cost_display=format_inr(cost),
                note=event.note,
                escalation_level=event.escalation_level,
            )
        )

    queue = await service.staging_queue(session, plant, business_date=day)
    names = await escalation_service.names_for(
        session, {row.staging.confirmed_by_id for row in queue if row.staging}
    )

    wait_cost = sum(row.wait_cost_paise for row in queue)
    return StoresDeskOut(
        business_date=day,
        alerts=alerts,
        open_waits=open_waits,
        queue=[_row_out(row, names) for row in queue],
        pending_count=sum(1 for row in queue if row.status is StagingStatus.PENDING),
        wait_cost_today_paise=wait_cost,
        wait_cost_today_display=format_inr(wait_cost),
    )


@router.post(
    "/staging",
    response_model=StagingRowOut,
    dependencies=[Depends(require(Role.STORES, Role.SUPERVISOR, Role.OWNER))],
)
async def confirm_staging(
    payload: StagingConfirmRequest,
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """§1 — "Confirms material staging." One tap, or a number when it is short.

    Answering 'short' is not a failure to record: it is the single most useful
    thing this screen produces, because it puts a named, timestamped warning
    against a job *before* the line stops on it. §5.2's recurring-pattern rule
    then has something to point at when the same job short-stages three times
    in five shifts — which is exactly §6's second Coach priority.
    """
    job_order = await in_plant(
        session, JobOrder, payload.job_order_id, plant.id, "Job order"
    )
    try:
        staging = await service.confirm_staging(
            session,
            plant,
            job_order=job_order,
            status=StagingStatus(payload.status),
            quantity_staged=payload.quantity_staged,
            note=payload.note,
            user=identity.user,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    # Re-read through the same query the desk uses, so the row the client gets
    # back is the row it would see on a refresh — including the wait cost.
    queue = await service.staging_queue(session, plant, business_date=staging.business_date)
    row = next((r for r in queue if r.job_order.id == job_order.id), None)
    if row is None:  # pragma: no cover - the job was just resolved in this plant
        raise HTTPException(status.HTTP_409_CONFLICT, "That job order is no longer live")
    return _row_out(row, {identity.user.id: identity.display_name})


def _row_out(row: service.StagingRow, names: dict) -> StagingRowOut:
    staging = row.staging
    return StagingRowOut(
        job_order_id=row.job_order.id,
        job_number=row.job_order.job_number,
        part_number=row.part.part_number,
        part_description=row.part.description,
        customer_name=row.job_order.customer_name,
        planned_quantity=row.job_order.planned_quantity,
        station_codes=row.station_codes,
        status=row.status.value,
        quantity_required=staging.quantity_required if staging else None,
        quantity_staged=staging.quantity_staged if staging else None,
        note=staging.note if staging else None,
        confirmed_by_name=names.get(staging.confirmed_by_id) if staging else None,
        confirmed_at=staging.confirmed_at if staging else None,
        wait_events=row.wait_events,
        wait_cost_paise=row.wait_cost_paise,
        wait_cost_display=format_inr(row.wait_cost_paise),
    )
