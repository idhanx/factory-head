"""Seed the demo plant from the specification's own worked example.

Kumaran Auto Components, Hosur — 18 machines, 2 shifts, tier-2 supplier to a
major OEM's tier-1 (§6). Six weeks of history, so the system is in the state the
spec describes: the AI Coach has earned Tier 2, Actions have been raised and
closed, and the Recovered Profit ledger has sustained weeks behind it.

Every rate, part, reason code and defect here comes from the spec. §10 is
explicit about why that matters: "the paper-first exercise is the demo your
screens should be designed around — real reason codes, real part numbers, real
cost rates from real plants. Build to that reality, not to a generic template."
"""

from __future__ import annotations

import asyncio
import random
import sys
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select

from app.core.db import AdminSessionFactory as SessionFactory
from app.core.security import hash_password
from app.kernel import downtime_event_cost, rupees
from app.models import (
    Action,
    AlertRecipient,
    Base,
    CostFamily,
    CostingMode,
    CostRate,
    DefectCode,
    Disposition,
    DowntimeEvent,
    DowntimeStatus,
    EscalationRoute,
    JobOrder,
    JobOrderStatus,
    JobSession,
    Lot,
    LotGranularity,
    MarginBand,
    MarginBandValue,
    MemoryEntry,
    OeeTreatment,
    Operator,
    Part,
    PdcaState,
    Plant,
    ProductionCount,
    ProductionTarget,
    QualityRecord,
    RateSource,
    ReasonCode,
    Recoverability,
    Role,
    RoutingOperation,
    Section,
    Shift,
    ShiftSession,
    ShiftSessionStatus,
    Station,
    User,
    UserPlantRole,
)
from app.services import actions as action_service
from app.services.costing import apply_snapshot, resolve_snapshot
from app.services.downtime import escalation_message, escalation_route_for

random.seed(20260908)  # deterministic demo data

TODAY = date(2026, 9, 12)
WEEKS_OF_HISTORY = 6
IST = timezone(timedelta(hours=5, minutes=30))


# ---------------------------------------------------------------------------
# T6 — the default reason-code library (§15, §16.4 "seedable before the first plant")
#
# Four attributes per code, not one. R/S drives the money report; planned/
# unplanned drives Availability; is_breakdown drives MTBF/MTTR; the route
# decides who gets the 15-minute WhatsApp. See ARCHITECTURE.md §2.3 for why
# collapsing these into a single tag silently inflates OEE on every power cut.
# ---------------------------------------------------------------------------
REASON_CODES = [
    # code, label, R/S, oee, breakdown, route, safety, order
    ("TOOL_BREAKAGE", "Tool Breakage", "R", "unplanned", False, "maintenance", False, 1),
    ("MATERIAL_WAIT", "Material Wait", "R", "unplanned", False, "stores", False, 2),
    (
        "SETUP_CHANGEOVER",
        "Setup / Changeover",
        "R",
        "unplanned",
        False,
        "production",
        False,
        3,
    ),
    (
        "TOOL_CHANGE",
        "Tool Change (planned)",
        "R",
        "unplanned",
        False,
        "maintenance",
        False,
        4,
    ),
    (
        "MACHINE_BREAKDOWN",
        "Machine Breakdown",
        "R",
        "unplanned",
        True,
        "maintenance",
        False,
        5,
    ),
    (
        "MINOR_ADJUSTMENT",
        "Minor Adjustments",
        "R",
        "unplanned",
        False,
        "production",
        False,
        6,
    ),
    ("NO_OPERATOR", "No Operator", "R", "unplanned", False, "production", False, 7),
    ("QUALITY_HOLD", "Quality Hold", "R", "unplanned", False, "quality", True, 8),
    (
        "PROGRAM_ISSUE",
        "Program / Setting Issue",
        "R",
        "unplanned",
        False,
        "production",
        False,
        9,
    ),
    (
        "FIXTURE_ISSUE",
        "Fixture / Jig Issue",
        "R",
        "unplanned",
        False,
        "maintenance",
        False,
        10,
    ),
    (
        "COOLANT_ISSUE",
        "Coolant / Consumable",
        "R",
        "unplanned",
        False,
        "maintenance",
        False,
        11,
    ),
    (
        "WIP_STARVED",
        "Waiting on Previous Stage",
        "R",
        "unplanned",
        False,
        "production",
        False,
        12,
    ),
    # Structural — the plant cannot recover these. Note POWER_CUT is Structural
    # AND unplanned: it must still reduce Availability.
    ("POWER_CUT", "Power Cut", "S", "unplanned", False, "none", False, 13),
    (
        "PLANNED_MAINTENANCE",
        "Planned Maintenance",
        "S",
        "planned",
        False,
        "maintenance",
        False,
        14,
    ),
    (
        "MEETING_TRAINING",
        "Meeting / Training",
        "S",
        "planned",
        False,
        "production",
        False,
        15,
    ),
    ("NO_ORDER", "No Order", "S", "planned", False, "production", False, 16),
    ("BREAK", "Scheduled Break", "S", "planned", False, "none", False, 17),
    (
        "SHIFT_START_WARMUP",
        "Shift Start / Warm-up",
        "S",
        "planned",
        False,
        "production",
        False,
        18,
    ),
]

DEFECT_CODES = [
    ("BURR", "Burr", "Brackets"),
    ("UNDERSIZE", "Undersize", "Brackets"),
    ("OVERSIZE", "Oversize", "Brackets"),
    ("RUST_SPOT", "Rust Spot", "Brackets"),
    ("TOOL_MARK", "Tool Mark", "Brackets"),
    ("SURFACE_FINISH", "Surface Finish", "Brackets"),
    ("WELD_DISTORTION", "Weld Distortion", "Assemblies"),
    ("MISSING_OP", "Missing Operation", "Assemblies"),
    ("THREAD_DAMAGE", "Thread Damage", "Fasteners"),
    ("DENT", "Dent / Handling Damage", None),
]

