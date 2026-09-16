"""OEE, production, quality and maintenance KPIs — §8.2, implemented once.

Two iron rules from the spec govern this module:

1. Every KPI derives from data already being entered. A KPI needing new daily
   entry is rejected — the 15-minute entry budget is sacred (§4.3).
2. No KPI zoo. Roughly a dozen KPIs across all roles at launch.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from .money import D
from .types import DowntimeSlice, OeeResult, OeeTreatment

ZERO = D(0)
ONE = D(1)
_RATIO = D("0.000001")


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator <= 0:
        return ZERO
    return (numerator / denominator).quantize(_RATIO)


# ---------------------------------------------------------------------------
# OEE = Availability x Performance x Quality
# ---------------------------------------------------------------------------


def planned_production_time(
    *, shift_duration_minutes: Decimal, planned_downtime_minutes: Decimal
) -> Decimal:
    """``shift duration − planned downtime (breaks, planned maintenance, no-order)``

    "Planned" here is the ``oee_treatment`` attribute of the reason code, *not*
    its Recoverable/Structural tag. A power cut is Structural but unplanned, and
    must reduce Availability (ARCHITECTURE.md §2.3).
    """
    return max(ZERO, D(shift_duration_minutes) - D(planned_downtime_minutes))


def availability(
    *, planned_minutes: Decimal, unplanned_downtime_minutes: Decimal
) -> Decimal:
    """``(planned time − unplanned downtime) / planned time``"""
    planned = D(planned_minutes)
    return _ratio(max(ZERO, planned - D(unplanned_downtime_minutes)), planned)


def performance(
    *, cycle_time_min: Decimal, total_count: int, actual_run_minutes: Decimal
) -> tuple[Decimal, Decimal]:
    """``(CT x total count) / actual run time``

    Returns ``(capped, uncapped)``. A value above 1.0 is not a better plant — it
    is a wrong cycle time or a padded count, so the raw value is preserved and
    the caller raises a data-quality flag, while screens show the capped figure.
    """
    raw = _ratio(D(cycle_time_min) * D(total_count), D(actual_run_minutes))
    return (min(raw, ONE), raw)


def quality_rate(*, good_count: int, total_count: int) -> Decimal:
    """``good count / total count``.

    Reworked-then-good parts count as good here — but not in First Pass Yield.
    """
    return _ratio(D(good_count), D(total_count))


def compute_oee(
    *,
    shift_duration_minutes: Decimal | int,
    downtime: Sequence[DowntimeSlice],
    cycle_time_min: Decimal,
    total_count: int,
    good_count: int,
) -> OeeResult:
    """The single OEE implementation. Every screen and report calls this one."""
    shift_minutes = D(shift_duration_minutes)

    planned_dt = sum(
        (e.minutes for e in downtime if e.oee_treatment is OeeTreatment.PLANNED),
        ZERO,
    )
    unplanned_dt = sum(
        (e.minutes for e in downtime if e.oee_treatment is OeeTreatment.UNPLANNED),
        ZERO,
    )

    planned = planned_production_time(
        shift_duration_minutes=shift_minutes, planned_downtime_minutes=planned_dt
    )
    run_minutes = max(ZERO, planned - unplanned_dt)

    a = availability(planned_minutes=planned, unplanned_downtime_minutes=unplanned_dt)
    p, p_raw = performance(
        cycle_time_min=cycle_time_min,
        total_count=total_count,
        actual_run_minutes=run_minutes,
    )
    q = quality_rate(good_count=good_count, total_count=total_count)

    flags: list[str] = []
    if p_raw > ONE:
        flags.append("PERFORMANCE_OVER_100")
    if total_count and good_count > total_count:
        flags.append("GOOD_EXCEEDS_TOTAL")
    if planned <= 0:
        flags.append("NO_PLANNED_TIME")

    return OeeResult(
        availability=a,
        performance=p,
        quality=q,
        oee=(a * p * q).quantize(_RATIO),
        planned_minutes=planned,
        unplanned_downtime_minutes=unplanned_dt,
        planned_downtime_minutes=planned_dt,
        run_minutes=run_minutes,
        total_count=total_count,
        good_count=good_count,
        performance_uncapped=p_raw,
        flags=tuple(flags),
    )


@dataclass(frozen=True, slots=True)
class StationRun:
    """One station's share of a period: what OEE needs, and nothing else."""

    station: str
    shift_minutes: Decimal  # the shift length less breaks, for this station
    downtime: Sequence[DowntimeSlice]
    cycle_time_min: Decimal  # count-weighted if it ran more than one part
    total_count: int
    good_count: int


