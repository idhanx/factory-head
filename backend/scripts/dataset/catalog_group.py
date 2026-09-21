"""Five more plants, chosen for the configurations the first four do not have.

The original four (:mod:`catalog`) cover the spec's worked example and the
costing/shift/lot axes. These five widen the data along the axes a real
customer base varies on — process type, plant size, and how long the plant has
been live — so that a screen, a rule or a report can be tested against more
than one shape of factory:

==========  ===========  ========  =============  ==========  ==================
Plant       Costing      Shifts    Lots           Live since  What it adds
==========  ===========  ========  =============  ==========  ==================
VELAVAN     standard     2         job_lot        6 Apr       Hot forging + machining under one roof; the
                                                              group's second-largest plant, and the only
                                                              one with a furnace on the critical path
ANNAI       basic        3         day_batch      18 May      Injection moulding: no machining at all,
                                                              cavity-level rejection, 24h running
NILGIRI     standard     2         shift_batch    15 Jun      Foundry + machining, castings bought in
                                                              and finished; highest scrap value per part
THENI       basic        2         shift_batch    13 Jul      Sheet-metal fabrication, smallest plant in
                                                              the set, WhatsApp not approved
VAIGAI      standard     3         job_lot        10 Aug      High-volume electrical assembly; hundreds
                                                              of small parts an hour, tiny margins
==========  ===========  ========  =============  ==========  ==================

Every company, customer, person and phone number here is fictional.
"""

from __future__ import annotations

from datetime import date

from .specs import (
    Chronic,
    Op,
    PartSpec,
    PersonSpec,
    PlantSpec,
    RateSpec,
    ShiftSpec,
    StationSpec,
)

# ---------------------------------------------------------------------------
# Reason codes these processes need that the default T6 library does not carry
# ---------------------------------------------------------------------------

FURNACE_REASONS = [
    ("FURNACE_HEAT", "Furnace Heat-up / Soak", "உலை சூடாக்கம்", "S", "planned", False, "production", False, 19),
    ("BILLET_WAIT", "Billet / Bar Wait", "பில்லட் காத்திருப்பு", "R", "unplanned", False, "stores", False, 20),
    ("DIE_PREHEAT", "Die Pre-heat", "டை முன் சூடாக்கம்", "R", "unplanned", False, "production", False, 21),
    ("SCALE_CLEAN", "Scale / Descaling Stop", "செதில் நீக்கம்", "R", "unplanned", False, "maintenance", False, 22),
]

MOULDING_REASONS = [
    ("MOULD_CHANGE", "Mould Change", "அச்சு மாற்றம்", "R", "unplanned", False, "production", False, 19),
    ("MATERIAL_DRY", "Material Drying", "பொருள் உலர்த்துதல்", "S", "planned", False, "stores", False, 20),
    ("SHORT_SHOT", "Short Shot / Process Setting", "குறை ஊசி", "R", "unplanned", False, "production", False, 21),
    ("HOT_RUNNER", "Hot Runner Fault", "ஹாட் ரன்னர் கோளாறு", "R", "unplanned", True, "maintenance", False, 22),
    ("CHILLER_DOWN", "Chiller / Cooling Down", "குளிரூட்டி பழுது", "R", "unplanned", True, "maintenance", False, 23),
]

FOUNDRY_REASONS = [
    ("CASTING_WAIT", "Casting Supply Wait", "வார்ப்பு காத்திருப்பு", "R", "unplanned", False, "stores", False, 19),
    ("SHOT_BLAST", "Shot Blast Queue", "ஷாட் பிளாஸ்ட் வரிசை", "R", "unplanned", False, "production", False, 20),
    ("LEAK_TEST_FAIL", "Leak Test Re-run", "கசிவு சோதனை மீள்", "R", "unplanned", False, "quality", False, 21),
]

SHEET_REASONS = [
    ("DIE_CHANGE", "Die Change", "டை மாற்றம்", "R", "unplanned", False, "production", False, 19),
    ("SHEET_WAIT", "Sheet / Blank Wait", "தகடு காத்திருப்பு", "R", "unplanned", False, "stores", False, 20),
    ("WELD_GAS", "Welding Gas / Wire Change", "வெல்டிங் வாயு மாற்றம்", "R", "unplanned", False, "stores", False, 21),
    ("BEND_SETUP", "Bend Program Setup", "வளைவு நிரல் அமைப்பு", "R", "unplanned", False, "production", False, 22),
]

ASSEMBLY_REASONS = [
    ("COMPONENT_WAIT", "Component / Kit Wait", "உதிரிபாகம் காத்திருப்பு", "R", "unplanned", False, "stores", False, 19),
    ("TEST_RIG_DOWN", "Test Rig Down", "சோதனை அமைப்பு பழுது", "R", "unplanned", True, "maintenance", False, 20),
    ("LINE_BALANCE", "Line Balancing Stop", "வரிசை சமநிலை நிறுத்தம்", "R", "unplanned", False, "production", False, 21),
    ("SOLDER_POT", "Solder Pot / Crimp Setup", "சாலிடர் அமைப்பு", "R", "unplanned", False, "production", False, 22),
]


# ---------------------------------------------------------------------------
# Plant 5 — Velavan Forge & Machining, Ranipet (forging + machining, standard)
# ---------------------------------------------------------------------------

