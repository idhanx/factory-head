"""Fact bundles — the only thing an LLM in this product is ever allowed to see.

Facts are assembled here by deterministic SQL and the kernel. The model then
phrases them (C5). This split is the difference between an assistant an owner
trusts and one wrong rupee figure that poisons every number on every screen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.phrases import say
from app.kernel import (
    CoachTier,
    DataSufficiency,
    coach_tier,
    format_inr,
)
from app.models import (
    Action,
    DowntimeEvent,
    DowntimeStatus,
    MemoryEntry,
    Operator,
    Part,
    PdcaState,
    Plant,
    ReasonCode,
    Recoverability,
    Shift,
    ShiftSession,
    ShiftSessionStatus,
    Station,
    UserPlantRole,
)
from app.services.reports import ranked_loss_report


class Limit(str):
    """A gap in what this plant's data can answer, that can say itself again.

    A limit has two readers. The model is handed it as an instruction — "say so
    plainly", "do not claim a machine always does anything" — and the person
    who asked is shown it as part of the deterministic answer when no model is
    available. Those are not the same sentence, and only the second one is ever
    translated.

    It subclasses ``str`` so everything that already treats a limit as a
    finished English sentence keeps working untouched: the prompt, the stored
    fact bundle, the dataset generator. What it adds is the memory of how it was
    built, which is what lets the same gap be stated in Tamil or Hindi instead
    of being a finished English sentence nobody can translate.
    """

    #: The English source, with ``{placeholders}`` — the key into the phrase book.
    template: str
    #: The values those placeholders take. Never translated: they are machine
    #: codes and counts.
    params: dict
    #: English-only tail aimed at the model. Never shown to a person.
    instruction: str

    def __new__(cls, template: str, *, instruction: str = "", **params: object):
        note = say("en", template, **params)
        whole = f"{note} {instruction}".strip() if instruction else note
        limit = super().__new__(cls, whole)
        limit.template = template
        limit.params = params
        limit.instruction = instruction
        return limit

    def note(self, language: str = "en") -> str:
        """The part a person reads, in their language."""
        return say(language, self.template, **self.params)


def limit_note(limit: str, language: str = "en") -> str:
    """``limit`` as a person should read it.

    Plain strings are accepted because the dataset generator builds bundles of
    its own (``scripts/dataset/simulate.py``) and appends English sentences
    directly. Those stay English rather than crashing the answer.
    """
    note = getattr(limit, "note", None)
    return note(language) if callable(note) else str(limit)


@dataclass(slots=True)
class FactBundle:
    """Everything the model may know, and nothing else."""

    plant_name: str
    as_of: date
    tier: int
    facts: dict = field(default_factory=dict)
    evidence: list[dict] = field(default_factory=list)
    limits: list[str] = field(default_factory=list)

    def to_prompt(self) -> str:
        lines = [
            f"PLANT: {self.plant_name}",
            f"DATE: {self.as_of.isoformat()}",
            "",
            "FACTS:",
        ]
        for key, value in self.facts.items():
            lines.append(f"- {key}: {value}")
        if self.evidence:
            lines.append("")
            lines.append("EVIDENCE:")
            for item in self.evidence:
                lines.append("- " + "; ".join(f"{k}={v}" for k, v in item.items()))
        if self.limits:
            lines.append("")
            lines.append("WHAT YOU DO NOT KNOW (say so if asked):")
            lines.extend(f"- {limit}" for limit in self.limits)
        return "\n".join(lines)


async def data_sufficiency(
    session: AsyncSession, *, plant: Plant, as_of: date
) -> DataSufficiency:
    """How much evidence does this plant actually have?

    §14.6's tiers are enforced here, by counting rows — never by asking a model
    to be modest in a prompt.
    """
    complete_shifts = await session.scalar(
        select(func.count(ShiftSession.id)).where(
            ShiftSession.plant_id == plant.id,
            ShiftSession.status == ShiftSessionStatus.CLOSED,
        )
    )
    events = await session.scalar(
        select(func.count(DowntimeEvent.id)).where(
            DowntimeEvent.plant_id == plant.id,
            DowntimeEvent.status == DowntimeStatus.CLOSED,
        )
    )
    first = await session.scalar(
        select(func.min(ShiftSession.business_date)).where(
            ShiftSession.plant_id == plant.id
        )
    )
    days_live = (as_of - first).days if first else 0

    return DataSufficiency(
        complete_shifts=int(complete_shifts or 0),
        days_live=days_live,
        distinct_events=int(events or 0),
    )


async def build_shift_facts(
    session: AsyncSession, *, plant: Plant, as_of: date
) -> FactBundle:
    """Facts for the 5:45 AM Coach briefing."""
    sufficiency = await data_sufficiency(session, plant=plant, as_of=as_of)
    tier = coach_tier(sufficiency)
    yesterday = as_of - timedelta(days=1)

    losses = await ranked_loss_report(session, plant=plant, start=yesterday, end=yesterday)

    bundle = FactBundle(
        plant_name=plant.name,
        as_of=as_of,
        tier=int(tier),
        facts={
            "yesterday_recoverable_loss": format_inr(losses.recoverable_total),
            "yesterday_structural_loss": format_inr(losses.structural_total),
            "yesterday_downtime_minutes": float(losses.total_minutes),
            "shifts_of_history": sufficiency.complete_shifts,
            "days_live": sufficiency.days_live,
        },
        evidence=[
            {
                "rank": index + 1,
                "station": line["station"],
                "reason": line["reason"],
                "minutes": line["minutes"],
                "cost": line["cost_display"],
                "occurrences": line["event_count"],
            }
            for index, line in enumerate(losses.recoverable[:5])
        ],
    )

    if tier >= CoachTier.PATTERN:
        bundle.facts["recurring_patterns"] = await _recurring_patterns(
            session, plant=plant, as_of=as_of
        )
    else:
        bundle.limits.append(
            Limit(
                "Not enough history for pattern analysis (needs about 4 weeks).",
                instruction=(
                    "Do not claim a machine 'usually' or 'always' does anything."
                ),
            )
        )

    overdue = await _overdue_actions(session, plant=plant, as_of=as_of)
    if overdue:
        bundle.facts["overdue_actions"] = overdue

    return bundle


#: Questions about the company itself rather than about a number it produced:
#: what we make, who we make it for, how many machines, which shifts, how many
#: people. Every one of those is answered from the setup data the plant entered
#: at onboarding (§15, templates T1-T7), so they are as grounded as a loss
#: figure is — and refusing them as "not a factory question" was the product
#: being obtuse about its own records.
#:
#: These also open the on-topic gate in `copilot.py`, which is the other half
#: of the same decision.
COMPANY_WORDS = (
    # Single words are matched whole (see `mentions`), so "production" is never
    # read as "product" and an output question is not answered with the part
    # master.
    "company",
    "companies",
    "business",
    "firm",
    "organisation",
    "organization",
    "factory",
    "owner",
    "owners",
    "address",
    "located",
    "employee",
    "employees",
    "staff",
    "workforce",
    "headcount",
    "customer",
    "customers",
    "client",
    "clients",
    "product",
    "products",
    "currency",
    # Phrases are matched as written.
    "plant name",
    "our plant",
    "about us",
    "who are we",
    "who owns",
    "where are we",
    "head office",
    "how many people",
    "how many machines",
    "how many stations",
    "how many operators",
    "part number",
    "what do we make",
    "what do we produce",
    "working days",
    "shift timing",
    "go live",
    "go-live",
    "since when",
    # Tamil and Hindi stems, matched as written. Deliberately narrow: "plant"
    # and "factory" are left out of both, because ஆலையில் இன்று and प्लांट में
    # आज open a question about today's losses, not about the company.
    "நிறுவன",
    "கம்பெனி",
    "வாடிக்கையாள",
    "உரிமையாள",
    "ஊழியர்",
    "பணியாளர்",
    "எத்தனை இயந்திர",
    "எத்தனை பேர்",
    "ஷிப்ட் நேர",
    "கஸ்டமர்",
    "कंपनी",
    "ग्राहक",
    "मालिक",
    "कर्मचारी",
    "स्टाफ",
    "स्टाफ़",
    "उत्पाद",
    "बनाते हैं",
    "बनाती है",
    "कितनी मशीन",
    "कितने लोग",
    "कितने ऑपरेटर",
    "शिफ़्ट का समय",
    "शिफ्ट का समय",
)


#: Which part of the company profile a question is actually about, and the
#: facts that answer it.
#:
#: Asking who our customers are and being read back the machine count, the
#: shift pattern and the working-day calendar is the same confidently
#: irrelevant reply the memory branch exists to avoid. So the profile is cut
#: down to what was asked for, and only a question with no particular aspect to
#: it — "tell me about the company" — gets all of it.
COMPANY_ASPECTS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ("name", "called", "நாம", "பெயர்", "नाम"),
        ("location",),
    ),
    (
        ("customer", "customers", "client", "clients", "வாடிக்கையாள", "கஸ்டமர்", "ग्राहक"),
        ("customers", "part_families"),
    ),
    (
        (
            "product",
            "products",
            "part number",
            "what do we make",
            "what do we produce",
            "தயாரி",
            "பாக",
            "उत्पाद",
            "बनात",
            "पुर्ज़",
        ),
        ("active_part_numbers", "part_families", "customers"),
    ),
    (
        ("machine", "machines", "station", "stations", "இயந்திர", "मशीन"),
        ("machines", "sections"),
    ),
    (
        (
            "employee",
            "employees",
            "staff",
            "people",
            "workforce",
            "headcount",
            "operator",
            "operators",
            "ஊழியர்",
            "பணியாள",
            "कर्मचारी",
            "स्टाफ",
            "लोग",
        ),
        ("operators_on_roll", "people_with_a_login"),
    ),
    (
        ("shift", "shifts", "timing", "timings", "ஷிப்ட்", "शिफ़्ट", "शिफ्ट"),
        ("shift_pattern", "working_days_per_year"),
    ),
    (
        ("where", "address", "located", "location", "இடம்", "எங்கே", "जगह", "कहाँ"),
        ("location",),
    ),
    (
        ("since", "go live", "go-live", "how long", "எப்போதிருந்து", "कब से"),
        ("running_since", "days_live", "shifts_of_history"),
    ),
)

#: On every company answer, whatever was asked. A profile that does not say
#: whose profile it is can be read against the wrong plant.
COMPANY_ALWAYS = ("company_name", "plant_code")


def mentions(text: str, words: tuple[str, ...]) -> bool:
    """Does this question use one of these words?

    Single words match whole words only. Substring matching is what turns
    "production" into a question about products and "reasonable" into a
    question about reason codes — a wrong branch produces the right data
    attached to the wrong noun, which is the one failure §3 Layer 3 is built
    to avoid.
    """
    tokens = set(re.findall(r"[a-z0-9'-]+", text.lower()))
    return any(
        word in tokens
        # Whole-word matching only makes sense for a language that separates
        # words the way English does. Tamil and Hindi inflect the noun itself —
        # நிறுவனம் becomes நிறுவனத்தின், कंपनी becomes कंपनी का — so those are
        # listed as stems and matched as written.
        if word.isascii() and " " not in word
        else word in text
        for word in words
    )


#: What each branch listens for. Matched as substrings rather than whole words,
#: which is why "defects" finds "defect" and "weekly" finds "week" — a blunt
#: match into the right branch beats a precise one into the fallback.
#:
#: Each list carries the Tamil and Hindi a question is actually typed in. The
#: language picker sets what the answer comes back in, but somebody reading the
#: screen in Hindi types in Hindi, and a question that classified as
#: "unrecognised" purely for being written in the language the product just
#: offered them is the picker breaking the thing it was added to improve.
PERIOD_WORDS = (
    "week",
    "month",
    "so far",
    "total",
    "வாரம்",
    "வார",
    "மாதம்",
    "மாத",
    "हफ़्त",
    "हफ्त",
    "सप्ताह",
    "महीन",
)

MEMORY_WORDS = (
    "ever",
    "before",
    "seen",
    "last time",
    "previously",
    "how do we fix",
    "what fixed",
    "முன்பு",
    "கடைசி முறை",
    "எப்படி சரி",
    "पहले कभी",
    "पिछली बार",
    "कैसे ठीक",
    "पहले भी",
)

QUALITY_WORDS = (
    "defect",
    "reject",
    "quality",
    "ppm",
    "burr",
    "scrap",
    "குறைபாடு",
    "தரம்",
    "நிராகரி",
    "ஸ்கிராப்",
    "दोष",
    "रिजेक्ट",
    "गुणवत्ता",
    "क्वालिटी",
    "स्क्रैप",
)

PATTERN_WORDS = (
    "why",
    "keep",
    "always",
    "usually",
    "pattern",
    "ஏன்",
    "திரும்பத் திரும்ப",
    "எப்போதும்",
    "क्यों",
    "बार-बार",
    "बार बार",
    "हमेशा",
)

#: Questions that really are about the state of the plant right now. Anything
#: that matches none of the branches below is answered with today's numbers too,
#: but is told so rather than being given them as the answer.
TODAY_WORDS = (
    "today",
    "now",
    "current",
    "currently",
    "right now",
    "how are we",
    "how is the plant",
    "this shift",
    "status",
    "இன்று",
    "இப்போது",
    "தற்போது",
    "आज",
    "अभी",
    "इस समय",
)


async def build_question_facts(
    session: AsyncSession, *, plant: Plant, question: str, as_of: date
) -> tuple[FactBundle, str]:
    """Facts for one Copilot question, plus the intent we classified it as.

    Intent classification is keyword-based on purpose: it decides which SQL to
    run, and a wrong guess should produce a narrower answer, not a wrong one.
    """
    text = question.lower()
    sufficiency = await data_sufficiency(session, plant=plant, as_of=as_of)
    tier = coach_tier(sufficiency)

    bundle = FactBundle(plant_name=plant.name, as_of=as_of, tier=int(tier))

    # If the question names a machine, every fact must be about that machine.
    # Answering "why does Drill-2 keep failing?" with CNC-3's pattern is the
    # right data attached to the wrong noun — and an owner who spots that once
    # stops believing the rest of the numbers.
    station_code = await _station_mentioned(session, plant=plant, text=text)
    if station_code:
        bundle.facts["about_station"] = station_code

    # Asked first. "How many people work here in total?" contains "total" and
    # would otherwise be answered with a week of losses.
    if mentions(text, COMPANY_WORDS):
        intent = "company"
        facts, evidence = await _company_facts(
            session, plant=plant, sufficiency=sufficiency, text=text
        )
        bundle.facts.update(facts)
        bundle.evidence = evidence

    elif any(word in text for word in PERIOD_WORDS):
        intent = "period_loss"
        start = as_of - timedelta(days=7)
        report = await ranked_loss_report(session, plant=plant, start=start, end=as_of)
        previous = await ranked_loss_report(
            session, plant=plant, start=start - timedelta(days=7), end=start
        )
        bundle.facts.update(
            {
                "period": f"{start.isoformat()} to {as_of.isoformat()}",
                "recoverable_loss": format_inr(report.recoverable_total),
                "previous_period_loss": format_inr(previous.recoverable_total),
                "change": format_inr(report.recoverable_total - previous.recoverable_total),
            }
        )
        bundle.evidence = (
            [line for line in report.recoverable if line["station"] == station_code][:5]
            if station_code
            else report.recoverable[:5]
        )

    # Memory is checked first: "have we ever had burrs on brackets before?"
    # contains a quality keyword but is a Factory Memory question, and answering
    # it with this month's PPM is exactly the kind of confidently-irrelevant
    # reply that loses trust.
    elif any(phrase in text for phrase in MEMORY_WORDS):
        intent = "memory"
        bundle.evidence = await _memory_matches(session, plant=plant, query=question)
        if not bundle.evidence:
            bundle.limits.append(
                Limit(
                    "Factory Memory has no recorded fix matching this.",
                    instruction="Say so plainly.",
                )
            )

    elif any(word in text for word in QUALITY_WORDS):
        intent = "quality"
        bundle.facts.update(await _quality_facts(session, plant=plant, as_of=as_of))

    elif any(word in text for word in PATTERN_WORDS):
        intent = "pattern"
        if tier < CoachTier.PATTERN:
            # §3 Layer 3 — the tier is enforced, not left to a disclaimer.
            bundle.limits.append(
                Limit(
                    "Only {shifts} shifts of history exist, so pattern questions "
                    "cannot be answered yet. I have seen {events} events like "
                    "this; I'll give you pattern analysis once I've seen more.",
                    instruction="Say that rather than describing a pattern.",
                    shifts=sufficiency.complete_shifts,
                    events=sufficiency.distinct_events,
                )
            )
        else:
            patterns = await _recurring_patterns(
                session, plant=plant, as_of=as_of, station_code=station_code
            )
            bundle.facts["recurring_patterns"] = patterns
            if station_code and not patterns:
                bundle.limits.append(
                    Limit(
                        "{station} has no recurring pattern in the last 4 weeks "
                        "— nothing repeated three or more times.",
                        instruction=(
                            "Say that plainly rather than describing a different "
                            "machine."
                        ),
                        station=station_code,
                    )
                )

    else:
        # Today is the default because it is what most unprompted questions are
        # about. But a question that matched nothing at all is not a question
        # about today, and answering "what is our ROI?" with this morning's
        # downtime — stated as though it were the answer — is the same
        # confidently-irrelevant reply the memory branch above exists to avoid.
        # The numbers still go out; what changes is that the answer says they
        # are not what was asked for.
        intent = "today" if any(word in text for word in TODAY_WORDS) else "unrecognised"
        report = await ranked_loss_report(session, plant=plant, start=as_of, end=as_of)
        bundle.facts.update(
            {
                "today_recoverable_loss": format_inr(report.recoverable_total),
                "today_downtime_minutes": float(report.total_minutes),
            }
        )
        bundle.evidence = report.recoverable[:5]
        if intent == "unrecognised":
            bundle.limits.append(
                Limit(
                    "That question was not understood, so this is today's "
                    "position rather than an answer to it. Losses for a period, "
                    "defects and quality, why a machine keeps stopping, what "
                    "fixed something last time, and the company's own details "
                    "can all be asked for by name."
                )
            )

    return bundle, intent


async def _company_facts(
    session: AsyncSession, *, plant: Plant, sufficiency: DataSufficiency, text: str
) -> tuple[dict, list[dict]]:
    """What this plant *is*, as opposed to how it is doing.

    Every line comes from the setup data entered at onboarding — the machine
    list, the part master, the shift pattern, the people who have a login. A
    field the plant never filled in is left out rather than answered with a
    placeholder: §4.1's don't-block rule means half these columns are legally
    empty, and "Location: not recorded" is noise in an answer.
    """
    machines = await session.scalar(
        select(func.count(Station.id)).where(
            Station.plant_id == plant.id, Station.is_active.is_(True)
        )
    )
    sections = (
        await session.scalars(
            select(Station.section)
            .where(Station.plant_id == plant.id, Station.is_active.is_(True))
            .distinct()
        )
    ).all()
    parts = await session.scalar(
        select(func.count(Part.id)).where(
            Part.plant_id == plant.id, Part.is_active.is_(True)
        )
    )
    families = (
        await session.scalars(
            select(Part.part_family)
            .where(
                Part.plant_id == plant.id,
                Part.is_active.is_(True),
                Part.part_family.is_not(None),
            )
            .distinct()
        )
    ).all()
    # Customers are a name and nothing more (§14.1). Capped, because a plant
    # with sixty of them does not want all sixty read out on a phone.
    customers = (
        await session.scalars(
            select(Part.customer_name)
            .where(
                Part.plant_id == plant.id,
                Part.is_active.is_(True),
                Part.customer_name.is_not(None),
            )
            .distinct()
            .order_by(Part.customer_name)
        )
    ).all()
    operators = await session.scalar(
        select(func.count(Operator.id)).where(
            Operator.plant_id == plant.id, Operator.is_active.is_(True)
        )
    )
    people = await session.scalar(
        select(func.count(UserPlantRole.id)).where(UserPlantRole.plant_id == plant.id)
    )
    shifts = (
        await session.scalars(
            select(Shift)
            .where(Shift.plant_id == plant.id, Shift.is_active.is_(True))
            .order_by(Shift.display_order)
        )
    ).all()

    profile: dict = {
        "company_name": plant.name,
        "plant_code": plant.code,
    }
    facts = profile
    if plant.location:
        facts["location"] = plant.location
    facts["machines"] = int(machines or 0)
    if sections:
        facts["sections"] = ", ".join(
            sorted(getattr(section, "value", section) for section in sections)
        )
    if shifts:
        facts["shift_pattern"] = " · ".join(
            f"{shift.name} {shift.start_time:%H:%M}-{shift.end_time:%H:%M}"
            for shift in shifts
        )
    facts["active_part_numbers"] = int(parts or 0)
    if families:
        facts["part_families"] = ", ".join(sorted(families))
    if customers:
        facts["customers"] = ", ".join(customers[:8])
    facts["operators_on_roll"] = int(operators or 0)
    facts["people_with_a_login"] = int(people or 0)
    facts["working_days_per_year"] = plant.working_days_per_year
    facts["currency"] = plant.currency
    if plant.go_live_date:
        facts["running_since"] = plant.go_live_date.isoformat()
    facts["days_live"] = sufficiency.days_live
    facts["shifts_of_history"] = sufficiency.complete_shifts

    evidence = [
        {
            "shift": shift.name,
            "starts": shift.start_time.strftime("%H:%M"),
            "ends": shift.end_time.strftime("%H:%M"),
            "break_minutes": shift.break_minutes,
            **({"weekly_off": shift.weekly_off} if shift.weekly_off else {}),
        }
        for shift in shifts
    ]

    wanted: set[str] = set()
    for words, keys in COMPANY_ASPECTS:
        if mentions(text, words):
            wanted.update(keys)
    if not wanted:
        return profile, evidence

    wanted.update(COMPANY_ALWAYS)
    return (
        {key: value for key, value in profile.items() if key in wanted},
        evidence if "shift_pattern" in wanted else [],
    )


async def _station_mentioned(
    session: AsyncSession, *, plant: Plant, text: str
) -> str | None:
    """Find a station code named in the question.

    Matched against the plant's real station list rather than a regex, so
    "Drill-2" resolves and "line 2" does not masquerade as one.
    """
    codes = (
        await session.scalars(select(Station.code).where(Station.plant_id == plant.id))
    ).all()
    # Longest first, so "CNC-10" is not shadowed by "CNC-1".
    for code in sorted(codes, key=len, reverse=True):
        if code.lower() in text:
            return code
    return None


async def _recurring_patterns(
    session: AsyncSession,
    *,
    plant: Plant,
    as_of: date,
    days: int = 28,
    station_code: str | None = None,
) -> list[dict]:
    rows = (
        await session.execute(
            select(
                Station.code,
                ReasonCode.label,
                ReasonCode.code,
                func.count(DowntimeEvent.id),
                func.sum(DowntimeEvent.cost_total),
                func.avg(DowntimeEvent.duration_minutes),
            )
            .join(Station, Station.id == DowntimeEvent.station_id)
            .join(ReasonCode, ReasonCode.id == DowntimeEvent.reason_code_id)
            .where(
                DowntimeEvent.plant_id == plant.id,
                DowntimeEvent.business_date >= as_of - timedelta(days=days),
                DowntimeEvent.business_date <= as_of,
                DowntimeEvent.status == DowntimeStatus.CLOSED,
                # Recoverable only. Coaching a supervisor about planned
                # maintenance or a power cut wastes the one briefing they read,
                # on money the plant cannot get back either way.
                ReasonCode.recoverability == Recoverability.RECOVERABLE,
                *([Station.code == station_code] if station_code else []),
            )
            .group_by(Station.code, ReasonCode.label, ReasonCode.code)
            .having(func.count(DowntimeEvent.id) >= 3)
            .order_by(func.sum(DowntimeEvent.cost_total).desc())
            .limit(5)
        )
    ).all()

    return [
        {
            "station": station_code,
            "reason": label,
            "reason_code": reason_code,
            "occurrences": int(count),
            "total_cost": format_inr(int(cost or 0)),
            "average_minutes": round(float(avg or 0), 1),
            "interval_days": round(days / max(1, int(count)), 1),
        }
        for station_code, label, reason_code, count, cost, avg in rows
    ]


async def _overdue_actions(
    session: AsyncSession, *, plant: Plant, as_of: date
) -> list[dict]:
    rows = (
        await session.scalars(
            select(Action)
            .where(
                Action.plant_id == plant.id,
                Action.due_date < as_of,
                Action.state.in_(
                    [
                        PdcaState.OPEN,
                        PdcaState.ROOT_CAUSE,
                        PdcaState.PLANNED,
                        PdcaState.IN_PROGRESS,
                    ]
                ),
            )
            .order_by(Action.linked_cost.desc())
            .limit(5)
        )
    ).all()
    return [
        {
            "reference": a.reference,
            "title": a.title,
            "owner": a.owner_name,
            "due": a.due_date.isoformat() if a.due_date else None,
            "cost": format_inr(a.linked_cost),
        }
        for a in rows
    ]


async def _quality_facts(
    session: AsyncSession, *, plant: Plant, as_of: date, days: int = 21
) -> dict:
    from app.models import DefectCode, QualityRecord

    records = (
        await session.scalars(
            select(QualityRecord).where(
                QualityRecord.plant_id == plant.id,
                QualityRecord.business_date >= as_of - timedelta(days=days),
                QualityRecord.business_date <= as_of,
            )
        )
    ).all()

    good = sum(r.good_count for r in records)
    rejected = sum(r.reject_count for r in records)

    defect_totals: dict[str, int] = {}
    for record in records:
        for entry in record.defects or []:
            defect_totals[entry.get("code", "unknown")] = defect_totals.get(
                entry.get("code", "unknown"), 0
            ) + int(entry.get("count", 0))

    top = sorted(defect_totals.items(), key=lambda kv: kv[1], reverse=True)[:3]
    labels = dict(
        (
            await session.execute(
                select(DefectCode.code, DefectCode.label).where(
                    DefectCode.plant_id == plant.id
                )
            )
        ).all()
    )

    from app.kernel import ppm as ppm_fn

    return {
        "period_days": days,
        "good_parts": good,
        "rejected_parts": rejected,
        "ppm": float(ppm_fn(rejected_parts=rejected, total_inspected=good + rejected)),
        "top_defects": [
            {"code": code, "label": labels.get(code, code), "count": count}
            for code, count in top
        ],
    }


async def _memory_matches(
    session: AsyncSession, *, plant: Plant, query: str, limit: int = 5
) -> list[dict]:
    """Factory Memory retrieval.

    Postgres full-text search carries this comfortably past 100 plants. Semantic
    recall via pgvector is added when a plant has the years of history that make
    it worth the second index — Memory with three weeks of history is worthless
    anyway (§3 Layer 4).
    """
    terms = [word for word in query.lower().split() if len(word) > 3][:6]
    if not terms:
        return []

    conditions = [
        MemoryEntry.what_fixed_it.ilike(f"%{term}%")
        | MemoryEntry.problem_summary.ilike(f"%{term}%")
        | MemoryEntry.check_first_next_time.ilike(f"%{term}%")
        for term in terms
    ]
    from sqlalchemy import or_

    rows = (
        await session.scalars(
            select(MemoryEntry)
            .where(MemoryEntry.plant_id == plant.id, or_(*conditions))
            .order_by(MemoryEntry.occurred_on.desc())
            .limit(limit)
        )
    ).all()

    return [
        {
            "when": row.occurred_on.isoformat() if row.occurred_on else "unknown",
            "problem": row.problem_summary,
            "what_fixed_it": row.what_fixed_it,
            "replaced": row.what_was_replaced,
            "check_first": row.check_first_next_time,
            "fixed_by": row.fixed_by_name,
        }
        for row in rows
    ]
