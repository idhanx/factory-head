"""Kaizen's money, calculated by Core (Plugin Spec §7).

    "Kaizen does not calculate savings. Core calculates savings; Kaizen
     classifies them and presents them."

THE INVARIANT (§7.1), which this module exists to protect:

    "For the same event, Core's loss screen and Kaizen's impact card must show
     the same rupee figure. If a supervisor can open the downtime log and the
     Kaizen card and see two different numbers for one tool breakage, every
     number in the platform becomes negotiable."

So there is no arithmetic here that Core does not already do. The three stages
of §7.1's call pattern map onto this file exactly:

  1. :func:`build_request`  — assemble the quantum from the interview answers
  2. :func:`core_values`    — hand it to Core's formula module and its rates
  3. :func:`price`          — run Kaizen's classification layer over the result

The one thing Kaizen adds is the hard / soft / intangible split, and that lives
in the calculation kernel (:mod:`app.kernel.kaizen`), not here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import kaizen_ai
from app.kernel import (
    D,
    ImpactStars,
    SavingSplit,
    SavingType,
    classify,
    cost_of_doing_nothing,
    floor_int,
    format_inr,
    payback_months,
    rework_cost,
    roi_percent,
    round_paise,
    scrap_cost,
)
from app.models import KaizenCategory, Part, Plant, RoutingOperation, Shift, Station
from app.services import costing as costing_service

#: One cubic foot per minute, run for an hour, is 1.699 m³. Free air delivery
#: is quoted in CFM on every Indian compressor nameplate; the cost master holds
#: ₹/m³, so the conversion has to happen somewhere and it happens once, here.
CFM_TO_M3_PER_HOUR = D("1.699")

MONTHS_PER_YEAR = 12
SECONDS_PER_HOUR = D(3600)
MINUTES_PER_HOUR = D(60)

#: Decimal is immutable, so these are safe as shared dataclass defaults.
ZERO = D(0)
ONE = D(1)


# ---------------------------------------------------------------------------
# Stage 0 — the plant constants every annualised figure needs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlantRhythm:
    """Shifts a day and days a year, read from Core — never asked of anyone."""

    shifts_per_day: int
    working_days: int
    source: str


async def plant_rhythm(session: AsyncSession, *, plant: Plant) -> PlantRhythm:
    """How often this plant runs. Both numbers come from Core's T7."""
    shifts = int(
        await session.scalar(
            select(func.count(Shift.id)).where(
                Shift.plant_id == plant.id, Shift.is_active.is_(True)
            )
        )
        or 0
    )
    shifts_per_day = shifts or 1
    days = int(plant.working_days_per_year or 300)
    return PlantRhythm(
        shifts_per_day=shifts_per_day,
        working_days=days,
        source=(
            f"{shifts_per_day} shift{'' if shifts_per_day == 1 else 's'}/day "
            f"× {days} working days/yr (Core)"
        ),
    )


# ---------------------------------------------------------------------------
# Stage 1 — the savings request (§7.1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SavingRequest:
    """What Kaizen asks Core to price.

    ``quantum`` is whatever the saving type is measured in — seconds, parts,
    kWh, m³ or rupees — and ``frequency``/``shifts``/``days`` annualise it.
    Keeping them separate (rather than pre-multiplying into one number) is what
    lets the impact card show its own working to an approver who doubts it.
    """

    saving_type: SavingType
    station_id: uuid.UUID | None = None
    part_id: uuid.UUID | None = None
    operation_id: uuid.UUID | None = None
    quantum: Decimal = ZERO
    frequency: Decimal = ZERO
    shifts: int = 1
    days: int = 300
    #: Extra per-type inputs the quantum alone cannot carry.
    extras: dict[str, Decimal] = field(default_factory=dict)
    #: Human-readable working, shown under the number.
    working: str = ""

    def as_dict(self) -> dict:
        return {
            "saving_type": self.saving_type.value,
            "quantum": str(self.quantum),
            "frequency": str(self.frequency),
            "shifts": self.shifts,
            "days": self.days,
            "extras": {k: str(v) for k, v in self.extras.items()},
            "working": self.working,
        }