VELAVAN = PlantSpec(
    code="VELAVAN",
    name="Velavan Forge & Machining Pvt Ltd",
    location="SIPCOT Ranipet, Vellore, Tamil Nadu",
    costing_mode="standard",
    go_live=date(2026, 4, 6),
    lot_granularity="job_lot",
    email_domain="velavanforge.in",
    families=[
        ("FORGE-family", "Forging Press", "1000T and 630T presses with induction heaters"),
        ("HT-family", "Heat Treatment", "Sealed quench furnace and tempering line"),
        ("CNC-family", "CNC Turning", "8 CNC lathes on the finishing line"),
        ("HMC-family", "Horizontal Machining", "HMC cells for flange faces"),
        ("FIN-family", "Deburr & Wash", "Deburr benches and parts washer"),
        ("QC-family", "Inspection & Dispatch", "CMM, hardness testers, dispatch bay"),
    ],
    rates=[
        RateSpec(
            "cost_family",
            "FORGE-family",
            machine="1240",
            note="Induction power dominates: 480 kW connected load measured over two weeks (§14.4 rung 2).",
        ),
        RateSpec("cost_family", "HT-family", machine="860"),
        RateSpec("cost_family", "CNC-family", machine="470"),
        RateSpec("cost_family", "HMC-family", machine="610"),
        RateSpec("cost_family", "FIN-family", machine="140"),
        RateSpec("cost_family", "QC-family", machine="95"),
        RateSpec("section", "machining", labour="210", source="measured"),
        RateSpec("section", "assembly", labour="195", source="measured"),
        RateSpec("section", "quality", labour="230", source="measured"),
        RateSpec("section", "dispatch", labour="165", source="measured"),
        RateSpec(
            "plant",
            "plant",
            overhead="260",
            note="Total factory overhead / available machine hours, from the FY26 trial balance.",
        ),
        # Two corrections in the plant's first months. History never restates:
        # a forging event in April keeps the ₹1,240 snapshot for ever.
        RateSpec(
            "cost_family",
            "FORGE-family",
            machine="1385",
            source="measured",
            effective_from=date(2026, 6, 8),
            note="TANGEDCO tariff revision plus a corrected billet-scale allowance.",
        ),
        RateSpec(
            "section",
            "machining",
            labour="228",
            source="measured",
            effective_from=date(2026, 7, 1),
            note="Annual wage settlement effective 1 July.",
        ),
    ],
    stations=[
        StationSpec("FP-1000", "Forging Press 1000T", "Forging press", "FORGE-family", "machining", "Forge Line", True),
        StationSpec("FP-630", "Forging Press 630T", "Forging press", "FORGE-family", "machining", "Forge Line"),
        StationSpec("TRIM-1", "Trim Press 250T", "Trim press", "FORGE-family", "machining", "Forge Line"),
        StationSpec("HT-1", "Sealed Quench Furnace", "Furnace", "HT-family", "machining", "Heat Treat", True),
        StationSpec("HT-2", "Tempering Furnace", "Furnace", "HT-family", "machining", "Heat Treat"),
        StationSpec("SB-1", "Shot Blast Cabinet", "Shot blast", "HT-family", "machining", "Heat Treat"),
        StationSpec("CNC-11", "CNC Lathe 1", "CNC turning", "CNC-family", "machining", "Finish Line 1"),
        StationSpec("CNC-12", "CNC Lathe 2", "CNC turning", "CNC-family", "machining", "Finish Line 1"),
        StationSpec("CNC-13", "CNC Lathe 3", "CNC turning", "CNC-family", "machining", "Finish Line 1"),
        StationSpec("CNC-14", "CNC Lathe 4", "CNC turning", "CNC-family", "machining", "Finish Line 2"),
        StationSpec("CNC-15", "CNC Lathe 5", "CNC turning", "CNC-family", "machining", "Finish Line 2"),
        StationSpec("HMC-1", "HMC Cell 1", "HMC", "HMC-family", "machining", "Finish Line 2", True),
        StationSpec("HMC-2", "HMC Cell 2", "HMC", "HMC-family", "machining", "Finish Line 2"),
        StationSpec("DEBURR", "Deburr & Wash", "Deburring", "FIN-family", "assembly", "Finish Line 2"),
        StationSpec("CMM-1", "CMM / Hardness Bay", "Inspection", "QC-family", "quality", None),
        StationSpec("DESP-V", "Dispatch Bay", "Dispatch", "QC-family", "dispatch", None),
    ],
    parts=[
        PartSpec(
            "VF-7100", "Front Axle Beam Forging", "Ashok Leyland", "Axle", "412", None, "684",
            (Op("Op10", "FORGE-family", "1.6"), Op("Op20", "HT-family", "2.2"),
             Op("Op30", "HMC-family", "6.4"), Op("Op40", "FIN-family", "1.1"),
             Op("Op50", "QC-family", "1.2"), Op("Op60", "QC-family", "0.4")),
            8, (120, 380),
        ),
        PartSpec(
            "VF-7115", "Steering Knuckle LH", "Rane Madras", "Knuckle", "268", None, "402",
            (Op("Op10", "FORGE-family", "1.1"), Op("Op20", "HT-family", "1.8"),
             Op("Op30", "CNC-family", "3.8"), Op("Op40", "HMC-family", "4.1"),
             Op("Op50", "QC-family", "0.9")),
            9, (150, 460),
        ),
        PartSpec(
            "VF-7116", "Steering Knuckle RH", "Rane Madras", "Knuckle", "268", None, "402",
            (Op("Op10", "FORGE-family", "1.1"), Op("Op20", "HT-family", "1.8"),
             Op("Op30", "CNC-family", "3.8"), Op("Op40", "HMC-family", "4.1"),
             Op("Op50", "QC-family", "0.9")),
            8, (150, 460),
        ),
        PartSpec(
            "VF-7240", "Crown Wheel Blank", "Sundaram Fasteners", "Gear Blank", "196", None, "318",
            (Op("Op10", "FORGE-family", "0.9"), Op("Op20", "HT-family", "1.6"),
             Op("Op30", "CNC-family", "3.2"), Op("Op40", "QC-family", "0.6")),
            7, (200, 700),
        ),
        PartSpec(
            "VF-7245", "Pinion Blank", "Sundaram Fasteners", "Gear Blank", "148", None, "242",
            (Op("Op10", "FORGE-family", "0.7"), Op("Op20", "HT-family", "1.4"),
             Op("Op30", "CNC-family", "2.8"), Op("Op40", "QC-family", "0.5")),
            7, (250, 800),
        ),
        PartSpec(
            "VF-7310", "Connecting Rod Forging", "Simpson & Co", "ConRod", "121", None, "186",
            (Op("Op10", "FORGE-family", "0.6"), Op("Op20", "HT-family", "1.2"),
             Op("Op30", "CNC-family", "2.1"), Op("Op40", "QC-family", "0.4")),
            10, (400, 1200),
        ),
        PartSpec(
            "VF-7420", "Hub Flange", "TAFE", "Flange", None, "H", "268",
            (Op("Op10", "FORGE-family", "0.8"), Op("Op20", "HT-family", "1.5"),
             Op("Op30", "HMC-family", "5.2"), Op("Op40", "FIN-family", "0.8"),
             Op("Op50", "QC-family", "0.7"), Op("Op60", "QC-family", "0.3")),
            6, (180, 520),
        ),
        PartSpec(
            "VF-7430", "Brake Anchor Bracket", "Brakes India", "Bracket", "86", None, "132",
            (Op("Op10", "FORGE-family", "0.5"), Op("Op20", "CNC-family", "1.9"),
             Op("Op30", "QC-family", "0.4")),
            8, (500, 1600),
        ),
        PartSpec(
            "VF-7510", "Tie Rod End Body", "Rane Madras", "Linkage", None, "M", "94",
            (Op("Op10", "FORGE-family", "0.4"), Op("Op20", "HT-family", "1.1"),
             Op("Op30", "CNC-family", "1.7"), Op("Op40", "QC-family", "0.3")),
            9, (600, 2000),
        ),
        PartSpec(
            "VF-7620", "Yoke Forging", "Ashok Leyland", "Linkage", "158", None, "244",
            (Op("Op10", "FORGE-family", "0.8"), Op("Op20", "HT-family", "1.5"),
             Op("Op30", "HMC-family", "3.6"), Op("Op40", "FIN-family", "0.7"),
             Op("Op50", "QC-family", "0.6"), Op("Op60", "QC-family", "0.3")),
            5, (200, 620),
        ),
    ],
    margin_bands={"Flange": ("120", "210", "340"), "Linkage": ("40", "78", "130")},
    shifts=[
        ShiftSpec("Shift A", "06:30", "15:00", 45),
        ShiftSpec("Shift B", "15:00", "23:30", 45),
    ],
    people=[
        PersonSpec("Mr. R. Velavan", "owner", "owner", "owner@velavanforge.in", "owner1234", 3),
        PersonSpec("Jagadeesan M.", "plant_head", "supervisor", "+919400000401", "plant1234", 3),
        PersonSpec("Alagappan S.", "production", "supervisor", "+919400000402", "shift1234", 1, "ta", "Shift A"),
        PersonSpec("Nagarajan K.", "production", "supervisor", "+919400000403", "shift1234", 1, "ta", "Shift B"),
        PersonSpec("Bhuvaneswari R.", "quality", "quality", "+919400000404", "quality1234", 2, "en", "Shift A"),
        PersonSpec("Chitra M.", "quality", "quality", "+919400000405", "quality1234", 2, "en", "Shift B"),
        PersonSpec("Perumal T.", "maintenance", "maintenance", "+919400000406", "maint1234", 2, "ta"),
        PersonSpec("Dhanasekaran V.", "maintenance", "maintenance", "+919400000407", "maint1234", 2, "ta"),
        PersonSpec("Kaliyaperumal S.", "stores", "stores", "+919400000408", "stores1234", 2, "ta"),
    ],
    operator_prefix="VFM",
    operators_per_shift=22,
    chronic=[
        Chronic("FP-1000", "DIE_PREHEAT", 0.26, (16, 42)),
        Chronic("HT-1", "FURNACE_HEAT", 0.22, (25, 70)),
        Chronic("FP-630", "BILLET_WAIT", 0.20, (12, 38)),
        Chronic("HMC-1", "TOOL_BREAKAGE", 0.17, (15, 44)),
        Chronic("CNC-13", "COOLANT_ISSUE", 0.15, (6, 20)),
        Chronic("TRIM-1", "MACHINE_BREAKDOWN", 0.05, (45, 130)),
        Chronic("SB-1", "SCALE_CLEAN", 0.13, (10, 26)),
        Chronic("CNC-15", "NO_OPERATOR", 0.11, (14, 38)),
    ],
    extra_reasons=FURNACE_REASONS,
    defects=[
        ("FORGING_LAP", "Forging Lap", "Knuckle", "FORGING_LAP"),
        ("UNDERFILL", "Underfill", "Axle", "UNDERFILL"),
        ("CRACK", "Forging Crack", "Axle", "CRACK"),
        ("HARDNESS_LOW", "Hardness Low", "Gear Blank", "HARDNESS_LOW"),
        ("HARDNESS_HIGH", "Hardness High", "Gear Blank", "HARDNESS_HIGH"),
        ("OVERSIZE", "Oversize", "Flange", "OVERSIZE"),
        ("UNDERSIZE", "Undersize", "Flange", "UNDERSIZE"),
        ("RUNOUT", "Runout Out of Tolerance", "ConRod", "RUNOUT"),
        ("SURFACE_FINISH", "Surface Finish", "Linkage", "SURFACE_FINISH"),
        ("TOOL_MARK", "Tool Mark", "Linkage", "TOOL_MARK"),
        ("DENT", "Dent / Handling Damage", None, "DENT"),
        ("MIXED_PARTS", "Mixed Parts", None, "MIXED_PARTS"),
    ],
    action_cost_threshold=4000,
    action_duration_minutes=30,
    recurrence_count=3,
    tolerance_parts=10,
    tolerance_bp=150,
    no_blame_days=30,
    monthly_fee=45000,
    event_weights={"BILLET_WAIT": 6, "DIE_PREHEAT": 5, "SCALE_CLEAN": 3, "FURNACE_HEAT": 2},
    kaizen_ideas=96,
)


