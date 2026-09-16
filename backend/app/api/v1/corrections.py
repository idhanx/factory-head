"""Corrections — changing a recorded entry, always with a reason, always on the record.

§2: "Data locks permanently at shift close; edits before close are logged; owner
sees an audit trail." So a supervisor may correct their own open shift, the
owner may correct a locked one, and every change — who, when, why, before and
after — goes to the audit log that the owner can read back.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Identity, get_identity, get_plant, get_session, require
from app.api.scoping import CLOCK_SKEW, in_plant
from app.api.v1.capture import _check_defects, _event_out, quality_out
from app.kernel import downtime_event_cost, format_inr
from app.models import (
    AuditLog,
    Disposition,
    DowntimeEvent,
    DowntimeStatus,
    Part,
    Plant,
    ProductionCount,
    QualityRecord,
    ReasonCode,
    Role,
    Shift,
    ShiftSession,
    Station,
    User,
)
from app.schemas import DefectEntry, DowntimeEventOut, QualityOut
from app.services import capture as capture_service
from app.services.costing import snapshot_from_row

router = APIRouter(tags=["corrections"])

OWNERS = (Role.OWNER, Role.CONSULTANT)


class DowntimeCorrection(BaseModel):
    reason_code_id: uuid.UUID | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    note: str | None = Field(default=None, max_length=1000)
    reason: str = Field(min_length=5, max_length=1000)


class ProductionCorrection(BaseModel):
    count: int = Field(ge=0, le=1_000_000)
    reason: str = Field(min_length=5, max_length=1000)


class QualityCorrection(BaseModel):
    good_count: int | None = Field(default=None, ge=0, le=1_000_000)
    reject_count: int | None = Field(default=None, ge=0, le=1_000_000)
    rework_count: int | None = Field(default=None, ge=0, le=1_000_000)
    rework_minutes: float | None = Field(default=None, ge=0, le=100_000)
    disposition: Literal["scrap", "use_as_is"] | None = None
    defects: list[DefectEntry] | None = Field(default=None, max_length=50)
    reason: str = Field(min_length=5, max_length=1000)


# ---------------------------------------------------------------------------
# What a shift recorded
# ---------------------------------------------------------------------------


@router.get("/shifts/{shift_session_id}/entries")
async def shift_entries(
    shift_session_id: uuid.UUID,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    shift_session = await in_plant(
        session, ShiftSession, shift_session_id, identity.plant_id, "Shift"
    )
    shift = await session.get(Shift, shift_session.shift_id)
    stations = {
        s.id: s.code
        for s in (
            await session.scalars(select(Station).where(Station.plant_id == identity.plant_id))
        ).all()
    }
    parts = {
        p.id: p.part_number
        for p in (
            await session.scalars(select(Part).where(Part.plant_id == identity.plant_id))
        ).all()
    }

    downtime = (
        await session.scalars(
            select(DowntimeEvent)
            .where(DowntimeEvent.shift_session_id == shift_session.id)
            .order_by(DowntimeEvent.started_at)
        )
    ).all()
    production = (
        await session.scalars(
            select(ProductionCount)
            .where(ProductionCount.shift_session_id == shift_session.id)
            .order_by(ProductionCount.recorded_at)
        )
    ).all()
    quality = (
        await session.scalars(
            select(QualityRecord)
            .where(QualityRecord.shift_session_id == shift_session.id)
            .order_by(QualityRecord.recorded_at)
        )
    ).all()

    return {
        "shift": {
            "id": str(shift_session.id),
            "shift_name": shift.name if shift else "",
            "business_date": shift_session.business_date,
            "status": shift_session.status.value,
            "started_at": shift_session.started_at,
            "ended_at": shift_session.ended_at,
            "locked_at": shift_session.locked_at,
        },
        "can_correct": _can_correct(identity, shift_session),
        "downtime": [
            (await _event_out(session, event)).model_dump(mode="json") for event in downtime
        ],
        "production": [
            {
                "id": str(row.id),
                "station_id": str(row.station_id),
                "station_code": stations.get(row.station_id),
                "count": row.count,
                "recorded_at": row.recorded_at,
                "source": row.source.value,
            }
            for row in production
        ],
        "quality": [
            {
                "id": str(row.id),
                "part_number": parts.get(row.part_id),
                "station_code": stations.get(row.station_id),
                "good_count": row.good_count,
                "reject_count": row.reject_count,
                "rework_count": row.rework_count,
                "disposition": row.disposition.value,
                "defects": row.defects or [],
                "cost_paise": row.cost_total,
                "cost_display": format_inr(row.cost_total),
                "recorded_at": row.recorded_at,
            }
            for row in quality
        ],
    }


def _can_correct(identity: Identity, shift_session: ShiftSession) -> bool:
    if identity.has(Role.ADMIN, *OWNERS):
        return True
    return identity.has(Role.SUPERVISOR) and shift_session.locked_at is None


async def _guard(session: AsyncSession, identity: Identity, shift_session_id) -> ShiftSession:
    shift_session = await session.get(ShiftSession, shift_session_id) if shift_session_id else None
    if shift_session is None:
        if not identity.has(Role.ADMIN, *OWNERS):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can correct this entry")
        return None
    if not _can_correct(identity, shift_session):
        if identity.has(Role.SUPERVISOR) and shift_session.locked_at is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "That shift is locked — ask the owner to correct it."
            )
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role cannot correct entries")
    return shift_session


def _audit(
    session: AsyncSession, identity: Identity, *, entity_type: str, entity_id, reason: str,
    before: dict, after: dict, locked: bool,
) -> None:
    session.add(
        AuditLog(
            plant_id=identity.plant_id,
            actor_id=identity.user.id,
            entity_type=entity_type,
            entity_id=entity_id,
            action="correction_after_lock" if locked else "correction",
            reason=reason,
            before=before,
            after=after,
            occurred_at=datetime.now(UTC),
        )
    )


# ---------------------------------------------------------------------------
# Corrections
# ---------------------------------------------------------------------------


@router.post(
    "/corrections/downtime/{event_id}",
    response_model=DowntimeEventOut,
    dependencies=[Depends(require(Role.SUPERVISOR, *OWNERS))],
)
async def correct_downtime(
    event_id: uuid.UUID,
    payload: DowntimeCorrection,
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Fix a stop's reason, times or note. Its cost is recomputed with the rates it
    was first costed with — a correction never restates rates (C3)."""
    event = await in_plant(session, DowntimeEvent, event_id, plant.id, "Stop")
    shift_session = await _guard(session, identity, event.shift_session_id)
    before = _downtime_state(event)

    if payload.reason_code_id is not None:
        await in_plant(session, ReasonCode, payload.reason_code_id, plant.id, "Reason code")
        event.reason_code_id = payload.reason_code_id
    if payload.note is not None:
        event.note = payload.note
    now = datetime.now(UTC)
    for moment in (payload.started_at, payload.ended_at):
        if moment is not None and (moment.tzinfo is None or moment > now + CLOCK_SKEW):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Times must be real and not in the future"
            )
    if payload.started_at is not None:
        event.started_at = payload.started_at
    if payload.ended_at is not None:
        if event.status is DowntimeStatus.OPEN:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Mark the machine running to end a stop that is still open"
            )
        event.ended_at = payload.ended_at
    if event.ended_at is not None:
        if event.ended_at < event.started_at:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "A stop cannot end before it started"
            )
        minutes = Decimal((event.ended_at - event.started_at).total_seconds()) / Decimal(60)
        cost = downtime_event_cost(minutes, snapshot_from_row(event))
        event.duration_minutes = minutes
        event.cost_total = cost.total
        event.cost_breakdown = {
            "machine": cost.machine_cost,
            "labour": cost.labour_cost,
            "overhead": cost.overhead_cost,
            "lost_contribution": cost.lost_contribution,
            "parts_not_made": cost.parts_not_made,
        }

    await session.flush()
    _audit(
        session, identity, entity_type="downtime_event", entity_id=event.id,
        reason=payload.reason, before=before, after=_downtime_state(event),
        locked=bool(shift_session and shift_session.locked_at),
    )
    await session.flush()
    return await _event_out(session, event)