# §14.4 — cost families. 18 machines collapse to 5 machine rates.
#
# Only the MACHINE rate is per family. Labour is a section rate and overhead is
# plant-wide (§8.1), so carrying them here would imply a granularity the cost
# engine does not have.
COST_FAMILIES = [
    # code, name, machine rate ₹/hr
    ("CNC-family", "CNC Turning", 420),
    ("VMC-family", "Vertical Machining", 380),
    ("PRESS-family", "Presses up to 100T", 260),
    ("ASSY-family", "Assembly Stations", 90),
    ("QC-family", "Inspection Bench", 60),
]

STATIONS = [
    # code, name, type, family, section, line, bottleneck
    (
        "CNC-1",
        "CNC Turning 1",
        "CNC turning",
        "CNC-family",
        Section.MACHINING,
        "Line 1",
        False,
    ),
    (
        "CNC-2",
        "CNC Turning 2",
        "CNC turning",
        "CNC-family",
        Section.MACHINING,
        "Line 1",
        False,
    ),
    (
        "CNC-3",
        "CNC Turning 3",
        "CNC turning",
        "CNC-family",
        Section.MACHINING,
        "Line 1",
        True,
    ),
    (
        "CNC-4",
        "CNC Turning 4",
        "CNC turning",
        "CNC-family",
        Section.MACHINING,
        "Line 2",
        False,
    ),
    (
        "CNC-5",
        "CNC Turning 5",
        "CNC turning",
        "CNC-family",
        Section.MACHINING,
        "Line 2",
        False,
    ),
    ("VMC-1", "VMC Milling 1", "VMC", "VMC-family", Section.MACHINING, "Line 1", False),
    ("VMC-2", "VMC Milling 2", "VMC", "VMC-family", Section.MACHINING, "Line 2", False),
    ("VMC-3", "VMC Milling 3", "VMC", "VMC-family", Section.MACHINING, "Line 2", False),
    ("Press-1", "Press 60T", "Press", "PRESS-family", Section.MACHINING, "Line 2", False),
    ("Press-2", "Press 100T", "Press", "PRESS-family", Section.MACHINING, "Line 2", True),
    ("Press-3", "Press 40T", "Press", "PRESS-family", Section.MACHINING, "Line 3", False),
    (
        "Drill-1",
        "Radial Drill 1",
        "Drilling",
        "VMC-family",
        Section.MACHINING,
        "Line 3",
        False,
    ),
    (
        "Drill-2",
        "Radial Drill 2",
        "Drilling",
        "VMC-family",
        Section.MACHINING,
        "Line 3",
        False,
    ),
    (
        "Assy-L1",
        "Assembly Line 1",
        "Assembly",
        "ASSY-family",
        Section.ASSEMBLY,
        "Line 1",
        True,
    ),
    (
        "Assy-L2",
        "Assembly Line 2",
        "Assembly",
        "ASSY-family",
        Section.ASSEMBLY,
        "Line 2",
        False,
    ),
    (
        "Weld-1",
        "Welding Booth 1",
        "Welding",
        "ASSY-family",
        Section.ASSEMBLY,
        "Line 2",
        False,
    ),
    ("QC-1", "Inspection Bench", "Inspection", "QC-family", Section.QUALITY, None, False),
    ("Dispatch", "Dispatch Desk", "Dispatch", "QC-family", Section.DISPATCH, None, False),
]

# T2/T3 — part master with routings and cycle times. AK-447 is the spec's own
# worked example: ₹110 selling − ₹72 material = ₹38 contribution, 2.4 min on CNC.
PARTS = [
    # number, description, customer, family, margin ₹, material ₹, routing
    (
        "AK-447",
        "Bracket Assembly LH",
        "Rane TRW",
        "Brackets",
        38,
        72,
        [
            ("Op10", "CNC-family", "2.4"),
            ("Op20", "ASSY-family", "1.8"),
            ("Op30", "QC-family", "0.5"),
            ("Op40", "QC-family", "0.2"),
        ],
    ),
    (
        "AK-448",
        "Bracket Assembly RH",
        "Rane TRW",
        "Brackets",
        38,
        72,
        [
            ("Op10", "CNC-family", "2.4"),
            ("Op20", "ASSY-family", "1.8"),
            ("Op30", "QC-family", "0.5"),
            ("Op40", "QC-family", "0.2"),
        ],
    ),
    (
        "BX-102",
        "Pivot Housing",
        "Sundaram Fasteners",
        "Housings",
        64,
        118,
        [
            ("Op10", "VMC-family", "3.6"),
            ("Op20", "QC-family", "0.6"),
            ("Op30", "QC-family", "0.2"),
        ],
    ),
    (
        "BX-110",
        "Pivot Housing Mk2",
        "Sundaram Fasteners",
        "Housings",
        71,
        124,
        [
            ("Op10", "VMC-family", "3.9"),
            ("Op20", "QC-family", "0.6"),
            ("Op30", "QC-family", "0.2"),
        ],
    ),
    (
        "PR-220",
        "Stamped Cover",
        "Lucas TVS",
        "Pressings",
        19,
        26,
        [
            ("Op10", "PRESS-family", "0.6"),
            ("Op20", "QC-family", "0.3"),
            ("Op30", "QC-family", "0.2"),
        ],
    ),
    (
        "PR-221",
        "Stamped Bracket",
        "Lucas TVS",
        "Pressings",
        22,
        31,
        [
            ("Op10", "PRESS-family", "0.7"),
            ("Op20", "QC-family", "0.3"),
            ("Op30", "QC-family", "0.2"),
        ],
    ),
    (
        "WF-330",
        "Welded Frame",
        "Ashok Leyland",
        "Assemblies",
        145,
        268,
        [
            ("Op10", "CNC-family", "2.1"),
            ("Op20", "ASSY-family", "4.2"),
            ("Op30", "QC-family", "0.8"),
            ("Op40", "QC-family", "0.3"),
        ],
    ),
    (
        "SP-090",
        "Spacer Ring",
        "Rane TRW",
        "Fasteners",
        9,
        12,
        [
            ("Op10", "CNC-family", "0.9"),
            ("Op20", "QC-family", "0.2"),
            ("Op30", "QC-family", "0.1"),
        ],
    ),
]

