"""The cost engine — §8.1, implemented exactly once.

    "Every formula above must be implemented once, in one place, and every
     screen/report/Copilot answer must draw from that single implementation.
     Two screens showing two different OEE numbers for the same shift is a
     credibility-ending bug."  — spec §8.1

Nothing in this module touches I/O. Callers resolve rates, cycle times and
bottleneck factors, then pass them in.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal

from .money import D, Paise, floor_int, round_paise
from .types import (
    CostRates,
    CostSnapshot,
    DowntimeCost,
    DowntimeSlice,
    LossLine,
    RankedLosses,
    Recoverability,
    ScrapCost,
)

# ---------------------------------------------------------------------------
# Cost rate derivation (§8.1 "Cost engine" table)
# ---------------------------------------------------------------------------


def blended_machine_rate(
    *,
    monthly_depreciation: Paise,
    monthly_power: Paise,
    monthly_operator_wages: Paise,
    monthly_maintenance: Paise,
    monthly_overhead_share: Paise,
    monthly_available_hours: Decimal,
) -> Paise:
    """Basic mode: one blended hourly rate per machine, 15 minutes with the owner.

    §4.2: a CNC at ₹35L over 10 years (~₹29K/month) + ₹18K power + ₹28K operator
    + ₹10K maintenance + ₹40K overhead share ~= ₹1.25L/month over 400 hours
    ~= ₹310/hr.
    """
    if monthly_available_hours <= 0:
        raise ValueError("monthly_available_hours must be positive")
    total = (
        monthly_depreciation
        + monthly_power
        + monthly_operator_wages
        + monthly_maintenance
        + monthly_overhead_share
    )
    return round_paise(D(total) / D(monthly_available_hours))


def machine_cost_rate(
    *,
    depreciation: Paise,
    power: Paise,
    tooling: Paise,
    maintenance: Paise,
    available_hours: Decimal,
) -> Paise:
    """Standard mode MCR — per machine, excludes labour and overhead."""
    if available_hours <= 0:
        raise ValueError("available_hours must be positive")
    return round_paise(D(depreciation + power + tooling + maintenance) / D(available_hours))


def labour_cost_rate(
    *, operator_wages: Paise, supervisor_share: Paise, worked_hours: Decimal
) -> Paise:
    """Standard mode LCR — per line/section."""
    if worked_hours <= 0:
        raise ValueError("worked_hours must be positive")
    return round_paise(D(operator_wages + supervisor_share) / D(worked_hours))


def overhead_rate(
    *, total_factory_overhead: Paise, total_available_machine_hours: Decimal
) -> Paise:
    """Standard mode OHR — one rate plant-wide."""
    if total_available_machine_hours <= 0:
        raise ValueError("total_available_machine_hours must be positive")
    return round_paise(D(total_factory_overhead) / D(total_available_machine_hours))


def contribution_margin(
    *, selling_price: Paise, direct_material: Paise, variable_outwork: Paise = 0
) -> Paise:
    """CM per part. §4.1's worked example: ₹110 - ₹72 = ₹38."""
    return selling_price - direct_material - variable_outwork


# ---------------------------------------------------------------------------
# Downtime event cost — the number the whole product hangs on
# ---------------------------------------------------------------------------


def downtime_event_cost(
    duration_minutes: Decimal | int | float | str,
    snapshot: CostSnapshot,
) -> DowntimeCost:
    """``(D/60) x (MCR + LCR + OHR) + ⌊D/CT⌋ x CM x BF``  (§8.1)

    Runs live as the cost ticker on a stopped machine, and again at event close.
    The same function serves both, so the ticker never disagrees with the report.

    ``⌊ ⌋`` is an integer floor — the spec is explicit that it rounds down: a
    machine down for 47 minutes on a 2.4 min cycle lost 19 whole parts, not 19.58.

    The bottleneck factor (§8.2) scales *only* the lost-contribution term.
    Machine, labour and overhead always apply in full: an idle machine and an
    idle operator cost money whether or not despatch drops.
    """
    minutes = D(duration_minutes)
    if minutes < 0:
        raise ValueError("duration cannot be negative")

    rates = snapshot.rates
    hours = minutes / D(60)

    # Each component rounds once, half-up, so the displayed breakdown sums to
    # the displayed total (ARCHITECTURE.md §2.2).
    machine = round_paise(hours * D(rates.machine_rate))
    labour = round_paise(hours * D(rates.labour_rate))
    overhead = round_paise(hours * D(rates.overhead_rate))

    parts_not_made = 0
    lost_contribution = 0
    cycle = snapshot.part.cycle_time_min
    if cycle and cycle > 0:
        parts_not_made = floor_int(minutes / D(cycle))
        lost_contribution = round_paise(
            D(parts_not_made)
            * D(snapshot.part.contribution_margin)
            * D(snapshot.bottleneck_factor)
        )

    return DowntimeCost(
        machine_cost=machine,
        labour_cost=labour,
        overhead_cost=overhead,
        lost_contribution=lost_contribution,
        total=machine + labour + overhead + lost_contribution,
        parts_not_made=parts_not_made,
        duration_minutes=minutes,
    )