@router.post(
    "/corrections/production/{count_id}",
    dependencies=[Depends(require(Role.SUPERVISOR, *OWNERS))],
)
async def correct_production(
    count_id: uuid.UUID,
    payload: ProductionCorrection,
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    row = await in_plant(session, ProductionCount, count_id, plant.id, "Count")
    shift_session = await _guard(session, identity, row.shift_session_id)
    before = {"count": row.count}
    row.count = payload.count
    _audit(
        session, identity, entity_type="production_count", entity_id=row.id,
        reason=payload.reason, before=before, after={"count": row.count},
        locked=bool(shift_session and shift_session.locked_at),
    )
    await session.flush()
    return {
        "id": str(row.id),
        "station_id": str(row.station_id),
        "count": row.count,
        "recorded_at": row.recorded_at,
        "covers_from": row.covers_from,
    }


@router.post(
    "/corrections/quality/{record_id}",
    response_model=QualityOut,
    dependencies=[Depends(require(Role.QUALITY, *OWNERS))],
)
async def correct_quality(
    record_id: uuid.UUID,
    payload: QualityCorrection,
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Fix an inspection. Scrap and rework are re-costed and the counts re-reconciled.

    The inspector corrects their own open shift; the supervisor cannot, for
    the same reason they cannot enter it (§2: one person must not hold both
    sides of the cross-check).
    """
    record = await in_plant(session, QualityRecord, record_id, plant.id, "Inspection")
    shift_session = await session.get(ShiftSession, record.shift_session_id)
    if shift_session is not None and shift_session.locked_at is not None and not identity.has(
        Role.ADMIN, *OWNERS
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "That shift is locked — ask the owner to correct it."
        )
    before = _quality_state(record)

    changes = payload.model_dump(exclude_unset=True, exclude={"reason", "defects"})
    for field, value in changes.items():
        if field == "disposition":
            record.disposition = Disposition(value)
        elif field == "rework_minutes":
            record.rework_minutes = Decimal(str(value)) if value is not None else None
        else:
            setattr(record, field, value)
    if record.rework_count > record.good_count:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Reworked parts are counted within the good parts, so they cannot exceed them",
        )
    if payload.defects is not None:
        await _check_defects(
            session, plant.id, payload.defects, record.reject_count + record.rework_count
        )
        record.defects = [d.model_dump() for d in payload.defects]

    part = await session.get(Part, record.part_id)
    station = await session.get(Station, record.station_id) if record.station_id else None
    if shift_session is not None:
        await capture_service.cost_and_reconcile(
            session, plant=plant, record=record, part=part, station=station,
            shift_session=shift_session,
        )
    _audit(
        session, identity, entity_type="quality_record", entity_id=record.id,
        reason=payload.reason, before=before, after=_quality_state(record),
        locked=bool(shift_session and shift_session.locked_at),
    )
    await session.flush()
    return quality_out(record)


# ---------------------------------------------------------------------------
# The trail
# ---------------------------------------------------------------------------


@router.get("/audit-log", dependencies=[Depends(require(*OWNERS))])
async def audit_log(
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    limit: int = 50,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    query = select(AuditLog).where(AuditLog.plant_id == identity.plant_id)
    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)
    if entity_id:
        query = query.where(AuditLog.entity_id == entity_id)
    rows = (
        await session.scalars(
            query.order_by(AuditLog.occurred_at.desc()).limit(max(1, min(limit, 500)))
        )
    ).all()
    names = dict(
        (
            await session.execute(
                select(User.id, User.full_name).where(
                    User.id.in_({row.actor_id for row in rows if row.actor_id})
                )
            )
        ).all()
    ) if rows else {}
    return [
        {
            "id": str(row.id),
            "entity_type": row.entity_type,
            "entity_id": str(row.entity_id) if row.entity_id else None,
            "action": row.action,
            "actor_name": names.get(row.actor_id),
            "reason": row.reason,
            "before": row.before,
            "after": row.after,
            "occurred_at": row.occurred_at,
        }
        for row in rows
    ]


def _downtime_state(event: DowntimeEvent) -> dict:
    return {
        "reason_code_id": str(event.reason_code_id) if event.reason_code_id else None,
        "started_at": event.started_at.isoformat() if event.started_at else None,
        "ended_at": event.ended_at.isoformat() if event.ended_at else None,
        "duration_minutes": float(event.duration_minutes) if event.duration_minutes else None,
        "cost_paise": event.cost_total,
        "note": event.note,
    }


def _quality_state(record: QualityRecord) -> dict:
    return {
        "good_count": record.good_count,
        "reject_count": record.reject_count,
        "rework_count": record.rework_count,
        "rework_minutes": float(record.rework_minutes) if record.rework_minutes else None,
        "disposition": record.disposition.value,
        "defects": record.defects or [],
        "cost_paise": record.cost_total,
    }