OPERATORS = [
    "Kumar M.",
    "Ravi S.",
    "Selvam P.",
    "Anbu R.",
    "Dinesh K.",
    "Vignesh T.",
    "Prakash N.",
    "Manikandan S.",
    "Karthik V.",
    "Saravanan B.",
    "Arun J.",
    "Gopi R.",
    "Mohan L.",
    "Sathish K.",
]

PEOPLE = [
    # name, role_tag, whatsapp, level, user email/phone, password, app role
    (
        "Mr. Kumaran",
        "owner",
        "+919000000010",
        3,
        "owner@kumaranauto.in",
        "owner1234",
        Role.OWNER,
    ),
    (
        "Murugan V.",
        "production",
        "+919000000011",
        1,
        "+919000000011",
        "shift1234",
        Role.SUPERVISOR,
    ),
    (
        "Kavitha R.",
        "quality",
        "+919000000012",
        2,
        "+919000000012",
        "quality1234",
        Role.QUALITY,
    ),
    (
        "Suresh K.",
        "maintenance",
        "+919000000013",
        2,
        "+919000000013",
        "maint1234",
        Role.MAINTENANCE,
    ),
    ("Ramesh P.", "stores", "+919000000014", 2, "+919000000014", "stores1234", Role.STORES),
    (
        "Anitha D.",
        "plant_head",
        "+919000000015",
        3,
        "+919000000015",
        "plant1234",
        Role.SUPERVISOR,
    ),
]

# How often each reason actually happens, and how long it runs. Tuned so the
# ranked loss table looks like a real plant's: a few big recoverable losses, a
# long tail of small ones, and structural time that never mixes with the rest.
EVENT_PROFILE = [
    # code, weight, min_minutes, max_minutes
    #
    # Durations matter as much as frequencies here. A real tier-2 plant's stops
    # are mostly short: only a handful a week cross the ₹2,500 / 30-minute
    # Action threshold. Generating a third of events above it produces hundreds
    # of open Actions — which is precisely the action fatigue §12 (risk 10)
    # says kills products like this within a month.
    ("MATERIAL_WAIT", 22, 6, 28),
    ("MINOR_ADJUSTMENT", 22, 2, 8),
    ("SETUP_CHANGEOVER", 16, 10, 32),
    ("TOOL_CHANGE", 15, 4, 12),
    ("TOOL_BREAKAGE", 7, 12, 47),
    ("MACHINE_BREAKDOWN", 3, 30, 120),
    ("NO_OPERATOR", 5, 6, 22),
    ("PROGRAM_ISSUE", 4, 5, 18),
    ("QUALITY_HOLD", 3, 8, 30),
    ("COOLANT_ISSUE", 4, 3, 14),
    ("POWER_CUT", 4, 6, 30),
    ("PLANNED_MAINTENANCE", 5, 25, 55),
    ("MEETING_TRAINING", 4, 12, 25),
]

DEFECT_CODES = [
    ("BURR", "Burr", "Brackets"),
    ("UNDERSIZE", "Undersize", "Brackets"),
    ("OVERSIZE", "Oversize", "Brackets"),
    ("RUST_SPOT", "Rust Spot", "Brackets"),
    ("TOOL_MARK", "Tool Mark", "Brackets"),
    ("SURFACE_FINISH", "Surface Finish", "Brackets"),
    ("WELD_DISTORTION", "Weld Distortion", "Assemblies"),
    ("MISSING_OP", "Missing Operation", "Assemblies"),
    ("THREAD_DAMAGE", "Thread Damage", "Fasteners"),
    ("DENT", "Dent / Handling Damage", None),
]

# §14.4 — cost families. 18 machines collapse to 5 machine rates.
#
# Only the MACHINE rate is per family. Labour is a section rate and overhead is
# plant-wide (§8.1), so carrying them here would imply a granularity the cost
# engine does not have.
COST_FAMILIES = [
    # code, name, machine rate ₹/hr
    ("CNC-family", "CNC Turning", 420),
    ("VMC-family", "Vertical Machining", 380),
    ("PRESS-family", "Presses up to 100T", 260),
    ("ASSY-family", "Assembly Stations", 90),
    ("QC-family", "Inspection Bench", 60),
]

STATIONS = [
    # code, name, type, family, section, line, bottleneck
    (
        "CNC-1",
        "CNC Turning 1",
        "CNC turning",
        "CNC-family",
        Section.MACHINING,
        "Line 1",
        False,
    ),
    (
        "CNC-2",
        "CNC Turning 2",
        "CNC turning",
        "CNC-family",
        Section.MACHINING,
        "Line 1",
        False,
    ),
    (
        "CNC-3",
        "CNC Turning 3",
        "CNC turning",
        "CNC-family",
        Section.MACHINING,
        "Line 1",
        True,
    ),
    (
        "CNC-4",
        "CNC Turning 4",
        "CNC turning",
        "CNC-family",
        Section.MACHINING,
        "Line 2",
        False,
    ),
    (
        "CNC-5",
        "CNC Turning 5",
        "CNC turning",
        "CNC-family",
        Section.MACHINING,
        "Line 2",
        False,
    ),
    ("VMC-1", "VMC Milling 1", "VMC", "VMC-family", Section.MACHINING, "Line 1", False),
    ("VMC-2", "VMC Milling 2", "VMC", "VMC-family", Section.MACHINING, "Line 2", False),
    ("VMC-3", "VMC Milling 3", "VMC", "VMC-family", Section.MACHINING, "Line 2", False),
    ("Press-1", "Press 60T", "Press", "PRESS-family", Section.MACHINING, "Line 2", False),
    ("Press-2", "Press 100T", "Press", "PRESS-family", Section.MACHINING, "Line 2", True),
    ("Press-3", "Press 40T", "Press", "PRESS-family", Section.MACHINING, "Line 3", False),
    (
        "Drill-1",
        "Radial Drill 1",
        "Drilling",
        "VMC-family",
        Section.MACHINING,
        "Line 3",
        False,
    ),
    (
        "Drill-2",
        "Radial Drill 2",
        "Drilling",
        "VMC-family",
        Section.MACHINING,
        "Line 3",
        False,
    ),
    (
        "Assy-L1",
        "Assembly Line 1",
        "Assembly",
        "ASSY-family",
        Section.ASSEMBLY,
        "Line 1",
        True,
    ),
    (
        "Assy-L2",
        "Assembly Line 2",
        "Assembly",
        "ASSY-family",
        Section.ASSEMBLY,
        "Line 2",
        False,
    ),
    (
        "Weld-1",
        "Welding Booth 1",
        "Welding",
        "ASSY-family",
        Section.ASSEMBLY,
        "Line 2",
        False,
    ),
    ("QC-1", "Inspection Bench", "Inspection", "QC-family", Section.QUALITY, None, False),
    ("Dispatch", "Dispatch Desk", "Dispatch", "QC-family", Section.DISPATCH, None, False),
]

