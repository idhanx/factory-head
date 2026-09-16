"""Lot identity for a job put on a station (§3)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import JobOrder, Lot, LotGranularity, Plant


async def ensure_lot(
    session: AsyncSession,
    *,
    plant: Plant,
    job_order: JobOrder,
    lot_code: str | None,
    business_date: date,
) -> Lot:
    """§3 — a plant with no lot discipline still gets traceability.

    If no lot is supplied we mint a day-batch code, which is "one rule,
    implementable on day one, and already better traceability than most tier-2
    plants have". The plant climbs to shift-batch and true job lots at its pace.
    """
    code = lot_code or f"{job_order.job_number}-{business_date:%d%m%y}"
    existing = await session.scalar(
        select(Lot).where(Lot.plant_id == plant.id, Lot.lot_code == code)
    )
    if existing:
        return existing

    lot = Lot(
        plant_id=plant.id,
        lot_code=code,
        job_order_id=job_order.id,
        granularity=LotGranularity.JOB_LOT if lot_code else LotGranularity.DAY_BATCH,
        opened_at=datetime.now(UTC),
    )
    session.add(lot)
    await session.flush()
    return lot
