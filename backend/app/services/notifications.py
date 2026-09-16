"""The delivery outbox: who is told what, on which channel, and whether it arrived.

A message is queued as one row per recipient per channel and sent by the
worker. WhatsApp is tried first for anyone with a number; if it is not
available or keeps failing, the same text goes by SMS (§12 risk 6: "SMS
fallback for the escalation ladder in the interim"). A signed-in user is also
pushed to every browser they enabled. When no channel can reach a recipient,
that is recorded too — a gap the owner can see is a gap that gets fixed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.delivery import transports
from app.models import (
    AlertRecipient,
    Notification,
    NotificationChannel,
    NotificationStatus,
    Plant,
    PushSubscription,
    Role,
    User,
    UserPlantRole,
)

log = structlog.get_logger()

#: Minutes to wait before each retry. Short at first — an escalation is only
#: useful in the next few minutes — then patient.
BACKOFF_MINUTES = (1, 3, 10, 30, 60)


@dataclass(frozen=True, slots=True)
class Recipient:
    name: str | None
    number: str | None = None
    user_id: uuid.UUID | None = None

    @property
    def key(self) -> str:
        return str(self.user_id or self.number or self.name)


# ---------------------------------------------------------------------------
# Who gets told
# ---------------------------------------------------------------------------


async def recipients_for_route(
    session: AsyncSession, plant_id: uuid.UUID, role_tag: str
) -> list[Recipient]:
    """T5 alert recipients for a role tag — maintenance, stores, plant_head…"""
    rows = (
        await session.scalars(
            select(AlertRecipient).where(
                AlertRecipient.plant_id == plant_id,
                AlertRecipient.role_tag == role_tag,
                AlertRecipient.is_active.is_(True),
            )
        )
    ).all()
    return [Recipient(name=r.name, number=r.whatsapp_number, user_id=r.user_id) for r in rows]


async def users_with_role(
    session: AsyncSession, plant_id: uuid.UUID, *roles: Role
) -> list[Recipient]:
    rows = (
        await session.execute(
            select(User)
            .join(UserPlantRole, UserPlantRole.user_id == User.id)
            .where(
                UserPlantRole.plant_id == plant_id,
                UserPlantRole.role.in_(list(roles)),
                User.is_active.is_(True),
            )
            .distinct()
        )
    ).scalars().all()
    return [Recipient(name=u.full_name, number=u.phone, user_id=u.id) for u in rows]


async def owners(session: AsyncSession, plant_id: uuid.UUID) -> list[Recipient]:
    """The owner's phone: T5's owner / plant-head rows, else the owner logins."""
    listed = [
        *await recipients_for_route(session, plant_id, "owner"),
        *await recipients_for_route(session, plant_id, "plant_head"),
    ]
    return _merge(listed, await users_with_role(session, plant_id, Role.OWNER))


def _merge(*groups: list[Recipient]) -> list[Recipient]:
    """One entry per person: a T5 row and a login for the same owner are one owner.

    Two entries are the same person when they share a phone number (last ten
    digits) or a login.
    """
    people: list[Recipient] = []
    for group in groups:
        for recipient in group:
            number = transports.digits(recipient.number)[-10:] or None
            match = next(
                (
                    index
                    for index, person in enumerate(people)
                    if (number and transports.digits(person.number)[-10:] == number)
                    or (recipient.user_id and person.user_id == recipient.user_id)
                ),
                None,
            )
            if match is None:
                people.append(recipient)
                continue
            person = people[match]
            people[match] = Recipient(
                name=person.name or recipient.name,
                number=person.number or recipient.number,
                user_id=person.user_id or recipient.user_id,
            )
    return people


merge_recipients = _merge


# ---------------------------------------------------------------------------
# Queueing
# ---------------------------------------------------------------------------


async def enqueue(
    session: AsyncSession,
    *,
    plant: Plant,
    kind: str,
    recipients: list[Recipient],
    body: str,
    title: str | None = None,
    url: str | None = None,
    subject_type: str | None = None,
    subject_id: uuid.UUID | None = None,
    dedupe_key: str | None = None,
    push_only: bool = False,
) -> list[Notification]:
    """Queue one message for each recipient on every channel that can reach them."""
    now = datetime.now(UTC)
    link = f"{settings.app_base_url.rstrip('/')}{url}" if url and url.startswith("/") else url
    queued: list[Notification] = []
    keys_this_call: set[str] = set()

    for recipient in _merge(recipients):
        planned: list[NotificationChannel] = []
        push_ready = transports.push.enabled and transports.push.configured
        if recipient.user_id and push_ready and await _has_subscription(session, recipient.user_id):
            planned.append(NotificationChannel.PUSH)
        if recipient.number and not push_only:
            if transports.whatsapp.enabled and transports.whatsapp.configured:
                planned.append(NotificationChannel.WHATSAPP)
            elif transports.sms.enabled and transports.sms.configured:
                planned.append(NotificationChannel.SMS)

        if not planned:
            planned = [NotificationChannel.IN_APP]

        for channel in planned:
            key = f"{dedupe_key}:{recipient.key}:{channel.value}" if dedupe_key else None
            if key in keys_this_call:
                continue
            if key:
                keys_this_call.add(key)
            if key and await session.scalar(
                select(Notification.id).where(
                    Notification.plant_id == plant.id, Notification.dedupe_key == key
                )
            ):
                continue
            unreachable = channel is NotificationChannel.IN_APP
            row = Notification(
                plant_id=plant.id,
                kind=kind,
                channel=channel,
                status=NotificationStatus.SKIPPED if unreachable else NotificationStatus.PENDING,
                recipient_name=recipient.name,
                recipient_number=recipient.number,
                user_id=recipient.user_id,
                title=title,
                body=body,
                url=link,
                subject_type=subject_type,
                subject_id=subject_id,
                dedupe_key=key,
                next_attempt_at=None if unreachable else now,
                last_error=_unreachable_reason(recipient, push_only) if unreachable else None,
            )
            session.add(row)
            queued.append(row)

    await session.flush()
    return queued


def reached_anyone(rows: list[Notification]) -> bool:
    """Whether any of the queued rows has a real channel to go out on."""
    return any(row.status is not NotificationStatus.SKIPPED for row in rows)


async def _has_subscription(session: AsyncSession, user_id: uuid.UUID) -> bool:
    return bool(
        await session.scalar(
            select(PushSubscription.id).where(PushSubscription.user_id == user_id).limit(1)
        )
    )


def _unreachable_reason(recipient: Recipient, push_only: bool) -> str:
    if not recipient.number and not recipient.user_id:
        return "No phone number or login for this recipient"
    if push_only or not recipient.number:
        return "No browser push enabled for this person"
    return "No WhatsApp or SMS channel is configured"


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


async def dispatch_due(session: AsyncSession, *, plant: Plant, limit: int = 100) -> int:
    """Send every message whose time has come. Returns how many were attempted."""
    now = datetime.now(UTC)
    due = (
        await session.scalars(
            select(Notification)
            .where(
                Notification.plant_id == plant.id,
                Notification.status == NotificationStatus.PENDING,
                Notification.next_attempt_at <= now,
            )
            .order_by(Notification.next_attempt_at)
            .limit(limit)
        )
    ).all()

    for row in due:
        try:
            result = await _send(session, row)
        except Exception as exc:  # network errors, bad keys: retry, and say why
            result = transports.SendResult(ok=False, error=str(exc)[:500])
        _record(row, result, now)
        if row.status is NotificationStatus.FAILED and row.channel is NotificationChannel.WHATSAPP:
            await _fall_back_to_sms(session, row, now)

    await session.flush()
    return len(due)


async def _send(session: AsyncSession, row: Notification) -> transports.SendResult:
    if row.channel is NotificationChannel.WHATSAPP:
        return await transports.whatsapp.send(number=row.recipient_number, body=row.body, kind=row.kind)
    if row.channel is NotificationChannel.SMS:
        return await transports.sms.send(number=row.recipient_number, body=row.body, kind=row.kind)
    if row.channel is NotificationChannel.PUSH:
        subscriptions = (
            await session.scalars(
                select(PushSubscription).where(PushSubscription.user_id == row.user_id)
            )
        ).all()
        if not subscriptions:
            return transports.SendResult(ok=False, error="No browser push enabled", retryable=False)
        delivered = False
        last_error = None
        for subscription in subscriptions:
            result = await transports.push.send(
                endpoint=subscription.endpoint,
                p256dh=subscription.p256dh,
                auth=subscription.auth,
                title=row.title or "FactoryManager",
                body=row.body,
                url=row.url,
                tag=f"{row.kind}:{row.subject_id or row.id}",
            )
            if result.ok:
                delivered = True
                subscription.failures = 0
                subscription.last_success_at = datetime.now(UTC)
            else:
                last_error = result.error
                subscription.failures = (subscription.failures or 0) + 1
                if not result.retryable:
                    await session.delete(subscription)
        return transports.SendResult(ok=delivered, error=None if delivered else last_error)
    return transports.SendResult(ok=False, error="Nothing to send on", retryable=False)


def _record(row: Notification, result: transports.SendResult, now: datetime) -> None:
    row.attempts = (row.attempts or 0) + 1
    row.provider_response = result.response or None
    if result.ok:
        row.status = NotificationStatus.SENT
        row.sent_at = now
        row.provider_message_id = result.provider_id
        row.last_error = None
        row.next_attempt_at = None
        return
    row.last_error = result.error
    if not result.retryable or row.attempts >= settings.delivery_max_attempts:
        row.status = NotificationStatus.FAILED
        row.next_attempt_at = None
        log.warning("delivery.failed", kind=row.kind, channel=row.channel.value, error=result.error)
        return
    delay = BACKOFF_MINUTES[min(row.attempts - 1, len(BACKOFF_MINUTES) - 1)]
    row.next_attempt_at = now + timedelta(minutes=delay)


async def _fall_back_to_sms(session: AsyncSession, row: Notification, now: datetime) -> None:
    """A WhatsApp that cannot be delivered goes out as an SMS instead."""
    if not (row.recipient_number and transports.sms.enabled and transports.sms.configured):
        return
    session.add(
        Notification(
            plant_id=row.plant_id,
            kind=row.kind,
            channel=NotificationChannel.SMS,
            status=NotificationStatus.PENDING,
            recipient_name=row.recipient_name,
            recipient_number=row.recipient_number,
            user_id=row.user_id,
            title=row.title,
            body=row.body,
            url=row.url,
            subject_type=row.subject_type,
            subject_id=row.subject_id,
            dedupe_key=f"{row.dedupe_key}:sms-fallback" if row.dedupe_key else None,
            next_attempt_at=now,
        )
    )


def mask(number: str | None) -> str | None:
    """+91•••••4321 — enough to recognise, not enough to copy."""
    only = transports.digits(number)
    if not only:
        return None
    return f"+{only[:-10]}•••••{only[-4:]}" if len(only) > 10 else f"•••••{only[-4:]}"