# T2/T3 — part master with routings and cycle times. AK-447 is the spec's own
# worked example: ₹110 selling − ₹72 material = ₹38 contribution, 2.4 min on CNC.
PARTS = [
    # number, description, customer, family, margin ₹, material ₹, routing
    (
        "AK-447",
        "Bracket Assembly LH",
        "Rane TRW",
        "Brackets",
        38,
        72,
        [
            ("Op10", "CNC-family", "2.4"),
            ("Op20", "ASSY-family", "1.8"),
            ("Op30", "QC-family", "0.5"),
            ("Op40", "QC-family", "0.2"),
        ],
    ),
    (
        "AK-448",
        "Bracket Assembly RH",
        "Rane TRW",
        "Brackets",
        38,
        72,
        [
            ("Op10", "CNC-family", "2.4"),
            ("Op20", "ASSY-family", "1.8"),
            ("Op30", "QC-family", "0.5"),
            ("Op40", "QC-family", "0.2"),
        ],
    ),
    (
        "BX-102",
        "Pivot Housing",
        "Sundaram Fasteners",
        "Housings",
        64,
        118,
        [
            ("Op10", "VMC-family", "3.6"),
            ("Op20", "QC-family", "0.6"),
            ("Op30", "QC-family", "0.2"),
        ],
    ),
    (
        "BX-110",
        "Pivot Housing Mk2",
        "Sundaram Fasteners",
        "Housings",
        71,
        124,
        [
            ("Op10", "VMC-family", "3.9"),
            ("Op20", "QC-family", "0.6"),
            ("Op30", "QC-family", "0.2"),
        ],
    ),
    (
        "PR-220",
        "Stamped Cover",
        "Lucas TVS",
        "Pressings",
        19,
        26,
        [
            ("Op10", "PRESS-family", "0.6"),
            ("Op20", "QC-family", "0.3"),
            ("Op30", "QC-family", "0.2"),
        ],
    ),
    (
        "PR-221",
        "Stamped Bracket",
        "Lucas TVS",
        "Pressings",
        22,
        31,
        [
            ("Op10", "PRESS-family", "0.7"),
            ("Op20", "QC-family", "0.3"),
            ("Op30", "QC-family", "0.2"),
        ],
    ),
    (
        "WF-330",
        "Welded Frame",
        "Ashok Leyland",
        "Assemblies",
        145,
        268,
        [
            ("Op10", "CNC-family", "2.1"),
            ("Op20", "ASSY-family", "4.2"),
            ("Op30", "QC-family", "0.8"),
            ("Op40", "QC-family", "0.3"),
        ],
    ),
    (
        "SP-090",
        "Spacer Ring",
        "Rane TRW",
        "Fasteners",
        9,
        12,
        [
            ("Op10", "CNC-family", "0.9"),
            ("Op20", "QC-family", "0.2"),
            ("Op30", "QC-family", "0.1"),
        ],
    ),
]

OPERATORS = [
    "Kumar M.",
    "Ravi S.",
    "Selvam P.",
    "Anbu R.",
    "Dinesh K.",
    "Vignesh T.",
    "Prakash N.",
    "Manikandan S.",
    "Karthik V.",
    "Saravanan B.",
    "Arun J.",
    "Gopi R.",
    "Mohan L.",
    "Sathish K.",
]

PEOPLE = [
    # name, role_tag, whatsapp, level, user email/phone, password, app role
    (
        "Mr. Kumaran",
        "owner",
        "+919000000010",
        3,
        "owner@kumaranauto.in",
        "owner1234",
        Role.OWNER,
    ),
    (
        "Murugan V.",
        "production",
        "+919000000011",
        1,
        "+919000000011",
        "shift1234",
        Role.SUPERVISOR,
    ),
    (
        "Kavitha R.",
        "quality",
        "+919000000012",
        2,
        "+919000000012",
        "quality1234",
        Role.QUALITY,
    ),
    (
        "Suresh K.",
        "maintenance",
        "+919000000013",
        2,
        "+919000000013",
        "maint1234",
        Role.MAINTENANCE,
    ),
    ("Ramesh P.", "stores", "+919000000014", 2, "+919000000014", "stores1234", Role.STORES),
    (
        "Anitha D.",
        "plant_head",
        "+919000000015",
        3,
        "+919000000015",
        "plant1234",
        Role.SUPERVISOR,
    ),
]


async def seed() -> None:
    async with SessionFactory() as session:
        await _wipe(session)

        plant = await _plant(session)
        families = await _cost_families(session, plant)
        stations = await _stations(session, plant, families)
        parts = await _parts(session, plant, families)
        reasons = await _reason_codes(session, plant)
        await _defect_codes(session, plant)
        shifts = await _shifts(session, plant)
        operators = await _operators(session, plant)
        await _people(session, plant)
        await _targets(session, plant, stations, shifts)
        await session.commit()

        print(f"  plant .......... {plant.name}")
        print(f"  stations ....... {len(stations)}")
        print(f"  parts .......... {len(parts)}")
        print(f"  reason codes ... {len(reasons)}")
        print(f"  operators ...... {len(operators)}")

        counts = await _history(session, plant, stations, parts, reasons, shifts, operators)
        await session.commit()
        print(f"  shifts ......... {counts['shifts']}")
        print(f"  downtime ....... {counts['downtime']} events")
        print(f"  production ..... {counts['production']} entries")
        print(f"  quality ........ {counts['quality']} records")
        print(f"  actions ........ {counts['actions']}")
        print(f"  memory ......... {counts['memory']} entries")
        print(f"  escalations .... {counts['escalations']} sent")

    # Layer 5 runs through the real plugin services against the history just
    # generated, so Kaizen savings and training-need exposure are the figures
    # the product would actually compute.
    from scripts.seed_plugins import seed_plugins

    await seed_plugins()

    # Stage completions and despatch for every lot, plus §3's lot #4471 story.
    from scripts.seed_stages import seed_stages

    await seed_stages()


