"""The escalation ladder, seen from the people it reaches.

"Every escalation is timestamped. Nobody can later say they didn't know."
(§3 Layer 2). The other half of that sentence is that somebody has to be able
to say they *do* know — an escalation that can be sent but never answered
blocks the shift it belongs to forever.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Identity, get_identity, get_session
from app.api.scoping import in_plant
from app.kernel import format_inr
from app.models import DowntimeEvent, Escalation, EscalationStatus, Station, User
from app.schemas import EscalationOut

router = APIRouter(prefix="/escalations", tags=["escalations"])

FIRED = (EscalationStatus.SENT, EscalationStatus.FAILED)


@router.get("", response_model=list[EscalationOut])
async def list_escalations(
    open: bool = True,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    """Fired rungs nobody has answered yet (``open=true``), or the recent ladder."""
    query = (
        select(Escalation, Station.code)
        .join(DowntimeEvent, DowntimeEvent.id == Escalation.downtime_event_id)
        .join(Station, Station.id == DowntimeEvent.station_id)
        .where(Escalation.plant_id == identity.plant_id)
    )
    if open:
        query = query.where(
            Escalation.status.in_(FIRED),
            Escalation.acknowledged_at.is_(None),
            Escalation.resolved_at.is_(None),
        )
    rows = (
        await session.execute(query.order_by(Escalation.due_at.desc()).limit(200))
    ).all()
    names = await _names(session, {e.acknowledged_by_id for e, _ in rows})
    return [_out(escalation, code, names) for escalation, code in rows]


@router.post("/{escalation_id}/acknowledge", response_model=EscalationOut)
async def acknowledge(
    escalation_id: uuid.UUID,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    """'On it.' Recorded with a name and a time, like the escalation itself."""
    escalation = await in_plant(
        session, Escalation, escalation_id, identity.plant_id, "Escalation"
    )
    if escalation.status not in FIRED:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Only an escalation that has been sent can be acknowledged"
        )
    if escalation.acknowledged_at is None:
        escalation.acknowledged_at = datetime.now(UTC)
        escalation.acknowledged_by_id = identity.user.id
        await session.flush()

    event = await session.get(DowntimeEvent, escalation.downtime_event_id)
    station = await session.get(Station, event.station_id) if event else None
    names = await _names(session, {escalation.acknowledged_by_id})
    return _out(escalation, station.code if station else None, names)


async def _names(session: AsyncSession, user_ids: set) -> dict:
    ids = {user_id for user_id in user_ids if user_id}
    if not ids:
        return {}
    return dict(
        (await session.execute(select(User.id, User.full_name).where(User.id.in_(ids)))).all()
    )


def _out(escalation: Escalation, station_code: str | None, names: dict) -> EscalationOut:
    return EscalationOut(
        id=escalation.id,
        level=escalation.level,
        status=escalation.status.value,
        channel=escalation.channel.value,
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
