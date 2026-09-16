"""Shop-floor language for the free-text columns.

Root causes, 5-Why chains, the four Factory Memory answers, Kaizen ideas and
Copilot questions. Written the way a supervisor or maintenance lead in a Tamil
Nadu tier-2 plant actually writes them: short, specific, part- and
machine-level, never generic ("improve maintenance").

``{station}`` is substituted at generation time.
"""

from __future__ import annotations

# reason code -> list of fixes
# (root_cause, what_fixed_it / countermeasure, what_was_replaced, check_first_next_time, five_whys)
FIXES: dict[str, list[tuple[str, str, str | None, str, list[tuple[str, str]]]]] = {
    "TOOL_BREAKAGE": [
        (
            "Insert grade too soft for the new EN8 bar lot; wear accelerated past the change interval",
            "Changed insert grade from P25 to P15 and reset the tool-life counter to 240 parts",
            "Carbide insert CNMG 120408 (P25 -> P15)",
            "Check insert wear at 240 parts, not 310",
            [
                (
                    "Why did the insert break?",
                    "Flank wear went past 0.4 mm before the change",
                ),
                (
                    "Why did wear go past the limit?",
                    "Tool-life counter still set to 310 parts",
                ),
                (
                    "Why was 310 too many?",
                    "The new bar lot is harder (EN8 at the top of the band)",
                ),
                (
                    "Why wasn't the change noticed?",
                    "Incoming material cert not shared with the setter",
                ),
                ("Why not shared?", "No step in the GRN process to flag hardness changes"),
            ],
        ),
        (
            "Coolant nozzle misaligned after cleaning, insert running dry on the finishing pass",
            "Re-aimed and locked the coolant nozzle; added nozzle position to the start-up checklist",
            "Coolant nozzle lock nut",
            "Look at the coolant stream on the cutting edge before first-off",
            [
                ("Why did the insert chip?", "Thermal cracking on the cutting edge"),
                (
                    "Why thermal cracking?",
                    "Insert running without coolant on the finish pass",
                ),
                ("Why no coolant?", "Nozzle knocked off target during weekly cleaning"),
                ("Why not re-aimed?", "Nozzle position is not on the start-up checklist"),
                (
                    "Why not on the checklist?",
                    "Checklist written before the new flexible nozzles were fitted",
                ),
            ],
        ),
        (
            "Drill bit regrinds done by eye; point angle inconsistent",
            "Moved regrinding to the tool room with a point-angle gauge; bits tagged with regrind count",
            "Drill bits 8.5 mm (3 nos, reground)",
            "Check the regrind tag — scrap the bit after 4 regrinds",
            [
                ("Why did the drill break?", "Unequal lip heights, one lip overloaded"),
                ("Why unequal lips?", "Regrind done on the bench grinder by eye"),
                (
                    "Why on the bench grinder?",
                    "Tool room drill-grinding fixture was missing",
                ),
                ("Why missing?", "Borrowed by another section and not returned"),
                ("Why not tracked?", "No issue register for tool room fixtures"),
            ],
        ),
    ],
    "MATERIAL_WAIT": [
        (
            "Upstream staging not completed before shift start; stores issues material on request only",
            "Moved material staging to the previous shift with a line-feed kanban trolley",
            "Line-feed kanban cards re-sequenced",
            "Confirm the trolley is staged before the shift bell",
            [
                ("Why did the machine wait?", "No blanks at the machine at shift start"),
                ("Why no blanks?", "Stores issues only when the supervisor asks"),
                (
                    "Why only on request?",
                    "No agreed staging time between stores and production",
                ),
                (
                    "Why no agreement?",
                    "Stores works general shift, production starts earlier",
                ),
                ("Why not aligned?", "Staging was never written into the shift handover"),
            ],
        ),
        (
            "Coil/bar stock arrived without heat number; held at GRN for quality clearance",
            "Supplier now sends the mill cert with the e-way bill; GRN clears on cert, not on lab test",
            None,
            "Check the mill cert is attached before unloading",
            [
                ("Why was material not available?", "Stock held at GRN"),
                ("Why held?", "Heat number missing on the bundle tag"),
                ("Why missing?", "Supplier re-bundled from a larger coil"),
                ("Why no cert?", "Cert sent by courier two days later"),
                (
                    "Why by courier?",
                    "No agreement to send certs digitally with the invoice",
                ),
            ],
        ),
        (
            "Forklift shared with dispatch; material movement queued behind truck loading",
            "Fixed forklift time slots: production feed 06:30-07:00 and 14:00-14:30",
            None,
            "Check the forklift slot board at the gate",
            [
                ("Why did the line wait?", "Pallet not moved from stores to the line"),
                ("Why not moved?", "Forklift busy loading a truck"),
                ("Why busy?", "One forklift shared between dispatch and production"),
                (
                    "Why the clash?",
                    "Trucks arrive at shift change, same time as line feeding",
                ),
                ("Why at shift change?", "No slotting agreed with transporters"),
            ],
        ),
    ],
    "SETUP_CHANGEOVER": [
        (
            "Tooling collected from stores during the changeover, not before it",
            "Pre-kitted the changeover trolley with fixtures, gauges and setting sheet one hour before job end",
            "Changeover trolley and setting sheets",
            "Confirm the kit is at the machine before the previous job ends",
            [
                ("Why did the changeover take 40 min?", "Setter walked to stores twice"),
                ("Why twice?", "Gauges and clamps were not at the machine"),
                ("Why not?", "Nobody prepares the next job's kit"),
                ("Why nobody?", "The next job is not known until the current one ends"),
                ("Why not known?", "Job sequence is decided verbally, not on the board"),
            ],
        ),
        (
            "First-off approval waits for the inspector, who is on another line",
            "Setter trained and authorised for first-off on standard parts; inspector signs later",
            None,
            "Check whether the part is on the setter-approval list",
            [
                ("Why was the machine idle after setup?", "Waiting for first-off approval"),
                ("Why waiting?", "Inspector on the other line"),
                ("Why only the inspector?", "Only QC can approve first-off"),
                ("Why only QC?", "Setters not trained on the gauge set"),
                ("Why not trained?", "No training plan for first-off authority"),
            ],
        ),
    ],
    "TOOL_CHANGE": [
        (
            "Planned tool changes happening mid-cycle because tool life is tracked on paper",
            "Tool-life counters entered in the controller; changes planned at break times",
            None,
            "Check remaining tool life on the controller before the break",
            [
                ("Why did tool changes stop production?", "Changed mid-run"),
                ("Why mid-run?", "Tool life reached unexpectedly"),
                ("Why unexpected?", "Life tracked on a paper card that is not updated"),
                ("Why not updated?", "Operators rotate between machines"),
                ("Why paper?", "Controller counters were never set up"),
            ],
        ),
    ],
    "MACHINE_BREAKDOWN": [
        (
            "Bearing seized — wrong grease grade used at last PM",
            "Replaced spindle bearing set; PM sheet now names the grease grade and quantity",
            "Spindle bearing set (NN3014) and grease",
            "Check the grease grade on the PM sheet matches the tin",
            [
                ("Why did the machine stop?", "Spindle bearing seized"),
                ("Why seized?", "Grease broke down under temperature"),
                (
                    "Why broke down?",
                    "General-purpose grease used instead of high-speed spindle grease",
                ),
                ("Why the wrong grease?", "PM sheet says 'grease' without a grade"),
                ("Why no grade?", "PM sheet copied from the old machine's manual"),
            ],
        ),
        (
            "Hydraulic pressure drop — suction strainer choked",
            "Cleaned the tank, replaced the suction strainer, oil changed; strainer added to monthly PM",
            "Suction strainer and 60 L hydraulic oil (VG 68)",
            "Check the hydraulic pressure gauge reads above 40 bar at start",
            [
                ("Why did the machine stop?", "Low hydraulic pressure alarm"),
                ("Why low pressure?", "Pump cavitating"),
                ("Why cavitating?", "Suction strainer choked with sludge"),
                ("Why sludge?", "Oil not changed for 3 years"),
                ("Why not changed?", "Hydraulic oil change not on the PM plan"),
            ],
        ),
        (
            "Drive belt slipping and burnt; tension never re-checked after motor replacement",
            "Replaced the belt set, aligned pulleys with a laser, tension re-check added at 50 hours",
            "V-belt set (B-54 x 3)",
            "Check belt tension with the gauge at the weekly PM",
            [
                ("Why did the machine stop?", "Main drive tripped"),
                ("Why tripped?", "Belt slipping, motor overloaded"),
                ("Why slipping?", "Belt glazed and loose"),
                ("Why loose?", "Tension not re-checked after the motor was replaced"),
                ("Why not re-checked?", "No run-in check after major repairs"),
            ],
        ),
    ],
    "MINOR_ADJUSTMENT": [
        (
            "Tool offset drifting with spindle warm-up; operator corrects every hour",
            "10-minute warm-up program at shift start; offsets set after warm-up",
            None,
            "Run the warm-up program before touching offsets",
            [
                ("Why so many small stops?", "Operator adjusts the offset every hour"),
                ("Why adjust?", "Size drifts by 15 microns"),
                ("Why drift?", "Spindle growing as it warms up"),
                ("Why not compensated?", "No warm-up before the first-off"),
                ("Why no warm-up?", "Seen as lost production time"),
            ],
        ),
    ],
    "NO_OPERATOR": [
        (
            "Only one operator certified on {station}; absence stops the machine",
            "Second operator trained and certified on {station}; skill matrix updated",
            None,
            "Check the skill matrix for a certified backup before the shift roster is fixed",
            [
                ("Why was {station} idle?", "Rostered operator absent"),
                ("Why no replacement?", "Nobody else certified on {station}"),
                ("Why only one?", "Training given only to the original operator"),
                ("Why not more?", "No time released for training"),
                ("Why no time?", "Training loss never compared with downtime loss"),
            ],
        ),
    ],
    "QUALITY_HOLD": [
        (
            "Gauge out of calibration flagged good parts as reject; lot held",
            "Recalibrated the plug gauge, added a daily master-check before first-off",
            "Plug gauge GO/NO-GO (recalibrated)",
            "Check the gauge against the master before the first-off",
            [
                ("Why was the lot held?", "Bore readings borderline"),
                ("Why borderline?", "Gauge worn beyond its tolerance"),
                ("Why not detected?", "Calibration due date passed"),
                ("Why passed?", "Calibration register not reviewed"),
                ("Why not reviewed?", "No owner for the calibration register"),
            ],
        ),
        (
            "Weld current drifting as tip dresses wear; nugget size falling",
            "Tip dressing every 250 welds with a counter; current checked with a weld meter each shift",
            "Electrode tips (CuCrZr)",
            "Check the tip-dress counter and weld current at shift start",
            [
                ("Why was the batch held?", "Peel test failed"),
                ("Why failed?", "Nugget diameter below 4 mm"),
                ("Why small?", "Electrode tip mushroomed"),
                ("Why mushroomed?", "Tips dressed only when the operator notices"),
                ("Why only then?", "No weld counter for dressing interval"),
            ],
        ),
    ],
    "PROGRAM_ISSUE": [
        (
            "Unproven program revision loaded — old and new programs saved with the same name",
            "Program library moved to a numbered revision folder; only released programs on the DNC",
            None,
            "Check the program revision on the setting sheet matches the controller",
            [
                ("Why did the first part fail?", "Wrong program revision"),
                ("Why the wrong one?", "Two programs with the same name"),
                ("Why the same name?", "Programmer saves edits over copies"),
                ("Why no control?", "No revision numbering rule"),
                ("Why no rule?", "Program control never part of the process"),
            ],
        ),
    ],
    "FIXTURE_ISSUE": [
        (
            "Fixture wear allowing part movement during the cut",
            "Re-cut the fixture locating pin; hardened pin fitted",
            "Locating pin and clamp pad",
            "Check pin height with the gauge before first-off",
            [
                ("Why was size inconsistent?", "Part moving in the fixture"),
                ("Why moving?", "Locating pin worn undersize"),
                ("Why worn?", "Soft pin, not hardened"),
                ("Why soft?", "Pin made in-house as a quick replacement"),
                ("Why not replaced properly?", "No spare pins stocked for this fixture"),
            ],
        ),
    ],
    "COOLANT_ISSUE": [
        (
            "Coolant diluted below spec, causing thermal drift and poor finish",
            "Corrected coolant concentration to 8%; refractometer check logged at shift start",
            "Coolant mix ratio set to 8%",
            "Check the refractometer reading at shift start",
            [
                (
                    "Why did the machine stop?",
                    "Surface finish rejects, operator stopped to top up",
                ),
                ("Why poor finish?", "Coolant concentration at 3%"),
                ("Why low?", "Topped up with plain water"),
                ("Why water?", "Concentrate not available at the machine"),
                ("Why not available?", "Concentrate issued monthly, runs out mid-month"),
            ],
        ),
    ],
    "WIP_STARVED": [
        (
            "Previous stage producing to its own plan, not to the assembly line's need",
            "Two-bin kanban between machining and assembly; machining pulls from the bin signal",
            "Kanban bins and cards",
            "Check the kanban bin level at the handover",
            [
                ("Why was assembly waiting?", "No machined parts at the line"),
                ("Why none?", "Machining ran a different part"),
                ("Why a different part?", "Machining schedule made independently"),
                ("Why independently?", "No signal from assembly to machining"),
                ("Why no signal?", "Push scheduling, no pull system"),
            ],
        ),
    ],
    "DIE_CHANGE": [
        (
            "Die clamping bolts different sizes on every die; setter hunts for spanners",
            "Standardised clamp heights and bolts on all dies; quick-clamps fitted on the bolster",
            "Clamp bolts and quick-clamps",
            "Check the die has the standard clamp height plate",
            [
                ("Why did the die change take 45 min?", "Clamping took 20 min"),
                ("Why 20 min?", "Different bolts and packing for each die"),
                ("Why different?", "Dies made by different toolmakers"),
                ("Why not standardised?", "No die design standard given to toolmakers"),
                ("Why no standard?", "Die change time never measured before"),
            ],
        ),
    ],
    "COIL_CHANGE": [
        (
            "Next coil not staged on the decoiler cart; crane shared with another bay",
            "Next coil staged on a second cart before the running coil ends; crane slot booked",
            None,
            "Check the coil ends indicator and the staged coil at every hour entry",
            [
                ("Why did the press stop?", "Coil ran out"),
                ("Why a long stop?", "Next coil still in stores"),
                ("Why in stores?", "Crane busy in the other bay"),
                ("Why not staged earlier?", "No visual warning before the coil ends"),
                ("Why no warning?", "Coil-end sensor never installed"),
            ],
        ),
    ],
    "DIE_CRACK": [
        (
            "Punch cracked from misfeed — pilot pin worn and strip not located",
            "Replaced punch and pilot pins; misfeed sensor wired to the press stop",
            "Piercing punch and 2 pilot pins",
            "Check the misfeed sensor trips the press before starting",
            [
                ("Why did the punch crack?", "Struck the strip off-position"),
                ("Why off-position?", "Pilot pin failed to locate the strip"),
                ("Why failed?", "Pilot pin worn"),
                ("Why not detected?", "Misfeed sensor disconnected"),
                ("Why disconnected?", "Nuisance trips after a wiring fault never fixed"),
            ],
        ),
    ],
    "CMM_QUEUE": [
        (
            "All first-off and in-process checks queue at the single CMM",
            "Height gauge and bore gauges at the cell for in-process checks; CMM reserved for first-off",
            None,
            "Check whether the dimension is on the in-cell gauge list before sending to CMM",
            [
                ("Why was the cell waiting?", "Parts waiting at the CMM"),
                ("Why waiting?", "CMM busy with in-process checks from 3 cells"),
                ("Why CMM for in-process?", "No gauges at the cell"),
                ("Why no gauges?", "All measurement centralised at start-up"),
                ("Why never changed?", "CMM queue time never measured"),
            ],
        ),
    ],
    "HT_LOAD_WAIT": [
        (
            "Parts reach the furnace in mixed small lots; furnace waits to fill a load",
            "Heat-treatment loading plan by hardness group; headers sequenced to feed full loads",
            None,
            "Check the furnace loading plan before releasing header jobs",
            [
                ("Why was the furnace waiting?", "Not enough parts for a full load"),
                ("Why not enough?", "Parts of different hardness groups arrived together"),
                ("Why mixed?", "Headers sequenced by customer due date only"),
                ("Why only due date?", "Furnace constraint not considered in planning"),
                (
                    "Why not considered?",
                    "Planning done without the heat-treatment in-charge",
                ),
            ],
        ),
    ],
    "POWER_CUT": [
        (
            "Grid supply interruption",
            "DG auto-changeover panel commissioned for the bottleneck machines",
            "Auto-mains-failure panel",
            "Check the DG diesel level at shift start",
            [
                ("Why did the plant stop?", "Grid power failed"),
                ("Why a long stop?", "DG started manually"),
                ("Why manually?", "No auto-changeover"),
                ("Why none?", "Panel quoted but never ordered"),
                ("Why not ordered?", "Cost of power cuts never computed"),
            ],
        ),
    ],
}

