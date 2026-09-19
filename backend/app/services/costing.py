"""Bridge between the database and the pure kernel.

The kernel knows nothing about tables. This module's job is to *resolve* the
inputs — which cost family, which effective-dated rate row, which cycle time,
which bottleneck factor — hand them to the kernel, and write both the outputs
and the resolved inputs back onto the row.

Writing the inputs back is the point (ARCHITECTURE.md C3). A costed event that
stores only its total can never be explained again; one that stores its inputs
can be defended in a morning meeting two years later.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.kernel import (
    CostingMode,
    CostRates,
    CostSnapshot,
    DowntimeCost,
    PartEconomics,
    downtime_event_cost,
)
from app.kernel.types import FORMULA_VERSION
from app.models import (
    CostRate,
    MarginBandValue,
    Part,
    Plant,
    RateSource,
    RoutingOperation,
    Station,
)

ZERO_RATES = CostRates(machine_rate=0, labour_rate=0, overhead_rate=0)


@dataclass(slots=True)
class ResolutionTrace:
    """Why each number is what it is — surfaced in the API so an owner asking
    'where did ₹420/hr come from?' gets an answer instead of a shrug."""

    machine_rate_source: str = "missing"
    labour_rate_source: str = "missing"
    overhead_rate_source: str = "missing"
    cycle_time_source: str = "missing"
    margin_source: str = "missing"
    warnings: tuple[str, ...] = ()


async def resolve_snapshot(
    session: AsyncSession,
    *,
    plant: Plant,
    station: Station,
    part: Part | None,
    on_date: date,
    routing_operation: RoutingOperation | None = None,
) -> tuple[CostSnapshot, ResolutionTrace]:
    """Assemble everything the kernel needs to cost one event.

    Follows §4.1's don't-block rules throughout: a missing cycle time, an
    undisclosed margin or absent cost data each degrade to a documented
    fallback rather than refusing to produce a number. A ±20% estimate still
    puts the biggest loss at the top of the list, and the ranking is what
    drives action.
    """
    warnings: list[str] = []
    trace = ResolutionTrace()

    machine_rate, trace.machine_rate_source = await _rate_for(
        session, plant.id, "cost_family", _family_ref(station), on_date, "machine_rate"
    )
    labour_rate, trace.labour_rate_source = await _rate_for(
        session, plant.id, "section", station.section.value, on_date, "labour_rate"
    )
    overhead, trace.overhead_rate_source = await _rate_for(
        session, plant.id, "plant", "plant", on_date, "overhead_rate"
    )

    if machine_rate == 0:
        warnings.append(
            f"No machine rate for {station.code}; downtime on this station will "
            "under-report until a rate is set."
        )

    cycle_time = Decimal(0)
    if routing_operation is not None and routing_operation.cycle_time_min:
        cycle_time = Decimal(routing_operation.cycle_time_min)
        trace.cycle_time_source = f"routing {routing_operation.operation_code}"
    elif part is not None:
        cycle_time, source = await _cycle_time_for(session, plant.id, part.id, station)
        trace.cycle_time_source = source

    if cycle_time == 0:
        warnings.append(
            "No cycle time available — lost contribution cannot be computed, so "
            "this event is costed on machine, labour and overhead only."
        )

    margin = 0
    if part is not None:
        margin, trace.margin_source = await _margin_for(session, plant.id, part)
        if margin == 0:
            warnings.append(
                f"No contribution margin for {part.part_number}; set an exact "
                "value or an L/M/H band for its part family."
            )

    snapshot = CostSnapshot(
        rates=CostRates(
            machine_rate=machine_rate, labour_rate=labour_rate, overhead_rate=overhead
        ),
        part=PartEconomics(
            cycle_time_min=cycle_time,
            contribution_margin=margin,
            material_cost=part.material_cost if part else 0,
        ),
        bottleneck_factor=Decimal(station.bottleneck_factor or Decimal("1.00")),
        costing_mode=CostingMode(plant.costing_mode.value),
        formula_version=FORMULA_VERSION,
    )
    trace.warnings = tuple(warnings)
    return snapshot, trace


def _family_ref(station: Station) -> str:
    """Rates hang off the cost family (§14.4); a station without one falls back
    to its own code so an exceptional machine can carry an individual override."""
    return str(station.cost_family_id) if station.cost_family_id else station.code


async def _rate_for(
    session: AsyncSession,
    plant_id: uuid.UUID,
    scope: str,
    scope_ref: str,
    on_date: date,
    column: str,
) -> tuple[int, str]:
    """Latest rate effective on ``on_date``.

    Effective-dated, never "current": re-costing a locked shift must produce the
    same number it produced the day it happened.
    """
    row = await session.scalar(
        select(CostRate)
        .where(
            CostRate.plant_id == plant_id,
            CostRate.scope == scope,
            CostRate.scope_ref == scope_ref,
            CostRate.effective_from <= on_date,
        )
        .order_by(CostRate.effective_from.desc())
        .limit(1)
    )
    if row is None:
        return (0, "missing")
    return (getattr(row, column) or 0, f"{scope}:{scope_ref} from {row.effective_from}")


async def resolve_routing_operation(
    session: AsyncSession, *, plant_id: uuid.UUID, part_id: uuid.UUID, station: Station
) -> RoutingOperation | None:
    """Which step of the part's routing a station performs.

    Resolved once, when a job is put on a station, and stored on the job
    session. Everything downstream — the Performance term of OEE, the cycle
    time a stop is costed with — reads it from there, so a job session without
    it has no speed to be measured against.

    The operation whose cost family matches the station's wins; otherwise the
    part's first operation that carries a cycle time, the same fallback the cost
    engine has always used.
    """
    operations = (
        await session.scalars(
            select(RoutingOperation)
            .where(
                RoutingOperation.plant_id == plant_id,
                RoutingOperation.part_id == part_id,
            )
            .order_by(RoutingOperation.sequence)
        )
    ).all()
    if not operations:
        return None
    if station.cost_family_id:
        for op in operations:
            if op.cost_family_id == station.cost_family_id:
                return op
    return next((op for op in operations if op.cycle_time_min), None)


async def _cycle_time_for(
    session: AsyncSession, plant_id: uuid.UUID, part_id: uuid.UUID, station: Station
) -> tuple[Decimal, str]:
    """Prefer the operation matching this station's cost family; fall back to
    the part's first operation with a cycle time."""
    operations = (
        await session.scalars(
            select(RoutingOperation)
            .where(
                RoutingOperation.plant_id == plant_id,
                RoutingOperation.part_id == part_id,
                RoutingOperation.cycle_time_min.is_not(None),
            )
            .order_by(RoutingOperation.sequence)
        )
    ).all()
    if not operations:
        return (Decimal(0), "missing")

    for op in operations:
        if station.cost_family_id and op.cost_family_id == station.cost_family_id:
            return (Decimal(op.cycle_time_min), f"routing {op.operation_code}")

    first = operations[0]
    return (Decimal(first.cycle_time_min), f"routing {first.operation_code} (fallback)")