def build_request(
    *,
    saving_type: SavingType,
    answers: dict[str, float],
    rhythm: PlantRhythm,
    station_id: uuid.UUID | None = None,
    part_id: uuid.UUID | None = None,
    operation_id: uuid.UUID | None = None,
) -> SavingRequest:
    """Turn four tapped answers into the quantum Core needs.

    One rule runs through every branch: *"people affected" never multiplies the
    money*. The operator reporting 12 trips a shift is reporting the total at
    that station, and multiplying it by the four people who share the toolbox
    would quadruple a real number into a fictional one. People affected drives
    the morale star and nothing else.
    """
    get = lambda key, default=0: D(answers.get(key, default))  # noqa: E731

    def yearly(per_shift: Decimal) -> Decimal:
        return per_shift * D(rhythm.shifts_per_day) * D(rhythm.working_days)

    common = {
        "saving_type": saving_type,
        "station_id": station_id,
        "part_id": part_id,
        "operation_id": operation_id,
        "shifts": rhythm.shifts_per_day,
        "days": rhythm.working_days,
    }

    if saving_type in (SavingType.MOTION, SavingType.TRANSPORT, SavingType.HANDLING):
        seconds, trips = get("seconds_per_trip"), get("trips_per_shift")
        hours = yearly(seconds * trips) / SECONDS_PER_HOUR
        return SavingRequest(
            **common,
            quantum=seconds,
            frequency=trips,
            extras={"hours_per_year": hours},
            working=(
                f"{seconds:g} sec × {trips:g} trips/shift × {rhythm.source} "
                f"= {hours.quantize(D('0.1'))} hours a year"
            ),
        )

    if saving_type is SavingType.WAITING:
        minutes, waits = get("minutes_per_wait"), get("waits_per_shift")
        hours = yearly(minutes * waits) / MINUTES_PER_HOUR
        return SavingRequest(
            **common,
            quantum=minutes,
            frequency=waits,
            extras={"hours_per_year": hours},
            working=(
                f"{minutes:g} min × {waits:g} waits/shift × {rhythm.source} "
                f"= {hours.quantize(D('0.1'))} hours a year"
            ),
        )

    if saving_type is SavingType.CYCLE_TIME:
        seconds, cycles = get("seconds_saved"), get("cycles_per_shift")
        total = yearly(seconds * cycles)
        return SavingRequest(
            **common,
            quantum=seconds,
            frequency=cycles,
            extras={"seconds_per_year": total},
            working=(
                f"{seconds:g} sec/part × {cycles:g} parts/shift × {rhythm.source} "
                f"= {(total / SECONDS_PER_HOUR).quantize(D('0.1'))} machine-hours a year"
            ),
        )

    if saving_type is SavingType.CHANGEOVER:
        minutes, per_month = get("minutes_saved"), get("changeovers_per_month")
        total = minutes * per_month * D(MONTHS_PER_YEAR) * MINUTES_PER_HOUR
        return SavingRequest(
            **common,
            quantum=minutes,
            frequency=per_month,
            extras={"seconds_per_year": total},
            working=(
                f"{minutes:g} min × {per_month:g} changeovers/month × 12 "
                f"= {(total / SECONDS_PER_HOUR).quantize(D('0.1'))} machine-hours a year"
            ),
        )

    if saving_type in (SavingType.SCRAP, SavingType.REWORK):
        per_month = get("parts_per_month")
        per_year = per_month * D(MONTHS_PER_YEAR)
        extras = {"parts_per_year": per_year}
        if saving_type is SavingType.REWORK:
            extras["rework_minutes"] = get("rework_minutes")
        return SavingRequest(
            **common,
            quantum=per_month,
            frequency=D(MONTHS_PER_YEAR),
            extras=extras,
            working=f"{per_month:g} parts/month × 12 = {per_year:g} parts a year",
        )

    if saving_type is SavingType.ENERGY:
        kw, hours = get("kw_rating"), get("idle_hours_per_day")
        kwh = kw * hours * D(rhythm.working_days)
        return SavingRequest(
            **common,
            quantum=kw,
            frequency=hours,
            extras={"kwh_per_year": kwh},
            working=(
                f"{kw:g} kW × {hours:g} hrs/day × {rhythm.working_days} days "
                f"= {kwh.quantize(D('1'))} kWh a year"
            ),
        )

    if saving_type is SavingType.COMPRESSED_AIR:
        cfm, hours = get("leak_cfm"), get("hours_per_day")
        m3 = cfm * CFM_TO_M3_PER_HOUR * hours * D(rhythm.working_days)
        return SavingRequest(
            **common,
            quantum=cfm,
            frequency=hours,
            extras={"m3_per_year": m3},
            working=(
                f"{cfm:g} CFM × {hours:g} hrs/day × {rhythm.working_days} days "
                f"= {m3.quantize(D('1'))} m³ a year"
            ),
        )

    if saving_type in (SavingType.TOOLING, SavingType.CONSUMABLE):
        count, unit_cost = get("items_per_month"), get("item_cost")
        reduction = get("reduction_percent", 50) / D(100)
        avoided = count * D(MONTHS_PER_YEAR) * reduction
        return SavingRequest(
            **common,
            quantum=count,
            frequency=D(MONTHS_PER_YEAR),
            extras={"items_avoided": avoided, "unit_cost_rupees": unit_cost},
            working=(
                f"{count:g}/month × 12, {reduction * 100:g}% avoided "
                f"= {avoided.quantize(D('0.1'))} items a year"
            ),
        )

    # Safety, 5S and anything unclassified: stars only, no rupees. Costing a
    # near-miss means inventing a probability, and an invented probability in a
    # savings total is exactly what the three-tier split exists to prevent.
    return SavingRequest(**common, working="")


