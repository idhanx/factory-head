"""The dataclasses a plant is described with.

Separated from :mod:`catalog` so that a second file of plants
(:mod:`catalog_group`) can import them without importing the plants themselves
— the two catalogs would otherwise form a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Op:
    code: str
    family: str
    cycle_min: str
    source: str = "measured"


@dataclass(frozen=True)
class PartSpec:
    number: str
    description: str
    customer: str
    family: str
    margin: str | None  # rupees per part, exact; None -> use the band
    band: str | None  # L | M | H
    material: str  # rupees per part
    routing: tuple[Op, ...]
    demand: int  # relative weight when a new job order is raised
    lot_size: tuple[int, int]


@dataclass(frozen=True)
class StationSpec:
    code: str
    name: str
    kind: str
    family: str
    section: str  # machining | assembly | quality | dispatch
    line: str | None
    bottleneck: bool = False
    factor: str | None = None  # bottleneck factor override


@dataclass(frozen=True)
class ShiftSpec:
    name: str
    start: str
    end: str
    break_minutes: int
    weekly_off: str = "Sun"


@dataclass(frozen=True)
class PersonSpec:
    name: str
    role_tag: (
        str  # alert-recipient tag: owner|production|quality|maintenance|stores|plant_head
    )
    app_role: str  # tenancy.Role value
    login: str  # email, or phone for floor roles
    password: str
    level: int
    language: str = "en"
    shift: str | None = None  # the shift a supervisor/inspector runs


@dataclass(frozen=True)
class RateSpec:
    scope: str  # cost_family | section | plant
    ref: str  # family code | section name | "plant"
    machine: str = "0"
    labour: str = "0"
    overhead: str = "0"
    source: str = "derived"
    effective_from: date | None = None  # None -> onboarding date
    note: str | None = None


@dataclass(frozen=True)
class Chronic:
    """A real, recurring problem on one machine.

    These are what make the Actions engine, Recovered Profit, the Coach's
    pattern tier and Kaizen scoring produce meaningful output: a problem that
    recurs, gets an Action, gets fixed — and then either stays fixed or doesn't.
    """

    station: str
    reason: str
    per_shift: float
    minutes: tuple[int, int]


@dataclass
class PlantSpec:
    code: str
    name: str
    location: str
    costing_mode: str
    go_live: date
    lot_granularity: str
    families: list[tuple[str, str, str]]
    rates: list[RateSpec]
    stations: list[StationSpec]
    parts: list[PartSpec]
    margin_bands: dict[str, tuple[str, str, str]]
    shifts: list[ShiftSpec]
    people: list[PersonSpec]
    operator_prefix: str
    operators_per_shift: int
    chronic: list[Chronic]
    extra_reasons: list[tuple]
    defects: list[tuple[str, str, str | None, str]]
    email_domain: str
    action_cost_threshold: int = 2500
    action_duration_minutes: int = 30
    recurrence_count: int = 3
    recurrence_window_shifts: int = 7
    tolerance_parts: int = 5
    tolerance_bp: int = 200
    no_blame_days: int = 30
    monthly_fee: int = 25000
    whatsapp_approved: bool = True
    event_weights: dict[str, float] = field(default_factory=dict)
    events_per_shift: tuple[int, int, int, int] = (56, 30, 10, 4)
    kaizen_ideas: int = 30
