"""Downtime capture (Screen 3) and the escalation ladder.

Twenty seconds to log a stop, one tap to close it. Everything else — costing,
escalation scheduling, action triggers — happens behind that.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.kernel import ESCALATION_LADDER, downtime_event_cost, format_inr
from app.models import (
    AlertRecipient,
    DataSource,
    DowntimeEvent,
    DowntimeStatus,
    Escalation,
    EscalationChannel,
    EscalationStatus,
    JobSession,
    Part,
    Plant,
    ReasonCode,
    RoutingOperation,
    ShiftSession,
    Station,
)
from app.services.costing import apply_snapshot, resolve_snapshot, snapshot_from_row


async def open_downtime(
    session: AsyncSession,
    *,
    plant: Plant,
    station: Station,
    reason_code: ReasonCode | None,
    shift_session: ShiftSession,
    started_at: datetime | None = None,
    note: str | None = None,
    logged_by_id: uuid.UUID | None = None,
    source: DataSource = DataSource.MANUAL,
    source_ref: str | None = None,
    client_ref: str | None = None,
) -> DowntimeEvent:
    """'Machine Stopped' — the rupee ticker starts here.

    The cost snapshot is resolved and frozen at *open*, not at close, so the
    ticker the shopfloor watches and the number the owner reads are computed
    from identical inputs.

    ``source`` is the seam (§2): a CT clamp emitting a stop later calls this
    same function with ``DataSource.SENSOR``, and nothing above the data layer
    changes.
    """
    started_at = started_at or datetime.now(UTC)

    if client_ref:
        replayed = await session.scalar(
            select(DowntimeEvent).where(
                DowntimeEvent.plant_id == plant.id, DowntimeEvent.client_ref == client_ref
            )
        )
        if replayed is not None:
            return replayed

    # A station cannot be stopped twice. If it already is, return the open event
    # rather than creating an overlapping one — two supervisors on a shared
    # tablet must not double-count a single stop.
    existing = await session.scalar(
        select(DowntimeEvent).where(
            DowntimeEvent.plant_id == plant.id,
            DowntimeEvent.station_id == station.id,
            DowntimeEvent.status == DowntimeStatus.OPEN,
        )
    )
    if existing is not None:
        # Two tablets logged the same stop: the second one's later "Running"
        # must still find it, so it learns this stop's reference if it has none.
        if client_ref and existing.client_ref is None:
            existing.client_ref = client_ref
        return existing

    job_session = await _active_job_session(session, plant.id, station.id, at=started_at)
    part = await _part_for(session, job_session)
    operation = (
        await session.get(RoutingOperation, job_session.routing_operation_id)
        if job_session and job_session.routing_operation_id
        else None
    )

    snapshot, _ = await resolve_snapshot(
        session,
        plant=plant,
        station=station,
        part=part,
        on_date=shift_session.business_date,
        routing_operation=operation,
    )

    event = DowntimeEvent(
        plant_id=plant.id,
        station_id=station.id,
        job_session_id=job_session.id if job_session else None,
        shift_session_id=shift_session.id,
        business_date=shift_session.business_date,
        reason_code_id=reason_code.id if reason_code else None,
        started_at=started_at,
        status=DowntimeStatus.OPEN,
        note=note,
        source=source,
        source_ref=source_ref,
        client_ref=client_ref,
        logged_by_id=logged_by_id,
    )
    apply_snapshot(event, snapshot)
    session.add(event)
    await session.flush()

    await schedule_escalations(session, plant=plant, event=event)
    return event


async def close_downtime(
    session: AsyncSession,
    *,
    event: DowntimeEvent,
    ended_at: datetime | None = None,
    reason_code: ReasonCode | None = None,
) -> DowntimeEvent:
    """'Running' — one tap. The final cost is computed and frozen here."""
    ended_at = ended_at or datetime.now(UTC)
    if ended_at < event.started_at:
        raise ValueError("A machine cannot restart before it stopped")

    if reason_code is not None:
        event.reason_code_id = reason_code.id

    minutes = Decimal((ended_at - event.started_at).total_seconds()) / Decimal(60)
    cost = downtime_event_cost(minutes, snapshot_from_row(event))

    event.ended_at = ended_at
    event.duration_minutes = minutes
    event.status = DowntimeStatus.CLOSED
    event.cost_total = cost.total
    event.cost_breakdown = {
        "machine": cost.machine_cost,
        "labour": cost.labour_cost,
        "overhead": cost.overhead_cost,
        "lost_contribution": cost.lost_contribution,
        "parts_not_made": cost.parts_not_made,
    }

    # Any escalation rung that hasn't fired yet is moot, and any that has is
    # resolved: the machine is running.
    await _settle_escalations(session, event.id, resolved_at=ended_at)
    await session.flush()
    return event


async def schedule_escalations(
    session: AsyncSession, *, plant: Plant, event: DowntimeEvent
) -> list[Escalation]:
    """Queue the 5/15/30-minute ladder as durable rows.

    Rows in a table, not timers in a process: "every escalation is timestamped,
    nobody can later say they didn't know" only holds if the schedule survives a
    restart. The worker claims due rows; the API never sleeps.
    """
    reason = (
        await session.get(ReasonCode, event.reason_code_id)
        if event.reason_code_id
        else None
    )

    escalations: list[Escalation] = []
    for level, delay in ESCALATION_LADDER:
        route = escalation_route_for(reason, int(level))
        recipient = await _recipient_for(session, plant.id, route)
        escalations.append(
            Escalation(
                plant_id=plant.id,
                downtime_event_id=event.id,
                level=int(level),
                due_at=event.started_at + delay,
                status=EscalationStatus.PENDING,
                channel=(
                    EscalationChannel.PUSH
                    if int(level) == 1
                    else EscalationChannel.WHATSAPP
                ),
                # The role is stored beside the person. A name answers "who was
                # told"; the route answers "whose problem is this" — which is
                # what the maintenance and stores desks filter their inbox on,
                # and it survives the recipient list being edited next month.
                route=route,
                recipient_name=recipient.name if recipient else None,
                recipient_number=recipient.whatsapp_number if recipient else None,
            )
        )
    session.add_all(escalations)
    await session.flush()
    return escalations


def escalation_message(
    *, station_code: str, reason_label: str, job_number: str | None, minutes: int, cost: int
) -> str:
    """§6, 10:40 AM — the message Stores actually receives.

    The running cost is in the text on purpose: 'Press-2 is stopped' is
    ignorable, '₹1,050 and counting' is not.
    """
    job = f" for Job #{job_number}" if job_number else ""
    return (
        f"{station_code} waiting on {reason_label.lower()}{job} — "
        f"{minutes} min, {format_inr(cost)} and counting."
    )


async def _settle_escalations(
    session: AsyncSession, event_id: uuid.UUID, *, resolved_at: datetime
) -> None:
    rows = (
        await session.scalars(
            select(Escalation).where(Escalation.downtime_event_id == event_id)
        )
    ).all()
    for row in rows:
        if row.status is EscalationStatus.PENDING:
            row.status = EscalationStatus.CANCELLED
        elif row.resolved_at is None and row.status in (
            EscalationStatus.SENT,
            EscalationStatus.FAILED,
        ):
            row.resolved_at = resolved_at


def escalation_route_for(reason: ReasonCode | None, level: int) -> str:
    """§3 Layer 2 — "the system picks the recipient from the reason code".

    A material wait goes to Stores, a breakdown to Maintenance. Nobody chooses
    from a dropdown at 10:40 AM with a line down.

    Level 1 is the push to the supervisor who is already looking at the screen,
    level 3 is the plant head with the running cost, and level 2 is the rung the
    reason code actually decides.
    """
    if level == 1:
        return "supervisor"
    if level >= 3:
        return "plant_head"
    if reason is not None:
        return reason.escalation_route.value
    return "production"


async def _recipient_for(
    session: AsyncSession, plant_id: uuid.UUID, route: str
) -> AlertRecipient | None:
    """The person holding that role tag in this plant, if anyone does."""
    if route == "supervisor":
        return None  # already on the screen; the push needs no recipient row

    return await session.scalar(
        select(AlertRecipient)
        .where(
            AlertRecipient.plant_id == plant_id,
            AlertRecipient.role_tag == route,
            AlertRecipient.is_active.is_(True),
        )
        .limit(1)
    )


async def _active_job_session(
    session: AsyncSession,
    plant_id: uuid.UUID,
    station_id: uuid.UUID,
    at: datetime | None = None,
) -> JobSession | None:
    """Whatever job this station was running at ``at`` (now, when not given).

    This is how an event inherits its part, customer, lot and operator without
    anyone typing them (§3: "the tags travel with the job").
    """
    from app.services.capture import _active_job_session as job_at

    return await job_at(session, plant_id, station_id, at=at)


async def _part_for(session: AsyncSession, job_session: JobSession | None) -> Part | None:
    if job_session is None:
        return None
    job_order = await session.scalar(
        select(JobSession)
        .where(JobSession.id == job_session.id)
        .options(selectinload(JobSession.job_order))
    )
    if job_order is None or job_order.job_order is None:
        return None
    return await session.get(Part, job_order.job_order.part_id)