# ---------------------------------------------------------------------------
# Stage 2 — Core prices it (§7.1)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CoreValues:
    """Exactly what §7.1 says Core's formula module returns."""

    labour_value: int = 0
    machine_value: int = 0
    overhead_value: int = 0
    contribution_value: int = 0
    material_value: int = 0
    bottleneck_factor: Decimal = ONE
    #: The value Kaizen's classification layer splits, for this saving type.
    subject_value: int = 0
    #: §7.3 — every rate Core could not supply. Any of these makes the whole
    #: Kaizen Provisional and keeps it out of the plant's verified total.
    provisional: tuple[str, ...] = ()
    #: One missing rate at a time, asked of the approver, never the operator.
    missing_rate_key: str | None = None
    missing_rate_label: str | None = None
    working: str = ""
    rate_trace: dict[str, str] = field(default_factory=dict)


async def core_values(
    session: AsyncSession,
    *,
    plant: Plant,
    request: SavingRequest,
    on_date: date | None = None,
) -> CoreValues:
    """Hand the request to Core's cost engine and its cost master.

    Nothing in this function invents a rate. Where Core has a gap it says so —
    §7.3's fallback ladder decides what happens next, and the Kaizen carries
    Core's Provisional flag until somebody supplies the number.
    """
    on_date = on_date or date.today()
    out = CoreValues(working=request.working)

    station = await session.get(Station, request.station_id) if request.station_id else None
    part = await session.get(Part, request.part_id) if request.part_id else None
    operation = (
        await session.get(RoutingOperation, request.operation_id)
        if request.operation_id
        else None
    )

    if station is not None:
        out.bottleneck_factor = D(station.bottleneck_factor or 1)

    snapshot = trace = None
    if station is not None:
        snapshot, trace = await costing_service.resolve_snapshot(
            session,
            plant=plant,
            station=station,
            part=part,
            on_date=on_date,
            routing_operation=operation,
        )
        out.rate_trace = {
            "machine_rate": trace.machine_rate_source,
            "labour_rate": trace.labour_rate_source,
            "overhead_rate": trace.overhead_rate_source,
            "cycle_time": trace.cycle_time_source,
            "margin": trace.margin_source,
        }

    kind = request.saving_type
    extras = request.extras

    # --- time recovered by a person: labour value (§7.2) -------------------
    if kind in (
        SavingType.MOTION,
        SavingType.TRANSPORT,
        SavingType.HANDLING,
        SavingType.WAITING,
    ):
        hours = extras.get("hours_per_year", D(0))
        labour = snapshot.rates.labour_rate if snapshot else 0
        if labour <= 0:
            out.provisional += ("labour rate",)
            out.missing_rate_key = out.missing_rate_key or "labour_rate"
            out.missing_rate_label = out.missing_rate_label or "Labour rate per hour"
        out.labour_value = round_paise(hours * D(labour))
        out.subject_value = out.labour_value
        if labour > 0:
            out.working = (
                f"{request.working}, at {format_inr(labour)}/hr labour "
                f"= {format_inr(out.labour_value)} a year"
            )

    # --- machine time recovered: extra parts x contribution (§7.2) ---------
    elif kind in (SavingType.CYCLE_TIME, SavingType.CHANGEOVER):
        seconds = extras.get("seconds_per_year", D(0))
        cycle_min = snapshot.part.cycle_time_min if snapshot else D(0)
        margin = snapshot.part.contribution_margin if snapshot else 0

        if not cycle_min or cycle_min <= 0:
            out.provisional += ("cycle time",)
        if margin <= 0:
            out.provisional += ("contribution margin",)
            out.missing_rate_key = out.missing_rate_key or "contribution_margin"
            out.missing_rate_label = (
                out.missing_rate_label or "Contribution margin per part"
            )

        extra_parts = 0
        if cycle_min and cycle_min > 0:
            # Priced at the CURRENT cycle time, which is the longer one — so
            # the parts count is a floor, not a best case.
            extra_parts = floor_int(seconds / (D(cycle_min) * MINUTES_PER_HOUR))
        out.contribution_value = round_paise(D(extra_parts) * D(margin))
        out.machine_value = (
            round_paise((seconds / SECONDS_PER_HOUR) * D(snapshot.rates.machine_rate))
            if snapshot
            else 0
        )
        out.subject_value = out.contribution_value
        if extra_parts and margin > 0:
            out.working = (
                f"{request.working} → {extra_parts:,} more parts at "
                f"{format_inr(margin)} contribution = "
                f"{format_inr(out.contribution_value)} a year"
            )

    # --- scrap avoided: Core's own scrap formula (§8.1) --------------------
    elif kind is SavingType.SCRAP:
        quantity = int(extras.get("parts_per_year", D(0)))
        if snapshot is None or part is None:
            out.provisional += ("part and station context",)
        else:
            completed = await _operations_up_to(
                session, plant=plant, part=part, operation=operation, on_date=on_date
            )
            cost = scrap_cost(
                quantity=quantity, snapshot=snapshot, completed_operations=completed
            )
            recovery, recovery_note = await _scrap_recovery(
                session, plant=plant, part=part, quantity=quantity, on_date=on_date
            )
            if recovery_note:
                out.provisional += (recovery_note,)
                out.missing_rate_key = (
                    out.missing_rate_key or costing_service.SCRAP_RECOVERY_KG
                )
                out.missing_rate_label = (
                    out.missing_rate_label
                    or costing_service.UTILITY_LABELS[costing_service.SCRAP_RECOVERY_KG]
                )
            out.material_value = cost.material_cost
            out.machine_value = cost.processing_cost
            out.contribution_value = cost.lost_contribution
            # Scrap sold back is money the plant already gets. Not deducting it
            # overstates every scrap saving by 20-30%.
            out.subject_value = max(0, cost.total - recovery)
            out.working = (
                f"{request.working}: material {format_inr(cost.material_cost)} "
                f"+ processing {format_inr(cost.processing_cost)} "
                f"+ lost contribution {format_inr(cost.lost_contribution)}"
                + (f" − scrap sale {format_inr(recovery)}" if recovery else "")
                + f" = {format_inr(out.subject_value)} a year"
            )

    # --- rework avoided: Core's rework formula (§8.1) ----------------------
    elif kind is SavingType.REWORK:
        quantity = extras.get("parts_per_year", D(0))
        minutes = extras.get("rework_minutes", D(0))
        if snapshot is None:
            out.provisional += ("station cost rates",)
        else:
            per_part = rework_cost(rework_minutes=minutes, station_rates=snapshot.rates)
            out.subject_value = round_paise(D(per_part) * quantity)
            out.machine_value = out.subject_value
            out.working = (
                f"{request.working} × {minutes:g} min rework at "
                f"{format_inr(snapshot.rates.burden_rate)}/hr "
                f"= {format_inr(out.subject_value)} a year"
            )

    # --- utilities: a per-unit rate from Core's cost master ----------------
    elif kind in (SavingType.ENERGY, SavingType.COMPRESSED_AIR):
        if kind is SavingType.ENERGY:
            key, quantity, unit = (
                costing_service.ELECTRICITY_KWH,
                extras.get("kwh_per_year", D(0)),
                "kWh",
            )
        else:
            key, quantity, unit = (
                costing_service.COMPRESSED_AIR_M3,
                extras.get("m3_per_year", D(0)),
                "m³",
            )
        rate, source = await costing_service.utility_rate(
            session, plant_id=plant.id, key=key, on_date=on_date
        )
        out.rate_trace[key] = source
        if rate <= 0:
            out.provisional += (costing_service.UTILITY_LABELS[key].lower(),)
            out.missing_rate_key = key
            out.missing_rate_label = costing_service.UTILITY_LABELS[key]
        out.subject_value = round_paise(quantity * D(rate))
        out.material_value = out.subject_value
        if rate > 0:
            out.working = (
                f"{request.working}, at {format_inr(rate)}/{unit} "
                f"= {format_inr(out.subject_value)} a year"
            )

    # --- consumables and tooling: a purchase not made ----------------------
    elif kind in (SavingType.TOOLING, SavingType.CONSUMABLE):
        items = extras.get("items_avoided", D(0))
        unit_cost = round_paise(extras.get("unit_cost_rupees", D(0)) * D(100))
        out.subject_value = round_paise(items * D(unit_cost))
        out.material_value = out.subject_value
        if unit_cost > 0:
            out.working = (
                f"{request.working} at {format_inr(unit_cost)} each "
                f"= {format_inr(out.subject_value)} a year"
            )

    # Safety, 5S, other: nothing to price, and that is the correct answer.
    return out


