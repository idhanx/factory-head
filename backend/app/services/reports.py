"""Read models for every screen and report.

One rule governs this module: **no screen computes anything.** Every number
comes from `app.kernel`, so the owner's home screen, the meeting pack, the
WhatsApp report and a Copilot answer are guaranteed to agree — which is exactly
what §8.1 demands.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.kernel import (
    D,
    DowntimeSlice,
    LossLine,
    OeeTreatment,
    Recoverability,
    StationRun,
    aggregate_oee,
    annualised_projection,
    breakdown_minutes,
    count_breakdowns,
    downtime_percent,
    first_pass_yield,
    format_inr,
    mtbf,
    mttr,
    ppm,
    productivity,
    rank_losses,
    running_roi,
    schedule_adherence,
)
from app.models import (
    Action,
    DowntimeEvent,
    DowntimeStatus,
    JobOrder,
    JobSession,
    Part,
    PdcaState,
    Plant,
    ProductionCount,
    ProductionTarget,
    QualityRecord,
    ReasonCode,
    RecoveredProfitEntry,
    RoutingOperation,
    Shift,
    ShiftSession,
    Station,
)
from app.services.clock import plant_today
from app.services.costing import cost_open_event

# ---------------------------------------------------------------------------
# Screen 2 — the machine grid (the supervisor's home)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class StationTile:
    """One tile. Green running / red stopped, count vs target, live ₹ ticker."""

    station_id: str
    code: str
    name: str
    line: str | None
    status: str  # running | stopped | idle
    count: int
    target: int
    job_number: str | None
    part_number: str | None
    open_event_id: str | None = None
    stopped_since: datetime | None = None
    stopped_minutes: Decimal = Decimal(0)
    reason_label: str | None = None
    live_cost: int = 0
    # Paise per minute, so the browser can interpolate smoothly between pushes
    # without ever inventing a number of its own.
    ticker_rate_per_minute: Decimal = Decimal(0)
    shift_cost: int = 0


async def machine_grid(
    session: AsyncSession, *, plant: Plant, shift_session: ShiftSession | None
) -> list[StationTile]:
    stations = (
        await session.scalars(
            select(Station)
            .where(Station.plant_id == plant.id, Station.is_active.is_(True))
            .order_by(Station.line, Station.code)
        )
    ).all()

    open_events = {
        event.station_id: event
        for event in (
            await session.scalars(
                select(DowntimeEvent).where(
                    DowntimeEvent.plant_id == plant.id,
                    DowntimeEvent.status == DowntimeStatus.OPEN,
                )
            )
        ).all()
    }
    reasons = {
        r.id: r
        for r in (
            await session.scalars(select(ReasonCode).where(ReasonCode.plant_id == plant.id))
        ).all()
    }

    counts: dict[uuid.UUID, int] = {}
    shift_costs: dict[uuid.UUID, int] = {}
    active_jobs: dict[uuid.UUID, tuple[str, str]] = {}
    if shift_session is not None:
        counts = dict(
            (
                await session.execute(
                    select(
                        ProductionCount.station_id,
                        func.coalesce(func.sum(ProductionCount.count), 0),
                    )
                    .where(ProductionCount.shift_session_id == shift_session.id)
                    .group_by(ProductionCount.station_id)
                )
            ).all()
        )
        shift_costs = dict(
            (
                await session.execute(
                    select(
                        DowntimeEvent.station_id,
                        func.coalesce(func.sum(DowntimeEvent.cost_total), 0),
                    )
                    .where(DowntimeEvent.shift_session_id == shift_session.id)
                    .group_by(DowntimeEvent.station_id)
                )
            ).all()
        )
        active_jobs = {
            station_id: (job_number, part_number)
            for station_id, job_number, part_number in (
                await session.execute(
                    select(JobSession.station_id, JobOrder.job_number, Part.part_number)
                    .join(JobOrder, JobOrder.id == JobSession.job_order_id)
                    .join(Part, Part.id == JobOrder.part_id)
                    .where(
                        JobSession.shift_session_id == shift_session.id,
                        JobSession.ended_at.is_(None),
                    )
                )
            ).all()
        }

    targets = (
        (await resolve_targets(session, plant.id, shift_session)).by_station
        if shift_session
        else {}
    )
    now = datetime.now(UTC)
    tiles: list[StationTile] = []

    for station in stations:
        event = open_events.get(station.id)
        job = active_jobs.get(station.id)

        tile = StationTile(
            station_id=str(station.id),
            code=station.code,
            name=station.name,
            line=station.line,
            status="stopped" if event else ("running" if job else "idle"),
            count=int(counts.get(station.id, 0)),
            target=targets.get(station.id, 0),
            job_number=job[0] if job else None,
            part_number=job[1] if job else None,
            shift_cost=int(shift_costs.get(station.id, 0)),
        )

        if event is not None:
            live = cost_open_event(event, as_of=now)
            reason = reasons.get(event.reason_code_id)
            tile.open_event_id = str(event.id)
            tile.stopped_since = event.started_at
            tile.stopped_minutes = live.duration_minutes.quantize(Decimal("0.1"))
            tile.reason_label = reason.label if reason else None
            tile.live_cost = live.total
            tile.ticker_rate_per_minute = (
                Decimal(
                    event.snapshot_machine_rate
                    + event.snapshot_labour_rate
                    + event.snapshot_overhead_rate
                )
                / Decimal(60)
            ).quantize(Decimal("0.01"))
            tile.shift_cost += live.total

        tiles.append(tile)

    return tiles


# ---------------------------------------------------------------------------
# The ranked loss table — Screens 7 and 8, the WhatsApp report, the Coach
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class LossReport:
    business_date: date
    recoverable_total: int
    structural_total: int
    total_minutes: Decimal
    recoverable: list[dict]
    structural: list[dict]

    @property
    def headline(self) -> str:
        return format_inr(self.recoverable_total)


async def ranked_loss_report(
    session: AsyncSession,
    *,
    plant: Plant,
    start: date,
    end: date,
    group_by_station: bool = True,
    shift_session_id: uuid.UUID | None = None,
) -> LossReport:
    """The table that made the product: ranked by rupees, split R/S.

    Never ranked by frequency and never ranked by minutes. A 10-minute stop on
    the bottleneck outranks a two-hour stop on a machine nobody was waiting for.
    """
    rows = (
        await session.execute(
            select(
                Station.code,
                ReasonCode.label,
                ReasonCode.recoverability,
                func.sum(DowntimeEvent.duration_minutes),
                func.sum(DowntimeEvent.cost_total),
                func.count(DowntimeEvent.id),
            )
            .join(Station, Station.id == DowntimeEvent.station_id)
            .join(ReasonCode, ReasonCode.id == DowntimeEvent.reason_code_id)
            .where(
                DowntimeEvent.plant_id == plant.id,
                DowntimeEvent.business_date >= start,
                DowntimeEvent.business_date <= end,
                DowntimeEvent.status == DowntimeStatus.CLOSED,
                *(
                    [DowntimeEvent.shift_session_id == shift_session_id]
                    if shift_session_id
                    else []
                ),
            )
            .group_by(Station.code, ReasonCode.label, ReasonCode.recoverability)
        )
    ).all()

    lines = [
        LossLine(
            station=code if group_by_station else "All",
            reason=label,
            minutes=Decimal(minutes or 0),
            cost=int(cost or 0),
            recoverability=Recoverability(recoverability.value),
            event_count=int(count),
        )
        for code, label, recoverability, minutes, cost, count in rows
    ]
    ranked = rank_losses(lines)

    return LossReport(
        business_date=end,
        recoverable_total=ranked.recoverable_total,
        structural_total=ranked.structural_total,
        total_minutes=ranked.total_minutes,
        recoverable=[_line_dict(line) for line in ranked.recoverable],
        structural=[_line_dict(line) for line in ranked.structural],
    )


def _line_dict(line: LossLine) -> dict:
    return {
        "station": line.station,
        "reason": line.reason,
        "minutes": float(line.minutes),
        "cost": line.cost,
        "cost_display": format_inr(line.cost),
        "recoverability": line.recoverability.value,
        "event_count": line.event_count,
    }


# ---------------------------------------------------------------------------
# Screen 8 — Owner Home. Four numbers and nothing else.
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class OwnerHome:
    """§7: "the owner's home is money, not machines".

    Four numbers: Recovered ₹, Lost ₹, Actions completed, running ROI. Every one
    is a door — tap it and you walk down to the events underneath.
    """

    recovered_profit: int
    lost_yesterday: int
    lost_month_to_date: int
    actions_completed: int
    actions_open: int
    running_roi: float
    annualised_projection: int
    top_losses: list[dict]
    structural_yesterday: int
    as_of: date


async def owner_home(
    session: AsyncSession, *, plant: Plant, as_of: date | None = None
) -> OwnerHome:
    as_of = as_of or plant_today(plant)
    yesterday = as_of - timedelta(days=1)
    month_start = as_of.replace(day=1)

    yesterday_losses = await ranked_loss_report(
        session, plant=plant, start=yesterday, end=yesterday
    )
    month_recoverable = await _recoverable_between(session, plant.id, month_start, as_of)
    # §8.1: "current 4-week recoverable loss run-rate x 13" — four complete
    # weeks, 28 days ending yesterday. Today is still being recorded.
    four_weeks = await _recoverable_between(
        session, plant.id, as_of - timedelta(days=28), yesterday
    )

    # The ledger, not Verified Actions: a fix that held for six weeks and then
    # reopened still recovered those six weeks (§5.6 — reopening stops accrual).
    recovered = int(
        await session.scalar(
            select(func.coalesce(func.sum(RecoveredProfitEntry.saving), 0)).where(
                RecoveredProfitEntry.plant_id == plant.id,
                RecoveredProfitEntry.counted.is_(True),
            )
        )
        or 0
    )

    completed = await session.scalar(
        select(func.count(Action.id)).where(
            Action.plant_id == plant.id,
            Action.state.in_([PdcaState.VERIFIED, PdcaState.MONITORING]),
        )
    )
    open_actions = await session.scalar(
        select(func.count(Action.id)).where(
            Action.plant_id == plant.id,
            Action.state.in_(
                [
                    PdcaState.OPEN,
                    PdcaState.ROOT_CAUSE,
                    PdcaState.PLANNED,
                    PdcaState.IN_PROGRESS,
                    PdcaState.REOPENED,
                ]
            ),
        )
    )

    months_live = _months_since(plant.go_live_date, as_of)
    fees_paid = plant.monthly_fee * months_live

    return OwnerHome(
        recovered_profit=recovered,
        lost_yesterday=yesterday_losses.recoverable_total,
        lost_month_to_date=month_recoverable,
        actions_completed=int(completed or 0),
        actions_open=int(open_actions or 0),
        running_roi=float(
            running_roi(cumulative_recovered=recovered, cumulative_fees_paid=fees_paid)
        ),
        annualised_projection=annualised_projection(four_weeks),
        top_losses=yesterday_losses.recoverable[:5],
        structural_yesterday=yesterday_losses.structural_total,
        as_of=as_of,
    )


# ---------------------------------------------------------------------------
# Trends — the owner's "is this getting better?" question
# ---------------------------------------------------------------------------


async def loss_trend(
    session: AsyncSession, *, plant: Plant, start: date, end: date
) -> list[dict]:
    """Daily recoverable and structural loss across a period.

    Reported as two separate series, never one total. The whole point of the
    split is that one of them is money someone can go and get back, and the
    other is not — a combined line would hide exactly the number that matters.

    Days with no production still appear, at zero: a gap in a trend line reads
    as missing data, which invites the wrong question.
    """
    rows = (
        await session.execute(
            select(
                DowntimeEvent.business_date,
                ReasonCode.recoverability,
                func.sum(DowntimeEvent.cost_total),
                func.sum(DowntimeEvent.duration_minutes),
            )
            .join(ReasonCode, ReasonCode.id == DowntimeEvent.reason_code_id)
            .where(
                DowntimeEvent.plant_id == plant.id,
                DowntimeEvent.business_date >= start,
                DowntimeEvent.business_date <= end,
                DowntimeEvent.status == DowntimeStatus.CLOSED,
            )
            .group_by(DowntimeEvent.business_date, ReasonCode.recoverability)
        )
    ).all()

    buckets: dict[date, dict] = {}
    cursor = start
    while cursor <= end:
        buckets[cursor] = {
            "date": cursor.isoformat(),
            "recoverable": 0,
            "structural": 0,
            "minutes": 0.0,
        }
        cursor += timedelta(days=1)

    for business_date, recoverability, cost, minutes in rows:
        bucket = buckets.get(business_date)
        if bucket is None:
            continue
        key = (
            "recoverable"
            if Recoverability(recoverability.value) is Recoverability.RECOVERABLE
            else "structural"
        )
        bucket[key] += int(cost or 0)
        bucket["minutes"] += float(minutes or 0)

    return list(buckets.values())


async def recovered_trend(
    session: AsyncSession, *, plant: Plant, weeks: int = 12
) -> list[dict]:
    """Cumulative credited Recovered Profit, week by week.

    Deliberately a separate chart from the loss trend rather than a second axis
    on it: they are different measures on different scales, and a dual-axis
    chart invites the reader to see a relationship the data does not support.
    """
    from app.models import RecoveredProfitEntry

    rows = (
        await session.execute(
            select(
                RecoveredProfitEntry.week_start,
                func.sum(RecoveredProfitEntry.saving),
            )
            .where(
                RecoveredProfitEntry.plant_id == plant.id,
                RecoveredProfitEntry.counted.is_(True),
            )
            .group_by(RecoveredProfitEntry.week_start)
            .order_by(RecoveredProfitEntry.week_start)
        )
    ).all()

    series: list[dict] = []
    running = 0
    for week_start, saving in rows[-weeks:]:
        running += int(saving or 0)
        series.append(
            {
                "week": week_start.isoformat(),
                "saving": int(saving or 0),
                "cumulative": running,
            }
        )
    return series


# ---------------------------------------------------------------------------
# KPI pack — one page per role (§8)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ShiftKpis:
    shift_session_id: str
    business_date: date
    shift_name: str
    oee: float
    availability: float
    performance: float
    quality: float
    planned_minutes: float
    downtime_minutes: float
    recoverable_downtime_minutes: float
    total_count: int
    good_count: int
    reject_count: int
    target: int
    schedule_adherence: float
    ppm: float
    first_pass_yield: float
    mtbf_minutes: float
    mttr_minutes: float
    breakdown_count: int
    recoverable_loss: int
    structural_loss: int
    man_hours: float = 0.0
    productivity_per_man_hour: float | None = None
    downtime_percent: float = 0.0
    recoverable_downtime_percent: float = 0.0
    structural_downtime_percent: float = 0.0
    flags: list[str] = field(default_factory=list)


async def station_runs(
    session: AsyncSession, *, plant: Plant, shift_session_ids: list[uuid.UUID]
) -> list[tuple[Station, uuid.UUID, StationRun]]:
    """One :class:`StationRun` per station per shift, for every station that ran.

    The shared input to every OEE figure above station level — a shift, a line,
    a week — so they are all aggregated the same way by
    :func:`app.kernel.aggregate_oee` and cannot drift apart.

    * planned time is the shift's, *per station*;
    * cycle time is weighted by what the station actually produced, from the
      routing step each job ran — not an average of downtime snapshots;
    * good = produced − rejected. Inspections record rejects against batches,
      so parts nobody rejected count as good, which is the OEE convention when
      only rejects are recorded.
    """
    if not shift_session_ids:
        return []

    planned = dict(
        (
            await session.execute(
                select(ShiftSession.id, ShiftSession.planned_minutes).where(
                    ShiftSession.id.in_(shift_session_ids)
                )
            )
        ).all()
    )

    produced: dict[tuple, tuple[int, Decimal, int]] = {}
    for station_id, shift_id, total, weighted, timed in (
        await session.execute(
            select(
                ProductionCount.station_id,
                ProductionCount.shift_session_id,
                func.coalesce(func.sum(ProductionCount.count), 0),
                func.coalesce(
                    func.sum(ProductionCount.count * RoutingOperation.cycle_time_min), 0
                ),
                func.coalesce(
                    func.sum(ProductionCount.count).filter(
                        RoutingOperation.cycle_time_min.is_not(None)
                    ),
                    0,
                ),
            )
            .outerjoin(JobSession, JobSession.id == ProductionCount.job_session_id)
            .outerjoin(
                RoutingOperation, RoutingOperation.id == JobSession.routing_operation_id
            )
            .where(
                ProductionCount.plant_id == plant.id,
                ProductionCount.shift_session_id.in_(shift_session_ids),
            )
            .group_by(ProductionCount.station_id, ProductionCount.shift_session_id)
        )
    ).all():
        produced[(station_id, shift_id)] = (int(total), Decimal(weighted or 0), int(timed))

    # A station that ran a job and made nothing still had planned time — it is a
    # 0% station, not an absent one.
    ran = set(produced) | set(
        (
            await session.execute(
                select(JobSession.station_id, JobSession.shift_session_id)
                .where(
                    JobSession.plant_id == plant.id,
                    JobSession.shift_session_id.in_(shift_session_ids),
                )
                .distinct()
            )
        ).all()
    )

    rejected = {
        (station_id, shift_id): int(total or 0)
        for station_id, shift_id, total in (
            await session.execute(
                select(
                    QualityRecord.station_id,
                    QualityRecord.shift_session_id,
                    func.sum(QualityRecord.reject_count),
                )
                .where(
                    QualityRecord.plant_id == plant.id,
                    QualityRecord.shift_session_id.in_(shift_session_ids),
                )
                .group_by(QualityRecord.station_id, QualityRecord.shift_session_id)
            )
        ).all()
    }

    slices: dict[tuple, list[DowntimeSlice]] = {}
    for event, reason, code in (
        await session.execute(
            select(DowntimeEvent, ReasonCode, Station.code)
            .join(ReasonCode, ReasonCode.id == DowntimeEvent.reason_code_id)
            .join(Station, Station.id == DowntimeEvent.station_id)
            .where(
                DowntimeEvent.plant_id == plant.id,
                DowntimeEvent.shift_session_id.in_(shift_session_ids),
                DowntimeEvent.duration_minutes.is_not(None),
            )
        )
    ).all():
        slices.setdefault((event.station_id, event.shift_session_id), []).append(
            DowntimeSlice(
                minutes=Decimal(event.duration_minutes or 0),
                recoverability=Recoverability(reason.recoverability.value),
                oee_treatment=OeeTreatment(reason.oee_treatment.value),
                is_breakdown=reason.is_breakdown,
                cost=event.cost_total,
                reason_code=reason.label,
                station_id=code,
            )
        )

    stations = {
        station.id: station
        for station in (
            await session.scalars(
                select(Station).where(
                    Station.plant_id == plant.id,
                    Station.id.in_({station_id for station_id, _ in ran}),
                )
            )
        ).all()
    }

    runs: list[tuple[Station, uuid.UUID, StationRun]] = []
    for station_id, shift_id in sorted(ran, key=lambda k: (str(k[1]), str(k[0]))):
        station = stations.get(station_id)
        if station is None:
            continue
        total, weighted, timed = produced.get((station_id, shift_id), (0, Decimal(0), 0))
        runs.append(
            (
                station,
                shift_id,
                StationRun(
                    station=station.code,
                    shift_minutes=Decimal(planned.get(shift_id) or 0),
                    downtime=slices.get((station_id, shift_id), []),
                    cycle_time_min=(weighted / Decimal(timed)) if timed else Decimal(0),
                    total_count=total,
                    good_count=max(0, total - rejected.get((station_id, shift_id), 0)),
                ),
            )
        )
    return runs


async def shift_kpis(
    session: AsyncSession, *, plant: Plant, shift_session: ShiftSession
) -> ShiftKpis:
    """Every KPI for one shift, from one pass over its events."""
    slices = await _downtime_slices(session, plant.id, shift_session.id)

    counts = await session.scalar(
        select(func.coalesce(func.sum(ProductionCount.count), 0)).where(
            ProductionCount.shift_session_id == shift_session.id
        )
    )
    quality_rows = (
        await session.execute(
            select(
                func.coalesce(func.sum(QualityRecord.good_count), 0),
                func.coalesce(func.sum(QualityRecord.reject_count), 0),
                func.coalesce(func.sum(QualityRecord.rework_count), 0),
            ).where(QualityRecord.shift_session_id == shift_session.id)
        )
    ).one()
    good, rejected, reworked = (int(x) for x in quality_rows)

    total_count = int(counts or 0)

    runs = await station_runs(session, plant=plant, shift_session_ids=[shift_session.id])
    oee, _ = aggregate_oee([run for _, _, run in runs])

    targets = await resolve_targets(session, plant.id, shift_session)
    target_total = targets.total_for([station for station, _, _ in runs])
    rates = production_rates(runs, await man_hours(session, [shift_session.id]))

    breakdowns = count_breakdowns(slices)
    shift_name = await session.scalar(
        select(Shift.name).where(Shift.id == shift_session.shift_id)
    )

    recoverable = sum(
        s.cost for s in slices if s.recoverability is Recoverability.RECOVERABLE
    )
    structural = sum(
        s.cost for s in slices if s.recoverability is Recoverability.STRUCTURAL
    )

    return ShiftKpis(
        shift_session_id=str(shift_session.id),
        business_date=shift_session.business_date,
        shift_name=shift_name or "",
        oee=float(oee.oee),
        availability=float(oee.availability),
        performance=float(oee.performance),
        quality=float(oee.quality),
        planned_minutes=float(oee.planned_minutes),
        downtime_minutes=float(
            oee.unplanned_downtime_minutes + oee.planned_downtime_minutes
        ),
        recoverable_downtime_minutes=float(
            sum(
                (
                    s.minutes
                    for s in slices
                    if s.recoverability is Recoverability.RECOVERABLE
                ),
                Decimal(0),
            )
        ),
        total_count=total_count,
        good_count=good,
        reject_count=rejected,
        target=target_total,
        schedule_adherence=float(
            schedule_adherence(actual_output=total_count, planned_output=target_total)
        ),
        ppm=float(ppm(rejected_parts=rejected, total_inspected=good + rejected)),
        first_pass_yield=float(
            first_pass_yield(
                good_first_time=max(0, good - reworked),
                parts_started=good + rejected,
            )
        ),
        mtbf_minutes=float(
            mtbf(total_run_minutes=oee.run_minutes, breakdown_count=breakdowns)
        ),
        mttr_minutes=float(
            mttr(
                total_breakdown_minutes=breakdown_minutes(slices),
                breakdown_count=breakdowns,
            )
        ),
        breakdown_count=breakdowns,
        recoverable_loss=recoverable,
        structural_loss=structural,
        man_hours=rates["man_hours"],
        productivity_per_man_hour=rates["per_man_hour"],
        downtime_percent=rates["downtime_percent"]["total"],
        recoverable_downtime_percent=rates["downtime_percent"]["recoverable"],
        structural_downtime_percent=rates["downtime_percent"]["structural"],
        flags=list(oee.flags),
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _downtime_slices(
    session: AsyncSession, plant_id: uuid.UUID, shift_session_id: uuid.UUID
) -> list[DowntimeSlice]:
    rows = (
        await session.execute(
            select(DowntimeEvent, ReasonCode, Station.code)
            .join(ReasonCode, ReasonCode.id == DowntimeEvent.reason_code_id)
            .join(Station, Station.id == DowntimeEvent.station_id)
            .where(
                DowntimeEvent.plant_id == plant_id,
                DowntimeEvent.shift_session_id == shift_session_id,
            )
        )
    ).all()
    return [
        DowntimeSlice(
            minutes=Decimal(event.duration_minutes or 0),
            recoverability=Recoverability(reason.recoverability.value),
            oee_treatment=OeeTreatment(reason.oee_treatment.value),
            is_breakdown=reason.is_breakdown,
            cost=event.cost_total,
            reason_code=reason.label,
            station_id=station_code,
        )
        for event, reason, station_code in rows
    ]


async def _recoverable_between(
    session: AsyncSession, plant_id: uuid.UUID, start: date, end: date
) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(DowntimeEvent.cost_total), 0))
        .join(ReasonCode, ReasonCode.id == DowntimeEvent.reason_code_id)
        .where(
            DowntimeEvent.plant_id == plant_id,
            DowntimeEvent.business_date >= start,
            DowntimeEvent.business_date <= end,
            ReasonCode.recoverability == Recoverability.RECOVERABLE.value,
        )
    )
    return int(total or 0)


@dataclass(slots=True)
class Targets:
    """T7 targets in force for one shift: per station, and per line."""

    by_station: dict[uuid.UUID, int]
    by_line: dict[str, int]

    def total_for(self, stations) -> int:
        """Target for the stations that ran: a station's own target where it has
        one, and a line's target once for a line with no station targets."""
        ran = {station.id: station for station in stations}
        total = sum(self.by_station.get(station_id, 0) for station_id in ran)
        lines_with_station_targets = {
            station.line for station_id, station in ran.items() if station_id in self.by_station
        }
        for line in {station.line for station in ran.values() if station.line}:
            if line not in lines_with_station_targets:
                total += self.by_line.get(line, 0)
        return total