async def _wipe(session) -> None:
    """Truncate in dependency order. The demo plant is rebuilt from scratch."""
    for table in reversed(Base.metadata.sorted_tables):
        await session.execute(delete(table))
    await session.commit()


async def _plant(session) -> Plant:
    plant = Plant(
        name="Kumaran Auto Components",
        code="KUMARAN",
        location="Hosur, Tamil Nadu",
        costing_mode=CostingMode.STANDARD,
        # §5.2 defaults: >₹2,500 or >30 min, 3 occurrences in a rolling 7 shifts.
        action_cost_threshold=rupees(2500),
        action_duration_minutes=30,
        recurrence_count=3,
        recurrence_window_shifts=7,
        # §10 — announced by the owner at kickoff, enforced by the product.
        no_blame_until=TODAY + timedelta(days=30),
        go_live_date=TODAY - timedelta(weeks=WEEKS_OF_HISTORY),
        monthly_fee=rupees(25000),
    )
    session.add(plant)
    await session.flush()
    return plant


async def _cost_families(session, plant) -> dict[str, CostFamily]:
    families: dict[str, CostFamily] = {}
    effective = TODAY - timedelta(weeks=WEEKS_OF_HISTORY + 1)

    for code, name, machine in COST_FAMILIES:
        family = CostFamily(plant_id=plant.id, code=code, name=name)
        session.add(family)
        await session.flush()
        families[code] = family

        session.add(
            CostRate(
                plant_id=plant.id,
                scope="cost_family",
                scope_ref=str(family.id),
                machine_rate=rupees(machine),
                source=RateSource.DERIVED,
                derivation={
                    "note": "Reconstructed from the owner's depreciation, power "
                    "and maintenance figures during onboarding (§14.4 rung 2)."
                },
                effective_from=effective,
            )
        )

    # Labour is per section (§8.1 "per line/machine"); overhead is plant-wide.
    for section, labour in (
        ("machining", 180),
        ("assembly", 200),
        ("quality", 180),
        ("dispatch", 150),
    ):
        session.add(
            CostRate(
                plant_id=plant.id,
                scope="section",
                scope_ref=section,
                labour_rate=rupees(labour),
                source=RateSource.MEASURED,
                effective_from=effective,
            )
        )

    session.add(
        CostRate(
            plant_id=plant.id,
            scope="plant",
            scope_ref="plant",
            overhead_rate=rupees(210),
            source=RateSource.DERIVED,
            derivation={"note": "Total factory overhead ÷ available machine hours"},
            effective_from=effective,
        )
    )
    await session.flush()
    return families


async def _stations(session, plant, families) -> dict[str, Station]:
    stations: dict[str, Station] = {}
    for code, name, kind, family, section, line, bottleneck in STATIONS:
        station = Station(
            plant_id=plant.id,
            code=code,
            name=name,
            station_type=kind,
            section=section,
            line=line,
            zone=line or section.value,
            is_bottleneck=bottleneck,
            # §8.2 — BF 1.0 on the bottleneck, 0.4 where a downstream buffer
            # genuinely absorbs a stop. Set on the floor walk, not guessed.
            bottleneck_factor=Decimal("1.00") if bottleneck else Decimal("0.40"),
            cost_family_id=families[family].id,
        )
        session.add(station)
        stations[code] = station
    await session.flush()
    return stations


async def _parts(session, plant, families) -> dict[str, Part]:
    parts: dict[str, Part] = {}

    for family_name, low, medium, high in [("Brackets", 15, 35, 60)]:
        for band, value in (("L", low), ("M", medium), ("H", high)):
            session.add(
                MarginBandValue(
                    plant_id=plant.id,
                    part_family=family_name,
                    band=MarginBand(band),
                    value=rupees(value),
                )
            )

    for number, description, customer, family, margin, material, routing in PARTS:
        part = Part(
            plant_id=plant.id,
            part_number=number,
            description=description,
            customer_name=customer,
            part_family=family,
            contribution_margin=rupees(margin),
            material_cost=rupees(material),
        )
        session.add(part)
        await session.flush()
        parts[number] = part

        for position, (operation, family_code, cycle) in enumerate(routing, start=1):
            session.add(
                RoutingOperation(
                    plant_id=plant.id,
                    part_id=part.id,
                    sequence=position * 10,
                    operation_code=operation,
                    name=family_code,
                    # §14.2: the last operation is Dispatch, even though it is
                    # costed at the QC bench's rate. Marking it 'quality' made
                    # inspection and despatch indistinguishable in the routing.
                    section=(
                        Section.DISPATCH
                        if position == len(routing)
                        else _section_for(family_code)
                    ),
                    cost_family_id=families[family_code].id,
                    cycle_time_min=Decimal(cycle),
                    cycle_time_source=RateSource.MEASURED,
                )
            )
    await session.flush()
    return parts


def _section_for(family_code: str) -> Section:
    return {
        "CNC-family": Section.MACHINING,
        "VMC-family": Section.MACHINING,
        "PRESS-family": Section.MACHINING,
        "ASSY-family": Section.ASSEMBLY,
        "QC-family": Section.QUALITY,
    }[family_code]


async def _reason_codes(session, plant) -> dict[str, ReasonCode]:
    reasons: dict[str, ReasonCode] = {}
    for code, label, rs, oee, breakdown, route, safety, order in REASON_CODES:
        reason = ReasonCode(
            plant_id=plant.id,
            code=code,
            label=label,
            recoverability=Recoverability(rs),
            oee_treatment=OeeTreatment(oee),
            is_breakdown=breakdown,
            escalation_route=EscalationRoute(route),
            safety_flag=safety,
            display_order=order,
        )
        session.add(reason)
        reasons[code] = reason
    await session.flush()
    return reasons