async def _operations_up_to(
    session: AsyncSession,
    *,
    plant: Plant,
    part: Part,
    operation: RoutingOperation | None,
    on_date: date,
) -> list[tuple[Decimal, object]]:
    """Cycle time and rates for every operation completed before rejection.

    *"A part scrapped at Op10 has not consumed Op20's machine time."* With no
    rejection stage known, the whole routing counts — the conservative reading
    for a plant that has not told us where the defect is caught would be the
    opposite, so the caller is expected to inherit the stage from the Job Order.
    """
    rows = (
        await session.scalars(
            select(RoutingOperation)
            .where(
                RoutingOperation.plant_id == plant.id,
                RoutingOperation.part_id == part.id,
                RoutingOperation.cycle_time_min.is_not(None),
            )
            .order_by(RoutingOperation.sequence)
        )
    ).all()
    if operation is not None:
        rows = [row for row in rows if row.sequence <= operation.sequence]

    out: list[tuple[Decimal, object]] = []
    for row in rows:
        station = await session.scalar(
            select(Station).where(
                Station.plant_id == plant.id,
                Station.cost_family_id == row.cost_family_id,
                Station.is_active.is_(True),
            )
        )
        if station is None:
            continue
        snapshot, _ = await costing_service.resolve_snapshot(
            session,
            plant=plant,
            station=station,
            part=part,
            on_date=on_date,
            routing_operation=row,
        )
        out.append((D(row.cycle_time_min), snapshot.rates))
    return out


