"""The plant's clock.

Every "today", "yesterday" and business date is the plant's, not the server's.
A shift that starts at 01:00 in Hosur is on the 13th even though it is still
the 12th in UTC, and a server in Frankfurt must not move the morning meeting's
"yesterday" by a day.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_ZONE = "Asia/Kolkata"


def plant_zone(plant) -> ZoneInfo:
    try:
        return ZoneInfo(getattr(plant, "timezone", None) or DEFAULT_ZONE)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_ZONE)


def plant_now(plant) -> datetime:
    return datetime.now(UTC).astimezone(plant_zone(plant))


def plant_today(plant) -> date:
    return plant_now(plant).date()


def local_date(plant, moment: datetime) -> date:
    """The plant-local calendar date of an aware timestamp."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(plant_zone(plant)).date()
