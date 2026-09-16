"""Plant definitions — the master data the simulation runs on.

Four plants chosen to exercise every configuration axis the product has, rather
than four copies of one plant:

=========  ===========  ========  ============  ==========  ==================
Plant      Costing      Shifts    Lots          Live since  What it exercises
=========  ===========  ========  ============  ==========  ==================
KUMARAN    standard     2         job_lot       1 Jun       The spec's own worked example (§6); Coach Tier 3;
                                                            a mid-period rate correction (history never restates)
SRIVARI    basic        3 (C      shift_batch   15 Jun      Blended rates only; a 22:00-06:00 shift; rate source
                        crosses                             moves default -> derived; margin bands for some parts
                        midnight)
KOVAI      standard     2 (2nd    job_lot       20 Jul      High-value, long-cycle parts; Coach Tier 2
                        crosses
                        midnight)
SAKTHI     basic        2         day_batch     24 Aug      Brand-new plant: Coach Tier 1, no-blame window active,
                                                            WhatsApp not approved (SMS fallback), bands only
=========  ===========  ========  ============  ==========  ==================

Customer names for the three non-spec plants are fictional. KUMARAN keeps the
customers the existing ``scripts/seed.py`` uses so the demo stays recognisable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# ---------------------------------------------------------------------------
# Spec types
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# T6 — the default reason-code library (§15, §16.4), shared by every plant
#
# code, label, Tamil label, R/S, oee treatment, is_breakdown, route, safety, order
# Tamil labels are for the info-icon on Screen 3; have a native speaker review
# them before a plant goes live.
# ---------------------------------------------------------------------------

REASON_LIBRARY: list[tuple] = [
    (
        "TOOL_BREAKAGE",
        "Tool Breakage",
        "கருவி உடைப்பு",
        "R",
        "unplanned",
        False,
        "maintenance",
        False,
        1,
    ),
    (
        "MATERIAL_WAIT",
        "Material Wait",
        "பொருளுக்காக காத்திருப்பு",
        "R",
        "unplanned",
        False,
        "stores",
        False,
        2,
    ),
    (
        "SETUP_CHANGEOVER",
        "Setup / Changeover",
        "அமைப்பு / மாற்றம்",
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
        "கருவி மாற்றம் (திட்டமிட்டது)",
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
        "இயந்திர பழுது",
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
        "சிறு சரிசெய்தல்",
        "R",
        "unplanned",
        False,
        "production",
        False,
        6,
    ),
    (
        "NO_OPERATOR",
        "No Operator",
        "இயக்குநர் இல்லை",
        "R",
        "unplanned",
        False,
        "production",
        False,
        7,
    ),
    (
        "QUALITY_HOLD",
        "Quality Hold",
        "தரச் சோதனை நிறுத்தம்",
        "R",
        "unplanned",
        False,
        "quality",
        True,
        8,
    ),
    (
        "PROGRAM_ISSUE",
        "Program / Setting Issue",
        "நிரல் / அமைப்பு பிழை",
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
        "பிடிப்பான் / ஜிக் பிழை",
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
        "குளிரூட்டி / நுகர்பொருள்",
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
        "முந்தைய நிலைக்காக காத்திருப்பு",
        "R",
        "unplanned",
        False,
        "production",
        False,
        12,
    ),
    # Structural. POWER_CUT is Structural AND unplanned: it must still cut Availability.
    ("POWER_CUT", "Power Cut", "மின்வெட்டு", "S", "unplanned", False, "none", False, 13),
    (
        "PLANNED_MAINTENANCE",
        "Planned Maintenance",
        "திட்டமிட்ட பராமரிப்பு",
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
        "கூட்டம் / பயிற்சி",
        "S",
        "planned",
        False,
        "production",
        False,
        15,
    ),
    ("NO_ORDER", "No Order", "ஆர்டர் இல்லை", "S", "planned", False, "production", False, 16),
    (
        "BREAK",
        "Scheduled Break",
        "திட்டமிட்ட இடைவேளை",
        "S",
        "planned",
        False,
        "none",
        False,
        17,
    ),
    (
        "SHIFT_START_WARMUP",
        "Shift Start / Warm-up",
        "ஷிஃப்ட் தொடக்கம் / சூடாக்குதல்",
        "S",
        "planned",
        False,
        "production",
        False,
        18,
    ),
]

# Weighted draw of background (non-chronic) stops, with durations in minutes.
# Durations are deliberately short: only a handful per week should cross the
# Action threshold, or the plant drowns in action fatigue (§12 risk 10).
BASE_EVENT_PROFILE: list[tuple[str, float, int, int]] = [
    ("MATERIAL_WAIT", 20, 8, 28),
    ("MINOR_ADJUSTMENT", 22, 3, 10),
    ("SETUP_CHANGEOVER", 1.5, 10, 28),
    ("TOOL_CHANGE", 14, 5, 15),
    ("TOOL_BREAKAGE", 6, 10, 29),
    ("MACHINE_BREAKDOWN", 1.2, 25, 80),
    ("NO_OPERATOR", 2, 8, 25),
    ("PROGRAM_ISSUE", 4, 5, 20),
    ("QUALITY_HOLD", 3, 8, 26),
    ("COOLANT_ISSUE", 4, 4, 15),
    ("FIXTURE_ISSUE", 3, 6, 22),
    ("PLANNED_MAINTENANCE", 0.8, 20, 45),
    ("MEETING_TRAINING", 1, 10, 25),
]

DEFECT_TAMIL = {
    "BURR": "பிசிறு",
    "UNDERSIZE": "அளவு குறைவு",
    "OVERSIZE": "அளவு அதிகம்",
    "RUST_SPOT": "துருப் புள்ளி",
    "TOOL_MARK": "கருவித் தடம்",
    "SURFACE_FINISH": "மேற்பரப்பு குறைபாடு",
    "WELD_DISTORTION": "வெல்டிங் சிதைவு",
    "MISSING_OP": "செயல்பாடு விடுபட்டது",
    "THREAD_DAMAGE": "திரி சேதம்",
    "DENT": "நசுங்கல் / கையாளுதல் சேதம்",
    "CRACK": "விரிசல்",
    "WRINKLE": "சுருக்கம்",
    "SPLIT": "பிளவு",
    "HOLE_OFFSET": "துளை இட விலகல்",
    "WELD_SPATTER": "வெல்டிங் தெறிப்பு",
    "WELD_MISS": "வெல்டிங் விடுபட்டது",
    "FLATNESS": "தட்டைத்தன்மை விலகல்",
    "BORE_OVERSIZE": "துளை அளவு அதிகம்",
    "OD_UNDERSIZE": "வெளிவிட்டம் குறைவு",
    "RUNOUT": "சுழல் விலகல்",
    "BLOW_HOLE": "வார்ப்பு காற்றுத் துளை",
    "POSITION": "துளை நிலை பிழை",
    "HEAD_CRACK": "தலைப்பகுதி விரிசல்",
    "THREAD_LAP": "திரி மடிப்பு",
    "HARDNESS_LOW": "கடினத்தன்மை குறைவு",
    "HARDNESS_HIGH": "கடினத்தன்மை அதிகம்",
    "LENGTH": "நீளப் பிழை",
    "MIXED_PARTS": "கலப்பு பாகங்கள்",
    "FORGING_LAP": "அடிப்பு மடிப்பு",
    "UNDERFILL": "நிரம்பாமை",
}

# Given names common on Tamil Nadu shop floors; combined with an initial.
OPERATOR_NAMES = [
    "Kumar",
    "Ravi",
    "Selvam",
    "Anbu",
    "Dinesh",
    "Vignesh",
    "Prakash",
    "Manikandan",
    "Karthik",
    "Saravanan",
    "Arun",
    "Gopi",
    "Mohan",
    "Sathish",
    "Murali",
    "Bala",
    "Siva",
    "Raja",
    "Vel",
    "Kannan",
    "Suresh",
    "Ramesh",
    "Senthil",
    "Mani",
    "Prabhu",
    "Vijay",
    "Ajith",
    "Surya",
    "Naveen",
    "Hari",
    "Yuvaraj",
    "Lokesh",
    "Dhanush",
    "Kavin",
    "Tamil",
    "Iyappan",
    "Pandi",
    "Muthu",
    "Sekar",
    "Velu",
    "Durai",
    "Jeeva",
    "Sanjay",
    "Bharath",
    "Gowtham",
    "Madhan",
    "Ranjith",
    "Sundar",
    "Thiru",
    "Vinoth",
    "Revathi",
    "Malar",
    "Kalai",
    "Priya",
    "Divya",
    "Meena",
]
INITIALS = "ABDGJKLMNPRSTV"

# ---------------------------------------------------------------------------
# Plant 1 — Kumaran Auto Components, Hosur (the spec's worked example)
# ---------------------------------------------------------------------------

_KUMARAN_GO_LIVE = date(2026, 6, 1)

KUMARAN = PlantSpec(
    code="KUMARAN",
    name="Kumaran Auto Components",
    location="Hosur, Tamil Nadu",
    costing_mode="standard",
    go_live=_KUMARAN_GO_LIVE,
    lot_granularity="job_lot",
    email_domain="kumaranauto.in",
    families=[
        ("CNC-family", "CNC Turning", "5 CNC lathes, 8-10 years old"),
        ("VMC-family", "Vertical Machining", "VMCs and radial drills"),
        ("PRESS-family", "Presses up to 100T", "Mechanical presses 40-100T"),
        ("ASSY-family", "Assembly Stations", "Assembly benches and welding booth"),
        ("QC-family", "Inspection Bench", "Final inspection and dispatch desk"),
    ],
    rates=[
        RateSpec(
            "cost_family",
            "CNC-family",
            machine="420",
            note="Depreciation, power and maintenance from the owner's figures (§14.4 rung 2).",
        ),
        RateSpec("cost_family", "VMC-family", machine="380"),
        RateSpec("cost_family", "PRESS-family", machine="260"),
        RateSpec("cost_family", "ASSY-family", machine="90"),
        RateSpec("cost_family", "QC-family", machine="60"),
        RateSpec("section", "machining", labour="180", source="measured"),
        RateSpec("section", "assembly", labour="200", source="measured"),
        RateSpec("section", "quality", labour="180", source="measured"),
        RateSpec("section", "dispatch", labour="150", source="measured"),
        RateSpec(
            "plant",
            "plant",
            overhead="210",
            note="Total factory overhead / available machine hours",
        ),
        # Week 6 correction after a power-meter study. History must NOT restate:
        # events before 13 Jul keep the ₹420 snapshot.
        RateSpec(
            "cost_family",
            "CNC-family",
            machine="455",
            source="measured",
            effective_from=date(2026, 7, 13),
            note="Power meter readings on CNC-1..5 over 2 weeks raised the power component.",
        ),
        RateSpec(
            "plant",
            "plant",
            overhead="225",
            source="measured",
            effective_from=date(2026, 8, 3),
            note="Accountant session: Q1 overhead actuals.",
        ),
    ],
    stations=[
        StationSpec(
            "CNC-1", "CNC Turning 1", "CNC turning", "CNC-family", "machining", "Line 1"
        ),
        StationSpec(
            "CNC-2", "CNC Turning 2", "CNC turning", "CNC-family", "machining", "Line 1"
        ),
        StationSpec(
            "CNC-3",
            "CNC Turning 3",
            "CNC turning",
            "CNC-family",
            "machining",
            "Line 1",
            True,
        ),
        StationSpec(
            "CNC-4", "CNC Turning 4", "CNC turning", "CNC-family", "machining", "Line 2"
        ),
        StationSpec(
            "CNC-5", "CNC Turning 5", "CNC turning", "CNC-family", "machining", "Line 2"
        ),
        StationSpec("VMC-1", "VMC Milling 1", "VMC", "VMC-family", "machining", "Line 1"),
        StationSpec("VMC-2", "VMC Milling 2", "VMC", "VMC-family", "machining", "Line 2"),
        StationSpec("VMC-3", "VMC Milling 3", "VMC", "VMC-family", "machining", "Line 2"),
        StationSpec("Press-1", "Press 60T", "Press", "PRESS-family", "machining", "Line 2"),
        StationSpec(
            "Press-2", "Press 100T", "Press", "PRESS-family", "machining", "Line 2", True
        ),
        StationSpec("Press-3", "Press 40T", "Press", "PRESS-family", "machining", "Line 3"),
        StationSpec(
            "Drill-1", "Radial Drill 1", "Drilling", "VMC-family", "machining", "Line 3"
        ),
        StationSpec(
            "Drill-2", "Radial Drill 2", "Drilling", "VMC-family", "machining", "Line 3"
        ),
        StationSpec(
            "Assy-L1",
            "Assembly Line 1",
            "Assembly",
            "ASSY-family",
            "assembly",
            "Line 1",
            True,
        ),
        StationSpec(
            "Assy-L2", "Assembly Line 2", "Assembly", "ASSY-family", "assembly", "Line 2"
        ),
        StationSpec(
            "Weld-1", "Welding Booth 1", "Welding", "ASSY-family", "assembly", "Line 2"
        ),
        StationSpec("QC-1", "Inspection Bench", "Inspection", "QC-family", "quality", None),
        StationSpec("Dispatch", "Dispatch Desk", "Dispatch", "QC-family", "dispatch", None),
    ],
    parts=[
        PartSpec(
            "AK-447",
            "Bracket Assembly LH",
            "Rane TRW",
            "Brackets",
            "38",
            None,
            "72",
            (
                Op("Op10", "CNC-family", "2.4"),
                Op("Op20", "ASSY-family", "1.8"),
                Op("Op30", "QC-family", "0.5"),
                Op("Op40", "QC-family", "0.2"),
            ),
            9,
            (300, 900),
        ),
        PartSpec(
            "AK-448",
            "Bracket Assembly RH",
            "Rane TRW",
            "Brackets",
            "38",
            None,
            "72",
            (
                Op("Op10", "CNC-family", "2.4"),
                Op("Op20", "ASSY-family", "1.8"),
                Op("Op30", "QC-family", "0.5"),
                Op("Op40", "QC-family", "0.2"),
            ),
            8,
            (300, 900),
        ),
        PartSpec(
            "BX-102",
            "Pivot Housing",
            "Sundaram Fasteners",
            "Housings",
            "64",
            None,
            "118",
            (
                Op("Op10", "VMC-family", "3.6"),
                Op("Op20", "QC-family", "0.6"),
                Op("Op30", "QC-family", "0.2"),
            ),
            6,
            (200, 600),
        ),
        PartSpec(
            "BX-110",
            "Pivot Housing Mk2",
            "Sundaram Fasteners",
            "Housings",
            "71",
            None,
            "124",
            (
                Op("Op10", "VMC-family", "3.9"),
                Op("Op20", "QC-family", "0.6"),
                Op("Op30", "QC-family", "0.2"),
            ),
            5,
            (200, 600),
        ),
        PartSpec(
            "BX-205",
            "Gear Cover",
            "Sundaram Fasteners",
            "Housings",
            "88",
            None,
            "146",
            (
                Op("Op10", "VMC-family", "4.4"),
                Op("Op20", "QC-family", "0.7"),
                Op("Op30", "QC-family", "0.2"),
            ),
            3,
            (150, 400),
        ),
        PartSpec(
            "PR-220",
            "Stamped Cover",
            "Lucas TVS",
            "Pressings",
            "19",
            None,
            "26",
            (
                Op("Op10", "PRESS-family", "0.6"),
                Op("Op20", "QC-family", "0.3"),
                Op("Op30", "QC-family", "0.2"),
            ),
            7,
            (1500, 4000),
        ),
        PartSpec(
            "PR-221",
            "Stamped Bracket",
            "Lucas TVS",
            "Pressings",
            "22",
            None,
            "31",
            (
                Op("Op10", "PRESS-family", "0.7"),
                Op("Op20", "QC-family", "0.3"),
                Op("Op30", "QC-family", "0.2"),
            ),
            6,
            (1500, 4000),
        ),
        PartSpec(
            "AK-512",
            "Mounting Plate",
            "Rane TRW",
            "Brackets",
            None,
            "M",
            "41",
            (
                Op("Op10", "PRESS-family", "0.8"),
                Op("Op20", "VMC-family", "2.2"),
                Op("Op30", "QC-family", "0.4"),
                Op("Op40", "QC-family", "0.2"),
            ),
            4,
            (800, 2000),
        ),
        PartSpec(
            "WF-330",
            "Welded Frame",
            "Ashok Leyland",
            "Assemblies",
            "145",
            None,
            "268",
            (
                Op("Op10", "CNC-family", "2.1"),
                Op("Op20", "ASSY-family", "4.2"),
                Op("Op30", "QC-family", "0.8"),
                Op("Op40", "QC-family", "0.3"),
            ),
            5,
            (150, 450),
        ),
        PartSpec(
            "WF-335",
            "Seat Frame",
            "Ashok Leyland",
            "Assemblies",
            "162",
            None,
            "295",
            (
                Op("Op10", "CNC-family", "2.6"),
                Op("Op20", "ASSY-family", "5.0"),
                Op("Op30", "QC-family", "0.9"),
                Op("Op40", "QC-family", "0.3"),
            ),
            3,
            (120, 360),
        ),
        PartSpec(
            "SP-090",
            "Spacer Ring",
            "Rane TRW",
            "Fasteners",
            "9",
            None,
            "12",
            (
                Op("Op10", "CNC-family", "0.9"),
                Op("Op20", "QC-family", "0.2"),
                Op("Op30", "QC-family", "0.1"),
            ),
            5,
            (800, 2400),
        ),
        PartSpec(
            "SP-095",
            "Bush 12mm",
            "Lucas TVS",
            "Fasteners",
            None,
            "L",
            "10",
            (
                Op("Op10", "CNC-family", "0.8", source="default"),
                Op("Op20", "QC-family", "0.2"),
                Op("Op30", "QC-family", "0.1"),
            ),
            3,
            (800, 2400),
        ),
    ],
    margin_bands={"Brackets": ("15", "35", "60"), "Fasteners": ("6", "10", "16")},
    shifts=[
        ShiftSpec("Shift A", "07:00", "15:30", 45),
        ShiftSpec("Shift B", "15:30", "00:00", 45),
    ],
    people=[
        PersonSpec("Mr. Kumaran", "owner", "owner", "owner@kumaranauto.in", "owner1234", 3),
        PersonSpec(
            "Murugan V.",
            "production",
            "supervisor",
            "+919000000011",
            "shift1234",
            1,
            "ta",
            "Shift A",
        ),
        PersonSpec(
            "Kavitha R.",
            "quality",
            "quality",
            "+919000000012",
            "quality1234",
            2,
            "en",
            "Shift A",
        ),
        PersonSpec(
            "Suresh K.", "maintenance", "maintenance", "+919000000013", "maint1234", 2, "ta"
        ),
        PersonSpec("Ramesh P.", "stores", "stores", "+919000000014", "stores1234", 2, "ta"),
        PersonSpec(
            "Anitha D.", "plant_head", "supervisor", "+919000000015", "plant1234", 3
        ),
        PersonSpec(
            "Senthil Kumar R.",
            "production",
            "supervisor",
            "+919000000016",
            "shift1234",
            1,
            "ta",
            "Shift B",
        ),
        PersonSpec(
            "Priya S.",
            "quality",
            "quality",
            "+919000000017",
            "quality1234",
            2,
            "en",
            "Shift B",
        ),
    ],
    operator_prefix="KAC",
    operators_per_shift=15,
    chronic=[
        Chronic("CNC-3", "TOOL_BREAKAGE", 0.20, (18, 47)),
        Chronic("Press-2", "MATERIAL_WAIT", 0.28, (12, 35)),
        Chronic("VMC-2", "FIXTURE_ISSUE", 0.14, (10, 26)),
        Chronic("Assy-L1", "MATERIAL_WAIT", 0.18, (10, 28)),
        Chronic("CNC-5", "NO_OPERATOR", 0.12, (15, 40)),
        Chronic("Drill-2", "MACHINE_BREAKDOWN", 0.04, (40, 110)),
        Chronic("CNC-1", "COOLANT_ISSUE", 0.14, (6, 18)),
    ],
    extra_reasons=[],
    defects=[
        ("BURR", "Burr", "Brackets", "BURR"),
        ("UNDERSIZE", "Undersize", "Brackets", "UNDERSIZE"),
        ("OVERSIZE", "Oversize", "Brackets", "OVERSIZE"),
        ("RUST_SPOT", "Rust Spot", "Brackets", "RUST_SPOT"),
        ("TOOL_MARK", "Tool Mark", "Housings", "TOOL_MARK"),
        ("SURFACE_FINISH", "Surface Finish", "Housings", "SURFACE_FINISH"),
        ("WELD_DISTORTION", "Weld Distortion", "Assemblies", "WELD_DISTORTION"),
        ("MISSING_OP", "Missing Operation", "Assemblies", "MISSING_OP"),
        ("THREAD_DAMAGE", "Thread Damage", "Fasteners", "THREAD_DAMAGE"),
        ("CRACK", "Crack", "Pressings", "CRACK"),
        ("DENT", "Dent / Handling Damage", None, "DENT"),
    ],
    monthly_fee=25000,
    no_blame_days=30,
    kaizen_ideas=48,
)

# ---------------------------------------------------------------------------
# Plant 2 — Sri Vari Pressings, Ambattur (press shop, 3 shifts, Basic costing)
# ---------------------------------------------------------------------------

_SRIVARI_GO_LIVE = date(2026, 6, 15)

SRIVARI = PlantSpec(
    code="SRIVARI",
    name="Sri Vari Pressings Pvt Ltd",
    location="Ambattur Industrial Estate, Chennai, Tamil Nadu",
    costing_mode="basic",
    go_live=_SRIVARI_GO_LIVE,
    lot_granularity="shift_batch",
    email_domain="srivaripressings.in",
    families=[
        ("PRESS-S", "Mechanical Presses up to 80T", "40T / 63T / 80T C-frame presses"),
        ("PRESS-L", "Presses 150-250T", "Straight-side presses with coil feeders"),
        ("WELD", "Spot / Projection Welding", "Resistance welding"),
        ("TAP", "Tapping & Drilling", "Secondary operations"),
        ("ASSY", "Sub-assembly", "Manual sub-assembly benches"),
        ("QC", "Inspection & Dispatch", "Final inspection bench and dispatch bay"),
    ],
    # Basic mode: one blended ₹/hr per machine family, labour and overhead included.
    # Day 1 used the pre-loaded library (±30%); three weeks in, the owner's wage
    # and electricity bills replaced it (rung 2, ±15-20%).
    rates=[
        RateSpec(
            "cost_family",
            "PRESS-S",
            machine="520",
            source="default",
            note="Pre-loaded library value for C-frame presses.",
        ),
        RateSpec("cost_family", "PRESS-L", machine="780", source="default"),
        RateSpec("cost_family", "WELD", machine="310", source="default"),
        RateSpec("cost_family", "TAP", machine="240", source="default"),
        RateSpec("cost_family", "ASSY", machine="190", source="default"),
        RateSpec("cost_family", "QC", machine="150", source="default"),
        RateSpec(
            "cost_family",
            "PRESS-S",
            machine="560",
            source="derived",
            effective_from=date(2026, 7, 6),
            note="Rebuilt from June wage register and TNEB bill.",
        ),
        RateSpec(
            "cost_family",
            "PRESS-L",
            machine="820",
            source="derived",
            effective_from=date(2026, 7, 6),
        ),
        RateSpec(
            "cost_family",
            "WELD",
            machine="300",
            source="derived",
            effective_from=date(2026, 7, 6),
        ),
        RateSpec(
            "cost_family",
            "TAP",
            machine="230",
            source="derived",
            effective_from=date(2026, 7, 6),
        ),
    ],
    stations=[
        StationSpec(
            "PRS-40-1", "Press 40T #1", "Press", "PRESS-S", "machining", "Press Shop"
        ),
        StationSpec(
            "PRS-40-2", "Press 40T #2", "Press", "PRESS-S", "machining", "Press Shop"
        ),
        StationSpec("PRS-63-1", "Press 63T", "Press", "PRESS-S", "machining", "Press Shop"),
        StationSpec(
            "PRS-80-1", "Press 80T #1", "Press", "PRESS-S", "machining", "Press Shop"
        ),
        StationSpec(
            "PRS-80-2", "Press 80T #2", "Press", "PRESS-S", "machining", "Press Shop"
        ),
        StationSpec(
            "PRS-150-1",
            "Press 150T (coil fed)",
            "Press",
            "PRESS-L",
            "machining",
            "Press Shop",
            True,
        ),
        StationSpec(
            "PRS-250-1",
            "Press 250T (coil fed)",
            "Press",
            "PRESS-L",
            "machining",
            "Press Shop",
            True,
        ),
        StationSpec(
            "SPW-1", "Spot Welder 1", "Spot welding", "WELD", "assembly", "Weld Shop"
        ),
        StationSpec(
            "SPW-2", "Spot Welder 2", "Spot welding", "WELD", "assembly", "Weld Shop"
        ),
        StationSpec(
            "PJW-1",
            "Projection Welder",
            "Projection welding",
            "WELD",
            "assembly",
            "Weld Shop",
            False,
            "0.60",
        ),
        StationSpec(
            "TAP-1", "Tapping Machine 1", "Tapping", "TAP", "machining", "Secondary Ops"
        ),
        StationSpec(
            "TAP-2", "Tapping Machine 2", "Tapping", "TAP", "machining", "Secondary Ops"
        ),
        StationSpec(
            "ASSY-1", "Sub-assembly Bench", "Assembly", "ASSY", "assembly", "Assembly"
        ),
        StationSpec("QC-1", "Final Inspection", "Inspection", "QC", "quality", None),
        StationSpec("DSP-1", "Dispatch Bay", "Dispatch", "QC", "dispatch", None),
    ],
    parts=[
        PartSpec(
            "SV-1101",
            "Seat Mounting Bracket",
            "Kaveri Seating Systems",
            "Brackets",
            "14",
            None,
            "22",
            (
                Op("Op10", "PRESS-S", "0.5"),
                Op("Op20", "TAP", "0.4"),
                Op("Op30", "QC", "0.15"),
                Op("Op40", "QC", "0.05"),
            ),
            8,
            (3000, 8000),
        ),
        PartSpec(
            "SV-1102",
            "Floor Pan Reinforcement",
            "Ennore Motors",
            "Reinforcements",
            "31",
            None,
            "58",
            (
                Op("Op10", "PRESS-L", "0.9"),
                Op("Op20", "WELD", "1.2"),
                Op("Op30", "QC", "0.2"),
                Op("Op40", "QC", "0.05"),
            ),
            6,
            (1500, 4000),
        ),
        PartSpec(
            "SV-2040",
            "Exhaust Heat Shield",
            "Ennore Motors",
            "Shields",
            None,
            "M",
            "44",
            (
                Op("Op10", "PRESS-L", "1.1"),
                Op("Op20", "QC", "0.2"),
                Op("Op30", "QC", "0.05"),
            ),
            5,
            (1200, 3500),
        ),
        PartSpec(
            "SV-2210",
            "Battery Tray",
            "Velan Two-Wheelers",
            "Trays",
            "26",
            None,
            "49",
            (
                Op("Op10", "PRESS-L", "1.4"),
                Op("Op20", "WELD", "1.6"),
                Op("Op30", "QC", "0.2"),
                Op("Op40", "QC", "0.05"),
            ),
            4,
            (1000, 3000),
        ),
        PartSpec(
            "SV-3300",
            "Brake Pedal Bracket",
            "Chakra Axles",
            "Pedal Brackets",
            "42",
            None,
            "61",
            (
                Op("Op10", "PRESS-S", "0.7"),
                Op("Op20", "WELD", "2.0"),
                Op("Op30", "ASSY", "1.5"),
                Op("Op40", "QC", "0.25"),
                Op("Op50", "QC", "0.05"),
            ),
            4,
            (800, 2400),
        ),
        PartSpec(
            "SV-3310",
            "Clutch Pedal Bracket",
            "Chakra Axles",
            "Pedal Brackets",
            "40",
            None,
            "59",
            (
                Op("Op10", "PRESS-S", "0.7"),
                Op("Op20", "WELD", "2.0"),
                Op("Op30", "ASSY", "1.5"),
                Op("Op40", "QC", "0.25"),
                Op("Op50", "QC", "0.05"),
            ),
            3,
            (800, 2400),
        ),
        PartSpec(
            "SV-4100",
            "Hinge Plate",
            "Kaveri Seating Systems",
            "Plates",
            None,
            "L",
            "9",
            (
                Op("Op10", "PRESS-S", "0.3"),
                Op("Op20", "QC", "0.1"),
                Op("Op30", "QC", "0.03"),
            ),
            6,
            (5000, 12000),
        ),
        PartSpec(
            "SV-4120",
            "Washer Plate M10",
            "Marutham Tractors",
            "Plates",
            None,
            "L",
            "4",
            (
                Op("Op10", "PRESS-S", "0.2"),
                Op("Op20", "QC", "0.05"),
                Op("Op30", "QC", "0.02"),
            ),
            5,
            (8000, 20000),
        ),
        PartSpec(
            "SV-5010",
            "Mudguard Stay",
            "Velan Two-Wheelers",
            "Brackets",
            "11",
            None,
            "18",
            (
                Op("Op10", "PRESS-S", "0.6"),
                Op("Op20", "TAP", "0.5"),
                Op("Op30", "QC", "0.15"),
                Op("Op40", "QC", "0.05"),
            ),
            5,
            (3000, 7000),
        ),
    ],
    margin_bands={"Shields": ("18", "30", "45"), "Plates": ("3", "6", "9")},
    shifts=[
        ShiftSpec("Shift A", "06:00", "14:00", 30),
        ShiftSpec("Shift B", "14:00", "22:00", 30),
        ShiftSpec("Shift C", "22:00", "06:00", 30),
    ],
    people=[
        PersonSpec(
            "Mr. Venkatesan S.",
            "owner",
            "owner",
            "owner@srivaripressings.in",
            "owner1234",
            3,
        ),
        PersonSpec(
            "Lakshmi Narayanan K.",
            "plant_head",
            "supervisor",
            "+919100000101",
            "plant1234",
            3,
        ),
        PersonSpec(
            "Palanisamy R.",
            "production",
            "supervisor",
            "+919100000102",
            "shift1234",
            1,
            "ta",
            "Shift A",
        ),
        PersonSpec(
            "Chandrasekar M.",
            "production",
            "supervisor",
            "+919100000103",
            "shift1234",
            1,
            "ta",
            "Shift B",
        ),
        PersonSpec(
            "Thangaraj V.",
            "production",
            "supervisor",
            "+919100000104",
            "shift1234",
            1,
            "ta",
            "Shift C",
        ),
        PersonSpec(
            "Revathi M.",
            "quality",
            "quality",
            "+919100000105",
            "quality1234",
            2,
            "en",
            "Shift A",
        ),
        PersonSpec(
            "Karpagam S.",
            "quality",
            "quality",
            "+919100000106",
            "quality1234",
            2,
            "ta",
            "Shift B",
        ),
        PersonSpec(
            "Balamurugan T.",
            "maintenance",
            "maintenance",
            "+919100000107",
            "maint1234",
            2,
            "ta",
        ),
        PersonSpec(
            "Jayakumar P.", "stores", "stores", "+919100000108", "stores1234", 2, "ta"
        ),
    ],
    operator_prefix="SVP",
    operators_per_shift=12,
    chronic=[
        Chronic("PRS-250-1", "DIE_CRACK", 0.04, (45, 150)),
        Chronic("PRS-150-1", "COIL_CHANGE", 0.22, (15, 40)),
        Chronic("PRS-80-2", "MATERIAL_WAIT", 0.26, (10, 32)),
        Chronic("TAP-2", "TOOL_BREAKAGE", 0.16, (10, 30)),
        Chronic("SPW-1", "QUALITY_HOLD", 0.08, (15, 45)),
        Chronic("PRS-40-1", "DIE_CHANGE", 0.18, (20, 48)),
    ],
    extra_reasons=[
        (
            "DIE_CHANGE",
            "Die Change",
            "டை மாற்றம்",
            "R",
            "unplanned",
            False,
            "production",
            False,
            19,
        ),
        (
            "COIL_CHANGE",
            "Coil / Strip Change",
            "சுருள் / பட்டை மாற்றம்",
            "R",
            "unplanned",
            False,
            "stores",
            False,
            20,
        ),
        (
            "DIE_CRACK",
            "Die Crack / Punch Breakage",
            "டை விரிசல் / பஞ்ச் உடைப்பு",
            "R",
            "unplanned",
            True,
            "maintenance",
            False,
            21,
        ),
    ],
    defects=[
        ("CRACK", "Crack", None, "CRACK"),
        ("WRINKLE", "Wrinkle", "Shields", "WRINKLE"),
        ("SPLIT", "Split", "Trays", "SPLIT"),
        ("BURR", "Burr", "Brackets", "BURR"),
        ("HOLE_OFFSET", "Hole Offset", "Brackets", "HOLE_OFFSET"),
        ("WELD_SPATTER", "Weld Spatter", "Pedal Brackets", "WELD_SPATTER"),
        ("WELD_MISS", "Missed Weld Nugget", "Reinforcements", "WELD_MISS"),
        ("FLATNESS", "Out of Flatness", "Plates", "FLATNESS"),
        ("THREAD_DAMAGE", "Thread Damage", "Brackets", "THREAD_DAMAGE"),
        ("RUST_SPOT", "Rust Spot", None, "RUST_SPOT"),
        ("DENT", "Dent / Handling Damage", None, "DENT"),
    ],
    action_cost_threshold=3000,
    action_duration_minutes=45,
    tolerance_parts=10,
    tolerance_bp=150,
    no_blame_days=45,
    monthly_fee=30000,
    event_weights={
        "DIE_CHANGE": 6,
        "COIL_CHANGE": 4,
        "COOLANT_ISSUE": 1,
        "PROGRAM_ISSUE": 1,
    },
    kaizen_ideas=40,
)

# ---------------------------------------------------------------------------
# Plant 3 — Kovai Precision Engineering, Coimbatore (pump & valve machining)
# ---------------------------------------------------------------------------

KOVAI = PlantSpec(
    code="KOVAI",
    name="Kovai Precision Engineering",
    location="SIDCO Industrial Estate, Kurichi, Coimbatore, Tamil Nadu",
    costing_mode="standard",
    go_live=date(2026, 7, 20),
    lot_granularity="job_lot",
    email_domain="kovaiprecision.in",
    families=[
        ("CNC-T", "CNC Turning Centres", "LMW / Ace turning centres"),
        ("VMC", "Vertical Machining Centres", "3-axis VMCs"),
        ("HMC", "Horizontal Machining Centre", "4-axis HMC with pallet changer"),
        ("GRIND", "Cylindrical Grinding", "Plain and centreless grinding"),
        ("FIN", "Deburr & Finishing", "Manual deburr and wash"),
        ("QC", "CMM & Inspection", "CMM room and dispatch"),
    ],
    rates=[
        RateSpec("cost_family", "CNC-T", machine="480", source="measured"),
        RateSpec("cost_family", "VMC", machine="520", source="measured"),
        RateSpec(
            "cost_family",
            "HMC",
            machine="890",
            source="measured",
            note="₹1.1Cr machine, 10-year life, 2-shift loading.",
        ),
        RateSpec("cost_family", "GRIND", machine="340", source="measured"),
        RateSpec("cost_family", "FIN", machine="110", source="derived"),
        RateSpec(
            "cost_family",
            "QC",
            machine="260",
            source="derived",
            note="CMM depreciation dominates.",
        ),
        RateSpec("section", "machining", labour="210", source="measured"),
        RateSpec("section", "assembly", labour="190", source="measured"),
        RateSpec("section", "quality", labour="230", source="measured"),
        RateSpec("section", "dispatch", labour="160", source="measured"),
        RateSpec("plant", "plant", overhead="260", source="derived"),
    ],
    stations=[
        StationSpec(
            "CNC-T1",
            "Turning Centre 1",
            "CNC turning",
            "CNC-T",
            "machining",
            "Cell A",
            False,
            "0.50",
        ),
        StationSpec(
            "CNC-T2",
            "Turning Centre 2",
            "CNC turning",
            "CNC-T",
            "machining",
            "Cell A",
            False,
            "0.50",
        ),
        StationSpec(
            "CNC-T3",
            "Turning Centre 3",
            "CNC turning",
            "CNC-T",
            "machining",
            "Cell B",
            False,
            "0.45",
        ),
        StationSpec(
            "CNC-T4",
            "Turning Centre 4",
            "CNC turning",
            "CNC-T",
            "machining",
            "Cell B",
            False,
            "0.45",
        ),
        StationSpec(
            "VMC-01", "VMC 850", "VMC", "VMC", "machining", "Cell A", False, "0.60"
        ),
        StationSpec(
            "VMC-02", "VMC 850", "VMC", "VMC", "machining", "Cell B", False, "0.60"
        ),
        StationSpec(
            "VMC-03", "VMC 1060", "VMC", "VMC", "machining", "Cell B", False, "0.60"
        ),
        StationSpec(
            "HMC-01", "HMC 500 (4-axis)", "HMC", "HMC", "machining", "Cell B", True
        ),
        StationSpec(
            "GRD-01",
            "Cylindrical Grinder 1",
            "Grinding",
            "GRIND",
            "machining",
            "Cell C",
            False,
            "0.40",
        ),
        StationSpec(
            "GRD-02",
            "Cylindrical Grinder 2",
            "Grinding",
            "GRIND",
            "machining",
            "Cell C",
            False,
            "0.40",
        ),
        StationSpec(
            "FIN-01",
            "Deburr & Wash Bench",
            "Finishing",
            "FIN",
            "assembly",
            "Cell C",
            False,
            "0.30",
        ),
        StationSpec("CMM-01", "CMM Room", "Inspection", "QC", "quality", None),
        StationSpec("DSP-01", "Packing & Dispatch", "Dispatch", "QC", "dispatch", None),
    ],
    parts=[
        PartSpec(
            "KP-VB-2001",
            "Valve Body 2 inch",
            "Nilgiri Flow Controls",
            "Valve Bodies",
            "640",
            None,
            "1150",
            (
                Op("Op10", "CNC-T", "6.5"),
                Op("Op20", "HMC", "11.0"),
                Op("Op30", "FIN", "3.0"),
                Op("Op40", "QC", "4.0"),
                Op("Op50", "QC", "0.5"),
            ),
            5,
            (60, 200),
        ),
        PartSpec(
            "KP-VB-3001",
            "Valve Body 3 inch",
            "Nilgiri Flow Controls",
            "Valve Bodies",
            "910",
            None,
            "1720",
            (
                Op("Op10", "CNC-T", "8.2"),
                Op("Op20", "HMC", "14.5"),
                Op("Op30", "FIN", "3.5"),
                Op("Op40", "QC", "5.0"),
                Op("Op50", "QC", "0.5"),
            ),
            3,
            (40, 150),
        ),
        PartSpec(
            "KP-IH-150",
            "Impeller Housing 150",
            "Aravind Pumps",
            "Housings",
            "520",
            None,
            "980",
            (
                Op("Op10", "VMC", "9.5"),
                Op("Op20", "FIN", "2.5"),
                Op("Op30", "QC", "3.0"),
                Op("Op40", "QC", "0.5"),
            ),
            5,
            (80, 240),
        ),
        PartSpec(
            "KP-SH-080",
            "Pump Shaft 80mm",
            "Aravind Pumps",
            "Shafts",
            "210",
            None,
            "340",
            (
                Op("Op10", "CNC-T", "4.2"),
                Op("Op20", "GRIND", "3.8"),
                Op("Op30", "QC", "1.5"),
                Op("Op40", "QC", "0.3"),
            ),
            6,
            (150, 400),
        ),
        PartSpec(
            "KP-SH-110",
            "Pump Shaft 110mm",
            "Aravind Pumps",
            "Shafts",
            "260",
            None,
            "450",
            (
                Op("Op10", "CNC-T", "5.0"),
                Op("Op20", "GRIND", "4.5"),
                Op("Op30", "QC", "1.8"),
                Op("Op40", "QC", "0.3"),
            ),
            4,
            (120, 320),
        ),
        PartSpec(
            "KP-FL-100",
            "Flange DN100",
            "Deccan Hydraulics",
            "Flanges",
            "180",
            None,
            "310",
            (
                Op("Op10", "CNC-T", "3.2"),
                Op("Op20", "VMC", "2.8"),
                Op("Op30", "QC", "1.0"),
                Op("Op40", "QC", "0.3"),
            ),
            6,
            (200, 500),
        ),
        PartSpec(
            "KP-CV-025",
            "Check Valve Seat",
            "Deccan Hydraulics",
            "Seats",
            "95",
            None,
            "120",
            (
                Op("Op10", "CNC-T", "2.1"),
                Op("Op20", "GRIND", "2.4", source="default"),
                Op("Op30", "QC", "1.2"),
                Op("Op40", "QC", "0.2"),
            ),
            4,
            (300, 800),
        ),
        PartSpec(
            "KP-MP-040",
            "Manifold Plate",
            "Nilgiri Flow Controls",
            "Plates",
            None,
            "H",
            "1400",
            (
                Op("Op10", "VMC", "12.0"),
                Op("Op20", "HMC", "8.0"),
                Op("Op30", "QC", "4.5"),
                Op("Op40", "QC", "0.5"),
            ),
            2,
            (40, 120),
        ),
    ],
    margin_bands={"Plates": ("300", "520", "800")},
    shifts=[
        ShiftSpec("Shift 1", "08:00", "16:30", 30),
        ShiftSpec("Shift 2", "16:30", "01:00", 30),
    ],
    people=[
        PersonSpec(
            "Ms. Deepa Ramasamy",
            "owner",
            "owner",
            "owner@kovaiprecision.in",
            "owner1234",
            3,
        ),
        PersonSpec(
            "Karthikeyan V.", "plant_head", "supervisor", "+919200000201", "plant1234", 3
        ),
        PersonSpec(
            "Sivakumar N.",
            "production",
            "supervisor",
            "+919200000202",
            "shift1234",
            1,
            "en",
            "Shift 1",
        ),
        PersonSpec(
            "Rajesh Babu M.",
            "production",
            "supervisor",
            "+919200000203",
            "shift1234",
            1,
            "ta",
            "Shift 2",
        ),
        PersonSpec(
            "Nandhini P.",
            "quality",
            "quality",
            "+919200000204",
            "quality1234",
            2,
            "en",
            "Shift 1",
        ),
        PersonSpec(
            "Gunasekaran A.",
            "maintenance",
            "maintenance",
            "+919200000205",
            "maint1234",
            2,
            "ta",
        ),
        PersonSpec(
            "Manoj Kumar S.", "stores", "stores", "+919200000206", "stores1234", 2, "en"
        ),
    ],
    operator_prefix="KPE",
    operators_per_shift=11,
    chronic=[
        Chronic("HMC-01", "MACHINE_BREAKDOWN", 0.06, (45, 160)),
        Chronic("CNC-T2", "PROGRAM_ISSUE", 0.15, (10, 35)),
        Chronic("GRD-02", "QUALITY_HOLD", 0.10, (15, 40)),
        Chronic("VMC-03", "TOOL_BREAKAGE", 0.16, (12, 38)),
        Chronic("CNC-T4", "NO_OPERATOR", 0.14, (20, 55)),
        Chronic("FIN-01", "CMM_QUEUE", 0.20, (15, 45)),
    ],
    extra_reasons=[
        (
            "CMM_QUEUE",
            "Waiting for CMM Inspection",
            "CMM ஆய்வுக்காக காத்திருப்பு",
            "R",
            "unplanned",
            False,
            "quality",
            False,
            19,
        ),
    ],
    defects=[
        ("BORE_OVERSIZE", "Bore Oversize", "Valve Bodies", "BORE_OVERSIZE"),
        ("OD_UNDERSIZE", "OD Undersize", "Shafts", "OD_UNDERSIZE"),
        ("RUNOUT", "Runout Out of Tolerance", "Shafts", "RUNOUT"),
        ("THREAD_DAMAGE", "Thread Damage", "Flanges", "THREAD_DAMAGE"),
        ("BLOW_HOLE", "Casting Blow Hole", "Valve Bodies", "BLOW_HOLE"),
        ("SURFACE_FINISH", "Surface Finish", None, "SURFACE_FINISH"),
        ("BURR", "Burr", None, "BURR"),
        ("TOOL_MARK", "Tool Mark", "Housings", "TOOL_MARK"),
        ("POSITION", "Hole Position Error", "Plates", "POSITION"),
        ("DENT", "Dent / Handling Damage", None, "DENT"),
    ],
    action_cost_threshold=5000,
    action_duration_minutes=30,
    tolerance_parts=3,
    tolerance_bp=200,
    no_blame_days=30,
    monthly_fee=35000,
    events_per_shift=(48, 34, 13, 5),
    kaizen_ideas=26,
)

# ---------------------------------------------------------------------------
# Plant 4 — Sakthi Fasteners & Forgings, Sriperumbudur (three weeks live)
# ---------------------------------------------------------------------------

SAKTHI = PlantSpec(
    code="SAKTHI",
    name="Sakthi Fasteners & Forgings",
    location="SIPCOT Sriperumbudur, Kanchipuram, Tamil Nadu",
    costing_mode="basic",
    go_live=date(2026, 8, 24),
    lot_granularity="day_batch",
    email_domain="sakthifasteners.in",
    families=[
        ("HEAD", "Cold Heading", "Multi-station cold headers and nut formers"),
        ("ROLL", "Thread Rolling", "Flat-die thread rollers"),
        ("FORGE", "Hot Forging Press", "400T forging press with induction heater"),
        ("HT", "Heat Treatment", "Continuous mesh-belt hardening furnace"),
        ("SORT", "Sorting & Dispatch", "Optical sorter and packing"),
    ],
    rates=[
        RateSpec("cost_family", "HEAD", machine="460", source="default"),
        RateSpec("cost_family", "ROLL", machine="260", source="default"),
        RateSpec("cost_family", "FORGE", machine="740", source="default"),
        RateSpec(
            "cost_family",
            "HT",
            machine="520",
            source="default",
            note="Furnace runs on LPG; power cut forces a restart cycle.",
        ),
        RateSpec("cost_family", "SORT", machine="120", source="default"),
    ],
    stations=[
        StationSpec(
            "HDR-1",
            "Cold Header 1 (4-die)",
            "Cold heading",
            "HEAD",
            "machining",
            "Bolt Line",
        ),
        StationSpec(
            "HDR-2",
            "Cold Header 2 (5-die)",
            "Cold heading",
            "HEAD",
            "machining",
            "Bolt Line",
        ),
        StationSpec("HDR-3", "Nut Former", "Nut forming", "HEAD", "machining", "Nut Line"),
        StationSpec(
            "THR-1", "Thread Roller 1", "Thread rolling", "ROLL", "machining", "Bolt Line"
        ),
        StationSpec(
            "THR-2", "Thread Roller 2", "Thread rolling", "ROLL", "machining", "Bolt Line"
        ),
        StationSpec(
            "FRG-1",
            "Forging Press 400T",
            "Hot forging",
            "FORGE",
            "machining",
            "Forge Line",
            True,
        ),
        StationSpec(
            "HT-1",
            "Mesh Belt Furnace",
            "Heat treatment",
            "HT",
            "machining",
            "Heat Treatment",
            True,
        ),
        StationSpec("SORT-1", "Optical Sorter", "Sorting", "SORT", "quality", None),
        StationSpec("DSP-1", "Packing & Dispatch", "Dispatch", "SORT", "dispatch", None),
    ],
    parts=[
        PartSpec(
            "SF-HB-0835",
            "Hex Bolt M8x35",
            "Chakra Axles",
            "Bolts",
            None,
            "M",
            "2.40",
            (
                Op("Op10", "HEAD", "0.05"),
                Op("Op20", "ROLL", "0.04"),
                Op("Op30", "HT", "0.02"),
                Op("Op40", "SORT", "0.01"),
                Op("Op50", "SORT", "0.005"),
            ),
            7,
            (40000, 90000),
        ),
        PartSpec(
            "SF-FB-1050",
            "Flange Bolt M10x50",
            "Ennore Motors",
            "Bolts",
            None,
            "H",
            "5.10",
            (
                Op("Op10", "HEAD", "0.07"),
                Op("Op20", "ROLL", "0.05"),
                Op("Op30", "HT", "0.03"),
                Op("Op40", "SORT", "0.01"),
                Op("Op50", "SORT", "0.005"),
            ),
            5,
            (30000, 70000),
        ),
        PartSpec(
            "SF-WS-1280",
            "Wheel Stud M12x80",
            "Marutham Tractors",
            "Studs",
            None,
            "H",
            "9.80",
            (
                Op("Op10", "HEAD", "0.09"),
                Op("Op20", "ROLL", "0.06"),
                Op("Op30", "HT", "0.03"),
                Op("Op40", "SORT", "0.01"),
                Op("Op50", "SORT", "0.005"),
            ),
            4,
            (20000, 50000),
        ),
        PartSpec(
            "SF-HN-M10",
            "Hex Nut M10",
            "Chakra Axles",
            "Nuts",
            None,
            "L",
            "1.60",
            (
                Op("Op10", "HEAD", "0.04"),
                Op("Op20", "HT", "0.02"),
                Op("Op30", "SORT", "0.01"),
                Op("Op40", "SORT", "0.005"),
            ),
            6,
            (50000, 120000),
        ),
        PartSpec(
            "SF-CP-0640",
            "Clevis Pin 6x40",
            "Velan Two-Wheelers",
            "Pins",
            None,
            "M",
            "1.90",
            (
                Op("Op10", "HEAD", "0.05"),
                Op("Op20", "HT", "0.02"),
                Op("Op30", "SORT", "0.01"),
                Op("Op40", "SORT", "0.005"),
            ),
            3,
            (30000, 80000),
        ),
        PartSpec(
            "SF-FY-220",
            "Forged Yoke 22mm",
            "Marutham Tractors",
            "Forgings",
            None,
            "H",
            "118",
            (
                Op("Op10", "FORGE", "0.8"),
                Op("Op20", "HT", "0.3"),
                Op("Op30", "SORT", "0.2"),
                Op("Op40", "SORT", "0.05"),
            ),
            5,
            (1500, 4000),
        ),
    ],
    margin_bands={
        "Bolts": ("0.90", "1.60", "2.80"),
        "Nuts": ("0.50", "0.90", "1.40"),
        "Studs": ("2.00", "3.50", "5.00"),
        "Pins": ("0.60", "1.10", "1.80"),
        "Forgings": ("25", "42", "65"),
    },
    shifts=[
        ShiftSpec("Shift I", "06:00", "14:30", 30),
        ShiftSpec("Shift II", "14:30", "23:00", 30),
    ],
    people=[
        PersonSpec(
            "Mr. Sakthivel P.",
            "owner",
            "owner",
            "owner@sakthifasteners.in",
            "owner1234",
            3,
            "ta",
        ),
        PersonSpec(
            "Muthukumar R.",
            "plant_head",
            "supervisor",
            "+919300000301",
            "plant1234",
            3,
            "ta",
        ),
        PersonSpec(
            "Ganesan K.",
            "production",
            "supervisor",
            "+919300000302",
            "shift1234",
            1,
            "ta",
            "Shift I",
        ),
        PersonSpec(
            "Arumugam S.",
            "production",
            "supervisor",
            "+919300000303",
            "shift1234",
            1,
            "ta",
            "Shift II",
        ),
        PersonSpec(
            "Sangeetha R.",
            "quality",
            "quality",
            "+919300000304",
            "quality1234",
            2,
            "en",
            "Shift I",
        ),
        PersonSpec(
            "Elango V.", "maintenance", "maintenance", "+919300000305", "maint1234", 2, "ta"
        ),
        PersonSpec(
            "Pandian M.", "stores", "stores", "+919300000306", "stores1234", 2, "ta"
        ),
    ],
    operator_prefix="SFF",
    operators_per_shift=8,
    chronic=[
        Chronic("HDR-1", "TOOL_BREAKAGE", 0.22, (15, 40)),
        Chronic("THR-2", "SETUP_CHANGEOVER", 0.26, (20, 45)),
        Chronic("FRG-1", "MACHINE_BREAKDOWN", 0.07, (40, 120)),
        Chronic("HDR-2", "MATERIAL_WAIT", 0.24, (12, 35)),
        Chronic("HT-1", "HT_LOAD_WAIT", 0.20, (15, 40)),
    ],
    extra_reasons=[
        (
            "DIE_CHANGE",
            "Die / Punch Change",
            "டை / பஞ்ச் மாற்றம்",
            "R",
            "unplanned",
            False,
            "production",
            False,
            19,
        ),
        (
            "HT_LOAD_WAIT",
            "Waiting for Furnace Load",
            "உலை ஏற்றத்துக்காக காத்திருப்பு",
            "R",
            "unplanned",
            False,
            "production",
            False,
            20,
        ),
    ],
    defects=[
        ("HEAD_CRACK", "Head Crack", "Bolts", "HEAD_CRACK"),
        ("THREAD_LAP", "Thread Lap", "Bolts", "THREAD_LAP"),
        ("THREAD_DAMAGE", "Thread Damage", "Studs", "THREAD_DAMAGE"),
        ("HARDNESS_LOW", "Hardness Low", None, "HARDNESS_LOW"),
        ("HARDNESS_HIGH", "Hardness High", None, "HARDNESS_HIGH"),
        ("LENGTH", "Length Out of Tolerance", "Bolts", "LENGTH"),
        ("MIXED_PARTS", "Mixed Parts", None, "MIXED_PARTS"),
        ("FORGING_LAP", "Forging Lap", "Forgings", "FORGING_LAP"),
        ("UNDERFILL", "Underfill", "Forgings", "UNDERFILL"),
        ("RUST_SPOT", "Rust Spot", None, "RUST_SPOT"),
    ],
    action_cost_threshold=2500,
    action_duration_minutes=30,
    tolerance_parts=200,
    tolerance_bp=100,
    no_blame_days=60,
    monthly_fee=20000,
    whatsapp_approved=False,
    event_weights={
        "DIE_CHANGE": 5,
        "COOLANT_ISSUE": 2,
        "PROGRAM_ISSUE": 0.5,
        "FIXTURE_ISSUE": 1,
    },
    kaizen_ideas=14,
)

PLANTS: list[PlantSpec] = [KUMARAN, SRIVARI, KOVAI, SAKTHI]

# Platform-level accounts that span plants (§1: "the same consultant account
# supports several plants").
PLATFORM_PEOPLE = [
    ("Arjun Mehta", "consultant@factorymanager.ai", "consult1234", "consultant"),
    ("Platform Admin", "admin@factorymanager.ai", "admin1234", "admin"),
]