def live_ticker_rate_per_minute(snapshot: CostSnapshot) -> Decimal:
    """Paise per minute for the client-side ticker between server pushes.

    The authoritative figure always comes from :func:`downtime_event_cost`;
    this only smooths the animation between WebSocket frames. Note it excludes
    the stepwise lost-contribution term, which jumps one part at a time — the
    UI adds those steps discretely so the number it shows stays truthful.
    """
    return D(snapshot.rates.burden_rate) / D(60)


# ---------------------------------------------------------------------------
# Scrap and rework (§8.1)
# ---------------------------------------------------------------------------


def scrap_cost(
    *,
    quantity: int,
    snapshot: CostSnapshot,
    completed_operations: Sequence[tuple[Decimal, CostRates]] = (),
) -> ScrapCost:
    """``material + Σ(processing cost of completed operations) + CM x BF``, per part.

    ``processing_cost_per_op = (CT_op / 60) x (MCR + LCR + OHR)`` — only for
    operations the part actually passed through. A part scrapped at Op10 has not
    consumed Op20's machine time.
    """
    if quantity < 0:
        raise ValueError("quantity cannot be negative")

    material = round_paise(D(snapshot.part.material_cost) * quantity)

    per_part_processing = D(0)
    for op_cycle_min, op_rates in completed_operations:
        per_part_processing += (D(op_cycle_min) / D(60)) * D(op_rates.burden_rate)
    processing = round_paise(per_part_processing * quantity)

    lost = round_paise(
        D(snapshot.part.contribution_margin) * D(snapshot.bottleneck_factor) * quantity
    )

    return ScrapCost(
        material_cost=material,
        processing_cost=processing,
        lost_contribution=lost,
        total=material + processing + lost,
        quantity=quantity,
    )


def rework_cost(
    *, rework_minutes: Decimal | int | float | str, station_rates: CostRates
) -> Paise:
    """``(rework time / 60) x (MCR + LCR + OHR)`` of the *rework station*.

    Reworked parts are never also counted as scrap — enforced as a database
    constraint, not a convention (§8.1).
    """
    minutes = D(rework_minutes)
    if minutes < 0:
        raise ValueError("rework time cannot be negative")
    return round_paise((minutes / D(60)) * D(station_rates.burden_rate))


# ---------------------------------------------------------------------------
# Aggregation — the ranked loss table (§3 Layer 2)
# ---------------------------------------------------------------------------


def rank_losses(lines: Iterable[LossLine]) -> RankedLosses:
    """Rank by rupees, split Recoverable / Structural.

    Not by frequency, not by minutes — by money. And the two classes are
    reported separately, never mixed into one total (§8.1): telling an owner
    they lost ₹9,380 when ₹2,380 of it was planned maintenance is how a number
    stops being trusted.
    """
    recoverable: list[LossLine] = []
    structural: list[LossLine] = []
    for line in lines:
        bucket = (
            recoverable if line.recoverability is Recoverability.RECOVERABLE else structural
        )
        bucket.append(line)

    recoverable.sort(key=lambda line: line.cost, reverse=True)
    structural.sort(key=lambda line: line.cost, reverse=True)

    return RankedLosses(
        recoverable=tuple(recoverable),
        structural=tuple(structural),
        recoverable_total=sum(line.cost for line in recoverable),
        structural_total=sum(line.cost for line in structural),
        total_minutes=sum((line.minutes for line in (*recoverable, *structural)), D(0)),
    )


def recoverable_loss(events: Iterable[DowntimeSlice]) -> Paise:
    """``Σ event costs where the reason code is tagged Recoverable`` (§8.1)."""
    return sum(
        event.cost for event in events if event.recoverability is Recoverability.RECOVERABLE
    )


def structural_loss(events: Iterable[DowntimeSlice]) -> Paise:
    return sum(
        event.cost for event in events if event.recoverability is Recoverability.STRUCTURAL
    )


def annualised_projection(four_week_recoverable_loss: Paise) -> Paise:
    """``current 4-week recoverable run-rate x 13`` (§8.1).

    Always labelled as a projection on screen, never as fact.
    """
    return four_week_recoverable_loss * 13
