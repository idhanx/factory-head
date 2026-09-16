"""Offline sync — entries a tablet recorded without a connection.

§7: "Everything must work offline on the tablet and sync when internet
returns — Tamil Nadu plant Wi-Fi cannot be trusted." §16.4 asks for the conflict
rule to be decided before the first pilot. The rule the product owner chose:

* every entry carries a reference made on the device, so a sync retried ten
  times records once;
* entries keep the time they happened, and belong to the shift and the job
  that were running then;
* an entry whose shift was locked before it arrived is not written into the
  locked shift — it is held for the owner, who applies it as a logged
  correction or discards it with a reason. Nothing is lost, nothing is silent.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Identity, get_identity, get_plant, get_session, require
from app.api.scoping import CLOCK_SKEW, in_plant
from app.api.v1 import capture as capture_api
from app.models import (
    AuditLog,
    DowntimeEvent,
    DowntimeStatus,
    HoldStatus,
    Plant,
    ProductionCount,
    QualityRecord,
    Role,
    ShiftSession,
    Station,
    SyncHold,
    User,
)
from app.schemas import QualityRequest

router = APIRouter(prefix="/sync", tags=["sync"])

OpKind = Literal["downtime_start", "downtime_close", "production", "quality"]

ROLES_FOR = {
    "downtime_start": (Role.SUPERVISOR, Role.MAINTENANCE, Role.OWNER),
    "downtime_close": (Role.SUPERVISOR, Role.MAINTENANCE, Role.OWNER),
    "production": (Role.SUPERVISOR, Role.OWNER),
    "quality": (Role.QUALITY, Role.OWNER),
}


class SyncOp(BaseModel):
    client_ref: str = Field(min_length=8, max_length=64)
    kind: OpKind
    shift_session_id: uuid.UUID | None = None
    occurred_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class SyncBatch(BaseModel):
    device_id: str | None = Field(default=None, max_length=64)
    ops: list[SyncOp] = Field(min_length=1, max_length=200)


class SyncResult(BaseModel):
    client_ref: str
    status: Literal["applied", "duplicate", "held", "rejected"]
    entity_id: uuid.UUID | None = None
    hold_id: uuid.UUID | None = None
    message: str | None = None


class HoldDecision(BaseModel):
    note: str = Field(min_length=3, max_length=2000)


class HoldOut(BaseModel):
    id: uuid.UUID
    kind: str
    client_ref: str
    shift_session_id: uuid.UUID | None
    business_date: Any
    station_code: str | None
    occurred_at: datetime
    received_at: datetime
    submitted_by_name: str | None
    payload: dict
    reason: str
    status: str
    resolution_note: str | None
    resolved_by_name: str | None
    resolved_at: datetime | None


class _Held(Exception):
    def __init__(self, reason: str, shift_session: ShiftSession | None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.shift_session = shift_session


# ---------------------------------------------------------------------------
# Batch
# ---------------------------------------------------------------------------


@router.post("/batch", response_model=dict)
async def sync_batch(
    batch: SyncBatch,
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Apply a tablet's queued entries in the order they were made."""
    results: list[SyncResult] = []
    for op in batch.ops:
        results.append(await _apply_one(session, identity, plant, op, batch.device_id))
    return {"results": [result.model_dump(mode="json") for result in results]}