async def _defect_codes(session, plant) -> None:
    for order, (code, label, family) in enumerate(DEFECT_CODES, start=1):
        session.add(
            DefectCode(
                plant_id=plant.id,
                code=code,
                label=label,
                part_family=family,
                display_order=order,
            )
        )
    await session.flush()


async def _shifts(session, plant) -> list[Shift]:
    shifts = [
        Shift(
            plant_id=plant.id,
            name="Shift A",
            start_time=time(7, 0),
            end_time=time(15, 30),
            break_minutes=45,
            weekly_off="Sun",
            display_order=1,
        ),
        Shift(
            plant_id=plant.id,
            name="Shift B",
            start_time=time(15, 30),
            end_time=time(0, 0),
            break_minutes=45,
            weekly_off="Sun",
            display_order=2,
        ),
    ]
    session.add_all(shifts)
    await session.flush()
    return shifts


async def _operators(session, plant) -> list[Operator]:
    operators = [
        Operator(plant_id=plant.id, name=name, employee_code=f"EMP{index:03d}")
        for index, name in enumerate(OPERATORS, start=1)
    ]
    session.add_all(operators)
    await session.flush()
    return operators


async def _people(session, plant) -> None:
    for name, role_tag, whatsapp, level, identifier, password, role in PEOPLE:
        user = User(
            full_name=name,
            email=identifier if "@" in identifier else None,
            phone=None if "@" in identifier else identifier,
            password_hash=hash_password(password),
            preferred_language="en",
        )
        session.add(user)
        await session.flush()

        session.add(UserPlantRole(user_id=user.id, plant_id=plant.id, role=role))
        session.add(
            AlertRecipient(
                plant_id=plant.id,
                name=name,
                role_tag=role_tag,
                whatsapp_number=whatsapp,
                escalation_level=level,
                user_id=user.id,
            )
        )
    await session.flush()


async def _targets(session, plant, stations, shifts) -> None:
    effective = TODAY - timedelta(weeks=WEEKS_OF_HISTORY + 1)
    for station in stations.values():
        if station.section in (Section.QUALITY, Section.DISPATCH):
            continue
        target = {"Line 1": 200, "Line 2": 190, "Line 3": 160}.get(station.line, 150)
        session.add(
            ProductionTarget(
                plant_id=plant.id,
                line=station.line,
                station_id=station.id,
                target_parts=target,
                effective_from=effective,
            )
        )
    await session.flush()


