"""Value types the kernel consumes and produces.

The kernel is pure: it never reads a database, a clock, a config file or the
network. Every input arrives through these structures, resolved by the caller.
This is what makes the same code usable by the API, the worker and the tests.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from .money import Paise

FORMULA_VERSION = 1
"""Bumped whenever any formula in this package changes.

Stamped onto every costed event (ARCHITECTURE.md C3) so a locked shift is
arithmetically frozen and can be explained years later.
"""


class CostingMode(str, Enum):
    """§4.2 — three modes, identical formulas, different input granularity."""

    BASIC = "basic"
    STANDARD = "standard"
    ADVANCED = "advanced"


class Recoverability(str, Enum):
    """§3 Layer 1 — can the plant get this money back?

    Drives the money report and the Action trigger rules. NOT the same axis as
    ``OeeTreatment`` (see ARCHITECTURE.md §2.3).
    """

    RECOVERABLE = "R"
    STRUCTURAL = "S"


class OeeTreatment(str, Enum):
    """Drives Availability only.

    A power cut is Structural (unrecoverable) but absolutely *unplanned* — it
    must reduce Availability. Mapping S -> planned silently inflates OEE.
    """

    PLANNED = "planned"
    UNPLANNED = "unplanned"


class MarginBand(str, Enum):
    """§4.1 — owners who won't disclose exact margins pick a band per family."""

    LOW = "L"
    MEDIUM = "M"
    HIGH = "H"


@dataclass(frozen=True, slots=True)
class CostRates:
    """Resolved hourly rates, in paise per hour.

    Resolution (which cost family, which effective-dated row) happens in the
    service layer. By the time the kernel sees them they are just numbers.
    """

    machine_rate: Paise  # MCR
    labour_rate: Paise  # LCR
    overhead_rate: Paise  # OHR

    @property
    def burden_rate(self) -> Paise:
        """MCR + LCR + OHR — the full hourly cost of a stopped station."""
        return self.machine_rate + self.labour_rate + self.overhead_rate


@dataclass(frozen=True, slots=True)
class PartEconomics:
    """Per-part economics resolved for the job that was running."""

    cycle_time_min: Decimal  # CT, minutes per part
    contribution_margin: Paise  # CM, paise per part
    material_cost: Paise = 0  # for scrap costing


@dataclass(frozen=True, slots=True)
class CostSnapshot:
    """The frozen inputs written onto every costed event (C3).

    History never restates: if the owner corrects a machine rate in week 6,
    yesterday's number does not silently change.
    """

    rates: CostRates
    part: PartEconomics
    bottleneck_factor: Decimal
    costing_mode: CostingMode
    formula_version: int = FORMULA_VERSION


@dataclass(frozen=True, slots=True)
class DowntimeCost:
    """§8.1 — the four components an owner sees, plus their sum.

    Components are each rounded once, half-up; the total is the sum of the
    rounded components, so a screen that lists the breakdown always adds up.
    """

    machine_cost: Paise
    labour_cost: Paise
    overhead_cost: Paise
    lost_contribution: Paise
    total: Paise
    parts_not_made: int
    duration_minutes: Decimal

    def as_components(self) -> list[tuple[str, Paise]]:
        return [
            ("Machine cost (idle machine still costs money)", self.machine_cost),
            ("Labour cost (operator standing idle)", self.labour_cost),
            ("Overhead (factory costs keep running)", self.overhead_cost),
            ("Lost contribution (parts NOT made)", self.lost_contribution),
        ]


@dataclass(frozen=True, slots=True)
class ScrapCost:
    """§8.1 — rejected, unrecoverable parts."""

    material_cost: Paise
    processing_cost: Paise
    lost_contribution: Paise
    total: Paise
    quantity: int


@dataclass(frozen=True, slots=True)
class OeeResult:
    """§8.2 — A x P x Q, with the basis kept alongside the number.

    Never stored denormalised without its basis: an OEE figure whose planned
    time is unknown cannot be defended in a morning meeting.
    """

    availability: Decimal
    performance: Decimal
    quality: Decimal
    oee: Decimal
    planned_minutes: Decimal
    unplanned_downtime_minutes: Decimal
    planned_downtime_minutes: Decimal
    run_minutes: Decimal
    total_count: int
    good_count: int
    performance_uncapped: Decimal
    flags: tuple[str, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class DowntimeSlice:
    """One downtime event, reduced to what the KPI formulas need."""

    minutes: Decimal
    recoverability: Recoverability
    oee_treatment: OeeTreatment
    is_breakdown: bool
    cost: Paise = 0
    reason_code: str = ""
    station_id: str = ""


@dataclass(frozen=True, slots=True)
class LossLine:
    """One row of the ranked loss table (§3 Layer 2)."""

    station: str
    reason: str
    minutes: Decimal
    cost: Paise
    recoverability: Recoverability
    event_count: int = 1


@dataclass(frozen=True, slots=True)
class RankedLosses:
    """The end-of-shift table, ranked by rupees — never by frequency.

    Recoverable and Structural are reported separately and never summed into
    the same total (§8.1).
    """

    recoverable: Sequence[LossLine]
    structural: Sequence[LossLine]
    recoverable_total: Paise
    structural_total: Paise
    total_minutes: Decimal