async def _apply_one(
    session: AsyncSession, identity: Identity, plant: Plant, op: SyncOp, device_id: str | None
) -> SyncResult:
    if not identity.has(Role.ADMIN, *ROLES_FOR[op.kind]):
        return SyncResult(
            client_ref=op.client_ref, status="rejected", message="Your role cannot record this"
        )

    replay = await _already_recorded(session, plant, op)
    if replay is not None:
        return replay

    try:
        # One savepoint per entry: a rejected entry must not undo the ones before it.
        async with session.begin_nested():
            entity_id = await _apply(session, identity, plant, op, allow_locked=False)
        return SyncResult(client_ref=op.client_ref, status="applied", entity_id=entity_id)
    except _Held as held:
        hold = await _hold(session, identity, plant, op, device_id, held)
        return SyncResult(
            client_ref=op.client_ref, status="held", hold_id=hold.id, message=held.reason
        )
    except HTTPException as exc:
        return SyncResult(client_ref=op.client_ref, status="rejected", message=_detail(exc))
    except (ValueError, ValidationError) as exc:
        return SyncResult(client_ref=op.client_ref, status="rejected", message=str(exc)[:300])
    except IntegrityError:
        # Two syncs of the same entry raced; the other one won.
        replay = await _already_recorded(session, plant, op)
        return replay or SyncResult(
            client_ref=op.client_ref, status="rejected", message="Could not be saved"
        )


async def _apply(
    session: AsyncSession, identity: Identity, plant: Plant, op: SyncOp, *, allow_locked: bool
) -> uuid.UUID:
    """Write one entry. Raises ``_Held`` when its shift is locked."""
    if op.occurred_at.tzinfo is None:
        raise ValueError("occurred_at must include a timezone")
    if op.occurred_at > datetime.now(UTC) + CLOCK_SKEW:
        raise ValueError("That entry is dated in the future — check the tablet's clock")

    if op.kind == "downtime_close":
        event = await _stop_for(session, plant, op)
        if event.status is DowntimeStatus.CLOSED:
            return event.id  # closed meanwhile by another tablet
        shift_session = await session.get(ShiftSession, event.shift_session_id)
        _check_lock(shift_session, allow_locked)
        out = await capture_api.end_stop(
            session,
            identity,
            plant,
            event=event,
            reason_code_id=_uuid(op.payload.get("reason_code_id")),
            ended_at=op.occurred_at,
        )
        return out.id

    shift_session = await _shift_for(session, plant, op)
    _check_lock(shift_session, allow_locked)

    if op.kind == "downtime_start":
        out = await capture_api.start_stop(
            session,
            identity,
            plant,
            station_id=_required_uuid(op.payload, "station_id"),
            reason_code_id=_uuid(op.payload.get("reason_code_id")),
            note=(op.payload.get("note") or None),
            shift_session=shift_session,
            started_at=op.occurred_at,
            client_ref=op.client_ref,
        )
        return out.id

    if op.kind == "production":
        row = await capture_api.count_entry(
            session,
            identity,
            plant,
            station_id=_required_uuid(op.payload, "station_id"),
            count=int(op.payload.get("count", -1)),
            shift_session=shift_session,
            recorded_at=op.occurred_at,
            client_ref=op.client_ref,
        )
        return row.id

    payload = QualityRequest.model_validate({**op.payload, "client_ref": op.client_ref})
    out = await capture_api.quality_entry(
        session,
        identity,
        plant,
        payload=payload,
        shift_session=shift_session,
        recorded_at=op.occurred_at,
    )
    return out.id


async def _already_recorded(session: AsyncSession, plant: Plant, op: SyncOp) -> SyncResult | None:
    if op.kind == "downtime_close":
        try:
            event = await _stop_for(session, plant, op)
        except (HTTPException, ValueError, _Held):
            event = None
        if event is not None and event.status is DowntimeStatus.CLOSED:
            return SyncResult(client_ref=op.client_ref, status="duplicate", entity_id=event.id)
    model = {
        "downtime_start": DowntimeEvent,
        "production": ProductionCount,
        "quality": QualityRecord,
    }.get(op.kind)
    if model is not None:
        entity_id = await session.scalar(
            select(model.id).where(model.plant_id == plant.id, model.client_ref == op.client_ref)
        )
        if entity_id is not None:
            return SyncResult(client_ref=op.client_ref, status="duplicate", entity_id=entity_id)
    hold = await session.scalar(
        select(SyncHold).where(SyncHold.plant_id == plant.id, SyncHold.client_ref == op.client_ref)
    )
    if hold is not None:
        return SyncResult(
            client_ref=op.client_ref,
            status="duplicate" if hold.status is HoldStatus.APPLIED else "held",
            hold_id=hold.id,
            entity_id=hold.entity_id,
            message=hold.reason,
        )
    return None


