"""Plants, users and role assignments.

Roles are per-plant, not global: the same consultant account supports several
plants, and an owner of Plant A must never see Plant B's losses.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import Enum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, uuid_pk
from .types import EnumString


class Role(str, Enum):
    """§1 'Who uses it' — five plant roles plus the onboarding consultant."""

    OWNER = "owner"
    SUPERVISOR = "supervisor"
    QUALITY = "quality"
    MAINTENANCE = "maintenance"
    STORES = "stores"
    CONSULTANT = "consultant"
    ADMIN = "admin"


class CostingMode(str, Enum):
    BASIC = "basic"
    STANDARD = "standard"
    ADVANCED = "advanced"


class Plant(Base, TimestampMixin):
    __tablename__ = "plants"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    location: Mapped[str | None] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    currency: Mapped[str] = mapped_column(String(8), default="INR")

    costing_mode: Mapped[CostingMode] = mapped_column(
        EnumString(CostingMode, 16), default=CostingMode.BASIC, nullable=False
    )

    # §16.5 open decision 3: does a 22:00-06:00 shift belong to its start date
    # or its end date? Plant-configurable, defaulting to start date. This
    # affects every "yesterday" number the owner sees.
    business_date_from_shift_start: Mapped[bool] = mapped_column(Boolean, default=True)

    # §5.2 / §16.5 — thresholds tuned per plant during onboarding.
    action_cost_threshold: Mapped[int] = mapped_column(default=250_000)  # paise
    action_duration_minutes: Mapped[int] = mapped_column(default=30)
    recurrence_count: Mapped[int] = mapped_column(default=3)
    recurrence_window_shifts: Mapped[int] = mapped_column(default=7)

    # §16.5 open decision 4 — tuned against the first plant's real data.
    reconciliation_tolerance_parts: Mapped[int] = mapped_column(default=5)
    reconciliation_tolerance_bp: Mapped[int] = mapped_column(default=200)  # basis pts

    # §10 — announced out loud by the owner at kickoff, enforced in the product.
    no_blame_until: Mapped[date | None] = mapped_column(Date)

    go_live_date: Mapped[date | None] = mapped_column(Date)
    #: §5.2 — the deadline an auto-created Action gets, in days from the shift.
    action_due_days: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    # §6.3 / §16.5 open decision 6 — internal-only, owner-visible. Feeds ROI.
    monthly_fee: Mapped[int] = mapped_column(default=0)  # paise

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    users: Mapped[list[UserPlantRole]] = relationship(
        back_populates="plant", cascade="all, delete-orphan"
    )

    @property
    def no_blame_active(self) -> bool:
        return bool(self.no_blame_until and self.no_blame_until >= date.today())


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str | None] = mapped_column(String(255), unique=True)
    # Supervisors on a shop floor sign in with a phone number and a PIN, not an
    # email and a 12-character password (§14.5: shared tablets, thick fingers).
    phone: Mapped[str | None] = mapped_column(String(20), unique=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    preferred_language: Mapped[str] = mapped_column(String(8), default="en")  # en | ta
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    plants: Mapped[list[UserPlantRole]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserPlantRole(Base, TimestampMixin):
    __tablename__ = "user_plant_roles"
    __table_args__ = (UniqueConstraint("user_id", "plant_id", "role"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    plant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("plants.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[Role] = mapped_column(EnumString(Role, 20), nullable=False)

    user: Mapped[User] = relationship(back_populates="plants")
    plant: Mapped[Plant] = relationship(back_populates="users")


class AuditLog(Base):
    """Append-only. Every edit before shift close is logged and owner-visible.

    §2: "edits before close are logged; owner sees an audit trail" — this is one
    of the three mechanisms that keep manually entered data honest.
    """

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = uuid_pk()
    plant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("plants.id", ondelete="CASCADE"), index=True
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    action: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # create|update|delete|override
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
