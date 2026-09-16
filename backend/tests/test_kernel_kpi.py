"""KPI tests — §8.2, including the OEE-treatment trap the spec leaves open."""

from datetime import datetime, timedelta
from decimal import Decimal

from app.kernel import (
    DowntimeSlice,
    OeeTreatment,
    Recoverability,
    compute_oee,
    count_reconciliation,
    first_pass_yield,
    mtbf,
    mttr,
    ppm,
    quality_rate,
    throughput_minutes,
)


def slice_(
    minutes, *, treatment, recoverability=Recoverability.RECOVERABLE, breakdown=False
):
    return DowntimeSlice(
        minutes=Decimal(minutes),
        recoverability=recoverability,
        oee_treatment=treatment,
        is_breakdown=breakdown,
    )


def test_oee_is_availability_times_performance_times_quality():
    """§8.2's own illustration: 85% × 90% × 97% = 74%."""
    result = compute_oee(
        shift_duration_minutes=480,
        downtime=[slice_(72, treatment=OeeTreatment.UNPLANNED)],
        cycle_time_min=Decimal("1.0"),
        total_count=367,
        good_count=356,
    )
    assert result.availability == Decimal("0.850000")
    assert round(result.oee, 2) == round(
        result.availability * result.performance * result.quality, 2
    )


def test_power_cut_is_structural_but_still_reduces_availability():
    """The trap: Recoverable/Structural and planned/unplanned are different axes.

    A power cut is Structural — the plant cannot recover the money — but it is
    absolutely unplanned. If S were mapped to 'planned', every power cut would
    silently *improve* the plant's OEE, and a plant manager would notice.
    """
    power_cut = slice_(
        60, treatment=OeeTreatment.UNPLANNED, recoverability=Recoverability.STRUCTURAL
    )
    planned_pm = slice_(
        60, treatment=OeeTreatment.PLANNED, recoverability=Recoverability.STRUCTURAL
    )

    with_cut = compute_oee(
        shift_duration_minutes=480,
        downtime=[power_cut],
        cycle_time_min=Decimal("1.0"),
        total_count=420,
        good_count=420,
    )
    with_pm = compute_oee(
        shift_duration_minutes=480,
        downtime=[planned_pm],
        cycle_time_min=Decimal("1.0"),
        total_count=420,
        good_count=420,
    )

    # Same class, same duration — opposite effect on Availability.
    assert with_cut.availability < Decimal("1.0")
    assert with_pm.availability == Decimal("1.000000")
    assert with_pm.planned_minutes == Decimal(420)


def test_performance_above_100_percent_is_capped_and_flagged():
    """A plant does not exceed its own cycle time. It has a bad CT or a padded
    count — surface the cap, keep the raw number, raise the flag."""
    result = compute_oee(
        shift_duration_minutes=480,
        downtime=[],
        cycle_time_min=Decimal("2.0"),
        total_count=400,  # implies 800 min of work inside a 480 min shift
        good_count=400,
    )
    assert result.performance == Decimal("1.000000")
    assert result.performance_uncapped > Decimal("1.0")
    assert "PERFORMANCE_OVER_100" in result.flags


def test_quality_counts_rework_as_good_but_fpy_does_not():
    """§8.1 — this is precisely the difference between Q and First Pass Yield."""
    assert quality_rate(good_count=380, total_count=400) == Decimal("0.950000")
    assert first_pass_yield(good_first_time=360, parts_started=400) == Decimal("0.900000")


def test_ppm_matches_the_automotive_convention():
    assert ppm(rejected_parts=9, total_inspected=371) == Decimal("24258.8")


def test_mtbf_and_mttr_use_breakdown_flagged_events_only():
    """'Breakdown' is a reason-code attribute, never a string match on a label."""
    assert mtbf(total_run_minutes=Decimal(2400), breakdown_count=4) == Decimal("600.00")
    assert mttr(total_breakdown_minutes=Decimal(188), breakdown_count=4) == Decimal("47.00")
    assert mtbf(total_run_minutes=Decimal(2400), breakdown_count=0) == Decimal(0)


