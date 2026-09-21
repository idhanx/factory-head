"""The AI Shift Coach (§3 Layer 2, §14.6).

Sends the incoming supervisor exactly three priorities at 5:45 AM. Each names a
machine, cites the plant's own history, gives a concrete action, a person, a
deadline and a rupee value.

    "It never gives generic advice; if it has nothing specific to say, it says
     less."

The rule-based version ships first and stays the fallback forever: the LLM adds
phrasing and Tamil, never facts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.facts import FactBundle, build_shift_facts
from app.ai.llm import GROUNDING_SYSTEM_PROMPT, LlmUnavailable, OllamaProvider
from app.kernel import CoachTier, format_inr
from app.models import AlertRecipient, CoachBriefing, Plant, Shift


@dataclass(slots=True)
class Priority:
    """One of the three. Every field is mandatory — a priority without an owner
    and a deadline is an observation, and observations do not get done."""

    rank: int
    station: str
    headline: str
    evidence: str
    action: str
    assignee: str
    deadline: str
    value_paise: int

    def as_text(self) -> str:
        return (
            f"PRIORITY {self.rank} — {self.station}: {self.headline}\n"
            f"{self.evidence}\n"
            f"ACTION {self.deadline}: {self.action} "
            f"Assign to {self.assignee}. Value: ~{format_inr(self.value_paise)}."
        )


async def generate_briefing(
    session: AsyncSession,
    *,
    plant: Plant,
    shift: Shift | None = None,
    recipient_id=None,
    as_of: date | None = None,
    use_llm: bool = True,
) -> CoachBriefing:
    as_of = as_of or date.today()
    bundle = await build_shift_facts(session, plant=plant, as_of=as_of)
    tier = CoachTier(bundle.tier)

    priorities = (
        [] if tier is CoachTier.SILENT else await _derive_priorities(session, plant, bundle)
    )
    rendered, generated_by = await _render(
        bundle=bundle, priorities=priorities, tier=tier, use_llm=use_llm
    )

    briefing = CoachBriefing(
        plant_id=plant.id,
        business_date=as_of,
        shift_id=shift.id if shift else None,
        recipient_id=recipient_id,
        tier=int(tier),
        data_basis={
            "shifts_of_history": bundle.facts.get("shifts_of_history"),
            "days_live": bundle.facts.get("days_live"),
        },
        priorities=[asdict(p) for p in priorities],
        fact_bundle={
            "facts": bundle.facts,
            "evidence": bundle.evidence,
            "limits": bundle.limits,
        },
        rendered_text=rendered,
        generated_by=generated_by,
        # Set by the worker once the briefing is actually queued to someone's
        # phone. Generating it is not delivering it.
        delivered_at=None,
    )
    session.add(briefing)
    await session.flush()
    return briefing


async def _derive_priorities(
    session: AsyncSession, plant: Plant, bundle: FactBundle
) -> list[Priority]:
    """Rank candidates by rupees and keep the top three.

    Three is a product decision, not a display limit: a supervisor given seven
    priorities has none. The Coach competes with a shift about to start.
    """
    candidates: list[Priority] = []
    recipients = {
        r.role_tag: r
        for r in (
            await session.scalars(
                select(AlertRecipient).where(
                    AlertRecipient.plant_id == plant.id,
                    AlertRecipient.is_active.is_(True),
                )
            )
        ).all()
    }

    # 1. Recurring patterns first — these are the losses that keep coming back.
    for pattern in bundle.facts.get("recurring_patterns", []) or []:
        owner = recipients.get(_route_for(pattern["reason"]))
        candidates.append(
            Priority(
                rank=0,
                station=pattern["station"],
                headline=f"High {pattern['reason'].lower()} risk today.",
                evidence=(
                    f"{pattern['station']} averages a {pattern['reason'].lower()} every "
                    f"{pattern['interval_days']} days — {pattern['occurrences']} in the "
                    f"last 4 weeks, {pattern['total_cost']} total."
                ),
                action=_action_for(pattern.get("reason_code", ""), pattern["station"]),
                assignee=owner.name if owner else "Production Head",
                deadline="before shift start",
                value_paise=_paise(pattern["total_cost"]),
            )
        )

    # 2. Yesterday's biggest single losses.
    for line in bundle.evidence[:3]:
        owner = recipients.get(_route_for(line.get("reason", "")))
        candidates.append(
            Priority(
                rank=0,
                station=line["station"],
                headline=f"{line['reason']} cost {line['cost']} yesterday.",
                evidence=(
                    f"{line['occurrences']} event(s), {line['minutes']} minutes, "
                    f"{line['cost']} on {line['station']}."
                ),
                action=_action_for(line.get("reason", ""), line["station"]),
                assignee=owner.name if owner else "Production Head",
                deadline="before shift start",
                value_paise=_paise(line["cost"]),
            )
        )

    # 3. Overdue actions — nagging is a feature (§14.6 Tier 1).
    for overdue in bundle.facts.get("overdue_actions", []) or []:
        candidates.append(
            Priority(
                rank=0,
                station=overdue["reference"],
                headline=f"{overdue['title']} is overdue.",
                evidence=f"Due {overdue['due']}, {overdue['cost']} attributed so far.",
                action="Close it out or record why it slipped.",
                assignee=overdue["owner"] or "Action owner",
                deadline="today",
                value_paise=_paise(overdue["cost"]),
            )
        )

    candidates.sort(key=lambda p: p.value_paise, reverse=True)
    seen: set[str] = set()
    top: list[Priority] = []
    for candidate in candidates:
        if candidate.station in seen:
            continue
        seen.add(candidate.station)
        candidate.rank = len(top) + 1
        top.append(candidate)
        if len(top) == 3:
            break
    return top


async def _render(
    *, bundle: FactBundle, priorities: list[Priority], tier: CoachTier, use_llm: bool
) -> tuple[str, str]:
    """Deterministic text first; the LLM only rephrases it.

    If generation fails, the supervisor still gets their briefing. The fallback
    is the product, not a degraded mode.
    """
    if tier is CoachTier.SILENT:
        return (
            "Not enough history yet to coach. Escalations are running; "
            "briefings begin once about ten complete shifts are recorded.",
            "rules",
        )

    deterministic = "\n\n".join(p.as_text() for p in priorities)
    if not deterministic:
        return ("Nothing specific to flag this morning. Have a good shift.", "rules")

    if not use_llm:
        return (deterministic, "rules")

    try:
        provider = OllamaProvider()
        generation = await provider.generate(
            system=GROUNDING_SYSTEM_PROMPT.format(language="English"),
            user=(
                f"{bundle.to_prompt()}\n\n"
                "Rewrite the three priorities below as a short shift briefing. "
                "Keep every number and name exactly as written. Do not add "
                "priorities, numbers or advice of your own.\n\n"
                f"{deterministic}"
            ),
        )
        return (generation.text, "llm")
    except LlmUnavailable:
        return (deterministic, "rules")


# The Coach's whole value is that it names a specific thing to do. "Inspect
# CNC-3" is the advice a supervisor already knows to ignore; "check insert wear
# and replace before the first-off" is the advice that saves the ₹3,200.
_ACTIONS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("tool_breakage", "tool breakage"),
        "Inspect the insert on {station} and replace it before the first-off.",
    ),
    (
        ("tool_change", "tool change"),
        "Pre-stage the replacement tool for {station} so the change happens off-cycle.",
    ),
    (
        ("material_wait", "material wait"),
        "Confirm with Stores that {station}'s full quantity is staged at the line before start.",
    ),
    (
        ("setup_changeover", "setup", "changeover"),
        "Pre-kit the fixtures and first-off gauges for {station}'s next changeover.",
    ),
    (
        ("machine_breakdown", "breakdown"),
        "Run the pre-shift check on {station} and review its last PM date.",
    ),
    (
        ("no_operator", "no operator"),
        "Confirm {station} has a certified operator rostered for the whole shift.",
    ),
    (
        ("quality_hold", "quality hold"),
        "Clear the open quality hold on {station} with the inspector before starting.",
    ),
    (
        ("program_issue", "program", "setting"),
        "Verify the proven program revision is loaded on {station} before first-off.",
    ),
    (
        ("coolant_issue", "coolant", "consumable"),
        "Check {station}'s coolant concentration and top up consumables before start.",
    ),
    (
        ("fixture_issue", "fixture", "jig"),
        "Check the fixture locating pins and clamps on {station} before first-off.",
    ),
    (
        ("minor_adjustment", "minor adjust"),
        "Watch {station}'s first hour — repeated small adjustments usually mean a "
        "drifting offset, not operator error.",
    ),
)


def _action_for(reason: str, station: str) -> str:
    """Map a reason to something a supervisor can actually do in five minutes."""
    text = reason.lower()
    for keywords, template in _ACTIONS:
        if any(keyword in text for keyword in keywords):
            return template.format(station=station)
    return f"Review {station}'s first hour and log what you find."


def _route_for(reason: str) -> str:
    text = reason.lower()
    if "material" in text or "stores" in text:
        return "stores"
    if "tool" in text or "breakdown" in text or "maintenance" in text:
        return "maintenance"
    if "quality" in text or "hold" in text:
        return "quality"
    return "production"


def _paise(display: str) -> int:
    """Parse a ₹-formatted figure back to paise.

    The Coach ranks on the same figures it shows, so there is no second source
    of truth to drift from.
    """
    digits = "".join(ch for ch in str(display) if ch.isdigit())
    return int(digits) * 100 if digits else 0