# ---------------------------------------------------------------------------
# Plant 6 — Annai Polymers, Sriperumbudur (injection moulding, 3 shifts, basic)
# ---------------------------------------------------------------------------

ANNAI = PlantSpec(
    code="ANNAI",
    name="Annai Polymers & Precision Moulding",
    location="Sriperumbudur, Kanchipuram, Tamil Nadu",
    costing_mode="basic",
    go_live=date(2026, 5, 18),
    lot_granularity="day_batch",
    email_domain="annaipolymers.in",
    families=[
        ("IM-LARGE", "Injection Moulding 250-450T", "Large tonnage moulding machines"),
        ("IM-SMALL", "Injection Moulding 80-150T", "Small tonnage moulding machines"),
        ("FIN-family", "Finishing & Deflash", "Deflash, ultrasonic welding, pad printing"),
        ("ASSY-family", "Sub-assembly", "Manual assembly and packing benches"),
        ("QC-family", "Inspection", "Dimensional and functional inspection"),
    ],
    rates=[
        # Basic costing: the plant has no measured split, so the library
        # defaults carry it until the accountant session in July.
        RateSpec("cost_family", "IM-LARGE", machine="520", source="default"),
        RateSpec("cost_family", "IM-SMALL", machine="310", source="default"),
        RateSpec("cost_family", "FIN-family", machine="120", source="default"),
        RateSpec("cost_family", "ASSY-family", machine="80", source="default"),
        RateSpec("cost_family", "QC-family", machine="55", source="default"),
        RateSpec("section", "machining", labour="165", source="default"),
        RateSpec("section", "assembly", labour="150", source="default"),
        RateSpec("section", "quality", labour="170", source="default"),
        RateSpec("section", "dispatch", labour="140", source="default"),
        RateSpec("plant", "plant", overhead="185", source="default"),
        # §14.4 rung 2 — the owner sits down with the accountant and the
        # library rates are replaced by the plant's own numbers.
        RateSpec(
            "cost_family", "IM-LARGE", machine="588", source="derived",
            effective_from=date(2026, 7, 6),
            note="Connected load x tariff / running hours, from the July energy audit.",
        ),
        RateSpec(
            "cost_family", "IM-SMALL", machine="342", source="derived",
            effective_from=date(2026, 7, 6),
        ),
        RateSpec(
            "plant", "plant", overhead="206", source="derived",
            effective_from=date(2026, 7, 6),
            note="Accountant session: Q1 overhead actuals over available machine hours.",
        ),
    ],
    stations=[
        StationSpec("IMM-450", "Moulding 450T", "Injection moulding", "IM-LARGE", "machining", "Moulding A", True),
        StationSpec("IMM-350", "Moulding 350T", "Injection moulding", "IM-LARGE", "machining", "Moulding A"),
        StationSpec("IMM-250", "Moulding 250T", "Injection moulding", "IM-LARGE", "machining", "Moulding A"),
        StationSpec("IMM-150", "Moulding 150T", "Injection moulding", "IM-SMALL", "machining", "Moulding B"),
        StationSpec("IMM-120", "Moulding 120T", "Injection moulding", "IM-SMALL", "machining", "Moulding B"),
        StationSpec("IMM-80A", "Moulding 80T A", "Injection moulding", "IM-SMALL", "machining", "Moulding B"),
        StationSpec("IMM-80B", "Moulding 80T B", "Injection moulding", "IM-SMALL", "machining", "Moulding B"),
        StationSpec("DEFLASH", "Deflash Bench", "Finishing", "FIN-family", "assembly", "Finishing"),
        StationSpec("USW-1", "Ultrasonic Welder", "Welding", "FIN-family", "assembly", "Finishing", True),
        StationSpec("PAD-1", "Pad Printing", "Printing", "FIN-family", "assembly", "Finishing"),
        StationSpec("ASSY-A", "Assembly Bench A", "Assembly", "ASSY-family", "assembly", "Finishing"),
        StationSpec("QC-A", "Inspection Bench", "Inspection", "QC-family", "quality", None),
        StationSpec("DESP-A", "Packing & Dispatch", "Dispatch", "QC-family", "dispatch", None),
    ],
    parts=[
        PartSpec(
            "AP-2010", "Fan Shroud Moulding", "TVS Motor", "Shrouds", None, "H", "162",
            (Op("Op10", "IM-LARGE", "1.9"), Op("Op20", "FIN-family", "0.7"),
             Op("Op30", "QC-family", "0.3")),
            9, (600, 1800),
        ),
        PartSpec(
            "AP-2011", "Radiator Top Tank", "TVS Motor", "Shrouds", None, "H", "188",
            (Op("Op10", "IM-LARGE", "2.2"), Op("Op20", "FIN-family", "0.8"),
             Op("Op30", "QC-family", "0.3")),
            7, (500, 1500),
        ),
        PartSpec(
            "AP-2120", "Battery Housing Lower", "Amara Raja", "Housings", "74", None, "121",
            (Op("Op10", "IM-LARGE", "1.6"), Op("Op20", "FIN-family", "0.6"),
             Op("Op30", "ASSY-family", "0.9"), Op("Op40", "QC-family", "0.3")),
            8, (700, 2100),
        ),
        PartSpec(
            "AP-2121", "Battery Housing Cover", "Amara Raja", "Housings", "58", None, "96",
            (Op("Op10", "IM-LARGE", "1.3"), Op("Op20", "FIN-family", "0.5"),
             Op("Op30", "QC-family", "0.2")),
            8, (700, 2100),
        ),
        PartSpec(
            "AP-2240", "Switch Bezel", "Lucas TVS", "Trim", "14", None, "19",
            (Op("Op10", "IM-SMALL", "0.4"), Op("Op20", "FIN-family", "0.2"),
             Op("Op30", "FIN-family", "0.3"), Op("Op40", "QC-family", "0.1")),
            10, (3000, 9000),
        ),
        PartSpec(
            "AP-2241", "Switch Housing", "Lucas TVS", "Trim", "17", None, "23",
            (Op("Op10", "IM-SMALL", "0.5"), Op("Op20", "FIN-family", "0.2"),
             Op("Op30", "QC-family", "0.1")),
            10, (3000, 9000),
        ),
        PartSpec(
            "AP-2330", "Cable Clip 8mm", "Motherson", "Clips", None, "L", "3",
            (Op("Op10", "IM-SMALL", "0.2"), Op("Op20", "QC-family", "0.1")),
            9, (8000, 24000),
        ),
        PartSpec(
            "AP-2331", "Cable Clip 12mm", "Motherson", "Clips", None, "L", "4",
            (Op("Op10", "IM-SMALL", "0.2"), Op("Op20", "QC-family", "0.1")),
            8, (8000, 24000),
        ),
        PartSpec(
            "AP-2450", "Filter Bowl Assembly", "Bosch", "Assemblies", "96", None, "148",
            (Op("Op10", "IM-LARGE", "1.4"), Op("Op20", "FIN-family", "0.6"),
             Op("Op30", "ASSY-family", "1.7"), Op("Op40", "QC-family", "0.4")),
            6, (400, 1200),
        ),
        PartSpec(
            "AP-2460", "Reservoir Body", "Brakes India", "Assemblies", "112", None, "174",
            (Op("Op10", "IM-LARGE", "1.8"), Op("Op20", "FIN-family", "0.7"),
             Op("Op30", "ASSY-family", "2.1"), Op("Op40", "QC-family", "0.4")),
            5, (350, 1000),
        ),
    ],
    margin_bands={"Shrouds": ("70", "130", "220"), "Clips": ("1", "3", "6"), "Trim": ("8", "15", "26")},
    shifts=[
        ShiftSpec("Shift A", "06:00", "14:00", 30),
        ShiftSpec("Shift B", "14:00", "22:00", 30),
        ShiftSpec("Shift C", "22:00", "06:00", 30),
    ],
    people=[
        PersonSpec("Mrs. Annapoorani G.", "owner", "owner", "owner@annaipolymers.in", "owner1234", 3),
        PersonSpec("Srinivasan R.", "plant_head", "supervisor", "+919500000501", "plant1234", 3),
        PersonSpec("Ezhilarasan K.", "production", "supervisor", "+919500000502", "shift1234", 1, "ta", "Shift A"),
        PersonSpec("Vasanthakumar P.", "production", "supervisor", "+919500000503", "shift1234", 1, "ta", "Shift B"),
        PersonSpec("Rajendran M.", "production", "supervisor", "+919500000504", "shift1234", 1, "ta", "Shift C"),
        PersonSpec("Amudha S.", "quality", "quality", "+919500000505", "quality1234", 2, "en", "Shift A"),
        PersonSpec("Jothi Lakshmi V.", "quality", "quality", "+919500000506", "quality1234", 2, "ta", "Shift B"),
        PersonSpec("Sridhar N.", "maintenance", "maintenance", "+919500000507", "maint1234", 2, "ta"),
        PersonSpec("Anbarasu L.", "stores", "stores", "+919500000508", "stores1234", 2, "ta"),
    ],
    operator_prefix="APM",
    operators_per_shift=14,
    chronic=[
        Chronic("IMM-450", "MOULD_CHANGE", 0.30, (22, 58)),
        Chronic("IMM-350", "SHORT_SHOT", 0.24, (8, 26)),
        Chronic("IMM-150", "HOT_RUNNER", 0.06, (35, 105)),
        Chronic("USW-1", "MATERIAL_WAIT", 0.21, (10, 30)),
        Chronic("IMM-120", "CHILLER_DOWN", 0.05, (30, 95)),
        Chronic("IMM-80A", "NO_OPERATOR", 0.14, (12, 34)),
        Chronic("PAD-1", "MINOR_ADJUSTMENT", 0.19, (4, 14)),
    ],
    extra_reasons=MOULDING_REASONS,
    defects=[
        ("SHORT_SHOT_D", "Short Shot", "Shrouds", "UNDERFILL"),
        ("FLASH", "Flash", "Housings", "BURR"),
        ("SINK_MARK", "Sink Mark", "Shrouds", "SURFACE_FINISH"),
        ("WELD_LINE", "Weld Line", "Housings", "SURFACE_FINISH"),
        ("WARPAGE", "Warpage", "Trim", "FLATNESS"),
        ("BLACK_SPOT", "Black Spot / Contamination", "Trim", "SURFACE_FINISH"),
        ("SPLAY", "Splay / Moisture Streak", None, "SURFACE_FINISH"),
        ("USW_LEAK", "Ultrasonic Weld Leak", "Assemblies", "WELD_MISS"),
        ("PRINT_OFFSET", "Print Off-position", "Trim", "POSITION"),
        ("SHORT_FILL_CAV", "Cavity Short Fill", "Clips", "UNDERFILL"),
        ("DENT", "Handling Damage", None, "DENT"),
    ],
    action_cost_threshold=2000,
    action_duration_minutes=25,
    recurrence_count=3,
    tolerance_parts=60,
    tolerance_bp=250,
    no_blame_days=45,
    monthly_fee=28000,
    event_weights={"MOULD_CHANGE": 7, "SHORT_SHOT": 5, "MATERIAL_DRY": 2, "CHILLER_DOWN": 1},
    kaizen_ideas=64,
)


