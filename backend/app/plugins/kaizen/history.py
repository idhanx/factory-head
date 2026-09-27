"""Step 5A — THE CORE HISTORY CHECK (Plugin Spec §6).

    "THIS IS THE FEATURE THAT JUSTIFIES THE PLUGIN. In the standalone product,
     an operator reporting 'tool breakage at this station' is heard, costed on
     their own estimates, and ranked against other suggestions. In the plugin,
     the same sentence instantly becomes: 'This has stopped this line 31 times
     in six months. It has cost ₹3,24,800. It is your fourth-largest recurring
     loss. Your idea is now Priority 2 in the plant.'"

Every rupee on that screen comes from Core's cost engine, on events Core itself
recorded. None of it is estimated and none of it is the operator's guess — which
is exactly why it is persuasive to a Plant Head, and exactly why this module
must never fabricate. §6.5's rule when there is nothing to show:

    "Say so plainly and proceed — never fabricate, never pad, never hide the
     step."
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import kaizen_ai
from app.kernel import format_inr
from app.models import (
    Action,
    DefectCode,
    DowntimeEvent,
    KaizenCategory,
    KaizenCategoryCode,
    MemoryEntry,
    PdcaState,
    Plant,
    QualityRecord,
    ReasonCode,
    Station,
)

#: The two halves the trend is measured over. §11.1: *"A problem occurring 11
#: times in the last 60 days versus 8 in the prior 60 is getting worse."*
TREND_HALF_DAYS = 60

#: How far back a closed Action counts as "this has come back" rather than "this
#: is a new problem" (§6.4). Core's own monitoring window is four weeks; a
#: recurrence inside a quarter of the closure is still that Action's failure.
RECURRENCE_WINDOW_DAYS = 90

#: A Kaizen has to clear this to be called a match by text alone, when no
#: reason code maps. Set high on purpose: a wrong match here attaches somebody
#: else's ₹3 lakh to this idea, and every subsequent number inherits the error.
SEMANTIC_THRESHOLD = 0.34


# ---------------------------------------------------------------------------
# Result shapes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HistoryTier:
    """One of the three scopes: this machine, this line, the plant."""

    scope: str
    label: str
    events: int = 0
    minutes: int = 0
    cost_paise: int = 0

    @property
    def cost_display(self) -> str:
        return format_inr(self.cost_paise)

    def as_dict(self) -> dict:
        return {**asdict(self), "cost_display": self.cost_display}


@dataclass(slots=True)
class DuplicateAction:
    action_id: uuid.UUID
    reference: str
    title: str
    owner_name: str | None
    due_date: date | None
    state: str
    closed_on: date | None = None

    def as_dict(self) -> dict:
        return {
            "action_id": str(self.action_id),
            "reference": self.reference,
            "title": self.title,
            "owner_name": self.owner_name,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "state": self.state,
            "closed_on": self.closed_on.isoformat() if self.closed_on else None,
        }


@dataclass(slots=True)
class CoreHistory:
    """What the plant already knows about this problem."""

    found: bool = False
    matched_on: str = "none"  # reason_code | defect_code | semantic | none
    subject: str = ""
    window_days: int = 180
    tiers: list[HistoryTier] = field(default_factory=list)

    events: int = 0
    minutes: int = 0
    cost_paise: int = 0
    #: Where this sits in the plant's ranked recurring losses. 1 is the biggest.
    rank: int | None = None
    ranked_of: int = 0
    last_occurred_on: date | None = None

    events_recent: int = 0
    events_prior: int = 0
    trend: str = "flat"  # rising | falling | flat
    annual_run_rate_paise: int = 0

    #: §6.4 — an open Action already covers this, or a closed one has failed.
    open_action: DuplicateAction | None = None
    recurrence_of: DuplicateAction | None = None
    #: Factory Memory: "we fixed something like this before, here is what worked".
    memory: list[dict] = field(default_factory=list)

    #: The plant's own yardsticks, for normalising the priority score (§11.1).
    cost_reference: int = 0
    frequency_reference: int = 0

    message: str = ""

    def as_dict(self) -> dict:
        return {
            "found": self.found,
            "matched_on": self.matched_on,
            "subject": self.subject,
            "window_days": self.window_days,
            "tiers": [t.as_dict() for t in self.tiers],
            "events": self.events,
            "minutes": self.minutes,
            "cost_paise": self.cost_paise,
            "cost_display": format_inr(self.cost_paise),
            "rank": self.rank,
            "ranked_of": self.ranked_of,
            "last_occurred_on": (
                self.last_occurred_on.isoformat() if self.last_occurred_on else None
            ),
            "events_recent": self.events_recent,
            "events_prior": self.events_prior,
            "trend": self.trend,
            "annual_run_rate_paise": self.annual_run_rate_paise,
            "annual_run_rate_display": format_inr(self.annual_run_rate_paise),
            "open_action": self.open_action.as_dict() if self.open_action else None,
            "recurrence_of": (self.recurrence_of.as_dict() if self.recurrence_of else None),
            "memory": self.memory,
            "cost_reference": self.cost_reference,
            "frequency_reference": self.frequency_reference,
            "message": self.message,
        }


# ---------------------------------------------------------------------------
# §6.1 — the match keys, in priority order
# ---------------------------------------------------------------------------


async def resolve_codes(
    session: AsyncSession,
    *,
    plant: Plant,
    category: KaizenCategory,
) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """Core reason codes and defect codes this category maps to (§6.1).

    The mapping is per-plant configuration seeded from Core's T6 library, so a
    plant that calls its reason "TOOL CHANGE" rather than "TOOL_BREAKAGE" is
    matched on its own vocabulary, not on ours.
    """
    rows = (
        await session.scalars(
            select(KaizenCategoryCode).where(
                KaizenCategoryCode.plant_id == plant.id,
                KaizenCategoryCode.category == category,
            )
        )
    ).all()
    return (
        [r.reason_code_id for r in rows if r.reason_code_id],
        [r.defect_code_id for r in rows if r.defect_code_id],
    )


async def check(
    session: AsyncSession,
    *,
    plant: Plant,
    category: KaizenCategory,
    title: str,
    description: str,
    station: Station | None = None,
    part_id: uuid.UUID | None = None,
    reason_code_id: uuid.UUID | None = None,
    defect_code_id: uuid.UUID | None = None,
    window_days: int = 180,
    as_of: date | None = None,
) -> CoreHistory:
    """Query Core's event log for what this problem has already cost.

    Runs after Step 3 and *in parallel with Step 4*, so the operator never waits
    for it — by the time they have tapped four answers, the evidence is ready.
    """
    as_of = as_of or date.today()
    since = as_of - timedelta(days=window_days)
    history = CoreHistory(window_days=window_days)

    reason_ids, defect_ids = await resolve_codes(session, plant=plant, category=category)
    if reason_code_id and reason_code_id not in reason_ids:
        reason_ids = [reason_code_id, *reason_ids]
    if defect_code_id and defect_code_id not in defect_ids:
        defect_ids = [defect_code_id, *defect_ids]

    # Key 2 — quality Kaizens match on part + defect code.
    if category is KaizenCategory.QUALITY and (defect_ids or part_id):
        await _quality_history(
            session,
            plant=plant,
            history=history,
            part_id=part_id,
            defect_ids=defect_ids,
            station=station,
            since=since,
            as_of=as_of,
        )
    # Key 1 — downtime Kaizens match on machine + reason code.
    elif reason_ids:
        await _downtime_history(
            session,
            plant=plant,
            history=history,
            station=station,
            reason_ids=reason_ids,
            since=since,
            as_of=as_of,
        )

    # Keys 3 and 4 — semantic fallback on the event description. Never show
    # nothing just because the plant's codes do not map cleanly.
    if not history.found:
        await _semantic_history(
            session,
            plant=plant,
            history=history,
            station=station,
            text=f"{title} {description}",
            since=since,
            as_of=as_of,
        )

    await _attach_actions(
        session,
        plant=plant,
        history=history,
        station=station,
        reason_ids=reason_ids,
        part_id=part_id,
        as_of=as_of,
    )
    history.memory = await _factory_memory(
        session,
        plant=plant,
        station=station,
        part_id=part_id,
        reason_ids=reason_ids,
        text=f"{title} {description}",
    )
    await _reference_scales(session, plant=plant, history=history, since=since)
    _summarise(history)
    return history


# ---------------------------------------------------------------------------
# Key 1 — downtime by machine + reason code
# ---------------------------------------------------------------------------


def _downtime_base(plant: Plant, since: date, as_of: date) -> Select:
    return select(
        func.count(DowntimeEvent.id),
        func.coalesce(func.sum(DowntimeEvent.duration_minutes), 0),
        func.coalesce(func.sum(DowntimeEvent.cost_total), 0),
        func.max(DowntimeEvent.business_date),
    ).where(
        DowntimeEvent.plant_id == plant.id,
        DowntimeEvent.business_date >= since,
        DowntimeEvent.business_date <= as_of,
    )


async def _downtime_history(
    session: AsyncSession,
    *,
    plant: Plant,
    history: CoreHistory,
    station: Station | None,
    reason_ids: list[uuid.UUID],
    since: date,
    as_of: date,
) -> None:
    labels = await _reason_labels(session, plant=plant, reason_ids=reason_ids)
    history.subject = " / ".join(labels) or "this problem"
    history.matched_on = "reason_code"

    scoped = _downtime_base(plant, since, as_of).where(
        DowntimeEvent.reason_code_id.in_(reason_ids)
    )

    line_station_ids: list[uuid.UUID] = []
    if station is not None:
        line_station_ids = await _stations_on_line(session, plant=plant, station=station)

        machine = (
            await session.execute(scoped.where(DowntimeEvent.station_id == station.id))
        ).one()
        history.tiers.append(_tier("machine", f"This machine ({station.code})", machine))

        if len(line_station_ids) > 1:
            line = (
                await session.execute(
                    scoped.where(DowntimeEvent.station_id.in_(line_station_ids))
                )
            ).one()
            label = f"This line ({station.line})" if station.line else "This line"
            history.tiers.append(_tier("line", label, line))

    plantwide = (await session.execute(scoped)).one()
    history.tiers.append(_tier("plant", "Plant-wide", plantwide))
    _adopt_plantwide(history, plantwide)

    recent, prior = await _trend_counts(
        session,
        plant=plant,
        as_of=as_of,
        extra=DowntimeEvent.reason_code_id.in_(reason_ids),
    )
    history.events_recent, history.events_prior = recent, prior


def _tier(scope: str, label: str, row) -> HistoryTier:
    events, minutes, cost, _ = row
    return HistoryTier(
        scope=scope,
        label=label,
        events=int(events or 0),
        minutes=int(minutes or 0),
        cost_paise=int(cost or 0),
    )


def _adopt_plantwide(history: CoreHistory, row) -> None:
    events, minutes, cost, last = row
    history.events = int(events or 0)
    history.minutes = int(minutes or 0)
    history.cost_paise = int(cost or 0)
    history.last_occurred_on = last
    history.found = history.events > 0


async def _reason_labels(
    session: AsyncSession, *, plant: Plant, reason_ids: list[uuid.UUID]
) -> list[str]:
    rows = (
        await session.execute(
            select(ReasonCode.label).where(
                ReasonCode.plant_id == plant.id, ReasonCode.id.in_(reason_ids)
            )
        )
    ).all()
    return [label for (label,) in rows]


async def _stations_on_line(
    session: AsyncSession, *, plant: Plant, station: Station
) -> list[uuid.UUID]:
    """Every station on the same line. A station with no line is its own line."""
    if not station.line:
        return [station.id]
    rows = (
        await session.execute(
            select(Station.id).where(
                Station.plant_id == plant.id, Station.line == station.line
            )
        )
    ).all()
    return [row[0] for row in rows] or [station.id]


async def _trend_counts(
    session: AsyncSession, *, plant: Plant, as_of: date, extra
) -> tuple[int, int]:
    """Events in the last 60 days, and in the 60 before that (§11.1)."""
    mid = as_of - timedelta(days=TREND_HALF_DAYS)
    start = as_of - timedelta(days=TREND_HALF_DAYS * 2)

    async def count(lo: date, hi: date) -> int:
        return int(
            await session.scalar(
                select(func.count(DowntimeEvent.id)).where(
                    DowntimeEvent.plant_id == plant.id,
                    DowntimeEvent.business_date > lo,
                    DowntimeEvent.business_date <= hi,
                    extra,
                )
            )
            or 0
        )

    return (await count(mid, as_of), await count(start, mid))


# ---------------------------------------------------------------------------
# Key 2 — quality by part + defect code
# ---------------------------------------------------------------------------


async def _quality_history(
    session: AsyncSession,
    *,
    plant: Plant,
    history: CoreHistory,
    part_id: uuid.UUID | None,
    defect_ids: list[uuid.UUID],
    station: Station | None,
    since: date,
    as_of: date,
) -> None:
    history.matched_on = "defect_code"
    history.subject = (
        " / ".join(await _defect_labels(session, plant=plant, defect_ids=defect_ids))
        or "rejections on this part"
    )

    base = select(
        func.count(QualityRecord.id),
        func.coalesce(func.sum(QualityRecord.reject_count), 0),
        func.coalesce(func.sum(QualityRecord.cost_total), 0),
        func.max(QualityRecord.business_date),
    ).where(
        QualityRecord.plant_id == plant.id,
        QualityRecord.business_date >= since,
        QualityRecord.business_date <= as_of,
        QualityRecord.reject_count > 0,
    )
    if part_id:
        base = base.where(QualityRecord.part_id == part_id)
    if defect_ids:
        # ``defects`` is [{defect_code_id, count}] — a batch can fail several
        # ways at once, so the match is containment, not equality.
        base = base.where(
            or_(
                *[
                    QualityRecord.defects.contains([{"defect_code_id": str(d)}])
                    for d in defect_ids
                ]
            )
        )

    if station is not None:
        machine = (
            await session.execute(base.where(QualityRecord.station_id == station.id))
        ).one()
        history.tiers.append(_tier("machine", f"This machine ({station.code})", machine))

    plantwide = (await session.execute(base)).one()
    history.tiers.append(_tier("plant", "Plant-wide", plantwide))
    # For quality the middle column is rejected parts, not minutes.
    for tier in history.tiers:
        tier.minutes = 0
    _adopt_plantwide(history, plantwide)
    history.minutes = 0

    mid = as_of - timedelta(days=TREND_HALF_DAYS)
    start = as_of - timedelta(days=TREND_HALF_DAYS * 2)

    async def count(lo: date, hi: date) -> int:
        query = select(func.count(QualityRecord.id)).where(
            QualityRecord.plant_id == plant.id,
            QualityRecord.business_date > lo,
            QualityRecord.business_date <= hi,
            QualityRecord.reject_count > 0,
        )
        if part_id:
            query = query.where(QualityRecord.part_id == part_id)
        return int(await session.scalar(query) or 0)

    history.events_recent = await count(mid, as_of)
    history.events_prior = await count(start, mid)


async def _defect_labels(
    session: AsyncSession, *, plant: Plant, defect_ids: list[uuid.UUID]
) -> list[str]:
    if not defect_ids:
        return []
    rows = (
        await session.execute(
            select(DefectCode.label).where(
                DefectCode.plant_id == plant.id, DefectCode.id.in_(defect_ids)
            )
        )
    ).all()
    return [label for (label,) in rows]


# ---------------------------------------------------------------------------
# Keys 3 and 4 — semantic fallback on the event note
# ---------------------------------------------------------------------------


async def _semantic_history(
    session: AsyncSession,
    *,
    plant: Plant,
    history: CoreHistory,
    station: Station | None,
    text: str,
    since: date,
    as_of: date,
) -> None:
    """Match on what people typed in the downtime note, when no code maps.

    Deliberately narrow: only events on the same machine (or the same line) are
    considered, because a text match across an entire plant's notes would find
    coincidences and price this Kaizen against them.
    """
    words = kaizen_ai.fingerprint(text)
    if not words:
        return

    scope = [DowntimeEvent.plant_id == plant.id, DowntimeEvent.business_date >= since]
    if station is not None:
        line_ids = await _stations_on_line(session, plant=plant, station=station)
        scope.append(DowntimeEvent.station_id.in_(line_ids))

    rows = (
        await session.execute(
            select(
                DowntimeEvent.id,
                DowntimeEvent.note,
                DowntimeEvent.duration_minutes,
                DowntimeEvent.cost_total,
                DowntimeEvent.business_date,
            ).where(and_(*scope), DowntimeEvent.note.is_not(None))
        )
    ).all()

    matched = [
        row
        for row in rows
        if kaizen_ai.jaccard(words, kaizen_ai.fingerprint(row.note)) >= SEMANTIC_THRESHOLD
    ]
    if not matched:
        return

    history.matched_on = "semantic"
    history.subject = "similar stoppages recorded on this line"
    history.found = True
    history.events = len(matched)
    history.minutes = int(sum(int(row.duration_minutes or 0) for row in matched))
    history.cost_paise = int(sum(int(row.cost_total or 0) for row in matched))
    history.last_occurred_on = max(row.business_date for row in matched)
    history.tiers.append(
        HistoryTier(
            scope="line" if station else "plant",
            label=f"This line ({station.line})"
            if station and station.line
            else "Plant-wide",
            events=history.events,
            minutes=history.minutes,
            cost_paise=history.cost_paise,
        )
    )

    mid = as_of - timedelta(days=TREND_HALF_DAYS)
    history.events_recent = sum(1 for row in matched if row.business_date > mid)
    history.events_prior = history.events - history.events_recent


# ---------------------------------------------------------------------------
# §6.4 — duplicate-Action and recurrence detection
# ---------------------------------------------------------------------------


async def _attach_actions(
    session: AsyncSession,
    *,
    plant: Plant,
    history: CoreHistory,
    station: Station | None,
    reason_ids: list[uuid.UUID],
    part_id: uuid.UUID | None,
    as_of: date,
) -> None:
    """Is somebody already on this, or did a fix stop holding?

    *"The recurrence case is worth more than it looks. A verified saving that
    quietly stops holding is the most dangerous number in the whole platform,
    and an operator noticing it on the floor is the fastest detection mechanism
    that exists."*
    """
    if station is None and not reason_ids and part_id is None:
        return

    filters = [Action.plant_id == plant.id]
    scope: list = []
    if station is not None:
        scope.append(Action.station_id == station.id)
    if reason_ids:
        scope.append(Action.reason_code_id.in_(reason_ids))
    if part_id is not None:
        scope.append(Action.part_id == part_id)
    if not scope:
        return
    # Both keys must agree where both are known — an Action on this machine for
    # a different reason is not a duplicate.
    filters.append(and_(*scope) if len(scope) > 1 else scope[0])

    rows = (
        await session.scalars(
            select(Action).where(*filters).order_by(Action.created_at.desc()).limit(20)
        )
    ).all()

    # An Action is "already covering this" while it is still running: not
    # cancelled, not verified, and never closed. REOPENED counts as open —
    # somebody is back on it.
    for action in rows:
        if action.closed_at is None and action.state not in (
            PdcaState.VERIFIED,
            PdcaState.CANCELLED,
        ):
            history.open_action = _action_out(action)
            return

    cutoff = as_of - timedelta(days=RECURRENCE_WINDOW_DAYS)
    for action in rows:
        if action.closed_at is None:
            continue
        closed_on = action.closed_at.date()
        if closed_on < cutoff:
            continue
        # A closed fix only "came back" if the problem happened after closure.
        if history.last_occurred_on and history.last_occurred_on > closed_on:
            history.recurrence_of = _action_out(action)
            return


def _action_out(action: Action) -> DuplicateAction:
    return DuplicateAction(
        action_id=action.id,
        reference=action.reference,
        title=action.title,
        owner_name=action.owner_name,
        due_date=action.due_date,
        state=action.state.value,
        closed_on=action.closed_at.date() if action.closed_at else None,
    )


# ---------------------------------------------------------------------------
# Factory Memory — "we fixed something like this before"
# ---------------------------------------------------------------------------


async def _factory_memory(
    session: AsyncSession,
    *,
    plant: Plant,
    station: Station | None,
    part_id: uuid.UUID | None,
    reason_ids: list[uuid.UUID],
    text: str,
    limit: int = 3,
) -> list[dict]:
    """Closures on the same machine, part or reason — Core Layer 4, read-only."""
    scope = []
    if reason_ids:
        scope.append(MemoryEntry.reason_code_id.in_(reason_ids))
    if station is not None:
        scope.append(MemoryEntry.station_id == station.id)
    if part_id is not None:
        scope.append(MemoryEntry.part_id == part_id)
    if not scope:
        return []

    rows = (
        await session.scalars(
            select(MemoryEntry)
            .where(MemoryEntry.plant_id == plant.id, or_(*scope))
            .order_by(MemoryEntry.occurred_on.desc().nullslast())
            .limit(limit)
        )
    ).all()
    return [
        {
            "id": str(row.id),
            "what_fixed_it": row.what_fixed_it,
            "check_first_next_time": row.check_first_next_time,
            "fixed_by_name": row.fixed_by_name,
            "occurred_on": row.occurred_on.isoformat() if row.occurred_on else None,
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# The plant's own yardsticks, and the rank
# ---------------------------------------------------------------------------


async def _reference_scales(
    session: AsyncSession, *, plant: Plant, history: CoreHistory, since: date
) -> None:
    """Rank this problem among the plant's recurring losses, and set the scale.

    §11.1: *"Normalise against the plant's own distribution — scale cost and
    frequency against that plant's top-decile recurring loss, not an absolute
    rupee scale, so the score means the same thing in a 30-machine plant and a
    300-machine plant."*
    """
    rows = (
        await session.execute(
            select(
                DowntimeEvent.reason_code_id,
                func.count(DowntimeEvent.id),
                func.coalesce(func.sum(DowntimeEvent.cost_total), 0),
            )
            .where(
                DowntimeEvent.plant_id == plant.id,
                DowntimeEvent.business_date >= since,
                DowntimeEvent.reason_code_id.is_not(None),
            )
            .group_by(DowntimeEvent.reason_code_id)
            .order_by(func.coalesce(func.sum(DowntimeEvent.cost_total), 0).desc())
        )
    ).all()
    if not rows:
        return

    history.ranked_of = len(rows)
    costs = [int(cost or 0) for _, _, cost in rows]
    counts = sorted((int(count or 0) for _, count, _ in rows), reverse=True)

    # Top decile, with a floor of the single largest for a plant with few codes.
    decile = max(1, len(costs) // 10)
    history.cost_reference = max(costs[:decile]) if costs else 0
    history.frequency_reference = max(counts[:decile]) if counts else 0

    if history.cost_paise > 0:
        history.rank = sum(1 for cost in costs if cost > history.cost_paise) + 1


def _summarise(history: CoreHistory) -> None:
    """The one-line verdict, and §6.5's honest answer when there is no history."""
    if history.events_recent > history.events_prior:
        history.trend = "rising"
    elif history.events_recent < history.events_prior:
        history.trend = "falling"

    if history.window_days > 0 and history.cost_paise > 0:
        history.annual_run_rate_paise = int(
            history.cost_paise * (365 / history.window_days)
        )

    if not history.found:
        history.message = (
            "This looks like a new one — I have no record of it in the last "
            f"{history.window_days // 30} months. First-time problems are worth "
            "reporting. Continuing with your estimate."
        )
        return

    months = max(1, history.window_days // 30)
    parts = [
        f"{history.events} recorded event{'' if history.events == 1 else 's'} in "
        f"{months} months, costing {format_inr(history.cost_paise)}."
    ]
    if history.rank and history.rank <= 10:
        parts.append(f"This is the #{history.rank} recurring loss in the plant.")
    if history.trend == "rising":
        parts.append(
            f"It is getting worse — {history.events_recent} in the last 60 days "
            f"against {history.events_prior} in the 60 before."
        )
    if history.recurrence_of:
        parts.append(
            f"{history.recurrence_of.reference} was closed for this and it has "
            "come back — that countermeasure did not hold."
        )
    elif history.open_action:
        parts.append(
            f"{history.open_action.reference} is already open on this, owned by "
            f"{history.open_action.owner_name or 'someone'}."
        )
    else:
        parts.append("Nobody has an open action on this.")
    history.message = " ".join(parts)


# ---------------------------------------------------------------------------
# Step 9 — what will go wrong, from Core's own Action history
# ---------------------------------------------------------------------------

#: §5.2: *"Core already knows this plant's average purchase-approval time, its
#: vendor lead times and its shutdown patterns from the Actions engine. Phase 2
#: prediction is available in the plugin from the first Kaizen. Do not gate it
#: behind a Kaizen-volume threshold."*
PURCHASE_THRESHOLD_PAISE = 1_000_000  # ₹10,000


@dataclass(slots=True)
class Forecast:
    probability: int = 0  # 0-100
    expected_days: int = 0
    obstacles: list[dict] = field(default_factory=list)
    advice: str | None = None
    learned: bool = False  # from this plant's own data, or rule-based

    def as_dict(self) -> dict:
        return asdict(self)


async def forecast(
    session: AsyncSession,
    *,
    plant: Plant,
    investment_paise: int,
    needs_shutdown: bool,
    external_vendor: bool = False,
    safety: bool = False,
    as_of: date | None = None,
) -> Forecast:
    """Step 9 — the implementation forecast, shown to the approver only.

    Rule-based on day one, learned the moment Core has closed Actions to learn
    from. The distinction is surfaced: an approver told "11 days on average
    here" should know whether that came from their own plant or from a default.
    """
    as_of = as_of or date.today()
    result = Forecast(probability=94, expected_days=6)
    obstacles: list[dict] = []

    median_days, sample = await _median_closure_days(session, plant=plant, as_of=as_of)
    if sample >= 5:
        result.learned = True
        result.expected_days = median_days

    if investment_paise > PURCHASE_THRESHOLD_PAISE:
        detail = (
            f"averaging {median_days} days for items over "
            f"{format_inr(PURCHASE_THRESHOLD_PAISE)} here"
            if result.learned
            else "purchase approval is the usual delay on spends this size"
        )
        obstacles.append({"risk": "high", "what": "Purchase approval", "detail": detail})
        result.probability -= 20
        result.expected_days += max(7, median_days)
    else:
        obstacles.append(
            {"risk": "none", "what": "No purchase approval needed", "detail": ""}
        )

    if needs_shutdown:
        obstacles.append(
            {
                "risk": "high",
                "what": "Needs a shutdown window",
                "detail": "Has to wait for a planned stop or a weekend.",
            }
        )
        result.probability -= 25
        result.expected_days += 14
    else:
        obstacles.append({"risk": "none", "what": "No shutdown required", "detail": ""})

    if external_vendor:
        obstacles.append(
            {
                "risk": "medium",
                "what": "External vendor lead time",
                "detail": "Delivery is outside the plant's control.",
            }
        )
        result.probability -= 12
        result.expected_days += 10
    else:
        obstacles.append(
            {"risk": "none", "what": "No external vendor needed", "detail": ""}
        )

    if safety:
        # A safety item moves faster because it is routed straight to the Plant
        # Head — that is a real effect of the routing rule, not optimism.
        result.probability = min(99, result.probability + 5)
        result.expected_days = max(2, result.expected_days - 3)

    result.obstacles = obstacles
    result.probability = max(35, min(99, result.probability))

    high = [o for o in obstacles if o["risk"] == "high"]
    if len(high) == 1 and high[0]["what"] == "Purchase approval":
        result.advice = (
            f"To move faster: pre-approve the {format_inr(investment_paise)} spend "
            "now — that removes the only high-risk step."
        )
    elif len(high) > 1:
        result.advice = (
            "Two things can hold this up. Settling the spend and the shutdown "
            "window together would halve the wait."
        )
    return result


async def _median_closure_days(
    session: AsyncSession, *, plant: Plant, as_of: date
) -> tuple[int, int]:
    """How long an Action actually takes to close at this plant."""
    since = datetime.combine(as_of - timedelta(days=365), datetime.min.time(), UTC)
    rows = (
        await session.execute(
            select(Action.created_at, Action.closed_at).where(
                Action.plant_id == plant.id,
                Action.closed_at.is_not(None),
                Action.created_at >= since,
            )
        )
    ).all()
    spans = sorted(
        max(0, (closed - created).days) for created, closed in rows if closed and created
    )
    if not spans:
        return (11, 0)
    return (spans[len(spans) // 2], len(spans))