async def _scrap_recovery(
    session: AsyncSession, *, plant: Plant, part: Part, quantity: int, on_date: date
) -> tuple[int, str | None]:
    """What the plant already gets back selling the scrap. ``(paise, gap)``."""
    rate, _ = await costing_service.utility_rate(
        session, plant_id=plant.id, key=costing_service.SCRAP_RECOVERY_KG, on_date=on_date
    )
    if rate <= 0:
        return (0, "scrap sale recovery rate")
    if not part.weight_kg:
        return (0, "part weight")
    return (round_paise(D(part.weight_kg) * D(rate) * D(quantity)), None)


# ---------------------------------------------------------------------------
# Stage 3 — Kaizen's classification layer (§7.2)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PricedKaizen:
    """Everything the Business Impact Card needs, and nothing it doesn't."""

    split: SavingSplit
    stars: ImpactStars
    core: CoreValues
    request: SavingRequest
    investment_paise: int
    payback: Decimal | None
    roi: Decimal | None
    nothing: object  # CostOfDoingNothing
    provisional: bool
    provisional_note: str | None

    @property
    def hard(self) -> int:
        return self.split.hard

    @property
    def soft(self) -> int:
        return self.split.soft


#: Default star ratings by category. Overridden by the interview where it has
#: something more specific to say (a reported injury beats a default).
_DEFAULT_STARS: dict[KaizenCategory, dict[str, int]] = {
    KaizenCategory.SAFETY: {"safety": 4, "morale": 3, "compliance": 3},
    KaizenCategory.QUALITY: {"quality": 4, "compliance": 3, "delivery": 2},
    KaizenCategory.MOTION: {"morale": 4, "safety": 2},
    KaizenCategory.TRANSPORT: {"morale": 3, "safety": 2, "delivery": 2},
    KaizenCategory.WAITING: {"delivery": 4, "morale": 2},
    KaizenCategory.FIVE_S: {"compliance": 4, "safety": 3, "morale": 2},
    KaizenCategory.MAINTENANCE: {"delivery": 3, "quality": 2},
    KaizenCategory.ENERGY: {"compliance": 3},
    KaizenCategory.INVENTORY: {"delivery": 3},
    KaizenCategory.PRODUCTIVITY: {"delivery": 3, "morale": 2},
    KaizenCategory.COST: {},
    KaizenCategory.OTHER: {},
}


