"""Skill Matrix AI (§3 Layer 5).

    "Tracks which operator is trained on which machine/operation, and connects
     that matrix directly to downtime data — 'No Operator' events point at
     exactly where a skill gap is costing money, and the plugin ranks training
     needs by the rupee loss they would remove. It reuses the operator list and
     event stream already in Core; no new registers."

Two kinds of gap come out of that, and they are genuinely different problems:

  * a **gap** — a station lost time to 'No Operator', so someone was missing
    when the machine needed them;
  * **coverage** — a station only one person can run, which has cost nothing yet
    and is one resignation or one fever away from costing a great deal.

The second is the one plants never see coming, which is why it is ranked
alongside the first rather than buried under it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.kernel import downtime_event_cost, format_inr
from app.models import (
    Action,
    ActionTrigger,
    DowntimeEvent,
    JobOrder,
    JobSession,
    Operator,
    OperatorSkill,
    Part,
    PdcaState,
    Plant,
    ReasonCode,
    RoutingOperation,
    Section,
    Shift,
    SkillLevel,
    Station,
    TrainingNeed,
)
from app.services import actions as action_service
from app.services.costing import resolve_snapshot
from app.services.shifts import planned_minutes

#: Below this, one absence stops the station. The spec does not set a number;
#: two is the smallest that survives a single person being away.
MIN_COVERAGE = 2

#: Only levels at or above this can actually run the station alone.
COMPETENT = int(SkillLevel.COMPETENT)

#: The reason code that points at a skill gap (§3 Layer 5).
NO_OPERATOR_CODE = "NO_OPERATOR"

#: 'No Operator' events needed in the window before a station counts as a gap.
#: One such stop in ninety days is noise — a late bus, a doctor's appointment —
#: and a training list padded with noise is a list nobody reads (§12 risk 10).
MIN_GAP_EVENTS = 2


async def matrix(session: AsyncSession, *, plant: Plant) -> dict:
    """The whole grid: operators down the side, stations across the top.

    Returned as one payload because that is how it hangs on a plant wall, and
    how it needs to read on screen.
    """
    stations = (
        await session.scalars(
            select(Station)
            .where(
                Station.plant_id == plant.id,
                Station.is_active.is_(True),
                Station.section.in_([Section.MACHINING, Section.ASSEMBLY]),
            )
            .order_by(Station.line, Station.code)
        )
    ).all()
    operators = (
        await session.scalars(
            select(Operator)
            .where(Operator.plant_id == plant.id, Operator.is_active.is_(True))
            .order_by(Operator.name)
        )
    ).all()
    skills = (
        await session.scalars(
            select(OperatorSkill).where(OperatorSkill.plant_id == plant.id)
        )
    ).all()

    by_pair = {(s.operator_id, s.station_id): s for s in skills}

    coverage: dict[uuid.UUID, int] = {}
    for station in stations:
        coverage[station.id] = sum(
            1
            for operator in operators
            if (by_pair.get((operator.id, station.id)) or None)
            and by_pair[(operator.id, station.id)].level >= COMPETENT
        )

    return {
        "stations": [
            {
                "id": str(s.id),
                "code": s.code,
                "line": s.line,
                "is_bottleneck": s.is_bottleneck,
                "coverage": coverage.get(s.id, 0),
                "at_risk": coverage.get(s.id, 0) < MIN_COVERAGE,
            }
            for s in stations
        ],
        "operators": [
            {
                "id": str(o.id),
                "name": o.name,
                "employee_code": o.employee_code,
                "skills": {
                    str(s.id): (
                        by_pair.get((o.id, s.id)).level if by_pair.get((o.id, s.id)) else 0
                    )
                    for s in stations
                },
                "certified_count": sum(
                    1
                    for s in stations
                    if by_pair.get((o.id, s.id))
                    and by_pair[(o.id, s.id)].level >= COMPETENT
                ),
            }
            for o in operators
        ],
        "min_coverage": MIN_COVERAGE,
    }


async def set_skill(
    session: AsyncSession,
    *,
    plant: Plant,
    operator_id: uuid.UUID,
    station_id: uuid.UUID,
    level: int,
    certified_on: date | None = None,
) -> OperatorSkill:
    """Set one cell of the matrix. The plugin's only data entry, and it is not daily."""
    if not 0 <= level <= 3:
        raise ValueError("Skill level must be between 0 (none) and 3 (can train others)")

    skill = await session.scalar(
        select(OperatorSkill).where(
            OperatorSkill.plant_id == plant.id,
            OperatorSkill.operator_id == operator_id,
            OperatorSkill.station_id == station_id,
        )
    )
    if skill is None:
        skill = OperatorSkill(
            plant_id=plant.id, operator_id=operator_id, station_id=station_id
        )
        session.add(skill)

    skill.level = level
    if level >= COMPETENT and certified_on:
        skill.certified_on = certified_on
    await session.flush()
    return skill


