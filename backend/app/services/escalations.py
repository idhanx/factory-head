"""Escalations, read back.

The ladder is scheduled and fired in ``services.downtime``; this module is the
other direction — the rungs as the people they reached see them. Three callers
need the same rows and the same presentation: the escalation list itself, the
maintenance desk and the stores desk. They read through here rather than each
rebuilding the query, so "is this still open?" cannot mean three things.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.kernel import format_inr
from app.models import (
    DowntimeEvent,
    Escalation,
    EscalationStatus,
    ReasonCode,
    Station,
    User,
)
from app.schemas import EscalationOut

#: Rungs that have actually gone out. A PENDING row is a future promise, not an
#: alert anybody has received, and must never appear in an inbox.
FIRED = (EscalationStatus.SENT, EscalationStatus.FAILED)


def base_query(plant_id: uuid.UUID) -> Select:
    return (
        select(Escalation, Station.code)
        .join(DowntimeEvent, DowntimeEvent.id == Escalation.downtime_event_id)
        .join(Station, Station.id == DowntimeEvent.station_id)
        .where(Escalation.plant_id == plant_id)
    )


def only_open(query: Select) -> Select:
    """Fired, not yet answered, and the machine is still down."""
    return query.where(
        Escalation.status.in_(FIRED),
        Escalation.acknowledged_at.is_(None),
        Escalation.resolved_at.is_(None),
    )


async def open_for_route(
    session: AsyncSession, plant_id: uuid.UUID, *, route: str, limit: int = 50
) -> list[EscalationOut]:
    """The unanswered rungs addressed to one role, dearest first.

    The route is read from the escalation row, never re-derived from the reason
    code: if the plant re-tags 'Material Wait' to Maintenance tomorrow, what
    Stores was told this morning must not change retrospectively.
    """
    rows = (
        await session.execute(
            only_open(base_query(plant_id))
            .where(Escalation.route == route)
            .order_by(Escalation.cost_at_send.desc(), Escalation.due_at.desc())
            .limit(limit)
        )
    ).all()
    names = await names_for(session, {e.acknowledged_by_id for e, _ in rows})
    return [present(escalation, code, names) for escalation, code in rows]


async def names_for(session: AsyncSession, user_ids: set) -> dict:
    ids = {user_id for user_id in user_ids if user_id}
    if not ids:
        return {}
    return dict(
        (await session.execute(select(User.id, User.full_name).where(User.id.in_(ids)))).all()
    )


async def reason_route(session: AsyncSession, event: DowntimeEvent) -> str | None:
    """Which role a stop belongs to, from its reason code (§3 Layer 2)."""
    if event.reason_code_id is None:
        return None
    reason = await session.get(ReasonCode, event.reason_code_id)
    return reason.escalation_route.value if reason else None


def present(escalation: Escalation, station_code: str | None, names: dict) -> EscalationOut:
    return EscalationOut(
        id=escalation.id,
        level=escalation.level,
        status=escalation.status.value,
        channel=escalation.channel.value,
        route=escalation.route,
        downtime_event_id=escalation.downtime_event_id,
        station_code=station_code,
        message=escalation.message,
        recipient_name=escalation.recipient_name,
        cost_at_send_paise=escalation.cost_at_send or 0,
        cost_at_send_display=format_inr(escalation.cost_at_send or 0),
        due_at=escalation.due_at,
        sent_at=escalation.sent_at,
        acknowledged_at=escalation.acknowledged_at,
        acknowledged_by_name=names.get(escalation.acknowledged_by_id),
        resolved_at=escalation.resolved_at,
        is_open=(
            escalation.status in FIRED
            and escalation.acknowledged_at is None
            and escalation.resolved_at is None
        ),
    )
