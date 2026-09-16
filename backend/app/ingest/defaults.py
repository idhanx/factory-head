"""Working defaults a plant starts from (§15 T6, §16.4).

"T6 has working defaults out of the box": the plant ticks, edits and adds —
nobody starts from a blank list at 7 AM on day one. Every downtime default is
pre-tagged Recoverable or Structural (§3 Layer 1) and, separately, planned or
unplanned for Availability, so a power cut (Structural but unplanned) is not
mistaken for planned maintenance.

The labels are the examples §3, §4.1 and §15 give. The plant's supervisor
tunes them during the floor walk (§4.1 Step 2).
"""

from __future__ import annotations

# code, label, recoverability (R/S), oee treatment, is_breakdown, escalation route
DOWNTIME_CODES: tuple[tuple[str, str, str, str, bool, str], ...] = (
    ("TOOL_BREAKAGE", "Tool Breakage", "R", "unplanned", False, "maintenance"),
    ("MACHINE_BREAKDOWN", "Machine Breakdown", "R", "unplanned", True, "maintenance"),
    ("MATERIAL_WAIT", "Material Wait", "R", "unplanned", False, "stores"),
    ("SETUP_CHANGEOVER", "Setup / Changeover", "R", "unplanned", False, "production"),
    ("QUALITY_HOLD", "Quality Hold", "R", "unplanned", False, "quality"),
    ("NO_OPERATOR", "No Operator", "R", "unplanned", False, "production"),
    ("MINOR_ADJUSTMENT", "Minor Adjustments", "R", "unplanned", False, "production"),
    ("PROGRAM_ISSUE", "Program / Setting Issue", "R", "unplanned", False, "production"),
    ("FIXTURE_ISSUE", "Fixture / Jig Issue", "R", "unplanned", False, "maintenance"),
    ("COOLANT_ISSUE", "Coolant / Consumables", "R", "unplanned", False, "maintenance"),
    ("GAUGE_WAIT", "Gauge / Inspection Wait", "R", "unplanned", False, "quality"),
    ("HANDLING_WAIT", "Crane / Handling Wait", "R", "unplanned", False, "production"),
    ("FIRST_OFF_APPROVAL", "First-off Approval Wait", "R", "unplanned", False, "quality"),
    ("TOOL_CHANGE_PLANNED", "Tool Change (planned)", "S", "planned", False, "none"),
    ("PLANNED_MAINTENANCE", "Planned Maintenance", "S", "planned", False, "maintenance"),
    ("POWER_CUT", "Power Cut", "S", "unplanned", False, "maintenance"),
    ("MEETING_TRAINING", "Meeting / Training", "S", "planned", False, "none"),
    ("NO_ORDER", "No Order / No Plan", "S", "planned", False, "none"),
)

# code, label
DEFECT_CODES: tuple[tuple[str, str], ...] = (
    ("BURR", "Burr"),
    ("UNDERSIZE", "Undersize"),
    ("OVERSIZE", "Oversize"),
    ("RUST_SPOT", "Rust Spot"),
    ("TOOL_MARK", "Tool Mark"),
    ("DENT_DAMAGE", "Dent / Handling Damage"),
    ("CRACK", "Crack"),
    ("THREAD_DAMAGE", "Thread Damage"),
    ("SURFACE_FINISH", "Poor Surface Finish"),
    ("MISSING_OPERATION", "Missing Operation"),
    ("WRONG_PART", "Wrong Part / Mix-up"),
    ("POROSITY", "Porosity"),
)
