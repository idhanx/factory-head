"""Plant checks for every id a client sends.

Row-level security refuses a query that reaches into another plant, but an id
in a request body is an easier leak than a missing WHERE: a station from plant
B assigned to a job in plant A is a valid foreign key and a silent corruption.
Every handler that accepts an id resolves it through here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ShiftSession, UserPlantRole

#: How far a device clock may run ahead of the server before a timestamp is
#: refused, and how far before the shift started an event may be back-dated.
CLOCK_SKEW = timedelta(minutes=2)
BACKDATE_BEFORE_SHIFT = timedelta(minutes=60)


async def in_plant[T](
    session: AsyncSession,
    model: type[T],
    row_id: uuid.UUID | None,
    plant_id: uuid.UUID,
    label: str,
) -> T | None:
    """The row, if it exists in this plant; ``None`` for ``None``; 404 otherwise."""
    if row_id is None:
        return None
    row = await session.get(model, row_id)
    if row is None or getattr(row, "plant_id", None) != plant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{label} not found")
    return row


async def require_member(
    session: AsyncSession, user_id: uuid.UUID | None, plant_id: uuid.UUID
) -> None:
    """A person named on a record must belong to the plant the record is in."""
    if user_id is None:
        return
    member = await session.scalar(
        select(UserPlantRole.id).where(
            UserPlantRole.user_id == user_id, UserPlantRole.plant_id == plant_id
        )
    )
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Person not found in this plant")


def check_event_time(
    moment: datetime | None, shift_session: ShiftSession, *, label: str = "Time"
) -> datetime | None:
    """Refuse times in the future or long before the shift began.

    Back-dating is legitimate — a supervisor logs a stop a few minutes after it
    started, and an offline tablet syncs later — but a stop 'started' two days
    ago inflates the rupee figure of the shift it lands in.
    """
    if moment is None:
        return None
    if moment.tzinfo is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"{label} must include a timezone"
        )
    now = datetime.now(UTC)
    if moment > now + CLOCK_SKEW:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"{label} cannot be in the future"
        )
    if moment < shift_session.started_at - BACKDATE_BEFORE_SHIFT:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"{label} is before this shift started",
        )
    return moment
