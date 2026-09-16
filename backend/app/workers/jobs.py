"""What the worker actually does, off the request path."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import coach as coach_service
from app.core.db import SessionFactory, set_plant_scope
from app.kernel import format_inr
from app.models import (
    Action,
    AlertRecipient,
    CoachBriefing,
    DowntimeEvent,
    DowntimeStatus,
    Escalation,
    EscalationChannel,
    EscalationStatus,
    JobOrder,
    JobSession,
    NotificationStatus,
    PdcaState,
    Plant,
    ReasonCode,
    Role,
    Shift,
    Station,
)
from app.services import actions as action_service
from app.services import compliance as compliance_service
from app.services import notifications
from app.services import reports as report_service
from app.services import skills as skills_service
from app.services.clock import plant_now, plant_today
from app.services.costing import cost_open_event
from app.services.downtime import escalation_message
from app.ws.manager import ESCALATION_FIRED, Event, manager

log = structlog.get_logger()

#: §3 Layer 2 — "every morning at 5:45 AM, before the shift starts".
COACH_TIME = time(5, 45)
#: §4.4 — the morning-meeting pack and the owner's summary, 8 AM.
DAILY_REPORT_TIME = time(8, 0)


async def fire_due_escalations() -> None:
    """Send every escalation whose moment has come.

    Runs every 30 seconds. The rungs are 5/15/30 minutes, so this is more than
    precise enough — and being a minute late is infinitely better than losing
    the escalation to a restart.
    """
    now = datetime.now(UTC)
    processed = 0

    async with SessionFactory() as session:
        # Row-level security binds every query to one plant, so the sweep walks
        # plants rather than reading one cross-plant queue.
        for plant in await _active_plants(session):
            await set_plant_scope(session, plant.id)
            processed += await _fire_for_plant(session, plant, now)
        await session.commit()
        if processed:
            log.info("worker.escalations", processed=processed)


async def _fire_for_plant(session: AsyncSession, plant: Plant, now: datetime) -> int:
    due = (
        await session.scalars(
            select(Escalation)
            .where(
                Escalation.status == EscalationStatus.PENDING,
                Escalation.due_at <= now,
            )
            .order_by(Escalation.due_at)
            .limit(100)
        )
    ).all()

    for escalation in due:
        event = await session.get(DowntimeEvent, escalation.downtime_event_id)

        # The machine restarted before this rung came due. Nothing to chase.
        if event is None or event.status is DowntimeStatus.CLOSED:
            escalation.status = EscalationStatus.CANCELLED
            continue

        station = await session.get(Station, event.station_id)
        reason = (
            await session.get(ReasonCode, event.reason_code_id)
            if event.reason_code_id
            else None
        )
        job_number = await _job_number_for(session, event)

        live = cost_open_event(event, as_of=now)
        escalation.cost_at_send = live.total
        escalation.message = escalation_message(
            station_code=station.code if station else "Station",
            reason_label=reason.label if reason else "an unrecorded stop",
            job_number=job_number,
            minutes=int(live.duration_minutes),
            cost=live.total,
        )

        queued = await _deliver(session, plant, escalation, event, reason)
        escalation.sent_at = now
        if notifications.reached_anyone(queued):
            escalation.status = EscalationStatus.SENT
            escalation.channel = EscalationChannel(
                next(
                    row.channel.value
                    for row in queued
                    if row.status is not NotificationStatus.SKIPPED
                )
            )
        else:
            # Recorded as failed, not quietly marked sent: nobody's phone rang.
            escalation.status = EscalationStatus.FAILED
            escalation.channel = EscalationChannel.IN_APP
            escalation.provider_response = {
                "error": "; ".join(sorted({row.last_error or "" for row in queued}))
                or "No recipient for this rung"
            }
        event.escalation_level = max(event.escalation_level, escalation.level)

        await manager.publish(
            escalation.plant_id,
            Event(
                ESCALATION_FIRED,
                {
                    "escalation_id": str(escalation.id),
                    "level": escalation.level,
                    "station": station.code if station else None,
                    "message": escalation.message,
                    "cost_display": format_inr(escalation.cost_at_send),
                },
            ),
        )

    return len(due)


async def _active_plant_ids(session: AsyncSession) -> list:
    return list(
        (await session.scalars(select(Plant.id).where(Plant.is_active.is_(True)))).all()
    )


async def _active_plants(session: AsyncSession) -> list[Plant]:
    return list(
        (await session.scalars(select(Plant).where(Plant.is_active.is_(True)))).all()
    )


async def _deliver(
    session: AsyncSession,
    plant: Plant,
    escalation: Escalation,
    event: DowntimeEvent,
    reason: ReasonCode | None,
) -> list:
    """Queue the rung for the people it is meant to reach (§3 Layer 2).

    5 min — a push to the supervisors, who have the tablet in hand.
    15 min — WhatsApp (or SMS) to Maintenance or Stores, picked by the reason.
    30 min — the Plant Head and the owner, with the running cost.
    """
    if escalation.level <= 1:
        recipients = await notifications.users_with_role(session, plant.id, Role.SUPERVISOR)
        push_only = True
    elif escalation.level == 2:
        route = getattr(getattr(reason, "escalation_route", None), "value", None) or "production"
        recipients = await notifications.recipients_for_route(
            session, plant.id, "production" if route == "none" else route
        )
        push_only = False
    else:
        recipients = notifications.merge_recipients(
            await notifications.recipients_for_route(session, plant.id, "plant_head"),
            await notifications.owners(session, plant.id),
        )
        push_only = False

    return await notifications.enqueue(
        session,
        plant=plant,
        kind="escalation",
        recipients=recipients,
        title=f"Machine stopped · level {escalation.level}",
        body=escalation.message or "A machine is stopped.",
        url="/floor",
        subject_type="escalation",
        subject_id=escalation.id,
        dedupe_key=f"escalation:{escalation.id}",
        push_only=push_only,
    )


async def _job_number_for(session: AsyncSession, event: DowntimeEvent) -> str | None:
    if event.job_session_id is None:
        return None
    job_session = await session.get(JobSession, event.job_session_id)
    if job_session is None:
        return None
    job_order = await session.get(JobOrder, job_session.job_order_id)
    return job_order.job_number if job_order else None


async def accrue_recovered_profit() -> None:
    """Weekly CHECK/ACT: did the fixes actually hold? (§5.6)

    Runs hourly. It is cheap, and an owner who opens the app on a Monday should
    see Sunday's verification already reflected.
    """

    async with SessionFactory() as session:
        plants = (
            await session.scalars(select(Plant).where(Plant.is_active.is_(True)))
        ).all()

        for plant in plants:
            await set_plant_scope(session, plant.id)
            monitoring = (
                await session.scalars(
                    select(Action).where(
                        Action.plant_id == plant.id,
                        Action.state.in_([PdcaState.MONITORING, PdcaState.VERIFIED]),
                        Action.closed_at.is_not(None),
                    )
                )
            ).all()

            today = plant_today(plant)
            for action in monitoring:
                # Accrue first, so a reopen keeps exactly the weeks that held.
                await action_service.accrue_recovered_profit(
                    session, plant=plant, action=action, as_of=today
                )
                await action_service.reopen_if_recurred(
                    session, plant=plant, action=action, as_of=today
                )

        await session.commit()


async def generate_coach_briefings() -> None:
    """The 5:45 AM briefing (§14.6), in each plant's own time.

    Generated and sent before the shift so the supervisor's phone buzzes before
    they leave home — not when they open the app on the floor an hour later. A
    Tier 0 Coach stays silent: nothing is sent (§14.6, "saying nothing beats
    guessing").
    """
    async with SessionFactory() as session:
        for plant in await _active_plants(session):
            await set_plant_scope(session, plant.id)
            local = plant_now(plant)
            if local.time() < COACH_TIME:
                continue
            today = local.date()

            briefing = await session.scalar(
                select(CoachBriefing)
                .where(CoachBriefing.plant_id == plant.id, CoachBriefing.business_date == today)
                .order_by(CoachBriefing.created_at.desc())
                .limit(1)
            )
            if briefing is None:
                first_shift = await session.scalar(
                    select(Shift)
                    .where(Shift.plant_id == plant.id, Shift.is_active.is_(True))
                    .order_by(Shift.display_order)
                    .limit(1)
                )
                recipient = await session.scalar(
                    select(AlertRecipient).where(
                        AlertRecipient.plant_id == plant.id,
                        AlertRecipient.role_tag == "production",
                    )
                )
                briefing = await coach_service.generate_briefing(
                    session,
                    plant=plant,
                    shift=first_shift,
                    recipient_id=recipient.user_id if recipient else None,
                    as_of=today,
                )
                log.info(
                    "worker.coach_briefing",
                    plant=plant.code,
                    tier=briefing.tier,
                    priorities=len(briefing.priorities or []),
                )

            if briefing.delivered_at is None and briefing.tier > 0 and briefing.priorities:
                queued = await notifications.enqueue(
                    session,
                    plant=plant,
                    kind="coach_briefing",
                    recipients=notifications.merge_recipients(
                        await notifications.recipients_for_route(session, plant.id, "production"),
                        await notifications.users_with_role(session, plant.id, Role.SUPERVISOR),
                    ),
                    title="Today's three priorities",
                    body=(briefing.rendered_text or "")[:1000],
                    url="/coach",
                    subject_type="coach_briefing",
                    subject_id=briefing.id,
                    dedupe_key=f"coach:{today.isoformat()}",
                )
                if notifications.reached_anyone(queued):
                    briefing.delivered_at = datetime.now(UTC)

        await session.commit()


async def send_daily_owner_reports() -> None:
    """Yesterday's losses on the owner's phone at 8 AM plant time (§4.4)."""
    async with SessionFactory() as session:
        for plant in await _active_plants(session):
            await set_plant_scope(session, plant.id)
            local = plant_now(plant)
            if local.time() < DAILY_REPORT_TIME:
                continue
            yesterday = local.date() - timedelta(days=1)
            await notifications.enqueue(
                session,
                plant=plant,
                kind="daily_report",
                recipients=await notifications.owners(session, plant.id),
                title=f"Yesterday at {plant.name}",
                body=await report_service.daily_owner_report_text(
                    session, plant=plant, day=yesterday
                ),
                url="/owner",
                dedupe_key=f"daily:{yesterday.isoformat()}",
            )
        await session.commit()


async def dispatch_notifications() -> None:
    """Send whatever the outbox has due, plant by plant."""
    sent = 0
    async with SessionFactory() as session:
        plants = await _active_plants(session)
    for plant in plants:
        async with SessionFactory() as session:
            await set_plant_scope(session, plant.id)
            sent += await notifications.dispatch_due(session, plant=plant)
            await session.commit()
    if sent:
        log.info("worker.dispatch", attempted=sent)


async def sweep_stale_open_events() -> None:
    """Flag downtime that has been 'open' far longer than any real stop.

    A machine marked stopped for eleven hours is almost always a supervisor who
    forgot to tap 'Running', and its ticker is quietly inflating the plant's
    loss figure. Rather than auto-closing it — which would invent an end time —
    this surfaces it for the shift-closure checklist to catch.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=12)

    async with SessionFactory() as session:
        for plant_id in await _active_plant_ids(session):
            await set_plant_scope(session, plant_id)
            stale = (
                await session.scalars(
                    select(DowntimeEvent).where(
                        DowntimeEvent.status == DowntimeStatus.OPEN,
                        DowntimeEvent.started_at < cutoff,
                    )
                )
            ).all()
            if stale:
                log.warning(
                    "worker.stale_downtime",
                    plant_id=str(plant_id),
                    count=len(stale),
                    ids=[str(e.id) for e in stale[:10]],
                )


async def refresh_plugins() -> None:
    """Re-score audit readiness and re-detect training needs for every plant.

    Reads of these screens no longer write, so this is what keeps them current.
    Each plant runs in its own transaction: one plant's bad data must not stop
    another plant's audit score from updating.
    """
    async with SessionFactory() as session:
        plant_ids = await _active_plant_ids(session)

    for plant_id in plant_ids:
        try:
            async with SessionFactory() as session:
                await set_plant_scope(session, plant_id)
                plant = await session.get(Plant, plant_id)
                await compliance_service.refresh(session, plant=plant)
                await skills_service.detect_needs(session, plant=plant)
                await session.commit()
        except Exception as exc:  # pragma: no cover - logged for the operator
            log.error("worker.refresh_plugins_failed", plant_id=str(plant_id), error=str(exc))
