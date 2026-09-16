"""Traceability (§3) and stage tracking (§14.2)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Identity, get_identity, get_plant, get_session, require
from app.models import Plant, Role, Station
from app.schemas import DispatchRequest, StageRequest
from app.services import traceability as trace_service

router = APIRouter(prefix="/trace", tags=["traceability"])


@router.get("/search")
async def search(
    q: str,
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """By lot code, job number, part number or customer — whatever is on the complaint."""
    return await trace_service.search(session, plant=plant, q=q)


@router.get("/lots/{lot_code}")
async def trace(
    lot_code: str,
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """The whole story of one lot — §3's "answer in seconds"."""
    lot = await trace_service.find_lot(session, plant, lot_code)
    if lot is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No lot {lot_code!r} in this plant")
    return await trace_service.trace_lot(session, plant=plant, lot=lot)


@router.get("/wip")
async def wip(
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Lots still moving, and the stage each one is waiting at."""
    return await trace_service.wip_board(session, plant=plant)


@router.post(
    "/stages",
    dependencies=[Depends(require(Role.SUPERVISOR, Role.QUALITY, Role.OWNER))],
)
async def record_stage(
    payload: StageRequest,
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    lot = await trace_service.find_lot(session, plant, payload.lot_code)
    if lot is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No lot {payload.lot_code!r}")

    station = None
    if payload.station_id:
        station = await session.get(Station, payload.station_id)
        if station is None or station.plant_id != plant.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Station not found")

    try:
        await trace_service.record_stage(
            session,
            plant=plant,
            lot=lot,
            operation_code=payload.operation_code,
            quantity_completed=payload.quantity_completed,
            quantity_rejected=payload.quantity_rejected,
            station=station,
        )
    except trace_service.TraceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    # Return the lot's flow as it now stands, so the screen can say at once
    # whether the count just entered made the numbers disagree.
    flow = (await trace_service.trace_lot(session, plant=plant, lot=lot))["flow"]
    return {"lot_code": lot.lot_code, "flow": flow}


@router.post(
    "/dispatch",
    dependencies=[Depends(require(Role.SUPERVISOR, Role.STORES, Role.OWNER))],
)
async def record_dispatch(
    payload: DispatchRequest,
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    lot = await trace_service.find_lot(session, plant, payload.lot_code)
    if lot is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No lot {payload.lot_code!r}")
    try:
        await trace_service.record_dispatch(
            session,
            plant=plant,
            lot=lot,
            quantity=payload.quantity,
            vehicle_reference=payload.vehicle_reference,
        )
    except trace_service.TraceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    flow = (await trace_service.trace_lot(session, plant=plant, lot=lot))["flow"]
    return {"lot_code": lot.lot_code, "flow": flow}
