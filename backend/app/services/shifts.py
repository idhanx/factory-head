"""Shift lifecycle — Screen 1 (start) and Screen 6 (closure lock).

The closure lock is the mechanism that makes manually entered data honest:

    "A supervisor cannot close their shift while any downtime event has no
     reason, any production entry is missing, or any escalation is unresolved.
     Closing the shift is how they go home — so the data gets filled."  (§2)
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.kernel import ClosureCheck, check_shift_closure
from app.models import (
    DowntimeEvent,
    DowntimeStatus,
    Escalation,
    EscalationStatus,
    JobSession,
    Plant,
    ProductionCount,
    Shift,
    ShiftSession,
    ShiftSessionStatus,
    Station,
)
from app.services.clock import local_date


def resolve_business_date(plant: Plant, shift: Shift, started_at: datetime) -> date:
    """Which plant-day does this shift belong to?

    §16.5 open decision 3. A 22:00-06:00 shift could reasonably belong to either
    calendar date, and every "yesterday" number the owner sees depends on the
    answer — so it is a plant setting, resolved once at shift start and then
    stored, never re-derived from a timestamp later.
    """
    started = local_date(plant, started_at)
    if plant.business_date_from_shift_start or not shift.crosses_midnight:
        return started
    return started - timedelta(days=1)


def planned_minutes(shift: Shift) -> int:
    """Shift duration less contractual breaks — the OEE denominator's starting point."""
    start = datetime.combine(date.min, shift.start_time)
    end = datetime.combine(date.min, shift.end_time)
    if end <= start:
        end += timedelta(days=1)
    total = int((end - start).total_seconds() // 60)
    return max(0, total - (shift.break_minutes or 0))


async def start_shift(
    session: AsyncSession,
    *,
    plant: Plant,
    shift: Shift,
    supervisor_id: uuid.UUID | None,
    started_at: datetime | None = None,
) -> ShiftSession:
    """Screen 1 — under 60 seconds.

    Idempotent by design: a supervisor who taps Start twice, or a second
    supervisor opening the same shift on a shared tablet, joins the existing
    session rather than creating a duplicate day.
    """
    started_at = started_at or datetime.now(UTC)
    business_date = resolve_business_date(plant, shift, started_at)

    existing = await session.scalar(
        select(ShiftSession).where(
            ShiftSession.plant_id == plant.id,
            ShiftSession.shift_id == shift.id,
            ShiftSession.business_date == business_date,
        )
    )
    if existing is not None:
        return existing

    shift_session = ShiftSession(
        plant_id=plant.id,
        shift_id=shift.id,
        business_date=business_date,
        started_at=started_at,
        supervisor_id=supervisor_id,
        status=ShiftSessionStatus.OPEN,
        planned_minutes=planned_minutes(shift),
    )
    session.add(shift_session)
    await session.flush()
    return shift_session


async def closure_blockers(
    session: AsyncSession, shift_session: ShiftSession
) -> ClosureCheck:
    """Everything standing between the supervisor and going home.

    Each blocker is actionable and names the station — a checklist, not an error
    message. The supervisor should be able to clear it in under two minutes
    (§3's "shift closure: 2-3 minutes").
    """
    unreasoned = (
        await session.execute(
            select(DowntimeEvent.id, Station.code)
            .join(Station, Station.id == DowntimeEvent.station_id)
            .where(
                DowntimeEvent.shift_session_id == shift_session.id,
                DowntimeEvent.reason_code_id.is_(None),
            )
        )
    ).all()

    still_open = (
        await session.execute(
            select(DowntimeEvent.id, Station.code)
            .join(Station, Station.id == DowntimeEvent.station_id)
            .where(
                DowntimeEvent.shift_session_id == shift_session.id,
                DowntimeEvent.status == DowntimeStatus.OPEN,
            )
        )
    ).all()

    # A station that ran a job this shift but has no count is a gap in the data,
    # not an empty station.
    worked = (
        await session.execute(
            select(Station.id, Station.code)
            .join(JobSession, JobSession.station_id == Station.id)
            .where(JobSession.shift_session_id == shift_session.id)
            .distinct()
        )
    ).all()
    counted = set(
        (
            await session.scalars(
                select(ProductionCount.station_id).where(
                    ProductionCount.shift_session_id == shift_session.id
                )
            )
        ).all()
    )
    missing_counts = [code for station_id, code in worked if station_id not in counted]

    unresolved = (
        await session.execute(
            select(Escalation.id, Escalation.message)
            .join(DowntimeEvent, DowntimeEvent.id == Escalation.downtime_event_id)
            .where(
                DowntimeEvent.shift_session_id == shift_session.id,
                Escalation.status.in_([EscalationStatus.SENT, EscalationStatus.FAILED]),
                Escalation.acknowledged_at.is_(None),
                Escalation.resolved_at.is_(None),
                Escalation.level >= 2,
            )
        )
    ).all()

    return check_shift_closure(
        downtime_without_reason=[(str(i), c) for i, c in unreasoned],
        stations_without_production=missing_counts,
        open_downtime_events=[(str(i), c) for i, c in still_open],
        unresolved_escalations=[
            (str(i), m or "Escalation awaiting acknowledgement") for i, m in unresolved
        ],
    )


async def close_shift(
    session: AsyncSession,
    *,
    shift_session: ShiftSession,
    closed_by_id: uuid.UUID,
    override_reason: str | None = None,
    override_by_id: uuid.UUID | None = None,
    closed_at: datetime | None = None,
) -> ShiftSession:
    """Lock the shift. After this, nothing inside it can be edited.

    An owner override is permitted — a supervisor should never have to invent a
    reason code to go home — but it is recorded on the row and reported, so
    overriding is always visible. Section 12 risk 1 tracks the percentage of
    shifts closed without override as the adoption health signal.
    """
    closed_at = closed_at or datetime.now(UTC)

    shift_session.status = ShiftSessionStatus.CLOSED
    shift_session.ended_at = closed_at
    shift_session.locked_at = closed_at
    shift_session.closed_by_id = closed_by_id
    if override_reason:
        shift_session.override_reason = override_reason
        shift_session.override_by_id = override_by_id

    # Close out any job sessions still running, so throughput maths terminates.
    open_sessions = (
        await session.scalars(
            select(JobSession).where(
                JobSession.shift_session_id == shift_session.id,
                JobSession.ended_at.is_(None),
            )
        )
    ).all()
    for job_session in open_sessions:
        job_session.ended_at = closed_at

    await session.flush()
    return shift_session


async def is_locked(session: AsyncSession, shift_session_id: uuid.UUID | None) -> bool:
    """Guard every write. A locked shift is arithmetically frozen."""
    if shift_session_id is None:
        return False
    locked = await session.scalar(
        select(ShiftSession.locked_at).where(ShiftSession.id == shift_session_id)
    )
    return locked is not None


async def current_open_session(
    session: AsyncSession, plant_id: uuid.UUID
) -> ShiftSession | None:
    return await session.scalar(
        select(ShiftSession)
        .where(
            ShiftSession.plant_id == plant_id,
            ShiftSession.status == ShiftSessionStatus.OPEN,
        )
        .order_by(ShiftSession.started_at.desc())
        .limit(1)
    )


async def shift_duration_minutes(
    session: AsyncSession, shift_session: ShiftSession
) -> Decimal:
    """Planned minutes for KPI denominators — the stored value, not a re-derivation."""
    if shift_session.planned_minutes:
        return Decimal(shift_session.planned_minutes)
    shift = await session.get(Shift, shift_session.shift_id)
    return Decimal(planned_minutes(shift)) if shift else Decimal(0)
