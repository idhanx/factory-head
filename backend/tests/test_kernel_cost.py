"""Golden tests for the cost engine, anchored on the spec's own worked examples.

A diff in any of these is a release blocker. This is the mechanism that stops
two screens from ever disagreeing about the same shift.
"""

from decimal import Decimal

import pytest

from app.kernel import (
    CostingMode,
    CostRates,
    CostSnapshot,
    LossLine,
    PartEconomics,
    Recoverability,
    downtime_event_cost,
    rank_losses,
    rework_cost,
    rupees,
    scrap_cost,
)

# §3 Layer 2 / §8.1 worked example: CNC-3 running Part AK-447, 47-minute tool break.
CNC3_RATES = CostRates(
    machine_rate=rupees(420), labour_rate=rupees(180), overhead_rate=rupees(210)
)
AK447 = PartEconomics(
    cycle_time_min=Decimal("2.4"),
    contribution_margin=rupees(38),  # §4.1: ₹110 − ₹72 = ₹38
    material_cost=rupees(72),
)
CNC3_SNAPSHOT = CostSnapshot(
    rates=CNC3_RATES,
    part=AK447,
    bottleneck_factor=Decimal("1.0"),
    costing_mode=CostingMode.BASIC,
)


def test_spec_worked_example_time_components():
    """The three time-based components reproduce §3 Layer 2 exactly."""
    result = downtime_event_cost(47, CNC3_SNAPSHOT)

    assert result.machine_cost == rupees(329)  # 47/60 × ₹420 = exactly ₹329
    assert result.labour_cost == rupees(141)  # 47/60 × ₹180 = exactly ₹141
    # 47/60 × ₹210 = ₹164.50 exactly. The spec prints ₹165 because its tables
    # display whole rupees; the ledger keeps the half-rupee. Rounding to the
    # rupee here would make the four components stop summing to the total.
    assert result.overhead_cost == rupees("164.50")


def test_parts_not_made_is_an_integer_floor():
    """⌊47 / 2.4⌋ = 19 whole parts. The spec is explicit that ⌊ ⌋ rounds down."""
    result = downtime_event_cost(47, CNC3_SNAPSHOT)
    assert result.parts_not_made == 19
    assert Decimal(47) / Decimal("2.4") > 19  # 19.58 — proves the floor bit


def test_components_sum_exactly_to_the_total():
    """A screen that lists the breakdown must always add up to the total shown."""
    result = downtime_event_cost(47, CNC3_SNAPSHOT)
    assert (
        result.machine_cost
        + result.labour_cost
        + result.overhead_cost
        + result.lost_contribution
        == result.total
    )


def test_spec_total_discrepancy_is_recorded_not_papered_over():
    """§3 Layer 2 says this event cost ₹3,290. §8.1's formula says ₹1,356.

    The spec's own arithmetic contradicts itself: it describes the lost
    contribution as "(47 ÷ 2.4) = 19 parts × ₹38 margin" and then prints
    ₹2,655 — but 19 × ₹38 = ₹722. For ₹2,655 to hold, CM would have to be
    ₹139.74, while ₹38 is stated independently twice (§4.1 and the T2 example).

    The formula is the contract, so the kernel implements §8.1 and this test
    pins the discrepancy in place. See ARCHITECTURE.md Part 12 decision 1 —
    do NOT "fix" the kernel to reproduce ₹3,290.
    """
    result = downtime_event_cost(47, CNC3_SNAPSHOT)

    assert result.lost_contribution == rupees(722)  # 19 × ₹38, per the formula
    assert result.total == rupees("1356.50")

    spec_narrative_total = rupees(3290)
    assert result.total != spec_narrative_total, (
        "If this ever passes, the spec owner has resolved the ₹38-vs-₹2,655 "
        "contradiction and the fixture needs updating deliberately."
    )


def test_bottleneck_factor_scales_only_lost_contribution():
    """§8.2 — an idle machine and an idle operator cost money whether or not
    despatch drops. Only the 'parts never made' claim is discounted."""
    buffered = CostSnapshot(
        rates=CNC3_RATES,
        part=AK447,
        bottleneck_factor=Decimal("0.5"),
        costing_mode=CostingMode.BASIC,
    )
    full = downtime_event_cost(47, CNC3_SNAPSHOT)
    half = downtime_event_cost(47, buffered)

    assert half.machine_cost == full.machine_cost
    assert half.labour_cost == full.labour_cost
    assert half.overhead_cost == full.overhead_cost
    assert half.lost_contribution == full.lost_contribution // 2