GENERIC_FIX = (
    "Standard work not followed at {station}",
    "Standard work re-explained and a visual checklist posted at {station}",
    None,
    "Check the checklist is being ticked at the start of every shift",
    [
        ("Why did the stop happen?", "Step missed"),
        ("Why missed?", "Not on a checklist"),
        ("Why no checklist?", "Knowledge held by senior operators"),
        ("Why not written?", "No time allotted"),
        ("Why no time?", "Not seen as a priority"),
    ],
)

DELAY_REASONS = [
    "Spare part on order — vendor ETA Thursday",
    "Maintenance team pulled onto a breakdown on the bottleneck",
    "Waiting for the owner's approval of the spend",
    "Customer schedule change, machine not free for the trial",
    "Toolmaker away this week",
    "Needs a weekend shutdown to fit",
]

CANCEL_REASONS = {
    "S": "Structural loss — logged and costed, but nothing the plant can recover. Cancelled in the morning meeting.",
    "dup": "Duplicate of an Action already open for the same machine and reason.",
}

KAIZEN_TEMPLATES: list[tuple[str, str]] = [
    (
        "Keep a spare insert set at the machine",
        "Every tool breakage we walk to the tool room for inserts. A small locked box at the machine with one set will save 10 minutes each time.",
    ),
    (
        "Stage material on the previous shift",
        "Night shift can stage the trolley for the morning shift. Material wait in the first hour will stop.",
    ),
    (
        "Colour-coded changeover kit",
        "Put all fixtures and gauges for each part in one coloured tray. Setup can start without searching.",
    ),
    (
        "Check the coolant concentration daily",
        "Coolant is often diluted. A refractometer at the machine and one reading per shift.",
    ),
    (
        "Magnetic tool holder on the machine door",
        "Spanners and allen keys fall into the chip tray. A magnetic strip on the door keeps them in reach.",
    ),
    (
        "Second operator trained on the bottleneck",
        "When the regular operator is absent the machine stands. Train one more person during the slow week.",
    ),
    (
        "Chip conveyor cleaning at break time",
        "Chips jam the conveyor and the machine stops. Clean it during the lunch break, not during production.",
    ),
    (
        "Mark the fixture locating pins",
        "Paint mark on the pin shows wear before it causes rejects.",
    ),
    (
        "Bin for rejected parts at every machine",
        "Rejects get mixed with good parts and the whole lot goes on quality hold. A red bin at every machine.",
    ),
    (
        "LED light inside the machine",
        "Hard to see the insert and the part inside the machine. An LED strip will help the operator check without stopping.",
    ),
    (
        "Staging area for the next coil",
        "Mark a place near the press for the next coil so the crane is not needed at the last moment.",
    ),
    (
        "Pre-heat the die before the first strike",
        "First few parts crack in the morning. Warm the die for 10 minutes.",
    ),
    (
        "Barcode on the job card",
        "Scanning the job card instead of writing the job number will save time and mistakes.",
    ),
    (
        "Anti-fatigue mat at the inspection bench",
        "Standing eight hours on concrete. A mat will reduce tiredness and missed defects.",
    ),
    (
        "Label the grease guns by grade",
        "Two grease guns look the same. Label them so the wrong grease is not used on the spindle.",
    ),
    (
        "Move the air gun closer",
        "The air gun hose is short and the operator walks to the pillar to blow chips. Extend the hose.",
    ),
    (
        "Weekly bearing temperature check",
        "Use the IR gun every Monday on spindle bearings. Early warning before breakdown.",
    ),
    (
        "Shadow board for setting tools",
        "Outline every setting tool on a board so missing tools are seen before the changeover.",
    ),
    (
        "Fix the leaking air line near the press",
        "Air hiss all day. Compressor runs more and pressure drops at the press.",
    ),
    (
        "Shift handover note on the board",
        "Next shift does not know what problem happened. Write the top issue on the whiteboard.",
    ),
    (
        "Tip-dress counter on the spot welder",
        "Tips are dressed only when welds look bad. A simple counter will remind every 250 welds.",
    ),
    ("Reduce walking to the CMM", "Keep a height gauge in the cell for simple checks."),
    (
        "Programme revision on the setting sheet",
        "Operators load old programs by mistake. Write the revision on the sheet.",
    ),
    (
        "Furnace loading chart",
        "Group parts by hardness so the furnace gets full loads and does not wait.",
    ),
    (
        "Safety guard on the tapping machine",
        "Chips fly towards the operator. A clear polycarbonate guard will protect and not block the view.",
    ),
    (
        "5S of the tool crib",
        "Tool crib is crowded and dusty. One afternoon of sorting will save time every day.",
    ),
    (
        "Drain cover for the coolant sump",
        "Rain water enters the coolant sump through the floor drain.",
    ),
    (
        "Spare V-belts in the maintenance store",
        "When a belt breaks we wait half a day for purchase.",
    ),
]