def aggregate_oee(runs: Sequence[StationRun]) -> tuple[OeeResult, list[OeeResult]]:
    """OEE for a line, a shift or a plant — built from per-station OEE.

    Averaging per-station percentages would be wrong (a machine that barely ran
    would count as much as the bottleneck), and so is computing one OEE from
    plant totals against a single shift's minutes: fourteen stations' downtime
    and output measured against one station's time drove Availability to 69%
    and Quality to 60% on a shift where both were near 98%.

    So the terms are aggregated by their own numerators and denominators:

        A = Σ run time / Σ planned time
        P = Σ (CT × count) / Σ run time      — stations with a known CT only
        Q = Σ good / Σ total

    Each station's own figure still comes from :func:`compute_oee`, the single
    implementation, so a station's tile and the line it belongs to cannot
    disagree.
    """
    per_station = [
        compute_oee(
            shift_duration_minutes=run.shift_minutes,
            downtime=run.downtime,
            cycle_time_min=run.cycle_time_min,
            total_count=run.total_count,
            good_count=run.good_count,
        )
        for run in runs
    ]

    planned = sum((r.planned_minutes for r in per_station), ZERO)
    unplanned = sum((r.unplanned_downtime_minutes for r in per_station), ZERO)
    planned_dt = sum((r.planned_downtime_minutes for r in per_station), ZERO)
    run_minutes = sum((r.run_minutes for r in per_station), ZERO)
    total = sum(run.total_count for run in runs)
    good = sum(run.good_count for run in runs)

    # Performance only over stations whose cycle time is known. A station with
    # no CT cannot say how fast it should have been, and counting its run time
    # in the denominator would drag the plant's Performance down for a reason
    # that has nothing to do with speed.
    timed = [
        (run, result)
        for run, result in zip(runs, per_station, strict=True)
        if run.cycle_time_min > 0
    ]
    ideal = sum((D(run.cycle_time_min) * D(run.total_count) for run, _ in timed), ZERO)
    timed_run = sum((result.run_minutes for _, result in timed), ZERO)

    a = availability(planned_minutes=planned, unplanned_downtime_minutes=unplanned)
    p_raw = _ratio(ideal, timed_run)
    p = min(p_raw, ONE)
    q = quality_rate(good_count=good, total_count=total)

    flags: list[str] = []
    over = [
        run.station
        for run, result in zip(runs, per_station, strict=True)
        if "PERFORMANCE_OVER_100" in result.flags
    ]
    if over:
        # Named, because the fix is a cycle time on a specific machine.
        flags.append(f"PERFORMANCE_OVER_100:{','.join(over)}")
    if len(timed) < len(runs):
        flags.append("PERFORMANCE_PARTIAL")
    if planned <= 0:
        flags.append("NO_PLANNED_TIME")

    aggregate = OeeResult(
        availability=a,
        performance=p,
        quality=q,
        oee=(a * p * q).quantize(_RATIO),
        planned_minutes=planned,
        unplanned_downtime_minutes=unplanned,
        planned_downtime_minutes=planned_dt,
        run_minutes=run_minutes,
        total_count=total,
        good_count=good,
        performance_uncapped=p_raw,
        flags=tuple(flags),
    )
    return aggregate, per_station


# ---------------------------------------------------------------------------
# Production KPIs
# ---------------------------------------------------------------------------


def productivity(*, good_parts: int, man_hours: Decimal) -> Decimal:
    """``good parts / man-hours``"""
    return _ratio(D(good_parts), D(man_hours))