def test_non_bottleneck_with_zero_factor_claims_no_lost_parts():
    """BF = 0: the downstream buffer absorbed it, despatch never dropped.

    One inflated number discredits every number — a plant manager who checks
    will find the claimed contribution never left the building."""
    absorbed = CostSnapshot(
        rates=CNC3_RATES,
        part=AK447,
        bottleneck_factor=Decimal("0"),
        costing_mode=CostingMode.BASIC,
    )
    result = downtime_event_cost(47, absorbed)
    assert result.lost_contribution == 0
    assert result.parts_not_made == 19  # still counted, just not claimed as money
    assert result.total == rupees("634.50")  # time terms only


def test_zero_duration_costs_nothing():
    result = downtime_event_cost(0, CNC3_SNAPSHOT)
    assert result.total == 0
    assert result.parts_not_made == 0


def test_missing_cycle_time_still_costs_the_time_terms():
    """§4.1 'don't-block' rule: no cycle time must never block onboarding."""
    unknown_ct = CostSnapshot(
        rates=CNC3_RATES,
        part=PartEconomics(cycle_time_min=Decimal(0), contribution_margin=rupees(38)),
        bottleneck_factor=Decimal("1.0"),
        costing_mode=CostingMode.BASIC,
    )
    result = downtime_event_cost(47, unknown_ct)
    assert result.lost_contribution == 0
    assert result.total == rupees("634.50")


def test_negative_duration_is_rejected():
    with pytest.raises(ValueError):
        downtime_event_cost(-5, CNC3_SNAPSHOT)


def test_scrap_costs_only_completed_operations():
    """A part scrapped at Op10 has not consumed Op20's machine time."""
    op10 = (Decimal("2.4"), CNC3_RATES)
    result = scrap_cost(quantity=9, snapshot=CNC3_SNAPSHOT, completed_operations=[op10])

    assert result.material_cost == rupees(72) * 9
    # (2.4/60) × ₹810 = ₹32.40/part × 9 = ₹291.60
    assert result.processing_cost == rupees("291.60")
    assert result.lost_contribution == rupees(38) * 9
    assert result.total == (
        result.material_cost + result.processing_cost + result.lost_contribution
    )


def test_rework_is_costed_at_the_rework_station_rate():
    bench = CostRates(
        machine_rate=rupees(60), labour_rate=rupees(180), overhead_rate=rupees(210)
    )
    assert rework_cost(rework_minutes=30, station_rates=bench) == rupees(225)


def test_recoverable_and_structural_are_never_summed_together():
    """§8.1 — telling an owner they lost ₹9,380 when ₹2,380 was planned
    maintenance is how a number stops being trusted."""
    ranked = rank_losses(
        [
            LossLine(
                "CNC-3",
                "Tool breakage",
                Decimal(47),
                rupees(3290),
                Recoverability.RECOVERABLE,
            ),
            LossLine(
                "Line 2",
                "Material wait",
                Decimal(31),
                rupees(2170),
                Recoverability.RECOVERABLE,
            ),
            LossLine(
                "VMC-1",
                "Minor adjustments",
                Decimal(22),
                rupees(1540),
                Recoverability.RECOVERABLE,
            ),
            LossLine(
                "All lines",
                "Planned maintenance",
                Decimal(34),
                rupees(2380),
                Recoverability.STRUCTURAL,
            ),
        ]
    )

    assert ranked.recoverable_total == rupees(7000)
    assert ranked.structural_total == rupees(2380)
    assert ranked.total_minutes == Decimal(134)  # matches the spec's table
    assert [line.station for line in ranked.recoverable] == ["CNC-3", "Line 2", "VMC-1"]


def test_losses_rank_by_money_not_by_minutes():
    """A short expensive stop outranks a long cheap one. That is the product."""
    ranked = rank_losses(
        [
            LossLine(
                "A", "long cheap", Decimal(120), rupees(400), Recoverability.RECOVERABLE
            ),
            LossLine(
                "B",
                "short expensive",
                Decimal(10),
                rupees(9000),
                Recoverability.RECOVERABLE,
            ),
        ]
    )
    assert [line.station for line in ranked.recoverable] == ["B", "A"]