async def _shift_for(session: AsyncSession, plant: Plant, op: SyncOp) -> ShiftSession:
    """The shift the tablet had open, else the one running when the entry was made."""
    if op.shift_session_id is not None:
        return await in_plant(session, ShiftSession, op.shift_session_id, plant.id, "Shift")
    shift_session = await session.scalar(
        select(ShiftSession)
        .where(
            ShiftSession.plant_id == plant.id,
            ShiftSession.started_at <= op.occurred_at,
            (ShiftSession.ended_at.is_(None)) | (ShiftSession.ended_at >= op.occurred_at),
        )
        .order_by(ShiftSession.started_at.desc())
        .limit(1)
    )
    if shift_session is None:
        raise _Held("No shift was open when this was recorded", None)
    return shift_session


async def _stop_for(session: AsyncSession, plant: Plant, op: SyncOp) -> DowntimeEvent:
    event_id = _uuid(op.payload.get("event_id"))
    if event_id is not None:
        return await in_plant(session, DowntimeEvent, event_id, plant.id, "Stop")
    start_ref = op.payload.get("start_client_ref")
    if not start_ref:
        raise ValueError("A restart needs event_id or start_client_ref")
    event = await session.scalar(
        select(DowntimeEvent).where(
            DowntimeEvent.plant_id == plant.id, DowntimeEvent.client_ref == str(start_ref)
        )
    )
    if event is None:
        held = await session.scalar(
            select(SyncHold).where(
                SyncHold.plant_id == plant.id, SyncHold.client_ref == str(start_ref)
            )
        )
        if held is not None and held.status is not HoldStatus.APPLIED:
            raise _Held("Its stop is itself held for the owner's review", None)
        raise ValueError("The stop this restart belongs to was not found")
    return event


def _check_lock(shift_session: ShiftSession | None, allow_locked: bool) -> None:
    if shift_session is not None and shift_session.locked_at is not None and not allow_locked:
        raise _Held("The shift was closed and locked before this entry synced", shift_session)


async def _hold(
    session: AsyncSession,
    identity: Identity,
    plant: Plant,
    op: SyncOp,
    device_id: str | None,
    held: _Held,
) -> SyncHold:
    hold = SyncHold(
        plant_id=plant.id,
        client_ref=op.client_ref,
        device_id=device_id,
        kind=op.kind,
        shift_session_id=held.shift_session.id if held.shift_session else op.shift_session_id,
        business_date=held.shift_session.business_date if held.shift_session else None,
        occurred_at=op.occurred_at,
        payload=op.payload,
        reason=held.reason,
        submitted_by_id=identity.user.id,
        status=HoldStatus.HELD,
    )
    session.add(hold)
    await session.flush()
    return hold


# ---------------------------------------------------------------------------
# Owner review
# ---------------------------------------------------------------------------

MANAGERS = (Role.OWNER, Role.CONSULTANT)


@router.get("/holds", response_model=list[HoldOut], dependencies=[Depends(require(*MANAGERS))])
async def list_holds(
    status_filter: str = Query(default="held", alias="status"),
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    try:
        wanted = HoldStatus(status_filter)
    except ValueError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown status {status_filter!r}"
        ) from None
    holds = (
        await session.scalars(
            select(SyncHold)
            .where(SyncHold.plant_id == identity.plant_id, SyncHold.status == wanted)
            .order_by(SyncHold.occurred_at.desc())
            .limit(500)
        )
    ).all()
    return [await _hold_out(session, hold) for hold in holds]