async def detect_needs(
    session: AsyncSession, *, plant: Plant, window_days: int = 90
) -> list[TrainingNeed]:
    """Rank training needs by the rupee loss they would remove.

    Generated from Core data, never entered. Regenerated each run so a gap that
    has been closed stops appearing — a stale training list gets ignored.
    """
    since = date.today() - timedelta(days=window_days)

    # Clear previous unresolved, unactioned needs so this is a fresh ranking.
    stale = (
        await session.scalars(
            select(TrainingNeed).where(
                TrainingNeed.plant_id == plant.id,
                TrainingNeed.resolved.is_(False),
                TrainingNeed.action_id.is_(None),
            )
        )
    ).all()
    for row in stale:
        await session.delete(row)
    await session.flush()

    needs: list[TrainingNeed] = []

    # --- 1. 'No Operator' downtime — a gap that has already cost money ------
    rows = (
        await session.execute(
            select(
                DowntimeEvent.station_id,
                Station.code,
                func.count(DowntimeEvent.id),
                func.coalesce(func.sum(DowntimeEvent.cost_total), 0),
                func.coalesce(func.sum(DowntimeEvent.duration_minutes), 0),
            )
            .join(Station, Station.id == DowntimeEvent.station_id)
            .join(ReasonCode, ReasonCode.id == DowntimeEvent.reason_code_id)
            .where(
                DowntimeEvent.plant_id == plant.id,
                DowntimeEvent.business_date >= since,
                ReasonCode.code == NO_OPERATOR_CODE,
            )
            .group_by(DowntimeEvent.station_id, Station.code)
            .having(func.count(DowntimeEvent.id) >= MIN_GAP_EVENTS)
            .order_by(func.sum(DowntimeEvent.cost_total).desc())
        )
    ).all()

    for station_id, code, count, cost, minutes in rows:
        annual = int(int(cost or 0) * (365 / window_days))
        needs.append(
            TrainingNeed(
                plant_id=plant.id,
                station_id=station_id,
                kind="gap",
                annual_loss_paise=annual,
                evidence={
                    "events": int(count),
                    "observed_paise": int(cost or 0),
                    "minutes": float(minutes or 0),
                    "window_days": window_days,
                },
                recommendation=(
                    f"{code} lost {format_inr(int(cost or 0))} to 'No Operator' across "
                    f"{int(count)} events in {window_days} days. Certify at least one "
                    f"more operator on {code}."
                ),
            )
        )

    # --- 2. Thin coverage — a gap that has not cost anything yet ------------
    #
    # Nothing has been lost here, so no annual loss is claimed: annual_loss_paise
    # stays at zero. What IS knowable is the cost of the day it happens — one
    # shift with the station stopped — and that comes from the same kernel
    # function that costs every downtime event, so it cannot disagree with them.
    #
    # An annual figure would need an absence rate, and inventing one would put a
    # number on screen the plant's own data never produced. An earlier version
    # annualised *all* of the station's downtime and multiplied it by 1.5; for
    # CNC-3 that claimed ₹1.6 lakh a year, which is exactly the kind of inflated
    # number §8.2 says discredits every other number in the product.
    grid = await matrix(session, plant=plant)
    shift_minutes = await _typical_shift_minutes(session, plant)

    already = {str(n.station_id) for n in needs}
    for station_row in grid["stations"]:
        if not station_row["at_risk"] or station_row["id"] in already:
            continue

        station = await session.get(Station, uuid.UUID(station_row["id"]))
        if station is None:
            continue
        per_day, basis = await _absence_day_exposure(
            session, plant=plant, station=station, shift_minutes=shift_minutes
        )
        certified = station_row["coverage"]

        needs.append(
            TrainingNeed(
                plant_id=plant.id,
                station_id=station.id,
                kind="coverage",
                annual_loss_paise=0,
                evidence={
                    "certified_operators": certified,
                    "minimum": MIN_COVERAGE,
                    "is_bottleneck": station.is_bottleneck,
                    "exposure_per_day_paise": per_day,
                    "basis": basis,
                },
                recommendation=(
                    f"{station.code} has {certified} certified operator"
                    f"{'' if certified == 1 else 's'}. "
                    + (
                        "Nobody is certified, so it cannot run at all without supervision. "
                        if certified == 0
                        else "One absence stops it"
                        + (
                            " — and it is a bottleneck, so it stops the line. "
                            if station.is_bottleneck
                            else ". "
                        )
                    )
                    + f"Each shift it stands idle costs about {format_inr(per_day)}. "
                    "Train a second."
                ),
            )
        )

    session.add_all(needs)
    await session.flush()
    needs.sort(key=rank_key)
    return needs