async def _margin_for(
    session: AsyncSession, plant_id: uuid.UUID, part: Part
) -> tuple[int, str]:
    """Exact margin if the owner gave one; otherwise the band value for the
    part family (§4.1 — "the loss ranking stays correct; precision comes when
    trust does")."""
    if part.contribution_margin:
        return (part.contribution_margin, "exact")

    if part.margin_band and part.part_family:
        # The column is a plain string; importers may hand it an enum member.
        band_code = str(getattr(part.margin_band, "value", part.margin_band))
        band = await session.scalar(
            select(MarginBandValue).where(
                MarginBandValue.plant_id == plant_id,
                MarginBandValue.part_family == part.part_family,
                MarginBandValue.band == band_code,
            )
        )
        if band:
            return (band.value, f"band {band_code} ({part.part_family})")

    return (0, "missing")


# ---------------------------------------------------------------------------
# Per-unit rates (electricity, compressed air, scrap recovery)
# ---------------------------------------------------------------------------

#: Scope refs for the per-unit rates the cost master holds. Effective-dated like
#: every other rate, so re-costing a locked period reproduces its own numbers.
ELECTRICITY_KWH = "electricity_kwh"
COMPRESSED_AIR_M3 = "compressed_air_m3"
SCRAP_RECOVERY_KG = "scrap_recovery_kg"

UTILITY_KEYS = (ELECTRICITY_KWH, COMPRESSED_AIR_M3, SCRAP_RECOVERY_KG)