async def _history(session, plant, stations, parts, reasons, shifts, operators) -> dict:
    """Generate six weeks of plausible shifts, events and closures."""
    counts = {
        "shifts": 0,
        "downtime": 0,
        "production": 0,
        "quality": 0,
        "actions": 0,
        "memory": 0,
        "escalations": 0,
    }

    producing = [
        s
        for s in stations.values()
        if s.section == Section.MACHINING or s.section == Section.ASSEMBLY
    ]
    part_list = list(parts.values())
    reason_pool = [
        (reasons[code], low, high)
        for code, weight, low, high in EVENT_PROFILE
        for _ in range(weight)
    ]

    job_counter = 2200
    start_date = TODAY - timedelta(weeks=WEEKS_OF_HISTORY)
    snapshot_cache: dict[tuple, object] = {}

    async def snapshot_for(station: Station, part: Part, on_date: date):
        """Resolve once per (station, part) — rates are effective-dated and the
        demo spans a single rate period, so the cache is safe here."""
        key = (station.id, part.id)
        if key not in snapshot_cache:
            operation = await session.scalar(
                select(RoutingOperation)
                .where(
                    RoutingOperation.part_id == part.id,
                    RoutingOperation.cost_family_id == station.cost_family_id,
                )
                .limit(1)
            )
            resolved, _ = await resolve_snapshot(
                session,
                plant=plant,
                station=station,
                part=part,
                on_date=on_date,
                routing_operation=operation,
            )
            snapshot_cache[key] = resolved
        return snapshot_cache[key]

    current = start_date
    while current <= TODAY:
        if current.weekday() == 6:  # Sunday off
            current += timedelta(days=1)
            continue

        for shift in shifts:
            # Today's later shift hasn't happened yet — leave it open so the
            # demo has a live machine grid to look at.
            is_today = current == TODAY
            if is_today and shift.name == "Shift B":
                continue

            session_row = ShiftSession(
                plant_id=plant.id,
                shift_id=shift.id,
                business_date=current,
                started_at=datetime.combine(current, shift.start_time, tzinfo=IST),
                planned_minutes=8 * 60 + 30 - shift.break_minutes,
                status=ShiftSessionStatus.OPEN,
            )
            session.add(session_row)
            await session.flush()
            counts["shifts"] += 1

            shift_start = session_row.started_at

            # --- job orders and sessions ---------------------------------
            active: list[tuple[Station, JobSession, Part]] = []
            for station in producing:
                if random.random() < 0.12:  # a few machines idle each shift
                    continue
                part = random.choice(part_list)
                job_counter += 1

                job = JobOrder(
                    plant_id=plant.id,
                    job_number=str(job_counter),
                    part_id=part.id,
                    customer_name=part.customer_name,
                    planned_quantity=random.randint(200, 600),
                    status=JobOrderStatus.RUNNING,
                    due_date=current + timedelta(days=random.randint(1, 6)),
                    opened_at=shift_start,
                )
                session.add(job)
                await session.flush()

                lot = Lot(
                    plant_id=plant.id,
                    lot_code=f"{job_counter}-{current:%d%m%y}",
                    job_order_id=job.id,
                    granularity=LotGranularity.JOB_LOT,
                    # A lot's quantity is what physically entered the routing,
                    # set once production is known (seed_stages.py). The plan
                    # belongs to the job order, not the lot.
                    quantity=0,
                    opened_at=shift_start,
                )
                session.add(lot)
                await session.flush()

                operation = await session.scalar(
                    select(RoutingOperation)
                    .where(
                        RoutingOperation.part_id == part.id,
                        RoutingOperation.cost_family_id == station.cost_family_id,
                    )
                    .limit(1)
                )

                job_session = JobSession(
                    plant_id=plant.id,
                    job_order_id=job.id,
                    shift_session_id=session_row.id,
                    station_id=station.id,
                    lot_id=lot.id,
                    routing_operation_id=operation.id if operation else None,
                    operator_id=random.choice(operators).id,
                    started_at=shift_start,
                    ended_at=None if is_today else shift_start + timedelta(hours=8),
                )
                session.add(job_session)
                await session.flush()
                active.append((station, job_session, part))

            # --- downtime events -----------------------------------------
            for station, job_session, part in active:
                for _ in range(random.choices([0, 1, 2, 3], [52, 32, 12, 4])[0]):
                    reason, low, high = random.choice(reason_pool)
                    minutes = random.randint(low, high)
                    offset = random.randint(15, 440)
                    started = shift_start + timedelta(minutes=offset)

                    snapshot = await snapshot_for(station, part, current)

                    event = DowntimeEvent(
                        plant_id=plant.id,
                        station_id=station.id,
                        job_session_id=job_session.id,
                        shift_session_id=session_row.id,
                        business_date=current,
                        reason_code_id=reason.id,
                        started_at=started,
                        ended_at=started + timedelta(minutes=minutes),
                        duration_minutes=Decimal(minutes),
                        status=DowntimeStatus.CLOSED,
                    )
                    cost = downtime_event_cost(minutes, snapshot)
                    apply_snapshot(event, snapshot, cost)
                    session.add(event)
                    counts["downtime"] += 1

                # --- hourly production counts -------------------------------
                snapshot = await snapshot_for(station, part, current)
                cycle = snapshot.part.cycle_time_min
                for hour in range(8):
                    if cycle and cycle > 0:
                        nominal = int(Decimal(60) / cycle)
                        made = max(0, int(nominal * random.uniform(0.62, 0.93)))
                    else:
                        made = random.randint(18, 40)
                    session.add(
                        ProductionCount(
                            plant_id=plant.id,
                            station_id=station.id,
                            job_session_id=job_session.id,
                            shift_session_id=session_row.id,
                            business_date=current,
                            count=made,
                            recorded_at=shift_start + timedelta(hours=hour + 1),
                            covers_from=shift_start + timedelta(hours=hour),
                        )
                    )
                    counts["production"] += 1

                # --- quality records ----------------------------------------
                if random.random() < 0.55:
                    total = random.randint(120, 420)
                    rejected = max(0, int(total * random.uniform(0.004, 0.035)))
                    defects = _defect_mix(rejected)
                    record = QualityRecord(
                        plant_id=plant.id,
                        station_id=station.id,
                        job_session_id=job_session.id,
                        shift_session_id=session_row.id,
                        lot_id=job_session.lot_id,
                        part_id=part.id,
                        business_date=current,
                        good_count=total - rejected,
                        reject_count=rejected,
                        disposition=Disposition.SCRAP,
                        defects=defects,
                        recorded_at=shift_start + timedelta(hours=random.randint(3, 7)),
                    )
                    apply_snapshot(record, snapshot)
                    record.cost_total = (
                        snapshot.part.material_cost + snapshot.part.contribution_margin
                    ) * rejected
                    session.add(record)
                    counts["quality"] += 1

            if not is_today:
                session_row.status = ShiftSessionStatus.CLOSED
                closed_at = shift_start + timedelta(hours=8, minutes=30)
                session_row.ended_at = closed_at
                session_row.locked_at = closed_at

            await session.flush()

            if not is_today:
                created = await action_service.evaluate_shift_close(
                    session, plant=plant, shift_session=session_row
                )
                counts["actions"] += len(created)

        current += timedelta(days=1)

    counts["memory"] += await _close_early_actions(session, plant)
    counts["escalations"] = await _backfill_escalations(session, plant)
    return counts


