"""The escalation ladder, seen from the people it reaches.

"Every escalation is timestamped. Nobody can later say they didn't know."
(§3 Layer 2). The other half of that sentence is that somebody has to be able
to say they *do* know — an escalation that can be sent but never answered
blocks the shift it belongs to forever.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Identity, get_identity, get_session
from app.api.scoping import in_plant
from app.models import DowntimeEvent, Escalation, Role, Station
from app.schemas import EscalationOut
from app.services import escalations as service

router = APIRouter(prefix="/escalations", tags=["escalations"])

#: Which role tag each plant role answers for. §3 Layer 2 routes a rung by the
#: reason code — a material wait to Stores, a breakdown to Maintenance — so the
#: inbox filter has to map the other way: from the signed-in person's roles to
#: the routes that are theirs to answer.
ROUTES_BY_ROLE: dict[Role, tuple[str, ...]] = {
    Role.MAINTENANCE: ("maintenance",),
    Role.STORES: ("stores",),
    Role.QUALITY: ("quality",),
    Role.SUPERVISOR: ("supervisor", "production"),
    # The third rung is the plant head's, and §7 puts the owner's home on money
    # rather than machines — but an owner who opens this list is looking for the
    # stops that reached them, plus the floor they oversee.
    Role.OWNER: ("plant_head", "production", "supervisor"),
    Role.CONSULTANT: ("plant_head", "production", "supervisor"),
}


def routes_for(identity: Identity) -> set[str]:
    routes: set[str] = set()
    for role in identity.roles:
        routes.update(ROUTES_BY_ROLE.get(role, ()))
    return routes


@router.get("", response_model=list[EscalationOut])
async def list_escalations(
    open: bool = True,
    mine: bool = Query(
        default=False,
        description=(
            "Only the rungs routed to the signed-in person's own role(s). "
            "This is what the maintenance and stores desks ask for."
        ),
    ),
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    """Fired rungs nobody has answered yet (``open=true``), or the recent ladder.

    ``mine=true`` narrows to the routes this person answers for. It is opt-in
    rather than the default because the morning meeting reads this list for the
    whole plant, and because an admin with no plant role would otherwise see an
    empty screen and believe nothing had escalated.
    """
    query = service.base_query(identity.plant_id)
    if open:
        query = service.only_open(query)
    if mine:
        routes = routes_for(identity)
        if not routes:
            return []
        query = query.where(Escalation.route.in_(routes))

    rows = (
        await session.execute(query.order_by(Escalation.due_at.desc()).limit(200))
    ).all()
    names = await service.names_for(session, {e.acknowledged_by_id for e, _ in rows})
    return [service.present(escalation, code, names) for escalation, code in rows]


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
    if escalation.status not in service.FIRED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Only an escalation that has been sent can be acknowledged",
        )
    if escalation.acknowledged_at is None:
        escalation.acknowledged_at = datetime.now(UTC)
        escalation.acknowledged_by_id = identity.user.id
        await session.flush()

    event = await session.get(DowntimeEvent, escalation.downtime_event_id)
    station = await session.get(Station, event.station_id) if event else None
    names = await service.names_for(session, {escalation.acknowledged_by_id})
    return service.present(escalation, station.code if station else None, names)