# ---------------------------------------------------------------------------
# Plant 7 — Nilgiri Pumps & Castings, Coimbatore (foundry finishing, standard)
# ---------------------------------------------------------------------------

NILGIRI = PlantSpec(
    code="NILGIRI",
    name="Nilgiri Pumps & Castings",
    location="Kurichi Industrial Estate, Coimbatore, Tamil Nadu",
    costing_mode="standard",
    go_live=date(2026, 6, 15),
    lot_granularity="shift_batch",
    email_domain="nilgiripumps.in",
    families=[
        ("VTL-family", "Vertical Turret Lathe", "VTLs for casing and cover faces"),
        ("CNC-family", "CNC Turning", "CNC lathes for impellers and shafts"),
        ("VMC-family", "Vertical Machining", "VMCs for mounting faces and ports"),
        ("TEST-family", "Test & Balance", "Hydro test rig and dynamic balancer"),
        ("ASSY-family", "Pump Assembly", "Assembly and paint booth"),
        ("QC-family", "Inspection & Dispatch", "Final inspection and dispatch"),
    ],
    rates=[
        RateSpec(
            "cost_family", "VTL-family", machine="640",
            note="Two VTLs, depreciation over 12 years plus measured power draw.",
        ),
        RateSpec("cost_family", "CNC-family", machine="445"),
        RateSpec("cost_family", "VMC-family", machine="395"),
        RateSpec("cost_family", "TEST-family", machine="215"),
        RateSpec("cost_family", "ASSY-family", machine="105"),
        RateSpec("cost_family", "QC-family", machine="70"),
        RateSpec("section", "machining", labour="195", source="measured"),
        RateSpec("section", "assembly", labour="185", source="measured"),
        RateSpec("section", "quality", labour="205", source="measured"),
        RateSpec("section", "dispatch", labour="155", source="measured"),
        RateSpec("plant", "plant", overhead="235"),
        RateSpec(
            "cost_family", "VTL-family", machine="672", source="measured",
            effective_from=date(2026, 8, 17),
            note="Re-derived after the second VTL was recommissioned in August.",
        ),
    ],
    stations=[
        StationSpec("VTL-1", "Vertical Turret Lathe 1", "VTL", "VTL-family", "machining", "Casing Line", True),
        StationSpec("VTL-2", "Vertical Turret Lathe 2", "VTL", "VTL-family", "machining", "Casing Line"),
        StationSpec("CNC-21", "CNC Lathe 1", "CNC turning", "CNC-family", "machining", "Rotating Line"),
        StationSpec("CNC-22", "CNC Lathe 2", "CNC turning", "CNC-family", "machining", "Rotating Line"),
        StationSpec("CNC-23", "CNC Lathe 3", "CNC turning", "CNC-family", "machining", "Rotating Line"),
        StationSpec("VMC-21", "VMC 1", "VMC", "VMC-family", "machining", "Casing Line"),
        StationSpec("VMC-22", "VMC 2", "VMC", "VMC-family", "machining", "Casing Line"),
        StationSpec("BAL-1", "Dynamic Balancer", "Balancing", "TEST-family", "machining", "Rotating Line", True),
        StationSpec("HYD-1", "Hydro Test Rig", "Test rig", "TEST-family", "assembly", "Assembly"),
        StationSpec("ASSY-N1", "Pump Assembly Bay 1", "Assembly", "ASSY-family", "assembly", "Assembly"),
        StationSpec("ASSY-N2", "Pump Assembly Bay 2", "Assembly", "ASSY-family", "assembly", "Assembly"),
        StationSpec("PAINT-1", "Paint Booth", "Painting", "ASSY-family", "assembly", "Assembly"),
        StationSpec("QC-N", "Final Inspection", "Inspection", "QC-family", "quality", None),
        StationSpec("DESP-N", "Dispatch", "Dispatch", "QC-family", "dispatch", None),
    ],
    parts=[
        PartSpec(
            "NP-4010", "Pump Casing 80mm", "KSB Pumps", "Casings", "486", None, "812",
            (Op("Op10", "VTL-family", "8.4"), Op("Op20", "VMC-family", "5.2"),
             Op("Op30", "QC-family", "1.4")),
            7, (60, 200),
        ),
        PartSpec(
            "NP-4012", "Pump Casing 100mm", "KSB Pumps", "Casings", "612", None, "1046",
            (Op("Op10", "VTL-family", "10.2"), Op("Op20", "VMC-family", "6.1"),
             Op("Op30", "QC-family", "1.6")),
            6, (50, 160),
        ),
        PartSpec(
            "NP-4110", "Impeller 6 Vane", "KSB Pumps", "Rotating", "284", None, "462",
            (Op("Op10", "CNC-family", "6.8"), Op("Op20", "TEST-family", "3.2"),
             Op("Op30", "QC-family", "0.9")),
            8, (100, 320),
        ),
        PartSpec(
            "NP-4115", "Impeller 8 Vane", "Kirloskar", "Rotating", "318", None, "524",
            (Op("Op10", "CNC-family", "7.4"), Op("Op20", "TEST-family", "3.4"),
             Op("Op30", "QC-family", "0.9")),
            6, (90, 280),
        ),
        PartSpec(
            "NP-4210", "Pump Shaft EN8", "Kirloskar", "Rotating", "148", None, "232",
            (Op("Op10", "CNC-family", "4.6"), Op("Op20", "QC-family", "0.6")),
            9, (180, 560),
        ),
        PartSpec(
            "NP-4310", "Bearing Housing", "Kirloskar", "Housings", "196", None, "312",
            (Op("Op10", "VTL-family", "5.1"), Op("Op20", "VMC-family", "3.4"),
             Op("Op30", "QC-family", "0.7")),
            7, (120, 380),
        ),
        PartSpec(
            "NP-4320", "Stuffing Box", "KSB Pumps", "Housings", None, "M", "178",
            (Op("Op10", "VTL-family", "3.8"), Op("Op20", "VMC-family", "2.6"),
             Op("Op30", "QC-family", "0.5")),
            8, (150, 450),
        ),
        PartSpec(
            "NP-4410", "Monoblock Pump 1HP", "Nilgiri Brand", "Assemblies", "742", None, "1284",
            (Op("Op10", "CNC-family", "5.2"), Op("Op20", "ASSY-family", "9.5"),
             Op("Op30", "TEST-family", "4.2"), Op("Op40", "QC-family", "1.8")),
            5, (40, 140),
        ),
        PartSpec(
            "NP-4420", "Monoblock Pump 2HP", "Nilgiri Brand", "Assemblies", "914", None, "1596",
            (Op("Op10", "CNC-family", "6.1"), Op("Op20", "ASSY-family", "11.2"),
             Op("Op30", "TEST-family", "4.8"), Op("Op40", "QC-family", "2.0")),
            4, (30, 110),
        ),
        PartSpec(
            "NP-4510", "Wear Ring Set", "Kirloskar", "Wear Parts", None, "L", "46",
            (Op("Op10", "CNC-family", "1.6"), Op("Op20", "QC-family", "0.3")),
            7, (400, 1200),
        ),
    ],
    margin_bands={"Housings": ("110", "190", "310"), "Wear Parts": ("18", "34", "58")},
    shifts=[
        ShiftSpec("Shift A", "08:00", "16:30", 45),
        ShiftSpec("Shift B", "16:30", "01:00", 45),
    ],
    people=[
        PersonSpec("Mr. Nallasamy P.", "owner", "owner", "owner@nilgiripumps.in", "owner1234", 3),
        PersonSpec("Ramakrishnan S.", "plant_head", "supervisor", "+919600000601", "plant1234", 3),
        PersonSpec("Marimuthu A.", "production", "supervisor", "+919600000602", "shift1234", 1, "ta", "Shift A"),
        PersonSpec("Selvaraj K.", "production", "supervisor", "+919600000603", "shift1234", 1, "ta", "Shift B"),
        PersonSpec("Hemalatha R.", "quality", "quality", "+919600000604", "quality1234", 2, "en", "Shift A"),
        PersonSpec("Vijayalakshmi T.", "quality", "quality", "+919600000605", "quality1234", 2, "en", "Shift B"),
        PersonSpec("Chinnasamy M.", "maintenance", "maintenance", "+919600000606", "maint1234", 2, "ta"),
        PersonSpec("Rajamani V.", "stores", "stores", "+919600000607", "stores1234", 2, "ta"),
    ],
    operator_prefix="NPC",
    operators_per_shift=16,
    chronic=[
        Chronic("VTL-1", "CASTING_WAIT", 0.27, (15, 45)),
        Chronic("BAL-1", "LEAK_TEST_FAIL", 0.16, (12, 32)),
        Chronic("CNC-22", "TOOL_BREAKAGE", 0.18, (14, 40)),
        Chronic("VMC-21", "FIXTURE_ISSUE", 0.15, (10, 28)),
        Chronic("VTL-2", "MACHINE_BREAKDOWN", 0.05, (40, 120)),
        Chronic("ASSY-N1", "SHOT_BLAST", 0.13, (10, 26)),
        Chronic("CNC-23", "NO_OPERATOR", 0.12, (14, 36)),
    ],
    extra_reasons=FOUNDRY_REASONS,
    defects=[
        ("BLOW_HOLE", "Blow Hole", "Casings", "BLOW_HOLE"),
        ("POROSITY", "Porosity", "Casings", "BLOW_HOLE"),
        ("BORE_OVERSIZE", "Bore Oversize", "Housings", "BORE_OVERSIZE"),
        ("OD_UNDERSIZE", "OD Undersize", "Rotating", "OD_UNDERSIZE"),
        ("RUNOUT", "Runout / Balance Fail", "Rotating", "RUNOUT"),
        ("LEAK", "Hydro Test Leak", "Assemblies", "CRACK"),
        ("SURFACE_FINISH", "Surface Finish", "Housings", "SURFACE_FINISH"),
        ("FLATNESS", "Face Flatness", "Casings", "FLATNESS"),
        ("THREAD_DAMAGE", "Thread Damage", "Wear Parts", "THREAD_DAMAGE"),
        ("PAINT_RUN", "Paint Run / Poor Cover", "Assemblies", "SURFACE_FINISH"),
        ("DENT", "Dent / Handling Damage", None, "DENT"),
    ],
    action_cost_threshold=3500,
    action_duration_minutes=30,
    recurrence_count=3,
    tolerance_parts=4,
    tolerance_bp=150,
    no_blame_days=30,
    monthly_fee=32000,
    event_weights={"CASTING_WAIT": 6, "SHOT_BLAST": 3, "LEAK_TEST_FAIL": 3},
    kaizen_ideas=44,
)