KAIZEN_REJECT_NOTES = [
    "Good idea but the machine is being replaced next quarter.",
    "Already covered by an open Action.",
    "Cost of the change is more than the loss it removes — revisit if losses rise.",
    "Needs customer approval for a process change; parked.",
]

COPILOT_QUESTIONS: dict[str, list[str]] = {
    "today": [
        "What happened today?",
        "How much did we lose today?",
        "Which machine is costing the most right now?",
        "Give me the status of the plant",
        "Anything I should know before the meeting?",
        "இன்று எவ்வளவு நஷ்டம்?",
        "இன்றைய நிலவரம் என்ன?",
    ],
    "period_loss": [
        "How much downtime this week?",
        "What is the total recoverable loss this month so far?",
        "How did {station} do this week?",
        "Total loss this week compared to last week?",
        "What did {reason_lower} cost us this month?",
    ],
    "memory": [
        "Have we seen {reason_lower} on {station} before?",
        "What fixed the {reason_lower} last time?",
        "How do we fix {reason_lower} on {station}?",
        "Has {station} ever had this problem previously?",
    ],
    "quality": [
        "What are the top defects?",
        "What is our PPM for the last three weeks?",
        "How many rejects on {part}?",
        "Is scrap going up?",
        "Which defect is costing the most?",
    ],
    "pattern": [
        "Why does {station} keep stopping?",
        "Is there a pattern in {reason_lower} on {station}?",
        "Why do we always lose time on {station}?",
        "What usually goes wrong on {station}?",
    ],
}