UTILITY_LABELS: dict[str, str] = {
    ELECTRICITY_KWH: "Electricity rate per unit (kWh)",
    COMPRESSED_AIR_M3: "Compressed air cost per m³",
    SCRAP_RECOVERY_KG: "Scrap sale recovery per kg",
}


async def utility_rate(
    session: AsyncSession, *, plant_id: uuid.UUID, key: str, on_date: date
) -> tuple[int, str]:
    """A per-unit rate from the cost master, in paise. ``0`` when unset.

    Ignoring the scrap recovery rate overstates every scrap saving by 20-30%,
    which is why it sits here beside the rates rather than being assumed away.
    """
    if key not in UTILITY_KEYS:
        raise ValueError(f"Unknown utility rate {key!r}")
    return await _rate_for(session, plant_id, "utility", key, on_date, "unit_rate")


async def set_utility_rate(
    session: AsyncSession,
    *,
    plant_id: uuid.UUID,
    key: str,
    paise: int,
    effective_from: date,
    source: RateSource = RateSource.DERIVED,
    derivation: dict | None = None,
) -> CostRate:
    """Write a per-unit rate into the cost master, with its audit trail.

    Called from the Kaizen approval card when the plant supplies a rate for the
    first time. It lands in Core's own cost master and is immediately visible
    and editable in Core's cost screens — never in a plugin-local store.
    """
    if key not in UTILITY_KEYS:
        raise ValueError(f"Unknown utility rate {key!r}")
    if paise <= 0:
        raise ValueError("A cost rate must be greater than zero")

    row = CostRate(
        plant_id=plant_id,
        scope="utility",
        scope_ref=key,
        unit_rate=paise,
        source=source,
        derivation=derivation,
        effective_from=effective_from,
    )
    session.add(row)
    await session.flush()
    return row


def apply_snapshot(row, snapshot: CostSnapshot, cost: DowntimeCost | None = None) -> None:
    """Freeze the resolved inputs (and outputs) onto a capture row."""
    row.snapshot_machine_rate = snapshot.rates.machine_rate
    row.snapshot_labour_rate = snapshot.rates.labour_rate
    row.snapshot_overhead_rate = snapshot.rates.overhead_rate
    row.snapshot_cycle_time_min = snapshot.part.cycle_time_min or None
    row.snapshot_contribution_margin = snapshot.part.contribution_margin
    row.snapshot_bottleneck_factor = snapshot.bottleneck_factor
    row.snapshot_costing_mode = snapshot.costing_mode.value
    row.formula_version = snapshot.formula_version

    if cost is not None:
        row.cost_total = cost.total
        row.cost_breakdown = {
            "machine": cost.machine_cost,
            "labour": cost.labour_cost,
            "overhead": cost.overhead_cost,
            "lost_contribution": cost.lost_contribution,
            "parts_not_made": cost.parts_not_made,
        }


def snapshot_from_row(row) -> CostSnapshot:
    """Rebuild the exact snapshot a row was costed with.

    Used to recompute a live ticker, and to prove a historical number: the
    inputs come off the row, never from today's master data.
    """
    return CostSnapshot(
        rates=CostRates(
            machine_rate=row.snapshot_machine_rate,
            labour_rate=row.snapshot_labour_rate,
            overhead_rate=row.snapshot_overhead_rate,
        ),
        part=PartEconomics(
            cycle_time_min=Decimal(row.snapshot_cycle_time_min or 0),
            contribution_margin=row.snapshot_contribution_margin,
        ),
        bottleneck_factor=Decimal(row.snapshot_bottleneck_factor or Decimal("1.00")),
        costing_mode=CostingMode(row.snapshot_costing_mode or "basic"),
        formula_version=row.formula_version or FORMULA_VERSION,
    )


def cost_open_event(row, *, as_of) -> DowntimeCost:
    """The live rupee ticker on a stopped machine.

    Same kernel function as the final cost, so the number climbing on the
    shopfloor screen is the number that appears in the owner's report — the
    ticker can never disagree with the ledger.
    """
    snapshot = snapshot_from_row(row)
    elapsed = Decimal((as_of - row.started_at).total_seconds()) / Decimal(60)
    return downtime_event_cost(max(Decimal(0), elapsed), snapshot)