async def resolve_targets(
    session: AsyncSession, plant_id: uuid.UUID, shift_session: ShiftSession
) -> Targets:
    """The latest target effective on the shift's date, a shift-specific one
    winning over one for all shifts."""
    rows = (
        await session.execute(
            select(ProductionTarget)
            .where(
                ProductionTarget.plant_id == plant_id,
                ProductionTarget.effective_from <= shift_session.business_date,
                (ProductionTarget.shift_id.is_(None))
                | (ProductionTarget.shift_id == shift_session.shift_id),
            )
            .order_by(
                ProductionTarget.shift_id.is_not(None),
                ProductionTarget.effective_from,
                ProductionTarget.created_at,
            )
        )
    ).scalars().all()
    by_station: dict[uuid.UUID, int] = {}
    by_line: dict[str, int] = {}
    for row in rows:  # later rows override earlier ones
        if row.station_id is not None:
            by_station[row.station_id] = int(row.target_parts)
        elif row.line:
            by_line[row.line] = int(row.target_parts)
    return Targets(by_station=by_station, by_line=by_line)


async def man_hours(session: AsyncSession, shift_session_ids: list[uuid.UUID]) -> Decimal:
    """Hours operators were on a job, from the job sessions supervisors already
    start — no roster to fill in (§8: "Counts + shift roster").

    One operator on two stations at once is one person's time, so each
    operator's intervals are merged before they are added up.
    """
    if not shift_session_ids:
        return Decimal(0)
    now = datetime.now(UTC)
    rows = (
        await session.execute(
            select(
                JobSession.operator_id,
                JobSession.shift_session_id,
                JobSession.started_at,
                JobSession.ended_at,
                ShiftSession.ended_at,
            )
            .join(ShiftSession, ShiftSession.id == JobSession.shift_session_id)
            .where(
                JobSession.shift_session_id.in_(shift_session_ids),
                JobSession.operator_id.is_not(None),
            )
        )
    ).all()

    intervals: dict[tuple, list[tuple[datetime, datetime]]] = {}
    for operator_id, shift_id, started, ended, shift_ended in rows:
        finish = ended or shift_ended or now
        if finish > started:
            intervals.setdefault((operator_id, shift_id), []).append((started, finish))

    seconds = Decimal(0)
    for spans in intervals.values():
        spans.sort()
        current_start, current_end = spans[0]
        for start, end in spans[1:]:
            if start <= current_end:
                current_end = max(current_end, end)
            else:
                seconds += Decimal((current_end - current_start).total_seconds())
                current_start, current_end = start, end
        seconds += Decimal((current_end - current_start).total_seconds())
    return (seconds / Decimal(3600)).quantize(Decimal("0.01"))


