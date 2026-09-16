"""The maintenance desk — §1, "Maintenance In-charge".

The spec gives this role exactly two duties and one cadence:

    Receives escalation alerts. Logs what was done to fix breakdowns.
    *When events occur.*

That last column is the whole design brief. Maintenance is not a dashboard
role: nobody in this job opens an app to browse. They open it because a machine
is down and a message just arrived, and what they need on screen is the stop
that is costing money right now, and a place to write what they did about it
before they walk back to the floor.

So this desk is one ranked list, not a report:

* the escalations routed to *maintenance* that nobody has answered (§3 Layer 2,
  the 15-minute rung), most expensive first — read through
  ``services.escalations``, which the stores desk shares;
* every open stop *routed to maintenance*, with its live cost, whether or not a
  rung has fired yet — a machine that stopped four minutes ago is still theirs;
* the stops closed recently that nobody has written a repair note against,
  because §1's second duty is the one that feeds Factory Memory and it is the
  one that silently gets skipped.

**What counts as "theirs" is the reason code's escalation route, not the
``is_breakdown`` flag.** §8.1 reserves that flag for MTBF/MTTR and says so
explicitly — "'Machine Breakdown' code only" — and the default library (§16.4)
sets it on exactly one of the eighteen codes. Six of them route to maintenance:
tool breakage, machine breakdown, fixture, coolant, planned maintenance, power
cut. Filtering this desk on ``is_breakdown`` would therefore hide five of the
six, including the tool breakage §6's own 5:45 AM briefing assigns to Suresh K.
in Maintenance. The route is the plant's own answer to "whose problem is this",
already tuned during onboarding, and it is the rule §3 Layer 2 uses to pick who
gets the 15-minute WhatsApp — so the desk and the ladder agree by construction.

Everything here is read off Core data already being captured. The only thing
this role ever writes is the repair note, which is §1's own instruction.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.kernel import downtime_event_cost, format_inr
from app.models import (
    DowntimeEvent,
    DowntimeStatus,
    Plant,
    ReasonCode,
    Recoverability,
    Station,
    User,
)
from app.services.costing import snapshot_from_row

#: How far back the desk looks for repairs nobody wrote up. Long enough to
#: catch yesterday's late shift, short enough that the list stays closeable —
#: a backlog of 200 unwritten notes is a list nobody ever opens (§12, risk 10).
REPAIR_BACKLOG_DAYS = 3


@dataclass(slots=True)
class OpenStop:
    """A machine that is down now, and what it has cost so far."""

    event: DowntimeEvent
    station: Station
    reason: ReasonCode | None
    minutes: Decimal
    cost_paise: int


def live_cost(event: DowntimeEvent, *, now: datetime | None = None) -> tuple[Decimal, int]:
    """The rupee ticker on an open stop (§3 Layer 1, 'a rupee counter climbing').

    Computed from the snapshot frozen onto the row when the stop was opened, so
    the number on this screen is the same number the supervisor's grid, the
    escalation message and the shift report show — §8.1's build rule: one
    formula, one implementation, never two screens disagreeing.
    """
    now = now or datetime.now(UTC)
    started = event.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    minutes = max(Decimal(0), Decimal((now - started).total_seconds()) / Decimal(60))
    return minutes, downtime_event_cost(minutes, snapshot_from_row(event)).total


def _open_stops_query(plant_id: uuid.UUID) -> Select:
    return (
        select(DowntimeEvent, Station, ReasonCode)
        .join(Station, Station.id == DowntimeEvent.station_id)
        .outerjoin(ReasonCode, ReasonCode.id == DowntimeEvent.reason_code_id)
        .where(
            DowntimeEvent.plant_id == plant_id,
            DowntimeEvent.status == DowntimeStatus.OPEN,
        )
    )


async def open_stops(
    session: AsyncSession,
    plant_id: uuid.UUID,
    *,
    route: str | None = None,
    now: datetime | None = None,
) -> list[OpenStop]:
    """Every machine currently stopped, dearest first.

    ``route`` narrows to the stops one role owns, by the reason code's own
    escalation route — see the module docstring for why that, and not
    ``is_breakdown``, is the rule.
    """
    query = _open_stops_query(plant_id)
    if route is not None:
        query = query.where(ReasonCode.escalation_route == route)

    stops: list[OpenStop] = []
    for event, station, reason in (await session.execute(query)).all():
        minutes, cost = live_cost(event, now=now)
        stops.append(
            OpenStop(event=event, station=station, reason=reason, minutes=minutes, cost_paise=cost)
        )
    stops.sort(key=lambda stop: stop.cost_paise, reverse=True)
    return stops


async def repairs_outstanding(
    session: AsyncSession,
    plant: Plant,
    *,
    route: str = "maintenance",
    days: int = REPAIR_BACKLOG_DAYS,
    now: datetime | None = None,
) -> list[tuple[DowntimeEvent, Station, ReasonCode | None]]:
    """Stops that ended without anyone writing down what fixed them.

    §1's second duty, made visible. A closed breakdown with no repair note is
    knowledge that left the plant with whoever fixed it — exactly the loss
    Factory Memory exists to prevent (§3 Layer 4).

    Narrowed to *recoverable* stops. A power cut and a planned maintenance
    window both route to maintenance, but §5's own definition of recoverable is
    "the plant's own process failure — fixable", and asking a fitter what fixed
    last Tuesday's power cut is how a list earns itself a reputation for
    nonsense and stops being opened (§12, risk 10 — action fatigue).
    """
    since = (now or datetime.now(UTC)) - timedelta(days=days)
    rows = (
        await session.execute(
            select(DowntimeEvent, Station, ReasonCode)
            .join(Station, Station.id == DowntimeEvent.station_id)
            .join(ReasonCode, ReasonCode.id == DowntimeEvent.reason_code_id)
            .where(
                DowntimeEvent.plant_id == plant.id,
                DowntimeEvent.status == DowntimeStatus.CLOSED,
                DowntimeEvent.ended_at >= since,
                ReasonCode.escalation_route == route,
                ReasonCode.recoverability == Recoverability.RECOVERABLE,
                DowntimeEvent.repair_note.is_(None),
            )
            .order_by(DowntimeEvent.cost_total.desc())
            .limit(50)
        )
    ).all()
    return [(event, station, reason) for event, station, reason in rows]


async def log_repair(
    session: AsyncSession,
    *,
    event: DowntimeEvent,
    note: str,
    part_changed: str | None,
    user: User,
    at: datetime | None = None,
) -> DowntimeEvent:
    """Write what was done. §1: "Logs what was done to fix breakdowns."

    Deliberately allowed while the stop is still open: the fitter who has just
    changed the insert should be able to record it without waiting for the
    supervisor to tap 'Running', and making them wait is how the note stops
    being written at all.

    It is equally deliberate that this never touches the event's cost, times or
    reason code. A repair note is testimony added beside the record, not an
    edit to it — the shift's arithmetic stays exactly as it was captured.
    """
    note = note.strip()
    if not note:
        raise ValueError("A repair note cannot be empty")

    event.repair_note = note
    event.repair_part_changed = (part_changed or "").strip() or None
    event.repaired_by_id = user.id
    event.repaired_at = at or datetime.now(UTC)
    await session.flush()
    return event


def cost_display(paise: int) -> str:
    return format_inr(paise)
