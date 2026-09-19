"""Idea → reason-code matching and credited share, for the simulation only.

Kaizen AI v2 (Plugin Edition) moved pricing out of the plugin entirely: an idea
is now priced by :mod:`app.services.kaizen_costing` against Core's own loss
history, through an async session. The simulation has no session — it is
*producing* the history that pricing would read — so it keeps the older,
purely-textual heuristic here to decide which reason code an invented idea is
about and how much of that reason's annualised loss to credit it with.

This is deliberately a simulation fixture, not product code. It exists to make
``kaizen_ideas`` rows that hang together (an idea about coolant is scored
against coolant stops), not to reproduce what the plugin would charge today.
"""

from __future__ import annotations

import re

# Words an operator actually uses, mapped to the reason codes they point at.
# Deliberately plain keyword matching, not a classifier: a wrong guess here
# produces a slightly-off saving estimate in a test row, never a wrong decision.
#
# Two rules learned the hard way:
#
#  * Keywords match at a word boundary. As bare substrings, "oil" matched "soil",
#    "tool" matched "stool" and "kit" matched "kitchen".
#  * No keyword that names a *place* rather than a *problem*. "Inspection" was
#    here once, and "put a fan near the inspection bench" scored as the plant's
#    most valuable idea — priced against every Quality Hold in the plant.
_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("tool", "insert", "cutter", "drill bit", "tool life"), "TOOL_BREAKAGE"),
    (("material", "stock", "stores", "staging", "trolley", "line feed"), "MATERIAL_WAIT"),
    (("setup", "set-up", "changeover", "change over", "shadow board"), "SETUP_CHANGEOVER"),
    (("breakdown", "motor", "bearing", "spindle", "hydraulic"), "MACHINE_BREAKDOWN"),
    (("coolant", "lubricant", "lubrication", "consumable"), "COOLANT_ISSUE"),
    (("fixture", "jig", "clamp", "locator", "locating pin"), "FIXTURE_ISSUE"),
    (
        ("operator", "manpower", "absent", "absence", "cross-train", "cross train"),
        "NO_OPERATOR",
    ),
    (
        ("defect", "reject", "rejection", "burr", "rework", "scrap", "quality hold"),
        "QUALITY_HOLD",
    ),
    (("program", "cnc code", "offset", "parameter"), "PROGRAM_ISSUE"),
)

#: Share of the annualised loss credited to an idea that names a station.
SCOPED_SHARE = 0.25
#: Share credited when the idea names no station. An idea that does not say
#: where it applies cannot plausibly remove the loss on every machine in the
#: plant, so it is credited far more cautiously.
UNSCOPED_SHARE = 0.10


def match_reason(title: str, description: str) -> str | None:
    """The reason code an idea addresses, or None.

    The title is searched before the description: an operator's title says what
    the idea *is*, while the description often mentions incidental things.
    """
    for text in (title.lower(), description.lower()):
        for keywords, code in _HINTS:
            if any(re.search(rf"\b{re.escape(k)}", text) for k in keywords):
                return code
    return None