def test_throughput_is_wall_clock_across_operations():
    """§3 — lot #4471 sat 2 days 6 hours getting through the routing. Nothing is
    excluded: the lot really was stuck in a WIP buffer over the weekend."""
    start = datetime(2026, 6, 14, 7, 0)
    end = start + timedelta(days=2, hours=6)
    assert throughput_minutes(
        first_operation_start=start, last_operation_completion=end
    ) == Decimal("3240.00")


def test_cross_verification_passes_a_small_gap_and_flags_a_large_one():
    """§6 — 374 counted vs 362 good + 9 rejected passes silently; 25 alerts."""
    passed, gap, _ = count_reconciliation(
        supervisor_count=374, inspector_good=362, inspector_rejected=9
    )
    assert not passed and gap == 3

    flagged, gap, _ = count_reconciliation(
        supervisor_count=396, inspector_good=362, inspector_rejected=9
    )
    assert flagged and gap == 25


def test_no_planned_time_degrades_safely_rather_than_dividing_by_zero():
    result = compute_oee(
        shift_duration_minutes=0,
        downtime=[],
        cycle_time_min=Decimal("2.4"),
        total_count=0,
        good_count=0,
    )
    assert result.oee == Decimal(0)
    assert "NO_PLANNED_TIME" in result.flags


# ---------------------------------------------------------------------------
# Aggregate OEE — a line, a shift, a plant
# ---------------------------------------------------------------------------

from app.kernel import StationRun, aggregate_oee  # noqa: E402


def run(station, *, downtime=(), ct="1.0", total=400, good=396, minutes=465):
    return StationRun(
        station=station,
        shift_minutes=Decimal(minutes),
        downtime=list(downtime),
        cycle_time_min=Decimal(ct),
        total_count=total,
        good_count=good,
    )


def test_many_stations_are_not_measured_against_one_stations_minutes():
    """Regression: fourteen stations' downtime and output against a single
    shift's minutes reported Availability 69% and Quality 60% on a shift where
    both were near 98%."""
    runs = [
        run(
            f"S{i}",
            downtime=[slice_(14, treatment=OeeTreatment.UNPLANNED)],
            total=440,
            good=436,
        )
        for i in range(14)
    ]
    plant, stations = aggregate_oee(runs)

    assert plant.planned_minutes == Decimal(465 * 14)
    assert (
        plant.availability == stations[0].availability
    )  # identical stations, identical plant
    assert plant.availability > Decimal("0.96")
    assert plant.quality > Decimal("0.99")
    assert "PERFORMANCE_OVER_100" not in ",".join(plant.flags)


def test_a_station_that_barely_ran_does_not_count_as_much_as_the_bottleneck():
    """Weighted by time, not averaged by head-count."""
    bottleneck = run(
        "CNC-3", minutes=465, downtime=[slice_(0, treatment=OeeTreatment.UNPLANNED)]
    )
    idle_most_of_shift = run(
        "VMC-3",
        minutes=465,
        downtime=[slice_(400, treatment=OeeTreatment.UNPLANNED)],
        total=60,
        good=60,
    )
    plant, (a, b) = aggregate_oee([bottleneck, idle_most_of_shift])

    simple_average = (a.availability + b.availability) / 2
    assert plant.availability != simple_average
    assert plant.availability == (Decimal(930 - 400) / Decimal(930)).quantize(
        Decimal("0.000001")
    )


def test_a_station_with_no_cycle_time_does_not_drag_performance_down():
    known = run("CNC-3", ct="1.0", total=450)
    unknown = run("Weld-1", ct="0", total=100)
    plant, _ = aggregate_oee([known, unknown])

    only_known, _ = aggregate_oee([known])
    assert plant.performance == only_known.performance
    assert "PERFORMANCE_PARTIAL" in plant.flags


def test_an_impossible_performance_names_the_station_to_fix():
    plant, _ = aggregate_oee([run("CNC-3", ct="2.0", total=400), run("VMC-1")])
    assert "PERFORMANCE_OVER_100:CNC-3" in plant.flags