def production_rates(runs, hours: Decimal) -> dict:
    """§8.2 Productivity and Downtime %, from the same station runs as OEE."""
    oee, _ = aggregate_oee([run for _, _, run in runs])
    machine_hours = oee.run_minutes / Decimal(60)
    shift_minutes = sum((D(run.shift_minutes) for _, _, run in runs), Decimal(0))
    recoverable = sum(
        (
            event.minutes
            for _, _, run in runs
            for event in run.downtime
            if event.recoverability is Recoverability.RECOVERABLE
        ),
        Decimal(0),
    )
    structural = sum(
        (
            event.minutes
            for _, _, run in runs
            for event in run.downtime
            if event.recoverability is Recoverability.STRUCTURAL
        ),
        Decimal(0),
    )
    return {
        "good_parts": oee.good_count,
        "man_hours": float(hours),
        "per_man_hour": (
            float(productivity(good_parts=oee.good_count, man_hours=hours)) if hours > 0 else None
        ),
        "machine_hours": float(machine_hours.quantize(Decimal("0.01"))),
        "per_machine_hour": (
            float(productivity(good_parts=oee.good_count, man_hours=machine_hours))
            if machine_hours > 0
            else None
        ),
        "downtime_percent": {
            "total": float(
                downtime_percent(
                    total_downtime_minutes=recoverable + structural, planned_minutes=shift_minutes
                )
            ),
            "recoverable": float(
                downtime_percent(total_downtime_minutes=recoverable, planned_minutes=shift_minutes)
            ),
            "structural": float(
                downtime_percent(total_downtime_minutes=structural, planned_minutes=shift_minutes)
            ),
        },
    }


