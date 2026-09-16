"""Reference data: read by every screen, maintained by the people who own it.

Reads are open to every role in the plant. Writes are for the owner, the
onboarding consultant and admins — except operators and job orders, which a
supervisor maintains day to day (§14.1: today's jobs are a two-minute task).

Nothing here hard-deletes. A station that ran for two years keeps its history;
it is deactivated with ``is_active: false``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Identity, get_identity, get_session, require
from app.api.scoping import in_plant
from app.kernel import rupees, to_rupees
from app.models import (
    AlertRecipient,
    CostFamily,
    CostRate,
    DefectCode,
    JobOrder,
    JobOrderStatus,
    Operator,
    Part,
    RateSource,
    ReasonCode,
    Role,
    Shift,
    Station,
)
from app.schemas import (
    AlertRecipientIn,
    AlertRecipientOut,
    AlertRecipientPatch,
    CostRateIn,
    CostRateOut,
    DefectCodeIn,
    DefectCodeOut,
    DefectCodePatch,
    JobOrderIn,
    JobOrderOut,
    JobOrderPatch,
    JobOrderRepeat,
    OperatorIn,
    OperatorOut,
    OperatorPatch,
    PartDetailOut,
    PartIn,
    PartOut,
    PartPatch,
    ReasonCodeIn,
    ReasonCodeOut,
    ReasonCodePatch,
    ShiftIn,
    ShiftOut,
    ShiftPatch,
    StationIn,
    StationOut,
    StationPatch,
)

router = APIRouter(prefix="/masters", tags=["masters"])

CONFIG_ROLES = (Role.OWNER, Role.CONSULTANT)
DAILY_ROLES = (Role.SUPERVISOR, Role.OWNER, Role.CONSULTANT)


def _can_configure(identity: Identity) -> bool:
    return identity.has(Role.ADMIN, *CONFIG_ROLES)


def _inactive_allowed(identity: Identity, include_inactive: bool) -> bool:
    if include_inactive and not _can_configure(identity):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only the owner or consultant can list inactive records"
        )
    return include_inactive


async def _flush_unique(session: AsyncSession, what: str) -> None:
    """Turn a duplicate natural key into a readable 409 instead of a 500."""
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"{what} already exists") from None


def _apply(row, patch) -> None:
    for field, value in patch.model_dump(exclude_unset=True).items():
        setattr(row, field, value)


# ---------------------------------------------------------------------------
# Stations
# ---------------------------------------------------------------------------


async def _family_for(session: AsyncSession, plant_id: uuid.UUID, code: str | None):
    if not code:
        return None
    family = await session.scalar(
        select(CostFamily).where(CostFamily.plant_id == plant_id, CostFamily.code == code)
    )
    if family is None:
        family = CostFamily(plant_id=plant_id, code=code, name=code)
        session.add(family)
        await session.flush()
    return family


async def _station_out(session: AsyncSession, station: Station) -> StationOut:
    family = (
        await session.get(CostFamily, station.cost_family_id)
        if station.cost_family_id
        else None
    )
    out = StationOut.model_validate(station)
    out.cost_family_code = family.code if family else None
    return out


@router.get("/stations", response_model=list[StationOut])
async def list_stations(
    include_inactive: bool = False,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    query = select(Station).where(Station.plant_id == identity.plant_id)
    if not _inactive_allowed(identity, include_inactive):
        query = query.where(Station.is_active.is_(True))
    stations = (await session.scalars(query.order_by(Station.line, Station.code))).all()
    families = {
        f.id: f.code
        for f in (
            await session.scalars(
                select(CostFamily).where(CostFamily.plant_id == identity.plant_id)
            )
        ).all()
    }
    out = []
    for station in stations:
        item = StationOut.model_validate(station)
        item.cost_family_code = families.get(station.cost_family_id)
        out.append(item)
    return out


@router.post(
    "/stations",
    response_model=StationOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(*CONFIG_ROLES))],
)
async def create_station(
    payload: StationIn,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    family = await _family_for(session, identity.plant_id, payload.cost_family_code)
    station = Station(
        plant_id=identity.plant_id,
        cost_family_id=family.id if family else None,
        **payload.model_dump(exclude={"cost_family_code"}),
    )
    session.add(station)
    await _flush_unique(session, f"Station {payload.code}")
    return await _station_out(session, station)


@router.patch(
    "/stations/{station_id}",
    response_model=StationOut,
    dependencies=[Depends(require(*CONFIG_ROLES))],
)
async def update_station(
    station_id: uuid.UUID,
    payload: StationPatch,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    station = await in_plant(session, Station, station_id, identity.plant_id, "Station")
    changes = payload.model_dump(exclude_unset=True)
    if "cost_family_code" in changes:
        family = await _family_for(session, identity.plant_id, changes.pop("cost_family_code"))
        station.cost_family_id = family.id if family else None
    for field, value in changes.items():
        setattr(station, field, value)
    await session.flush()
    return await _station_out(session, station)


# ---------------------------------------------------------------------------
# Reason codes and defect codes (T6)
# ---------------------------------------------------------------------------


@router.get("/reason-codes", response_model=list[ReasonCodeOut])
async def list_reason_codes(
    include_inactive: bool = False,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    """Ordered for thick fingers: the commonest reasons get the biggest buttons,
    so a stop is logged in 20 seconds (Screen 3)."""
    query = select(ReasonCode).where(ReasonCode.plant_id == identity.plant_id)
    if not _inactive_allowed(identity, include_inactive):
        query = query.where(ReasonCode.is_active.is_(True))
    return (
        await session.scalars(query.order_by(ReasonCode.display_order, ReasonCode.label))
    ).all()


@router.post(
    "/reason-codes",
    response_model=ReasonCodeOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(*CONFIG_ROLES))],
)
async def create_reason_code(
    payload: ReasonCodeIn,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    reason = ReasonCode(plant_id=identity.plant_id, **payload.model_dump())
    session.add(reason)
    await _flush_unique(session, f"Reason code {payload.code}")
    return reason


@router.patch(
    "/reason-codes/{reason_id}",
    response_model=ReasonCodeOut,
    dependencies=[Depends(require(*CONFIG_ROLES))],
)
async def update_reason_code(
    reason_id: uuid.UUID,
    payload: ReasonCodePatch,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    # The code itself is immutable: Actions, baselines and Coach rules key on it.
    reason = await in_plant(session, ReasonCode, reason_id, identity.plant_id, "Reason code")
    _apply(reason, payload)
    await session.flush()
    return reason


@router.get("/defect-codes", response_model=list[DefectCodeOut])
async def list_defect_codes(
    include_inactive: bool = False,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    query = select(DefectCode).where(DefectCode.plant_id == identity.plant_id)
    if not _inactive_allowed(identity, include_inactive):
        query = query.where(DefectCode.is_active.is_(True))
    return (
        await session.scalars(query.order_by(DefectCode.display_order, DefectCode.label))
    ).all()


@router.post(
    "/defect-codes",
    response_model=DefectCodeOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(*CONFIG_ROLES))],
)
async def create_defect_code(
    payload: DefectCodeIn,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    defect = DefectCode(plant_id=identity.plant_id, **payload.model_dump())
    session.add(defect)
    await _flush_unique(session, f"Defect code {payload.code}")
    return defect


@router.patch(
    "/defect-codes/{defect_id}",
    response_model=DefectCodeOut,
    dependencies=[Depends(require(*CONFIG_ROLES))],
)
async def update_defect_code(
    defect_id: uuid.UUID,
    payload: DefectCodePatch,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    defect = await in_plant(session, DefectCode, defect_id, identity.plant_id, "Defect code")
    _apply(defect, payload)
    await session.flush()
    return defect


# ---------------------------------------------------------------------------
# Parts (T2) — margins are the owner's, so they only travel to config roles
# ---------------------------------------------------------------------------


def _part_detail(part: Part) -> PartDetailOut:
    return PartDetailOut(
        id=part.id,
        part_number=part.part_number,
        description=part.description,
        customer_name=part.customer_name,
        part_family=part.part_family,
        is_active=part.is_active,
        contribution_margin_rupees=(
            float(to_rupees(part.contribution_margin))
            if part.contribution_margin is not None
            else None
        ),
        margin_band=str(getattr(part.margin_band, "value", part.margin_band)) if part.margin_band else None,
        material_cost_rupees=float(to_rupees(part.material_cost or 0)),
    )


def _part_money(values: dict) -> dict:
    if "contribution_margin_rupees" in values:
        margin = values.pop("contribution_margin_rupees")
        values["contribution_margin"] = rupees(str(margin)) if margin is not None else None
    if "material_cost_rupees" in values:
        values["material_cost"] = rupees(str(values.pop("material_cost_rupees") or 0))
    return values


@router.get("/parts", response_model=list[PartDetailOut] | list[PartOut])
async def list_parts(
    include_inactive: bool = False,
    detail: bool = False,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    if detail and not _can_configure(identity):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Part economics are visible to the owner only"
        )
    query = select(Part).where(Part.plant_id == identity.plant_id)
    if not _inactive_allowed(identity, include_inactive):
        query = query.where(Part.is_active.is_(True))
    parts = (await session.scalars(query.order_by(Part.part_number))).all()
    if detail:
        return [_part_detail(part) for part in parts]
    return [PartOut.model_validate(part) for part in parts]


@router.post(
    "/parts",
    response_model=PartDetailOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(*CONFIG_ROLES))],
)
async def create_part(
    payload: PartIn,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    part = Part(plant_id=identity.plant_id, **_part_money(payload.model_dump()))
    session.add(part)
    await _flush_unique(session, f"Part {payload.part_number}")
    return _part_detail(part)


@router.patch(
    "/parts/{part_id}",
    response_model=PartDetailOut,
    dependencies=[Depends(require(*CONFIG_ROLES))],
)
async def update_part(
    part_id: uuid.UUID,
    payload: PartPatch,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    part = await in_plant(session, Part, part_id, identity.plant_id, "Part")
    for field, value in _part_money(payload.model_dump(exclude_unset=True)).items():
        setattr(part, field, value)
    await session.flush()
    return _part_detail(part)


# ---------------------------------------------------------------------------
# Operators (T5-B) — names only, no logins at launch (§14.1)
# ---------------------------------------------------------------------------


@router.get("/operators", response_model=list[OperatorOut])
async def list_operators(
    include_inactive: bool = False,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    query = select(Operator).where(Operator.plant_id == identity.plant_id)
    if not _inactive_allowed(identity, include_inactive):
        query = query.where(Operator.is_active.is_(True))
    return (await session.scalars(query.order_by(Operator.name))).all()


@router.post(
    "/operators",
    response_model=OperatorOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(*DAILY_ROLES))],
)
async def create_operator(
    payload: OperatorIn,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    operator = Operator(plant_id=identity.plant_id, **payload.model_dump())
    session.add(operator)
    await _flush_unique(session, f"Operator {payload.name}")
    return operator


@router.patch(
    "/operators/{operator_id}",
    response_model=OperatorOut,
    dependencies=[Depends(require(*DAILY_ROLES))],
)
async def update_operator(
    operator_id: uuid.UUID,
    payload: OperatorPatch,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    operator = await in_plant(session, Operator, operator_id, identity.plant_id, "Operator")
    _apply(operator, payload)
    await _flush_unique(session, "An operator with that name")
    return operator


# ---------------------------------------------------------------------------
# Shifts (T7)
# ---------------------------------------------------------------------------


def _hhmm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def _shift_out(shift: Shift) -> ShiftOut:
    return ShiftOut(
        id=shift.id,
        name=shift.name,
        start_time=shift.start_time.strftime("%H:%M"),
        end_time=shift.end_time.strftime("%H:%M"),
        break_minutes=shift.break_minutes or 0,
        weekly_off=shift.weekly_off,
        display_order=shift.display_order or 0,
        is_active=shift.is_active,
    )


@router.get("/shifts", response_model=list[ShiftOut])
async def list_shifts(
    include_inactive: bool = False,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    query = select(Shift).where(Shift.plant_id == identity.plant_id)
    if not _inactive_allowed(identity, include_inactive):
        query = query.where(Shift.is_active.is_(True))
    return [
        _shift_out(shift)
        for shift in (await session.scalars(query.order_by(Shift.display_order))).all()
    ]


@router.post(
    "/shifts",
    response_model=ShiftOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(*CONFIG_ROLES))],
)
async def create_shift(
    payload: ShiftIn,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    values = payload.model_dump()
    values["start_time"] = _hhmm(values["start_time"])
    values["end_time"] = _hhmm(values["end_time"])
    shift = Shift(plant_id=identity.plant_id, **values)
    session.add(shift)
    await _flush_unique(session, f"Shift {payload.name}")
    return _shift_out(shift)


@router.patch(
    "/shifts/{shift_id}",
    response_model=ShiftOut,
    dependencies=[Depends(require(*CONFIG_ROLES))],
)
async def update_shift(
    shift_id: uuid.UUID,
    payload: ShiftPatch,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    # Changing a pattern never restates history: each shift session stored its
    # planned minutes when it started.
    shift = await in_plant(session, Shift, shift_id, identity.plant_id, "Shift")
    values = payload.model_dump(exclude_unset=True)
    for key in ("start_time", "end_time"):
        if values.get(key):
            values[key] = _hhmm(values[key])
    for field, value in values.items():
        setattr(shift, field, value)
    await _flush_unique(session, "A shift with that name")
    return _shift_out(shift)


# ---------------------------------------------------------------------------
# Alert recipients (T5-A) — who the escalation ladder and reports reach
# ---------------------------------------------------------------------------


@router.get(
    "/alert-recipients",
    response_model=list[AlertRecipientOut],
    dependencies=[Depends(require(*CONFIG_ROLES))],
)
async def list_alert_recipients(
    include_inactive: bool = False,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    query = select(AlertRecipient).where(AlertRecipient.plant_id == identity.plant_id)
    if not include_inactive:
        query = query.where(AlertRecipient.is_active.is_(True))
    return (
        await session.scalars(
            query.order_by(AlertRecipient.escalation_level, AlertRecipient.name)
        )
    ).all()


@router.post(
    "/alert-recipients",
    response_model=AlertRecipientOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(*CONFIG_ROLES))],
)
async def create_alert_recipient(
    payload: AlertRecipientIn,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    recipient = AlertRecipient(plant_id=identity.plant_id, **payload.model_dump())
    session.add(recipient)
    await session.flush()
    return recipient


@router.patch(
    "/alert-recipients/{recipient_id}",
    response_model=AlertRecipientOut,
    dependencies=[Depends(require(*CONFIG_ROLES))],
)
async def update_alert_recipient(
    recipient_id: uuid.UUID,
    payload: AlertRecipientPatch,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    recipient = await in_plant(
        session, AlertRecipient, recipient_id, identity.plant_id, "Alert recipient"
    )
    _apply(recipient, payload)
    await session.flush()
    return recipient


# ---------------------------------------------------------------------------
# Cost rates (T4) — effective-dated, never edited in place
# ---------------------------------------------------------------------------


@router.get(
    "/cost-rates",
    response_model=list[CostRateOut],
    dependencies=[Depends(require(*CONFIG_ROLES))],
)
async def list_cost_rates(
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    families = {
        str(f.id): f.code
        for f in (
            await session.scalars(
                select(CostFamily).where(CostFamily.plant_id == identity.plant_id)
            )
        ).all()
    }
    rows = (
        await session.scalars(
            select(CostRate)
            .where(CostRate.plant_id == identity.plant_id)
            .order_by(CostRate.scope, CostRate.scope_ref, CostRate.effective_from.desc())
        )
    ).all()
    return [
        CostRateOut(
            id=row.id,
            scope=row.scope,
            reference=families.get(row.scope_ref, row.scope_ref)
            if row.scope == "cost_family"
            else row.scope_ref,
            machine_rate_rupees=float(to_rupees(row.machine_rate or 0)),
            labour_rate_rupees=float(to_rupees(row.labour_rate or 0)),
            overhead_rate_rupees=float(to_rupees(row.overhead_rate or 0)),
            source=row.source.value if hasattr(row.source, "value") else str(row.source),
            effective_from=row.effective_from,
        )
        for row in rows
    ]


@router.post(
    "/cost-rates",
    response_model=CostRateOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(*CONFIG_ROLES))],
)
async def add_cost_rate(
    payload: CostRateIn,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    """A new rate from a date. Yesterday's events keep yesterday's rate (C3)."""
    reference = payload.reference.strip()
    scope_ref = reference
    if payload.scope == "cost_family":
        family = await _family_for(session, identity.plant_id, reference)
        scope_ref = str(family.id)
    elif payload.scope == "section":
        scope_ref = reference.lower()
        if scope_ref not in {"machining", "assembly", "quality", "dispatch"}:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Section must be machining, assembly, quality or dispatch",
            )
    else:
        scope_ref = "plant"

    effective = payload.effective_from or date.today()
    row = await session.scalar(
        select(CostRate).where(
            CostRate.plant_id == identity.plant_id,
            CostRate.scope == payload.scope,
            CostRate.scope_ref == scope_ref,
            CostRate.effective_from == effective,
        )
    )
    if row is None:
        row = CostRate(
            plant_id=identity.plant_id,
            scope=payload.scope,
            scope_ref=scope_ref,
            effective_from=effective,
        )
        session.add(row)
    row.machine_rate = rupees(str(payload.machine_rate_rupees))
    row.labour_rate = rupees(str(payload.labour_rate_rupees))
    row.overhead_rate = rupees(str(payload.overhead_rate_rupees))
    row.source = RateSource(payload.source)
    await session.flush()
    return CostRateOut(
        id=row.id,
        scope=row.scope,
        reference=reference if payload.scope != "plant" else "plant",
        machine_rate_rupees=payload.machine_rate_rupees,
        labour_rate_rupees=payload.labour_rate_rupees,
        overhead_rate_rupees=payload.overhead_rate_rupees,
        source=payload.source,
        effective_from=effective,
    )


# ---------------------------------------------------------------------------
# Job orders — the spine every event hangs on (§3, §14.1)
# ---------------------------------------------------------------------------


async def _job_out(session: AsyncSession, job: JobOrder) -> JobOrderOut:
    part = await session.get(Part, job.part_id)
    return JobOrderOut(
        id=job.id,
        job_number=job.job_number,
        part_id=job.part_id,
        part_number=part.part_number if part else None,
        customer_name=job.customer_name,
        planned_quantity=job.planned_quantity,
        status=job.status.value,
        due_date=job.due_date,
        notes=job.notes,
    )


async def _next_job_number(session: AsyncSession, plant_id: uuid.UUID) -> str:
    """JO-<yymmdd>-<n>: readable on a route card, unique per plant per day."""
    prefix = f"JO-{date.today():%y%m%d}-"
    count = await session.scalar(
        select(func.count(JobOrder.id)).where(
            JobOrder.plant_id == plant_id, JobOrder.job_number.like(f"{prefix}%")
        )
    )
    number = int(count or 0) + 1
    while await session.scalar(
        select(JobOrder.id).where(
            JobOrder.plant_id == plant_id, JobOrder.job_number == f"{prefix}{number:02d}"
        )
    ):
        number += 1
    return f"{prefix}{number:02d}"


@router.get("/job-orders", response_model=list[JobOrderOut])
async def list_job_orders(
    include_closed: bool = False,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    """Today's runnable jobs — §14.1: created each morning from the production
    plan in about two minutes, because parts, routings and cycle times already
    live in the master."""
    query = select(JobOrder, Part.part_number).join(Part, Part.id == JobOrder.part_id).where(
        JobOrder.plant_id == identity.plant_id
    )
    if not include_closed:
        query = query.where(
            JobOrder.status.in_(
                [JobOrderStatus.PLANNED, JobOrderStatus.RUNNING, JobOrderStatus.PAUSED]
            )
        )
    rows = (
        await session.execute(
            query.order_by(JobOrder.due_date, JobOrder.job_number).limit(500)
        )
    ).all()
    return [
        JobOrderOut(
            id=job.id,
            job_number=job.job_number,
            part_id=job.part_id,
            part_number=part_number,
            customer_name=job.customer_name,
            planned_quantity=job.planned_quantity,
            status=job.status.value,
            due_date=job.due_date,
            notes=job.notes,
        )
        for job, part_number in rows
    ]


@router.post(
    "/job-orders",
    response_model=JobOrderOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(*DAILY_ROLES))],
)
async def create_job_order(
    payload: JobOrderIn,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    part = await in_plant(session, Part, payload.part_id, identity.plant_id, "Part")
    if not part.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Part {part.part_number} is inactive")

    job_number = payload.job_number.strip() if payload.job_number else None
    job = JobOrder(
        plant_id=identity.plant_id,
        job_number=job_number or await _next_job_number(session, identity.plant_id),
        part_id=part.id,
        # Customer comes from the part: nobody types a customer name (§3).
        customer_name=payload.customer_name or part.customer_name,
        planned_quantity=payload.planned_quantity,
        due_date=payload.due_date,
        notes=payload.notes,
        status=JobOrderStatus.PLANNED,
    )
    session.add(job)
    await _flush_unique(session, f"Job order {job.job_number}")
    return await _job_out(session, job)


@router.patch(
    "/job-orders/{job_id}",
    response_model=JobOrderOut,
    dependencies=[Depends(require(*DAILY_ROLES))],
)
async def update_job_order(
    job_id: uuid.UUID,
    payload: JobOrderPatch,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    job = await in_plant(session, JobOrder, job_id, identity.plant_id, "Job order")
    changes = payload.model_dump(exclude_unset=True)
    if "status" in changes:
        target = JobOrderStatus(changes.pop("status"))
        finished = (JobOrderStatus.COMPLETED, JobOrderStatus.CANCELLED)
        if job.status in finished and target != job.status:
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"Job {job.job_number} is already {job.status.value}"
            )
        job.status = target
        if target is JobOrderStatus.COMPLETED:
            job.completed_at = datetime.now(UTC)
    for field, value in changes.items():
        setattr(job, field, value)
    await session.flush()
    return await _job_out(session, job)


@router.post(
    "/job-orders/{job_id}/repeat",
    response_model=JobOrderOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(*DAILY_ROLES))],
)
async def repeat_job_order(
    job_id: uuid.UUID,
    payload: JobOrderRepeat,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    """A repeat job for the same part and customer, under a new number (§14.1)."""
    source = await in_plant(session, JobOrder, job_id, identity.plant_id, "Job order")
    job = JobOrder(
        plant_id=identity.plant_id,
        job_number=await _next_job_number(session, identity.plant_id),
        part_id=source.part_id,
        customer_name=source.customer_name,
        planned_quantity=payload.planned_quantity or source.planned_quantity,
        due_date=payload.due_date,
        notes=f"Repeat of {source.job_number}",
        status=JobOrderStatus.PLANNED,
    )
    session.add(job)
    await _flush_unique(session, f"Job order {job.job_number}")
    return await _job_out(session, job)