# ---------------------------------------------------------------------------
# Plant 8 — Theni Sheet Metal, Madurai (smallest plant, no WhatsApp approval)
# ---------------------------------------------------------------------------

THENI = PlantSpec(
    code="THENI",
    name="Theni Sheet Metal & Fabrication",
    location="Kappalur Industrial Estate, Madurai, Tamil Nadu",
    costing_mode="basic",
    go_live=date(2026, 7, 13),
    lot_granularity="shift_batch",
    email_domain="thenisheetmetal.in",
    families=[
        ("LASER-family", "Laser Cutting", "3kW fibre laser"),
        ("BEND-family", "Press Brake", "CNC press brakes"),
        ("WELD-family", "Welding", "MIG welding bays and spot welder"),
        ("POWDER-family", "Powder Coating", "Seven-tank pre-treatment and oven"),
        ("QC-family", "Inspection & Dispatch", "Inspection table and dispatch"),
    ],
    rates=[
        RateSpec("cost_family", "LASER-family", machine="780", source="default"),
        RateSpec("cost_family", "BEND-family", machine="290", source="default"),
        RateSpec("cost_family", "WELD-family", machine="185", source="default"),
        RateSpec("cost_family", "POWDER-family", machine="340", source="default"),
        RateSpec("cost_family", "QC-family", machine="50", source="default"),
        RateSpec("section", "machining", labour="155", source="default"),
        RateSpec("section", "assembly", labour="150", source="default"),
        RateSpec("section", "quality", labour="160", source="default"),
        RateSpec("section", "dispatch", labour="130", source="default"),
        RateSpec("plant", "plant", overhead="150", source="default"),
    ],
    stations=[
        StationSpec("LASER-1", "Fibre Laser 3kW", "Laser", "LASER-family", "machining", "Cutting", True),
        StationSpec("SHEAR-1", "Hydraulic Shear", "Shearing", "LASER-family", "machining", "Cutting"),
        StationSpec("BRAKE-1", "Press Brake 100T", "Press brake", "BEND-family", "machining", "Forming"),
        StationSpec("BRAKE-2", "Press Brake 60T", "Press brake", "BEND-family", "machining", "Forming"),
        StationSpec("WELD-T1", "MIG Welding Bay 1", "Welding", "WELD-family", "assembly", "Fabrication", True),
        StationSpec("WELD-T2", "MIG Welding Bay 2", "Welding", "WELD-family", "assembly", "Fabrication"),
        StationSpec("SPOT-1", "Spot Welder", "Welding", "WELD-family", "assembly", "Fabrication"),
        StationSpec("PT-1", "Pre-treatment Line", "Pre-treatment", "POWDER-family", "assembly", "Finishing"),
        StationSpec("OVEN-1", "Powder Coat Oven", "Oven", "POWDER-family", "assembly", "Finishing"),
        StationSpec("QC-T", "Inspection Table", "Inspection", "QC-family", "quality", None),
        StationSpec("DESP-T", "Dispatch", "Dispatch", "QC-family", "dispatch", None),
    ],
    parts=[
        PartSpec(
            "TS-6010", "Control Panel Enclosure 600", "Schneider Dealer", "Enclosures", None, "H", "684",
            (Op("Op10", "LASER-family", "4.2"), Op("Op20", "BEND-family", "5.6"),
             Op("Op30", "WELD-family", "7.8"), Op("Op40", "POWDER-family", "3.4"),
             Op("Op50", "QC-family", "1.2")),
            7, (30, 110),
        ),
        PartSpec(
            "TS-6012", "Control Panel Enclosure 800", "Schneider Dealer", "Enclosures", None, "H", "892",
            (Op("Op10", "LASER-family", "5.4"), Op("Op20", "BEND-family", "6.8"),
             Op("Op30", "WELD-family", "9.2"), Op("Op40", "POWDER-family", "3.8"),
             Op("Op50", "QC-family", "1.4")),
            5, (25, 90),
        ),
        PartSpec(
            "TS-6120", "Cable Tray 300mm", "L&T Electricals", "Trays", "62", None, "104",
            (Op("Op10", "LASER-family", "1.1"), Op("Op20", "BEND-family", "1.4"),
             Op("Op30", "POWDER-family", "1.2"), Op("Op40", "QC-family", "0.3")),
            10, (200, 700),
        ),
        PartSpec(
            "TS-6121", "Cable Tray 150mm", "L&T Electricals", "Trays", "41", None, "68",
            (Op("Op10", "LASER-family", "0.8"), Op("Op20", "BEND-family", "1.1"),
             Op("Op30", "POWDER-family", "1.0"), Op("Op40", "QC-family", "0.2")),
            10, (250, 850),
        ),
        PartSpec(
            "TS-6230", "Machine Guard Panel", "Local OEM", "Guards", "118", None, "186",
            (Op("Op10", "LASER-family", "2.1"), Op("Op20", "BEND-family", "2.4"),
             Op("Op30", "WELD-family", "3.6"), Op("Op40", "POWDER-family", "1.8"),
             Op("Op50", "QC-family", "0.5")),
            8, (80, 260),
        ),
        PartSpec(
            "TS-6310", "Equipment Base Frame", "Local OEM", "Frames", "246", None, "398",
            (Op("Op10", "LASER-family", "2.8"), Op("Op20", "BEND-family", "1.9"),
             Op("Op30", "WELD-family", "12.4"), Op("Op40", "POWDER-family", "2.6"),
             Op("Op50", "QC-family", "0.8")),
            6, (40, 150),
        ),
        PartSpec(
            "TS-6410", "Ducting Section", "HVAC Contractor", "Ducting", None, "M", "148",
            (Op("Op10", "LASER-family", "1.6"), Op("Op20", "BEND-family", "2.2"),
             Op("Op30", "WELD-family", "2.8"), Op("Op40", "QC-family", "0.4")),
            8, (100, 340),
        ),
        PartSpec(
            "TS-6510", "Junction Box Small", "L&T Electricals", "Enclosures", "38", None, "58",
            (Op("Op10", "LASER-family", "0.6"), Op("Op20", "BEND-family", "0.9"),
             Op("Op30", "WELD-family", "0.7"), Op("Op40", "POWDER-family", "0.9"),
             Op("Op50", "QC-family", "0.2")),
            9, (300, 950),
        ),
    ],
    margin_bands={"Enclosures": ("180", "320", "540"), "Ducting": ("55", "95", "160")},
    shifts=[
        ShiftSpec("Shift A", "08:30", "17:00", 45),
        ShiftSpec("Shift B", "17:00", "01:30", 45),
    ],
    people=[
        PersonSpec("Mr. Thangavel M.", "owner", "owner", "owner@thenisheetmetal.in", "owner1234", 3),
        PersonSpec("Balasubramanian R.", "plant_head", "supervisor", "+919700000701", "plant1234", 3),
        PersonSpec("Ilangovan S.", "production", "supervisor", "+919700000702", "shift1234", 1, "ta", "Shift A"),
        PersonSpec("Manikandan P.", "production", "supervisor", "+919700000703", "shift1234", 1, "ta", "Shift B"),
        PersonSpec("Sumathi K.", "quality", "quality", "+919700000704", "quality1234", 2, "ta", "Shift A"),
        PersonSpec("Karuppasamy V.", "maintenance", "maintenance", "+919700000705", "maint1234", 2, "ta"),
        PersonSpec("Ponnusamy N.", "stores", "stores", "+919700000706", "stores1234", 2, "ta"),
    ],
    operator_prefix="TSM",
    operators_per_shift=10,
    chronic=[
        Chronic("LASER-1", "SHEET_WAIT", 0.29, (12, 36)),
        Chronic("BRAKE-1", "BEND_SETUP", 0.25, (10, 30)),
        Chronic("WELD-T1", "WELD_GAS", 0.18, (8, 22)),
        Chronic("OVEN-1", "MACHINE_BREAKDOWN", 0.06, (35, 110)),
        Chronic("WELD-T2", "NO_OPERATOR", 0.16, (15, 42)),
        Chronic("BRAKE-2", "DIE_CHANGE", 0.20, (12, 32)),
    ],
    extra_reasons=SHEET_REASONS,
    defects=[
        ("BURR", "Cut Edge Burr", "Trays", "BURR"),
        ("BEND_ANGLE", "Bend Angle Out", "Enclosures", "FLATNESS"),
        ("WELD_SPATTER", "Weld Spatter", "Frames", "WELD_SPATTER"),
        ("WELD_MISS", "Missed Weld", "Frames", "WELD_MISS"),
        ("WELD_DISTORTION", "Weld Distortion", "Guards", "WELD_DISTORTION"),
        ("PAINT_THIN", "Powder Coat Thin", "Enclosures", "SURFACE_FINISH"),
        ("PAINT_ORANGE", "Orange Peel", "Enclosures", "SURFACE_FINISH"),
        ("HOLE_OFFSET", "Hole Position Out", "Trays", "HOLE_OFFSET"),
        ("SCRATCH", "Scratch / Handling", None, "DENT"),
        ("LENGTH", "Length Out of Tolerance", "Ducting", "LENGTH"),
    ],
    action_cost_threshold=1800,
    action_duration_minutes=30,
    recurrence_count=3,
    tolerance_parts=3,
    tolerance_bp=300,
    no_blame_days=45,
    monthly_fee=15000,
    whatsapp_approved=False,
    event_weights={"SHEET_WAIT": 6, "BEND_SETUP": 5, "WELD_GAS": 4, "DIE_CHANGE": 3},
    events_per_shift=(40, 22, 8, 3),
    kaizen_ideas=20,
)