PROSPECT_NAMES = [
    "Arul Engineering Works",
    "Bhavani Castings",
    "Chola Precision Components",
    "Dhanalakshmi Auto Parts",
    "Eswari Machine Tools",
    "Ganga Forgings",
    "Hari Priya Industries",
    "Indhu Sheet Metal",
    "Jothi Springs",
    "Kaveri Die Castings",
    "Lakshmi Pressings",
    "Madhura Engineering",
    "Nandhi Gears",
    "Oviya Auto Components",
    "Pavithra Fasteners",
    "Rathna Precision",
    "Saraswathi Machining",
    "Thamarai Engineering",
    "Uma Rubber & Metal",
    "Vasanth Forge",
    "Yamuna Tubes",
    "Aadhi Hydraulics",
    "Bharani Stampings",
    "Cauvery Valves",
    "Deva Engineering",
    "Ezhil Pumps",
    "Guru Tool Room",
    "Ilango Brackets",
    "Kalaivani Castings",
    "Mahesh Precision",
    "Nila Auto Ancillary",
    "Periyar Industries",
    "Ramya Sheet Metal Works",
    "Sri Balaji Components",
    "Sri Murugan Engineering",
    "Sri Ranga Forgings",
    "Thangam Industries",
    "Vetri Engineering",
    "Vinayaga Precision",
    "Anna Auto Parts",
    "Bhuvana Machine Works",
    "Chitra Engineering",
    "Durga Fabricators",
    "Geetha Pressings",
    "Jaya Auto Components",
    "Kanchi Castings",
    "Madurai Precision Tools",
    "Nellai Engineering",
    "Pollachi Pumps & Motors",
    "Salem Steel Components",
    "Tiruppur Engineering",
    "Vellore Forge Works",
    "Hosur Gear Works",
    "Karur Machine Parts",
    "Erode Die Casting",
    "Namakkal Engineering",
]
PROSPECT_CITIES = [
    "Hosur",
    "Coimbatore",
    "Chennai",
    "Madurai",
    "Salem",
    "Tiruchirappalli",
    "Erode",
    "Tiruppur",
    "Karur",
    "Sivakasi",
    "Ranipet",
    "Bengaluru",
    "Tumakuru",
    "Puducherry",
]
