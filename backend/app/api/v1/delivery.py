"""Browser push subscriptions and the delivery log the owner can check."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Identity, get_identity, get_session, require
from app.delivery import transports
from app.models import Notification, PushSubscription, Role
from app.services.notifications import mask

router = APIRouter(tags=["delivery"])


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=10, max_length=200)
    auth: str = Field(min_length=8, max_length=100)


class PushSubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2000, pattern=r"^https://")
    keys: PushKeys
    user_agent: str | None = Field(default=None, max_length=300)


class PushUnsubscribe(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2000)


class NotificationOut(BaseModel):
    id: str
    kind: str
    channel: str
    recipient_name: str | None
    recipient_masked: str | None
    status: str
    attempts: int
    last_error: str | None
    body: str
    created_at: str
    sent_at: str | None


@router.get("/push/public-key")
async def push_public_key(identity: Identity = Depends(get_identity)):
    """What the browser needs to subscribe — or ``enabled: false`` until VAPID keys exist."""
    key = transports.push.public_key()
    return {"enabled": key is not None, "public_key": key}


@router.post("/push/subscriptions", status_code=status.HTTP_201_CREATED)
async def subscribe(
    payload: PushSubscriptionIn,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    """Wake this browser for this person in this plant. Re-subscribing updates the row."""
    # One row per browser per plant: a shared tablet belongs to whoever in this
    # plant subscribed last.
    await session.execute(
        delete(PushSubscription).where(
            PushSubscription.plant_id == identity.plant_id,
            PushSubscription.endpoint == payload.endpoint,
        )
    )
    row = PushSubscription(
        plant_id=identity.plant_id,
        user_id=identity.user.id,
        endpoint=payload.endpoint,
        p256dh=payload.keys.p256dh,
        auth=payload.keys.auth,
        user_agent=payload.user_agent,
    )
    session.add(row)
    await session.flush()
    return {"id": str(row.id)}


@router.delete("/push/subscriptions", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe(
    payload: PushUnsubscribe,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    await session.execute(
        delete(PushSubscription).where(
            PushSubscription.plant_id == identity.plant_id,
            PushSubscription.user_id == identity.user.id,
            PushSubscription.endpoint == payload.endpoint,
        )
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/notifications/channels",
    dependencies=[Depends(require(Role.OWNER, Role.CONSULTANT))],
)
async def channels():
    return transports.channel_status()


@router.get(
    "/notifications",
    response_model=list[NotificationOut],
    dependencies=[Depends(require(Role.OWNER, Role.CONSULTANT))],
)
async def delivery_log(
    kind: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = 100,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    """Every message the product tried to send, and what happened to it."""
    query = select(Notification).where(Notification.plant_id == identity.plant_id)
    if kind:
        query = query.where(Notification.kind == kind)
    if status_filter:
        query = query.where(Notification.status == status_filter)
    rows = (
        await session.scalars(
            query.order_by(Notification.created_at.desc()).limit(max(1, min(limit, 500)))
        )
    ).all()
    return [
        NotificationOut(
            id=str(row.id),
            kind=row.kind,
            channel=row.channel.value,
            recipient_name=row.recipient_name,
            recipient_masked=mask(row.recipient_number),
            status=row.status.value,
            attempts=row.attempts or 0,
            last_error=row.last_error,
            body=row.body,
            created_at=row.created_at.isoformat(),
            sent_at=row.sent_at.isoformat() if row.sent_at else None,
        )
        for row in rows
    ]