# ---------------------------------------------------------------------------
# Plant 9 — Vaigai Auto Electricals, Ambattur (high-volume assembly, 3 shifts)
# ---------------------------------------------------------------------------

VAIGAI = PlantSpec(
    code="VAIGAI",
    name="Vaigai Auto Electricals Pvt Ltd",
    location="Ambattur Industrial Estate, Chennai, Tamil Nadu",
    costing_mode="standard",
    go_live=date(2026, 8, 10),
    lot_granularity="job_lot",
    email_domain="vaigaielectricals.in",
    families=[
        ("WIND-family", "Armature Winding", "Automatic armature winders"),
        ("PRESS-family", "Stamping & Lamination", "40T and 63T presses for laminations"),
        ("ASSY-family", "Assembly Lines", "Motor and starter assembly lines"),
        ("TEST-family", "End-of-line Test", "Dynamometer and electrical test rigs"),
        ("QC-family", "Inspection & Packing", "Audit bench and packing"),
    ],
    rates=[
        RateSpec("cost_family", "WIND-family", machine="385"),
        RateSpec("cost_family", "PRESS-family", machine="245"),
        RateSpec("cost_family", "ASSY-family", machine="130"),
        RateSpec("cost_family", "TEST-family", machine="290"),
        RateSpec("cost_family", "QC-family", machine="65"),
        RateSpec("section", "machining", labour="175", source="measured"),
        RateSpec("section", "assembly", labour="168", source="measured"),
        RateSpec("section", "quality", labour="190", source="measured"),
        RateSpec("section", "dispatch", labour="145", source="measured"),
        RateSpec("plant", "plant", overhead="198"),
    ],
    stations=[
        StationSpec("WIND-1", "Armature Winder 1", "Winding", "WIND-family", "machining", "Winding", True),
        StationSpec("WIND-2", "Armature Winder 2", "Winding", "WIND-family", "machining", "Winding"),
        StationSpec("WIND-3", "Armature Winder 3", "Winding", "WIND-family", "machining", "Winding"),
        StationSpec("PRESS-V1", "Lamination Press 63T", "Press", "PRESS-family", "machining", "Stamping"),
        StationSpec("PRESS-V2", "Lamination Press 40T", "Press", "PRESS-family", "machining", "Stamping"),
        StationSpec("ASSY-V1", "Motor Assembly Line 1", "Assembly", "ASSY-family", "assembly", "Assembly", True),
        StationSpec("ASSY-V2", "Motor Assembly Line 2", "Assembly", "ASSY-family", "assembly", "Assembly"),
        StationSpec("ASSY-V3", "Starter Assembly Line", "Assembly", "ASSY-family", "assembly", "Assembly"),
        StationSpec("SOLDER-1", "Commutator Solder Station", "Soldering", "ASSY-family", "assembly", "Assembly"),
        StationSpec("TEST-V1", "Dynamometer Rig 1", "Test rig", "TEST-family", "assembly", "Test"),
        StationSpec("TEST-V2", "Electrical Test Rig", "Test rig", "TEST-family", "assembly", "Test"),
        StationSpec("QC-V", "Audit Bench", "Inspection", "QC-family", "quality", None),
        StationSpec("DESP-V2", "Packing & Dispatch", "Dispatch", "QC-family", "dispatch", None),
    ],
    parts=[
        PartSpec(
            "VE-8010", "Starter Motor Armature", "Lucas TVS", "Armatures", "128", None, "214",
            (Op("Op10", "PRESS-family", "0.4"), Op("Op20", "WIND-family", "1.8"),
             Op("Op30", "ASSY-family", "1.1"), Op("Op40", "QC-family", "0.3")),
            10, (500, 1600),
        ),
        PartSpec(
            "VE-8015", "Alternator Rotor", "Lucas TVS", "Armatures", "156", None, "262",
            (Op("Op10", "PRESS-family", "0.5"), Op("Op20", "WIND-family", "2.2"),
             Op("Op30", "ASSY-family", "1.3"), Op("Op40", "QC-family", "0.3")),
            9, (450, 1400),
        ),
        PartSpec(
            "VE-8110", "Wiper Motor Assembly", "TVS Motor", "Motors", "218", None, "368",
            (Op("Op10", "WIND-family", "1.6"), Op("Op20", "ASSY-family", "2.8"),
             Op("Op30", "TEST-family", "1.2"), Op("Op40", "QC-family", "0.4")),
            9, (400, 1200),
        ),
        PartSpec(
            "VE-8120", "Blower Motor 12V", "TVS Motor", "Motors", "186", None, "312",
            (Op("Op10", "WIND-family", "1.4"), Op("Op20", "ASSY-family", "2.4"),
             Op("Op30", "TEST-family", "1.1"), Op("Op40", "QC-family", "0.3")),
            8, (400, 1200),
        ),
        PartSpec(
            "VE-8210", "Starter Motor Complete", "Ashok Leyland", "Starters", "642", None, "1086",
            (Op("Op10", "WIND-family", "2.4"), Op("Op20", "ASSY-family", "5.6"),
             Op("Op30", "TEST-family", "2.8"), Op("Op40", "QC-family", "0.8")),
            7, (150, 500),
        ),
        PartSpec(
            "VE-8215", "Starter Motor Heavy Duty", "Ashok Leyland", "Starters", "824", None, "1412",
            (Op("Op10", "WIND-family", "3.1"), Op("Op20", "ASSY-family", "6.8"),
             Op("Op30", "TEST-family", "3.2"), Op("Op40", "QC-family", "0.9")),
            5, (100, 340),
        ),
        PartSpec(
            "VE-8310", "Stator Lamination Stack", "Bosch", "Laminations", "34", None, "52",
            (Op("Op10", "PRESS-family", "0.3"), Op("Op20", "ASSY-family", "0.5"),
             Op("Op30", "QC-family", "0.1")),
            10, (2000, 6000),
        ),
        PartSpec(
            "VE-8320", "Rotor Lamination Stack", "Bosch", "Laminations", None, "L", "41",
            (Op("Op10", "PRESS-family", "0.3"), Op("Op20", "ASSY-family", "0.5"),
             Op("Op30", "QC-family", "0.1")),
            9, (2000, 6000),
        ),
        PartSpec(
            "VE-8410", "Solenoid Switch", "Lucas TVS", "Switches", None, "M", "96",
            (Op("Op10", "ASSY-family", "1.4"), Op("Op20", "TEST-family", "0.8"),
             Op("Op30", "QC-family", "0.2")),
            9, (800, 2400),
        ),
        PartSpec(
            "VE-8420", "Brush Holder Assembly", "Lucas TVS", "Switches", "42", None, "64",
            (Op("Op10", "ASSY-family", "0.9"), Op("Op20", "QC-family", "0.2")),
            8, (1200, 3600),
        ),
    ],
    margin_bands={"Laminations": ("14", "26", "44"), "Switches": ("38", "68", "112")},
    shifts=[
        ShiftSpec("Shift A", "06:00", "14:30", 40),
        ShiftSpec("Shift B", "14:30", "23:00", 40),
        ShiftSpec("Shift C", "23:00", "06:00", 30),
    ],
    people=[
        PersonSpec("Mr. Vaigaivanan K.", "owner", "owner", "owner@vaigaielectricals.in", "owner1234", 3),
        PersonSpec("Shanmugasundaram R.", "plant_head", "supervisor", "+919800000801", "plant1234", 3),
        PersonSpec("Prabakaran M.", "production", "supervisor", "+919800000802", "shift1234", 1, "ta", "Shift A"),
        PersonSpec("Yuvarani S.", "production", "supervisor", "+919800000803", "shift1234", 1, "ta", "Shift B"),
        PersonSpec("Kandasamy T.", "production", "supervisor", "+919800000804", "shift1234", 1, "ta", "Shift C"),
        PersonSpec("Sasirekha P.", "quality", "quality", "+919800000805", "quality1234", 2, "en", "Shift A"),
        PersonSpec("Nithya K.", "quality", "quality", "+919800000806", "quality1234", 2, "en", "Shift B"),
        PersonSpec("Loganathan V.", "maintenance", "maintenance", "+919800000807", "maint1234", 2, "ta"),
        PersonSpec("Arulmozhi S.", "stores", "stores", "+919800000808", "stores1234", 2, "ta"),
    ],
    operator_prefix="VAE",
    operators_per_shift=20,
    chronic=[
        Chronic("WIND-1", "COMPONENT_WAIT", 0.28, (10, 32)),
        Chronic("ASSY-V1", "LINE_BALANCE", 0.24, (8, 24)),
        Chronic("TEST-V1", "TEST_RIG_DOWN", 0.07, (30, 95)),
        Chronic("SOLDER-1", "SOLDER_POT", 0.20, (9, 26)),
        Chronic("WIND-2", "TOOL_BREAKAGE", 0.14, (12, 34)),
        Chronic("PRESS-V1", "SETUP_CHANGEOVER", 0.17, (12, 34)),
        Chronic("ASSY-V3", "NO_OPERATOR", 0.13, (13, 36)),
    ],
    extra_reasons=ASSEMBLY_REASONS,
    defects=[
        ("WIND_SHORT", "Winding Short", "Armatures", "CRACK"),
        ("WIND_OPEN", "Open Circuit", "Armatures", "WELD_MISS"),
        ("SOLDER_DRY", "Dry Solder Joint", "Motors", "WELD_MISS"),
        ("COMM_RUNOUT", "Commutator Runout", "Armatures", "RUNOUT"),
        ("TORQUE_LOW", "Torque Below Spec", "Starters", "HARDNESS_LOW"),
        ("NOISE", "Noise / Vibration", "Motors", "RUNOUT"),
        ("BURR", "Lamination Burr", "Laminations", "BURR"),
        ("STACK_HEIGHT", "Stack Height Out", "Laminations", "LENGTH"),
        ("CONTACT_PIT", "Contact Pitting", "Switches", "SURFACE_FINISH"),
        ("MISSING_OP", "Missing Component", None, "MISSING_OP"),
        ("DENT", "Dent / Handling Damage", None, "DENT"),
    ],
    action_cost_threshold=2200,
    action_duration_minutes=20,
    recurrence_count=3,
    tolerance_parts=80,
    tolerance_bp=200,
    no_blame_days=30,
    monthly_fee=38000,
    event_weights={"COMPONENT_WAIT": 7, "LINE_BALANCE": 5, "SOLDER_POT": 4},
    events_per_shift=(72, 38, 12, 5),
    kaizen_ideas=26,
)

#: The five plants this module adds, in go-live order. Appended to the original
#: four rather than mixed in, so the first four simulate against exactly the
#: same random draws as before and their rows do not move.
EXTRA_PLANTS: list[PlantSpec] = [VELAVAN, ANNAI, NILGIRI, THENI, VAIGAI]