def _defect_mix(rejected: int) -> list[dict]:
    if rejected <= 0:
        return []
    codes = random.sample([code for code, _, _ in DEFECT_CODES], k=min(2, rejected))
    split = [rejected // len(codes)] * len(codes)
    split[0] += rejected - sum(split)
    return [
        {"code": code, "count": count}
        for code, count in zip(codes, split, strict=True)
        if count
    ]


async def _backfill_escalations(session, plant) -> int:
    """Recreate the escalation trail for historical stops.

    The history generator writes downtime events directly rather than through
    `open_downtime`, because scheduling three future rungs for 719 events that
    closed weeks ago would be noise. But the audit trail is a product feature —

        "Every escalation is timestamped. Nobody can later say they didn't know."

    — and a demo with an empty escalation log cannot show it. So every stop that
    actually outran a rung gets the rungs it would have triggered, marked sent.
    """
    from app.kernel import ESCALATION_LADDER
    from app.models import Escalation, EscalationChannel, EscalationStatus

    events = (
        await session.scalars(
            select(DowntimeEvent)
            .where(
                DowntimeEvent.plant_id == plant.id,
                DowntimeEvent.duration_minutes > 5,
            )
            .order_by(DowntimeEvent.started_at.desc())
            .limit(120)
        )
    ).all()

    recipients = {
        r.role_tag: r
        for r in (
            await session.scalars(
                select(AlertRecipient).where(AlertRecipient.plant_id == plant.id)
            )
        ).all()
    }
    stations = {
        s.id: s
        for s in (
            await session.scalars(select(Station).where(Station.plant_id == plant.id))
        ).all()
    }
    reasons = {
        r.id: r
        for r in (
            await session.scalars(select(ReasonCode).where(ReasonCode.plant_id == plant.id))
        ).all()
    }

    created = 0
    for event in events:
        reason = reasons.get(event.reason_code_id)
        station = stations.get(event.station_id)
        if reason is None or station is None:
            continue
        # Structural stops route nowhere: nobody escalates a scheduled break.
        if reason.escalation_route is EscalationRoute.NONE:
            continue

        for level, delay in ESCALATION_LADDER:
            # Only the rungs the stop actually outlasted.
            if Decimal(delay.total_seconds() / 60) >= (event.duration_minutes or 0):
                break

            # The same rule the live ladder applies, imported rather than
            # restated: a seed that routes differently from production would
            # quietly invalidate every demo of the maintenance and stores desks.
            route = escalation_route_for(reason, int(level))
            recipient = None if route == "supervisor" else recipients.get(route)

            fired_at = event.started_at + delay
            elapsed = Decimal(delay.total_seconds()) / Decimal(60)
            # The running cost at the moment the rung fired — what made it urgent.
            burden = (
                event.snapshot_machine_rate
                + event.snapshot_labour_rate
                + event.snapshot_overhead_rate
            )
            cost_at_send = int(elapsed * Decimal(burden) / Decimal(60))

            session.add(
                Escalation(
                    plant_id=plant.id,
                    downtime_event_id=event.id,
                    level=int(level),
                    due_at=fired_at,
                    sent_at=fired_at,
                    channel=(
                        EscalationChannel.PUSH
                        if int(level) == 1
                        else EscalationChannel.WHATSAPP
                    ),
                    status=EscalationStatus.SENT,
                    route=route,
                    recipient_name=recipient.name if recipient else None,
                    recipient_number=recipient.whatsapp_number if recipient else None,
                    cost_at_send=cost_at_send,
                    message=escalation_message(
                        station_code=station.code,
                        reason_label=reason.label,
                        job_number=None,
                        minutes=int(elapsed),
                        cost=cost_at_send,
                    ),
                    acknowledged_at=fired_at + timedelta(minutes=random.randint(1, 8)),
                )
            )
            created += 1

    await session.flush()
    return created


async def _close_early_actions(session, plant) -> int:
    """Work through the backlog the way a real plant does.

    A plant six weeks in has not closed everything — but it has not ignored
    everything either. Leaving 145 actions open and 8 closed tells the wrong
    story: it shows a plant that installed software and changed nothing.

    So older actions are worked through at a realistic rate, closure dates are
    spread out, and the oldest ones have had long enough to verify — which is
    what puts real weeks behind the Recovered Profit ledger, the number an owner
    actually renews on.
    """
    actions = (
        await session.scalars(
            select(Action)
            .where(Action.plant_id == plant.id)
            .order_by(Action.linked_cost.desc())
        )
    ).all()

    fixes = [
        (
            "Replaced the carbide insert grade on Station 3",
            "Insert grade changed from P25 to P15",
            "Check insert wear at 240 parts, not 310",
            "Tool wear accelerating past the standard change interval",
        ),
        (
            "Moved material staging to the previous shift",
            "Line-feed kanban re-sequenced",
            "Confirm the trolley is staged before 06:45",
            "Upstream staging not completed before shift start",
        ),
        (
            "Re-cut the fixture locating pin",
            "Locating pin and clamp pad",
            "Check pin height with the gauge before first-off",
            "Fixture wear allowing part movement during cut",
        ),
        (
            "Corrected the coolant concentration",
            "Coolant mix ratio set to 8%",
            "Check refractometer reading at shift start",
            "Coolant diluted below spec, causing thermal drift",
        ),
        (
            "Pre-kitted the changeover tooling",
            "Changeover trolley and setting sheets",
            "Confirm the kit is at the machine before the previous job ends",
            "Tooling collected from stores during the changeover, not before it",
        ),
    ]

    owners = {
        "maintenance": "Suresh K.",
        "stores": "Ramesh P.",
        "production": "Murugan V.",
        "quality": "Kavitha R.",
    }

    closed = 0
    for index, action in enumerate(actions):
        age_days = (TODAY - (action.surfaced_in_meeting_on or TODAY)).days

        # Newer actions are still being worked; older ones have mostly landed.
        # Nothing younger than a week is closed — the plant does not fix
        # everything the morning after it appears.
        if age_days < 7:
            continue
        closure_rate = 0.85 if age_days > 28 else 0.6 if age_days > 14 else 0.35
        if random.random() > closure_rate:
            continue

        fix, replaced, check, root_cause = fixes[index % len(fixes)]
        reason = (
            await session.get(ReasonCode, action.reason_code_id)
            if action.reason_code_id
            else None
        )
        route = reason.escalation_route.value if reason else "production"
        owner = owners.get(route, "Murugan V.")

        # Closed a few days after it was raised, never the same day.
        closed_on = (action.surfaced_in_meeting_on or TODAY) + timedelta(
            days=random.randint(2, 6)
        )
        if closed_on >= TODAY:
            continue

        action.root_cause = root_cause
        action.countermeasure = fix
        action.owner_name = owner
        action.due_date = closed_on
        action.closed_at = datetime.combine(closed_on, time(16, 0), tzinfo=IST)
        action.state = PdcaState.MONITORING
        action.meeting_status = "done"

        session.add(
            MemoryEntry(
                plant_id=plant.id,
                action_id=action.id,
                station_id=action.station_id,
                reason_code_id=action.reason_code_id,
                what_fixed_it=fix,
                what_was_replaced=replaced,
                check_first_next_time=check,
                problem_summary=action.title,
                fixed_by_name=owner,
                occurred_on=closed_on,
            )
        )
        closed += 1

    # Give the rest an owner and a deadline: an action without a named person
    # and a date is an observation, and observations do not get done.
    for action in actions:
        if action.closed_at is None and action.owner_name is None:
            reason = (
                await session.get(ReasonCode, action.reason_code_id)
                if action.reason_code_id
                else None
            )
            route = reason.escalation_route.value if reason else "production"
            action.owner_name = owners.get(route, "Murugan V.")
            action.due_date = (action.surfaced_in_meeting_on or TODAY) + timedelta(
                days=random.randint(2, 7)
            )

    await session.flush()

    # Now run the real CHECK/ACT step over every closed action, so the
    # Recovered Profit ledger is produced by the same code the worker runs —
    # never hand-written into the demo.
    for action in actions:
        if action.closed_at is not None:
            await action_service.accrue_recovered_profit(
                session, plant=plant, action=action, as_of=TODAY
            )

    await session.flush()
    return closed


if __name__ == "__main__":
    print("Seeding FactoryManager AI demo plant...")
    asyncio.run(seed())
    print("Done.")