def downtime_percent(
    *, total_downtime_minutes: Decimal, planned_minutes: Decimal
) -> Decimal:
    """``total downtime / planned time`` — reported split R/S."""
    return _ratio(D(total_downtime_minutes), D(planned_minutes))


def schedule_adherence(*, actual_output: int, planned_output: int) -> Decimal:
    """``actual output / planned output`` per job order."""
    return _ratio(D(actual_output), D(planned_output))


# ---------------------------------------------------------------------------
# Quality KPIs
# ---------------------------------------------------------------------------


def ppm(*, rejected_parts: int, total_inspected: int) -> Decimal:
    """``(rejected / total inspected) x 1,000,000`` — per part, machine, customer."""
    if total_inspected <= 0:
        return ZERO
    return ((D(rejected_parts) / D(total_inspected)) * D(1_000_000)).quantize(D("0.1"))


def first_pass_yield(*, good_first_time: int, parts_started: int) -> Decimal:
    """``parts good first time / parts started``.

    Reworked-then-good parts do NOT count as first-pass (§8.1). This is the
    difference between FPY and the Quality term of OEE.
    """
    return _ratio(D(good_first_time), D(parts_started))


# ---------------------------------------------------------------------------
# Maintenance KPIs
# ---------------------------------------------------------------------------


def mtbf(*, total_run_minutes: Decimal, breakdown_count: int) -> Decimal:
    """``total run time / number of breakdowns``, in minutes.

    A "breakdown" is any event whose reason code carries ``is_breakdown`` — a
    code attribute, never a string match on the label 'Machine Breakdown'.
    """
    if breakdown_count <= 0:
        return ZERO
    return (D(total_run_minutes) / D(breakdown_count)).quantize(D("0.01"))


def mttr(*, total_breakdown_minutes: Decimal, breakdown_count: int) -> Decimal:
    """``total breakdown repair time / number of breakdowns``, in minutes."""
    if breakdown_count <= 0:
        return ZERO
    return (D(total_breakdown_minutes) / D(breakdown_count)).quantize(D("0.01"))


def count_breakdowns(downtime: Iterable[DowntimeSlice]) -> int:
    return sum(1 for e in downtime if e.is_breakdown)


def breakdown_minutes(downtime: Iterable[DowntimeSlice]) -> Decimal:
    return sum((e.minutes for e in downtime if e.is_breakdown), ZERO)


def throughput_minutes(*, first_operation_start, last_operation_completion) -> Decimal:
    """``last-operation completion − first-operation start``, per lot (§8.2).

    Wall-clock, as the plant experiences it — nothing is excluded. A lot sitting
    in a WIP buffer over a weekend really did take that long to get through.
    """
    if first_operation_start is None or last_operation_completion is None:
        return ZERO
    delta = last_operation_completion - first_operation_start
    return (D(delta.total_seconds()) / D(60)).quantize(D("0.01"))


# ---------------------------------------------------------------------------
# Cross-verification (§6, §16.5)
# ---------------------------------------------------------------------------


def count_reconciliation(
    *,
    supervisor_count: int,
    inspector_good: int,
    inspector_rejected: int,
    tolerance_parts: int = 5,
    tolerance_fraction: Decimal = D("0.02"),
) -> tuple[bool, int, Decimal]:
    """Does the inspector's independent count reconcile with the supervisor's?

    Two people would have to collude to fake the numbers — that is the point.
    §6's worked example: 374 counted vs 362 + 9 = 371, a gap of 3, passes
    silently; a gap of 25 alerts the owner.

    Returns ``(flagged, gap, gap_fraction)``.
    """
    inspected = inspector_good + inspector_rejected
    gap = abs(supervisor_count - inspected)
    fraction = _ratio(D(gap), D(supervisor_count)) if supervisor_count else ZERO
    threshold = max(D(tolerance_parts), D(supervisor_count) * tolerance_fraction)
    return (D(gap) > threshold, gap, fraction)
