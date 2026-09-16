"""Phase 2 tables: offline sync holds, the delivery outbox, push subscriptions, photos."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import Enum

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, PlantScopedMixin, TimestampMixin, uuid_pk
from .types import EnumString


class HoldStatus(str, Enum):
    HELD = "held"
    APPLIED = "applied"
    DISCARDED = "discarded"


class SyncHold(Base, PlantScopedMixin, TimestampMixin):
    """An offline entry that reached the server after its shift was locked.

    §16.4 asks for the offline conflict rule to be decided before the first
    pilot. The rule chosen: nothing is lost and nothing lands in a locked shift
    silently — the owner applies it as a logged correction, or discards it with
    a reason.
    """

    __tablename__ = "sync_holds"
    __table_args__ = (
        UniqueConstraint("plant_id", "client_ref", name="uq_sync_holds_client_ref"),
        Index("ix_sync_holds_status", "plant_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    client_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    device_id: Mapped[str | None] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    shift_session_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("shift_sessions.id", ondelete="SET NULL")
    )
    business_date: Mapped[date | None] = mapped_column(Date)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    status: Mapped[HoldStatus] = mapped_column(
        EnumString(HoldStatus, 12), default=HoldStatus.HELD, nullable=False
    )
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))


class NotificationChannel(str, Enum):
    WHATSAPP = "whatsapp"
    SMS = "sms"
    PUSH = "push"
    IN_APP = "in_app"


class NotificationStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"  # no channel could reach this recipient


class Notification(Base, PlantScopedMixin, TimestampMixin):
    """One message to one recipient on one channel — the delivery outbox.

    Rows, not calls: a WhatsApp that failed at 10:55 is retried at 10:56 and
    its failure is visible to the owner, instead of vanishing into a log.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_due", "plant_id", "status", "next_attempt_at"),
        UniqueConstraint("plant_id", "dedupe_key", name="uq_notifications_dedupe"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    channel: Mapped[NotificationChannel] = mapped_column(
        EnumString(NotificationChannel, 12), nullable=False
    )
    status: Mapped[NotificationStatus] = mapped_column(
        EnumString(NotificationStatus, 12), default=NotificationStatus.PENDING, nullable=False
    )
    recipient_name: Mapped[str | None] = mapped_column(String(200))
    recipient_number: Mapped[str | None] = mapped_column(String(20))
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    title: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(String(300))
    #: Provider template and its variables, for channels that need approved templates.
    template: Mapped[str | None] = mapped_column(String(64))
    variables: Mapped[dict | None] = mapped_column(JSONB)
    subject_type: Mapped[str | None] = mapped_column(String(32))
    subject_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: One shift report per shift, one daily report per day — a retry of the job
    #: that queues them must not queue them twice.
    dedupe_key: Mapped[str | None] = mapped_column(String(160))
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    provider_message_id: Mapped[str | None] = mapped_column(String(128))
    provider_response: Mapped[dict | None] = mapped_column(JSONB)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PushSubscription(Base, PlantScopedMixin, TimestampMixin):
    """A browser on a tablet or phone that asked to be woken for this plant."""

    __tablename__ = "push_subscriptions"
    __table_args__ = (
        UniqueConstraint("plant_id", "endpoint", name="uq_push_subscriptions_plant_endpoint"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    p256dh: Mapped[str] = mapped_column(String(200), nullable=False)
    auth: Mapped[str] = mapped_column(String(100), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(300))
    failures: Mapped[int] = mapped_column(default=0, server_default="0")
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Attachment(Base, PlantScopedMixin, TimestampMixin):
    """A photo of the fix (§5.1) or for Factory Memory (§3 Layer 4)."""

    __tablename__ = "attachments"
    __table_args__ = (Index("ix_attachments_entity", "plant_id", "entity_type", "entity_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    entity_type: Mapped[str] = mapped_column(String(24), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    filename: Mapped[str | None] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(nullable=False)
    width: Mapped[int | None] = mapped_column()
    height: Mapped[int | None] = mapped_column()
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(300), nullable=False)
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