def rate_stars(
    *,
    category: KaizenCategory,
    answers: dict[str, float],
    vision: list[dict] | None = None,
) -> ImpactStars:
    """The star half of the impact card — what cannot be costed, never in rupees.

    *"Money is shown in rupees; everything that can't be costed is shown in
    stars — the two never get mixed into a single figure."*
    """
    stars = dict.fromkeys(("safety", "quality", "delivery", "morale", "compliance"), 0)
    stars.update(_DEFAULT_STARS.get(category, {}))

    severity = answers.get("severity")
    if severity:
        stars["safety"] = max(stars["safety"], min(5, int(severity)))
    people = answers.get("people_affected")
    if people:
        stars["morale"] = max(stars["morale"], 2 if people < 4 else 4 if people < 7 else 5)

    for finding in vision or ():
        if not finding.get("accepted"):
            continue
        match finding.get("category"):
            case "safety_hazard" | "obstruction":
                stars["safety"] = max(stars["safety"], 4)
            case "leak_or_spill":
                stars["safety"] = max(stars["safety"], 3)
                stars["compliance"] = max(stars["compliance"], 3)
            case "housekeeping" | "missing_label":
                stars["compliance"] = max(stars["compliance"], 3)

    return ImpactStars(**stars)


def price(
    *,
    core: CoreValues,
    request: SavingRequest,
    category: KaizenCategory,
    answers: dict[str, float],
    vision: list[dict] | None = None,
    investment_paise: int = 0,
    days_waiting: int = 0,
) -> PricedKaizen:
    """Run Kaizen's classification over Core's numbers. The last stage of §7.1."""
    split = classify(
        saving_type=request.saving_type,
        core_value=core.subject_value,
        bottleneck_factor=core.bottleneck_factor,
    )
    stars = rate_stars(category=category, answers=answers, vision=vision)
    total_claim = split.hard + split.soft

    note = None
    if core.provisional:
        note = (
            "Provisional — calculated without "
            + ", ".join(core.provisional)
            + ". It is excluded from the plant's verified savings total until "
            "that is confirmed."
        )

    return PricedKaizen(
        split=split,
        stars=stars,
        core=core,
        request=request,
        investment_paise=investment_paise,
        payback=payback_months(investment=investment_paise, annual_saving=split.hard),
        roi=roi_percent(investment=investment_paise, annual_saving=split.hard),
        nothing=cost_of_doing_nothing(annual_value=total_claim, days_waiting=days_waiting),
        provisional=bool(core.provisional),
        provisional_note=note,
    )


async def quote(
    session: AsyncSession,
    *,
    plant: Plant,
    saving_type: SavingType,
    category: KaizenCategory,
    interview: list[dict],
    station_id: uuid.UUID | None = None,
    part_id: uuid.UUID | None = None,
    operation_id: uuid.UUID | None = None,
    vision: list[dict] | None = None,
    investment_paise: int = 0,
    days_waiting: int = 0,
    on_date: date | None = None,
) -> PricedKaizen:
    """The whole of §7.1 in one call: request → Core → classification."""
    answers = kaizen_ai.numeric_answers(saving_type, interview)
    rhythm = await plant_rhythm(session, plant=plant)
    request = build_request(
        saving_type=saving_type,
        answers=answers,
        rhythm=rhythm,
        station_id=station_id,
        part_id=part_id,
        operation_id=operation_id,
    )
    values = await core_values(session, plant=plant, request=request, on_date=on_date)
    return price(
        core=values,
        request=request,
        category=category,
        answers=answers,
        vision=vision,
        investment_paise=investment_paise,
        days_waiting=days_waiting,
    )