def rank_key(need: TrainingNeed) -> tuple:
    """Order training needs without mixing units.

    Gaps carry a measured annual loss; coverage risks carry a cost per absence
    day. Those are different quantities, so they are never sorted against each
    other — gaps come first, ranked by what they have already cost, then
    coverage risks, with bottlenecks leading (a bottleneck stopping stops the
    line, §8.2) and the rest by their per-day exposure.
    """
    evidence = need.evidence or {}
    if need.kind == "gap":
        return (0, 0, -need.annual_loss_paise)
    return (
        1,
        0 if evidence.get("is_bottleneck") else 1,
        -int(evidence.get("exposure_per_day_paise", 0)),
    )


async def _typical_shift_minutes(session: AsyncSession, plant: Plant) -> int:
    """Planned minutes of the plant's first shift — one day's worth of stoppage."""
    shift = await session.scalar(
        select(Shift)
        .where(Shift.plant_id == plant.id, Shift.is_active.is_(True))
        .order_by(Shift.display_order)
        .limit(1)
    )
    return planned_minutes(shift) if shift else 8 * 60


async def _absence_day_exposure(
    session: AsyncSession, *, plant: Plant, station: Station, shift_minutes: int
) -> tuple[int, dict]:
    """What one shift with this station stopped costs, via the kernel.

    Uses the part the station most recently ran, so lost contribution is priced
    at a real margin and cycle time, and the station's own bottleneck factor —
    which is what keeps a buffered machine from claiming parts the line never
    actually missed.
    """
    part_id = await session.scalar(
        select(JobOrder.part_id)
        .join(JobSession, JobSession.job_order_id == JobOrder.id)
        .where(JobSession.plant_id == plant.id, JobSession.station_id == station.id)
        .order_by(JobSession.started_at.desc())
        .limit(1)
    )
    part = await session.get(Part, part_id) if part_id else None
    operation = None
    if part is not None:
        operation = await session.scalar(
            select(RoutingOperation)
            .where(
                RoutingOperation.part_id == part.id,
                RoutingOperation.cost_family_id == station.cost_family_id,
            )
            .limit(1)
        )

    snapshot, _ = await resolve_snapshot(
        session,
        plant=plant,
        station=station,
        part=part,
        on_date=date.today(),
        routing_operation=operation,
    )
    cost = downtime_event_cost(shift_minutes, snapshot)
    return cost.total, {
        "shift_minutes": shift_minutes,
        "part": part.part_number if part else None,
        "time_cost_paise": cost.machine_cost + cost.labour_cost + cost.overhead_cost,
        "lost_contribution_paise": cost.lost_contribution,
        "bottleneck_factor": float(snapshot.bottleneck_factor),
    }


async def raise_action(
    session: AsyncSession,
    *,
    plant: Plant,
    need: TrainingNeed,
    owner_name: str | None = None,
) -> Action:
    """Turn a training need into a tracked Action.

    Same engine, same PDCA, same closure prompt into Factory Memory — §3 Layer
    5's build rule again. A skill gap is not a separate kind of work.
    """
    station = await session.get(Station, need.station_id)
    reference = await action_service._next_reference(session, plant.id)
    # Measured on the loss the gap causes — 'No Operator' stops on this station —
    # so the fix earns Recovered Profit like any other Action (§3 Layer 5).
    no_operator = await session.scalar(
        select(ReasonCode).where(
            ReasonCode.plant_id == plant.id, ReasonCode.code == NO_OPERATOR_CODE
        )
    )

    action = Action(
        plant_id=plant.id,
        reference=reference,
        title=f"Train a second operator on {station.code if station else 'station'}",
        description=need.recommendation,
        trigger=ActionTrigger.SKILL_GAP,
        trigger_rationale=(
            f"Skill Matrix gap: {format_inr(need.annual_loss_paise)}/year lost to 'No Operator'"
            if need.kind == "gap"
            else "Skill Matrix coverage risk: "
            f"{format_inr(int((need.evidence or {}).get('exposure_per_day_paise', 0)))} "
            "per shift the station stands idle"
        ),
        state=PdcaState.OPEN,
        station_id=need.station_id,
        reason_code_id=no_operator.id if no_operator else None,
        linked_cost=need.annual_loss_paise,
        owner_name=owner_name or "Production Head",
        due_date=date.today() + timedelta(days=30),
        surfaced_in_meeting_on=date.today(),
    )
    session.add(action)
    await session.flush()
    action.baseline_weekly_loss = await action_service.compute_baseline(
        session,
        plant_id=plant.id,
        station_id=action.station_id,
        reason_code_id=action.reason_code_id,
        as_of=date.today(),
    )
    action.baseline_frozen_at = datetime.now(UTC)

    need.action_id = action.id
    await session.flush()
    return action