async def shift_report_text(
    session: AsyncSession, *, plant: Plant, shift_session: ShiftSession
) -> dict:
    """§6, 3:35 PM — the end-of-shift message the owner reads in 90 seconds.

    Every figure is this shift's own. The month-so-far line is labelled as such,
    and the annualised figure as a projection (§8.1).
    """
    kpis = await shift_kpis(session, plant=plant, shift_session=shift_session)
    losses = await ranked_loss_report(
        session,
        plant=plant,
        start=shift_session.business_date,
        end=shift_session.business_date,
        shift_session_id=shift_session.id,
    )
    home = await owner_home(
        session, plant=plant, as_of=shift_session.business_date + timedelta(days=1)
    )
    top = losses.recoverable[:3]
    lines = [
        f"{kpis.shift_name} {shift_session.business_date:%d %b}: {kpis.total_count:,} parts"
        + (f" (target {kpis.target:,})." if kpis.target else "."),
        f"Recoverable loss {format_inr(losses.recoverable_total)}.",
    ]
    if top:
        lines.append(
            "Top: "
            + "; ".join(
                f"{t['reason'].lower()} {t['station']} {t['cost_display']}" for t in top
            )
            + "."
        )
    lines.append(
        f"Month so far: {format_inr(home.lost_month_to_date, compact=True)} recoverable. "
        f"Annualised (projection): {format_inr(home.annualised_projection, compact=True)}."
    )
    return {
        "business_date": shift_session.business_date,
        "shift_name": kpis.shift_name,
        "text": " ".join(lines),
        "parts": kpis.total_count,
        "target": kpis.target,
        "oee": kpis.oee,
        "recoverable_loss_paise": losses.recoverable_total,
        "structural_loss_paise": losses.structural_total,
        "top_losses": top,
    }


async def daily_owner_report_text(session: AsyncSession, *, plant: Plant, day: date) -> str:
    """The morning summary of yesterday for the owner's phone (§4.4)."""
    losses = await ranked_loss_report(session, plant=plant, start=day, end=day)
    home = await owner_home(session, plant=plant, as_of=day + timedelta(days=1))
    top = losses.recoverable[:3]
    parts = [
        f"{plant.name}, {day:%a %d %b}: recoverable loss {format_inr(losses.recoverable_total)}"
        f" (structural {format_inr(losses.structural_total)}).",
    ]
    if top:
        parts.append(
            "Biggest: "
            + "; ".join(f"{t['reason'].lower()} {t['station']} {t['cost_display']}" for t in top)
            + "."
        )
    parts.append(
        f"Month so far {format_inr(home.lost_month_to_date, compact=True)}. "
        f"Recovered to date {format_inr(home.recovered_profit, compact=True)}, "
        f"{home.actions_open} actions open."
    )
    return " ".join(parts)


def _months_since(go_live: date | None, as_of: date) -> int:
    if go_live is None:
        return 0
    months = (as_of.year - go_live.year) * 12 + (as_of.month - go_live.month)
    return max(1, months + 1)