@router.post(
    "/holds/{hold_id}/apply", response_model=HoldOut, dependencies=[Depends(require(*MANAGERS))]
)
async def apply_hold(
    hold_id: uuid.UUID,
    decision: HoldDecision,
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Write a held entry into its locked shift, as an owner correction on the record."""
    hold = await in_plant(session, SyncHold, hold_id, plant.id, "Held entry")
    if hold.status is not HoldStatus.HELD:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Already {hold.status.value}")

    op = SyncOp(
        client_ref=hold.client_ref,
        kind=hold.kind,
        shift_session_id=hold.shift_session_id,
        occurred_at=hold.occurred_at,
        payload=hold.payload,
    )
    # The entry is applied as the person who recorded it; the owner's decision
    # is what the audit log records.
    submitter = await session.get(User, hold.submitted_by_id) if hold.submitted_by_id else None
    acting = Identity(
        user=submitter or identity.user, plant_id=identity.plant_id, roles=identity.roles
    )
    try:
        async with session.begin_nested():
            entity_id = await _apply(session, acting, plant, op, allow_locked=True)
    except _Held as held:
        raise HTTPException(status.HTTP_409_CONFLICT, held.reason) from None
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)[:300]) from None

    hold.status = HoldStatus.APPLIED
    hold.entity_id = entity_id
    hold.resolution_note = decision.note
    hold.resolved_by_id = identity.user.id
    hold.resolved_at = datetime.now(UTC)
    session.add(
        AuditLog(
            plant_id=plant.id,
            actor_id=identity.user.id,
            entity_type=_ENTITY_TYPE[hold.kind],
            entity_id=entity_id,
            action="sync_hold_applied",
            reason=decision.note,
            after={"hold_id": str(hold.id), "kind": hold.kind, "payload": hold.payload},
            occurred_at=datetime.now(UTC),
        )
    )
    await session.flush()
    return await _hold_out(session, hold)


@router.post(
    "/holds/{hold_id}/discard", response_model=HoldOut, dependencies=[Depends(require(*MANAGERS))]
)
async def discard_hold(
    hold_id: uuid.UUID,
    decision: HoldDecision,
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    hold = await in_plant(session, SyncHold, hold_id, plant.id, "Held entry")
    if hold.status is not HoldStatus.HELD:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Already {hold.status.value}")
    hold.status = HoldStatus.DISCARDED
    hold.resolution_note = decision.note
    hold.resolved_by_id = identity.user.id
    hold.resolved_at = datetime.now(UTC)
    await session.flush()
    return await _hold_out(session, hold)


_ENTITY_TYPE = {
    "downtime_start": "downtime_event",
    "downtime_close": "downtime_event",
    "production": "production_count",
    "quality": "quality_record",
}


async def _hold_out(session: AsyncSession, hold: SyncHold) -> HoldOut:
    station_id = _uuid((hold.payload or {}).get("station_id"))
    station = await session.get(Station, station_id) if station_id else None
    submitter = await session.get(User, hold.submitted_by_id) if hold.submitted_by_id else None
    resolver = await session.get(User, hold.resolved_by_id) if hold.resolved_by_id else None
    return HoldOut(
        id=hold.id,
        kind=hold.kind,
        client_ref=hold.client_ref,
        shift_session_id=hold.shift_session_id,
        business_date=hold.business_date,
        station_code=station.code if station else None,
        occurred_at=hold.occurred_at,
        received_at=hold.created_at,
        submitted_by_name=submitter.full_name if submitter else None,
        payload=hold.payload or {},
        reason=hold.reason,
        status=hold.status.value,
        resolution_note=hold.resolution_note,
        resolved_by_name=resolver.full_name if resolver else None,
        resolved_at=hold.resolved_at,
    )


def _uuid(value) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise ValueError(f"{value!r} is not a valid id") from None


def _required_uuid(payload: dict, key: str) -> uuid.UUID:
    value = _uuid(payload.get(key))
    if value is None:
        raise ValueError(f"{key} is required")
    return value


def _detail(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, dict):
        return str(detail.get("message") or detail)
    return str(detail)
