"""The AI that does 90% of the work in a Kaizen (Plugin Spec §5, Steps 1-9).

Five jobs, in the order an operator meets them:

  Step 1  clean up what they said        :func:`clean_transcript`
  Step 2  read the photo                 :func:`analyse_photo`
  Step 3  classify it                    :func:`classify`
  Step 4  ask 4-6 questions, not 10      :func:`questions_for`
  Step 7  argue back with a cheaper idea :func:`propose_alternative`

Every one of them runs against a **local Ollama** and every one of them has a
deterministic fallback, because the alternative is an operator standing at a
machine watching a spinner. The rule the whole module is written to:

    "Design the calculation so it silently drops to the next rung if the number
     above isn't available."

Nothing here touches the database and nothing here calculates money. Savings
come from Core's formula module (:mod:`app.services.kaizen_costing`); this
module only decides *what to ask* and *what to call things*.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import structlog

from app.ai.llm import Generation, LlmUnavailable, OllamaProvider
from app.kernel import SavingType
from app.models import KaizenCategory, VisionCategory

log = structlog.get_logger()


# ===========================================================================
# Step 3 — classification
# ===========================================================================

#: Words an operator actually uses, mapped to the category they point at.
#: Matched at word boundaries: as bare substrings "oil" matched "soil", "tool"
#: matched "stool" and "kit" matched "kitchen". Ordered most specific first —
#: the first hit wins, so "walking to get the tool" reads as motion, not tooling.
_CATEGORY_HINTS: tuple[tuple[KaizenCategory, tuple[str, ...]], ...] = (
    (
        KaizenCategory.SAFETY,
        (
            "safety",
            "unsafe",
            "injury",
            "injured",
            "hurt",
            "near miss",
            "near-miss",
            "guard",
            "pinch",
            "sharp edge",
            "trip hazard",
            "fall",
            "shock",
            "burn",
            "ppe",
            "helmet",
            "goggles",
            "fire",
        ),
    ),
    (
        KaizenCategory.QUALITY,
        (
            "defect",
            "reject",
            "rejection",
            "scrap",
            "rework",
            "burr",
            "crack",
            "dimension",
            "out of tolerance",
            "quality hold",
            "customer complaint",
            "ppm",
            "inspection fail",
        ),
    ),
    (
        KaizenCategory.MOTION,
        (
            "walk",
            "walking",
            "walks",
            "reach",
            "reaching",
            "bend",
            "bending",
            "stretch",
            "fetch",
            "go to",
            "metres",
            "meters",
            "steps",
            "twist",
        ),
    ),
    (
        KaizenCategory.WAITING,
        (
            "wait",
            "waiting",
            "waits",
            "idle",
            "queue",
            "delay",
            "held up",
            "stand around",
            "no material",
            "shortage",
        ),
    ),
    (
        KaizenCategory.TRANSPORT,
        (
            "trolley",
            "forklift",
            "carry",
            "carrying",
            "move material",
            "transport",
            "shift the bin",
            "haul",
            "pallet",
        ),
    ),
    (
        KaizenCategory.ENERGY,
        (
            "air leak",
            "compressed air",
            "electricity",
            "power",
            "kwh",
            "energy",
            "motor running",
            "lights on",
            "idling",
            "compressor",
        ),
    ),
    (
        KaizenCategory.MAINTENANCE,
        (
            "breakdown",
            "bearing",
            "spindle",
            "hydraulic",
            "leak",
            "belt",
            "maintenance",
            "preventive",
            "lubrication",
            "coolant",
            "vibration",
            # An operator saying "the insert breaks" is reporting tool breakage,
            # which is a maintenance problem with a recorded reason code behind
            # it — not a "cost" Kaizen with nothing in Core to match against.
            # These match at a word boundary, so "toolbox" is still motion.
            "tool",
            "insert",
            "cutter",
            "drill bit",
            "tool life",
            "tool wear",
        ),
    ),
    (
        KaizenCategory.FIVE_S,
        (
            "5s",
            "shadow board",
            "housekeeping",
            "clutter",
            "untidy",
            "labelled",
            "unlabelled",
            "label",
            "marking",
            "floor marking",
            "sort out",
            "storage",
        ),
    ),
    (
        KaizenCategory.INVENTORY,
        ("stock", "inventory", "wip", "buffer", "over-produce", "overproduce", "kanban"),
    ),
    (
        KaizenCategory.PRODUCTIVITY,
        (
            "cycle time",
            "changeover",
            "setup",
            "set-up",
            "smed",
            "die change",
            "faster",
            "output",
            "speed up",
            "takt",
        ),
    ),
    (
        KaizenCategory.COST,
        # "insert" and "tool life" belong to Maintenance above: what the money
        # is called is decided by the saving type, not by the category.
        ("cost", "expensive", "consumable", "spend", "price", "purchase"),
    ),
)

#: The saving type a category defaults to. Refined by :func:`saving_type_for`
#: when the words say something more specific — a "changeover" Kaizen filed
#: under Productivity is priced as recovered machine hours, not as motion.
_DEFAULT_SAVING_TYPE: dict[KaizenCategory, SavingType] = {
    KaizenCategory.MOTION: SavingType.MOTION,
    KaizenCategory.WAITING: SavingType.WAITING,
    KaizenCategory.TRANSPORT: SavingType.TRANSPORT,
    KaizenCategory.INVENTORY: SavingType.HANDLING,
    KaizenCategory.QUALITY: SavingType.SCRAP,
    KaizenCategory.SAFETY: SavingType.SAFETY,
    KaizenCategory.ENERGY: SavingType.ENERGY,
    KaizenCategory.PRODUCTIVITY: SavingType.CYCLE_TIME,
    KaizenCategory.COST: SavingType.CONSUMABLE,
    KaizenCategory.FIVE_S: SavingType.HOUSEKEEPING,
    KaizenCategory.MAINTENANCE: SavingType.WAITING,
    KaizenCategory.OTHER: SavingType.OTHER,
}

_SAVING_TYPE_HINTS: tuple[tuple[SavingType, tuple[str, ...]], ...] = (
    (
        SavingType.CHANGEOVER,
        ("changeover", "change over", "setup", "set-up", "smed", "die change"),
    ),
    (
        SavingType.CYCLE_TIME,
        ("cycle time", "cycle-time", "seconds per part", "faster cycle"),
    ),
    (SavingType.REWORK, ("rework", "reworked", "re-work", "touch up", "touch-up")),
    (SavingType.SCRAP, ("scrap", "scrapped", "reject", "rejection")),
    (SavingType.COMPRESSED_AIR, ("compressed air", "air leak", "cfm", "pneumatic leak")),
    (SavingType.ENERGY, ("electricity", "kwh", "power bill", "lights", "motor idling")),
    (SavingType.TOOLING, ("tool life", "insert life", "cutter life", "tool wear")),
    (SavingType.CONSUMABLE, ("consumable", "coolant", "lubricant", "abrasive", "glove")),
)


#: A trailing plural, so "inserts", "coolants" and "walks" match their keyword.
#: Deliberately not a bare prefix match: as a prefix, "tool" matched "toolbox"
#: and sent every toolbox Kaizen to Maintenance instead of Motion.
_PLURAL = r"(?:e?s)?"


def _mentions(text: str, words: Iterable[str]) -> bool:
    """Word-boundary matching, both ends.

    As bare substrings, "oil" matched "soil", "tool" matched "stool" and "kit"
    matched "kitchen" — each of which priced an idea against a loss it had
    nothing to do with.
    """
    return any(
        re.search(rf"(?<!\w){re.escape(word)}{_PLURAL}(?!\w)", text) for word in words
    )


def classify(
    title: str, description: str, *, vision: Sequence[dict] = ()
) -> KaizenCategory:
    """The category AI proposes; the operator confirms it in one tap (Step 3).

    The title is searched before the description: an operator's title says what
    the idea *is*, while the description often mentions incidental things. What
    the camera saw is consulted last and only to break a tie — a photo showing a
    5S gap should not overrule an operator who came to report a safety issue.
    """
    for text in (title.lower(), description.lower()):
        for category, words in _CATEGORY_HINTS:
            if _mentions(text, words):
                return category

    seen = {str(f.get("category")) for f in vision}
    if VisionCategory.SAFETY_HAZARD.value in seen:
        return KaizenCategory.SAFETY
    if VisionCategory.LEAK_OR_SPILL.value in seen:
        return KaizenCategory.MAINTENANCE
    if seen & {VisionCategory.HOUSEKEEPING.value, VisionCategory.MISSING_LABEL.value}:
        return KaizenCategory.FIVE_S
    if VisionCategory.OBSTRUCTION.value in seen:
        return KaizenCategory.MOTION
    return KaizenCategory.OTHER


def saving_type_for(
    category: KaizenCategory, title: str = "", description: str = ""
) -> SavingType:
    """How this Kaizen's money must be calculated — which decides the split.

    The distinction that matters is not the lean label but whether the
    bottleneck test applies (§7.2). Getting it wrong is how a scrap saving ends
    up halved by a bottleneck factor that should never have touched it.
    """
    haystack = f"{title} {description}".lower()
    for saving_type, words in _SAVING_TYPE_HINTS:
        if _mentions(haystack, words):
            return saving_type
    return _DEFAULT_SAVING_TYPE.get(category, SavingType.OTHER)


def looks_safety_related(title: str, description: str, vision: Sequence[dict] = ()) -> bool:
    """Safety routing must not depend on the operator picking the right category."""
    if _mentions(
        f"{title} {description}".lower(), dict(_CATEGORY_HINTS)[KaizenCategory.SAFETY]
    ):
        return True
    return any(f.get("category") == VisionCategory.SAFETY_HAZARD.value for f in vision)


#: §10.3 — the same 90 seconds of operator effort also produces a training
#: signal and audit evidence, because other plugins read these tags.
_SIGNAL_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "skill_gap",
        (
            "no operator",
            "not trained",
            "untrained",
            "training",
            "nobody knows",
            "only one person",
            "cross-train",
            "cross train",
            "skill",
        ),
    ),
    (
        "compliance",
        (
            "audit",
            "iso",
            "iatf",
            "zed",
            "documentation",
            "standard work",
            "work instruction",
            "sop",
            "calibration",
            "record",
        ),
    ),
    (
        "maintenance",
        ("breakdown", "preventive", "pm schedule", "bearing", "spindle", "mtbf"),
    ),
)


def cross_plugin_signals(title: str, description: str) -> list[str]:
    """Tags read by Skill Matrix AI and Compliance/Audit AI (§10.3).

    None of these require new data entry from anybody — they are tags on a
    Kaizen that already exists, read by plugins that already exist.
    """
    haystack = f"{title} {description}".lower()
    return [name for name, words in _SIGNAL_HINTS if _mentions(haystack, words)]


# ===========================================================================
# Step 4 — the AI interview
# ===========================================================================


@dataclass(frozen=True, slots=True)
class Question:
    """One question, asked one at a time, like a WhatsApp chat.

    ``options``/``values`` exist because tapping a range beats typing a number
    on a shop floor. The midpoint in ``values`` is what enters the calculation,
    and it is labelled as an estimate everywhere it surfaces.

    ``satisfied_by`` is the plugin edition's whole advantage in Step 4: when
    Core already knows the answer, the question is not asked at all.
    """

    key: str
    text: str
    kind: str = "number"  # choice | number | text | boolean
    options: tuple[str, ...] = ()
    values: tuple[float, ...] = ()
    unit: str | None = None
    satisfied_by: str | None = None
    depends_on: tuple[str, str] | None = None
    optional: bool = False

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "text": self.text,
            "kind": self.kind,
            "options": list(self.options),
            "values": list(self.values),
            "unit": self.unit,
            "depends_on": list(self.depends_on) if self.depends_on else None,
            "optional": self.optional,
        }


_PEOPLE = Question(
    key="people_affected",
    text="How many people does this affect?",
    kind="choice",
    options=("1", "2-3", "4-6", "7+"),
    values=(1, 2.5, 5, 8),
)
_FIX_COST = Question(
    key="fix_cost",
    text="Roughly what would your fix cost?",
    kind="choice",
    options=(
        "Nothing",
        "Under ₹1,000",
        "₹1,000-10,000",
        "₹10,000-50,000",
        "More than ₹50,000",
    ),
    values=(0, 500, 5_000, 25_000, 75_000),
    unit="₹",
)
#: The bottleneck question (§Step 4). Asked only when the answer changes the
#: number, and in the plugin edition almost never asked at all — Core carries a
#: Bottleneck Factor per machine, so ``satisfied_by`` normally removes it.
_BOTTLENECK = Question(
    key="bottleneck",
    text="Does this station ever make the line wait?",
    kind="boolean",
    satisfied_by="bottleneck_factor",
)

#: Each tree is at most five questions. There is no universal question list:
#: asking a canteen Kaizen about cycle time is how an AI looks stupid.
QUESTION_TREES: dict[SavingType, tuple[Question, ...]] = {
    SavingType.MOTION: (
        _PEOPLE,
        Question("trips_per_shift", "How many trips per shift, roughly?", unit="trips"),
        Question(
            "seconds_per_trip",
            "How long is one round trip?",
            kind="choice",
            options=("Under 30 sec", "30-60 sec", "1-2 min", "More than 2 min"),
            values=(20, 45, 90, 180),
            unit="sec",
        ),
        _BOTTLENECK,
        _FIX_COST,
    ),
    SavingType.WAITING: (
        Question("waiting_for", "What is being waited for?", kind="text"),
        Question(
            "minutes_per_wait",
            "How long is one wait?",
            kind="choice",
            options=("Under 5 min", "5-15 min", "15-30 min", "More than 30 min"),
            values=(3, 10, 22, 45),
            unit="min",
        ),
        Question("waits_per_shift", "How many times a shift?", unit="times"),
        _BOTTLENECK,
        _FIX_COST,
    ),
    SavingType.SCRAP: (
        Question(
            "defect_type", "What is the defect?", kind="text", satisfied_by="defect_code"
        ),
        Question(
            "stage",
            "At which stage is it being rejected?",
            kind="text",
            satisfied_by="operation",
        ),
        Question("parts_per_month", "Roughly how many per month?", unit="parts"),
        _FIX_COST,
    ),
    SavingType.REWORK: (
        Question(
            "defect_type", "What is the defect?", kind="text", satisfied_by="defect_code"
        ),
        Question("parts_per_month", "Roughly how many per month?", unit="parts"),
        Question("rework_minutes", "How long does reworking one part take?", unit="min"),
        _FIX_COST,
    ),
    SavingType.CYCLE_TIME: (
        Question("seconds_saved", "How many seconds per part would this save?", unit="sec"),
        Question(
            "cycles_per_shift",
            "How many parts does this station run per shift?",
            unit="parts",
        ),
        _BOTTLENECK,
        _FIX_COST,
    ),
    SavingType.CHANGEOVER: (
        Question(
            "minutes_saved",
            "How many minutes would this take off a changeover?",
            unit="min",
        ),
        Question("changeovers_per_month", "How many changeovers a month?", unit="times"),
        _BOTTLENECK,
        _FIX_COST,
    ),
    SavingType.ENERGY: (
        Question("kw_rating", "What is the equipment's rating?", unit="kW"),
        Question(
            "idle_hours_per_day",
            "How many hours a day does it run when it needn't?",
            unit="hrs",
        ),
        _FIX_COST,
    ),
    SavingType.COMPRESSED_AIR: (
        Question(
            "leak_cfm",
            "How big is the leak?",
            kind="choice",
            options=("A hiss you can hear", "A steady jet", "A loud jet"),
            values=(2, 6, 15),
            unit="CFM",
        ),
        Question(
            "hours_per_day", "How many hours a day is the line pressurised?", unit="hrs"
        ),
        _FIX_COST,
    ),
    SavingType.SAFETY: (
        Question(
            "severity",
            "Has this caused an injury, or is it a near-miss so far?",
            kind="choice",
            options=("Near-miss", "Minor injury", "Serious injury"),
            values=(3, 4, 5),
        ),
        Question(
            "exposure_per_shift",
            "How many times a shift is somebody exposed?",
            unit="times",
        ),
        _PEOPLE,
        _FIX_COST,
    ),
    SavingType.TOOLING: (
        Question(
            "items_per_month", "How many do you get through in a month?", unit="items"
        ),
        Question("item_cost", "Roughly what does one cost?", unit="₹"),
        Question(
            "reduction_percent",
            "How much would your fix cut that by?",
            kind="choice",
            options=("A quarter", "A half", "Three quarters", "Nearly all of it"),
            values=(25, 50, 75, 90),
            unit="%",
        ),
        _FIX_COST,
    ),
    SavingType.HOUSEKEEPING: (
        Question("what_changes", "What would be different afterwards?", kind="text"),
        _PEOPLE,
        _FIX_COST,
    ),
}
#: Types that share a tree with one already defined.
QUESTION_TREES[SavingType.TRANSPORT] = QUESTION_TREES[SavingType.MOTION]
QUESTION_TREES[SavingType.HANDLING] = QUESTION_TREES[SavingType.MOTION]
QUESTION_TREES[SavingType.CONSUMABLE] = QUESTION_TREES[SavingType.TOOLING]
QUESTION_TREES[SavingType.OTHER] = QUESTION_TREES[SavingType.HOUSEKEEPING]

MAX_QUESTIONS = 5


def questions_for(
    saving_type: SavingType,
    *,
    known: Iterable[str] = (),
    answered: Iterable[str] = (),
) -> list[Question]:
    """The 4-6 questions this Kaizen needs, minus everything Core already knows.

    *"Repeating a question the operator already answered is the fastest way to
    feel like a government form."* ``known`` carries the context keys Core
    resolved — ``bottleneck_factor``, ``defect_code``, ``operation`` — and every
    question they satisfy disappears from the interview.
    """
    known_keys = set(known)
    already = set(answered)
    tree = QUESTION_TREES.get(saving_type, QUESTION_TREES[SavingType.OTHER])
    out = [
        q
        for q in tree
        if q.key not in already and not (q.satisfied_by and q.satisfied_by in known_keys)
    ]
    return out[:MAX_QUESTIONS]


def answer_value(question: Question, answer: object) -> float | None:
    """The number an answer contributes, resolving a tapped range to its midpoint."""
    if answer is None or answer == "":
        return None
    if question.kind == "choice" and question.values:
        text = str(answer)
        for label, value in zip(question.options, question.values, strict=False):
            if text == label:
                return float(value)
    if question.kind == "boolean":
        return 1.0 if answer in (True, "true", "yes", "Yes") else 0.0
    try:
        return float(str(answer).replace(",", "").replace("₹", "").strip())
    except ValueError:
        return None


def numeric_answers(saving_type: SavingType, interview: Sequence[dict]) -> dict[str, float]:
    """Interview rows collapsed to the numbers the cost engine needs."""
    tree = {q.key: q for q in QUESTION_TREES.get(saving_type, ())}
    out: dict[str, float] = {}
    for row in interview or ():
        key = str(row.get("key", ""))
        question = tree.get(key)
        if question is None:
            continue
        value = answer_value(question, row.get("answer"))
        if value is not None:
            out[key] = value
    return out


# ===========================================================================
# Step 1 — the voice note, tidied
# ===========================================================================

_TRANSCRIPT_SYSTEM = """You tidy up what a factory operator said into one clear sentence for a Kaizen report.

ABSOLUTE RULES:
1. Change NOTHING factual. Keep every number, distance, machine name and part
   number exactly as spoken. If they said "20 metres", write "20 metres".
2. Add nothing. No cause, no suggestion, no severity, no opinion the operator
   did not say. You are rewriting, not analysing.
3. If the transcript is unclear, keep the unclear part rather than guessing.
4. One or two plain sentences. No bullet points, no headings.
5. Reply with the sentence only — no preamble, no quotes."""


async def clean_transcript(raw: str, *, provider: OllamaProvider | None = None) -> str:
    """Step 1: turn what was said into a clear line, changing no facts.

    Falls back to whitespace-normalised input. A slightly rough sentence costs
    nothing; an invented number costs the product its credibility.
    """
    tidy = re.sub(r"\s+", " ", (raw or "").strip())
    if not tidy:
        return ""
    client = provider or OllamaProvider()
    if not client.enabled:
        return tidy
    try:
        result = await client.generate(
            system=_TRANSCRIPT_SYSTEM, user=tidy, temperature=0.1, max_tokens=200
        )
    except LlmUnavailable as exc:
        log.info("kaizen.transcript.fallback", error=str(exc))
        return tidy
    cleaned = result.text.strip().strip('"')
    # A model that "helpfully" expanded two sentences into a paragraph has
    # started analysing. Keep the operator's own words instead.
    if not cleaned or len(cleaned) > max(400, len(tidy) * 3):
        return tidy
    return _guard_numbers(original=tidy, rewritten=cleaned)


_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def _guard_numbers(*, original: str, rewritten: str) -> str:
    """Refuse a rewrite that introduced a number the operator never said.

    This is the one failure mode that matters here: a wrong number silently
    corrupts the savings calculation, and a wrong savings number is the single
    thing that kills credibility with a Plant Head.
    """
    spoken = set(_NUMBER.findall(original))
    if set(_NUMBER.findall(rewritten)) - spoken:
        return original
    return rewritten


# ===========================================================================
# Step 2 — Vision AI, under its three hard rules
# ===========================================================================

#: Rule 2 — AI always speaks in "possible", never in "confirmed". Vision *will*
#: misread a wet floor as oil, a shadow as a crack and a rag as debris.
#: Confident wrong statements destroy trust faster than no feature at all.
_VISION_SYSTEM = """You look at one photograph taken on a factory shop floor and report only what is visibly in it.

You may report findings in these FIVE categories and no others:
  housekeeping    - things out of place, no marked location, clutter, no shadow board
  safety_hazard   - exposed moving parts, blocked walkway, trip or pinch risk, missing guard
  leak_or_spill   - a possible fluid leak, wet patch, oil or coolant on the floor
  missing_label   - unlabelled, untagged or unidentified items, bins or valves
  obstruction     - something blocking access, an aisle, a gangway or a panel

ABSOLUTE RULES:
1. Every finding must be phrased as a POSSIBILITY, never a certainty. Write
   "Possible oil patch near the machine base", never "Oil leak detected".
2. Report only what you can SEE in this photograph. Never infer history, cause,
   cost or blame. Never name or describe a person.
3. At most FIVE findings. If you can see more, report the five most significant.
4. If the photo is too dark, blurred or close-up to judge, return an empty list.
5. Reply with JSON only, in exactly this shape and nothing else. The value
   below describes the SHAPE, not an answer — never repeat it:
{"findings":[{"category":"<one of the five above>","text":"<what you can see, phrased as a possibility>"}]}"""

MAX_VISION_FINDINGS = 5


@dataclass(slots=True)
class VisionResult:
    """What the camera offered, before the operator decided.

    ``findings`` are *suggestions shown only to the operator*. Nothing here
    reaches a supervisor until the operator taps "Add to this Kaizen".
    """

    findings: list[dict] = field(default_factory=list)
    ran: bool = False
    note: str | None = None
    model: str | None = None


async def analyse_photo(
    image: bytes,
    *,
    media_type: str = "image/jpeg",
    said: str = "",
    provider: OllamaProvider | None = None,
) -> VisionResult:
    """Step 2: read the photo — the key differentiator, under three hard rules.

    *"The operator came to report walking distance. AI found a 5S gap, a safety
    hazard and a possible leak in the same photo — issues the operator had
    stopped noticing because they see them every day. One photo becomes three
    improvements."*

    Rule 1 (consent) is enforced by the caller, which persists only the findings
    the operator accepted. Rules 2 and 3 are enforced here: the prompt demands
    "possible" phrasing and five categories, and :func:`_clean_findings` drops
    anything that came back outside them.
    """
    client = provider or OllamaProvider()
    if not client.enabled:
        return VisionResult(
            ran=False,
            note="Photo saved as evidence. Automatic photo analysis is switched off here.",
        )
    if not image:
        return VisionResult(ran=False, note="No photo to look at.")

    prompt = "What can you see in this photograph?"
    if said:
        # Context helps the model describe the right machine, but it must not
        # become a hint about what to find — hence the explicit instruction.
        prompt += (
            f"\n\nThe operator said: {said}\nUse this only to name things correctly. "
            "Do not report anything you cannot see in the photograph itself."
        )

    try:
        result = await client.see(
            system=_VISION_SYSTEM, user=prompt, images=[image], media_type=media_type
        )
    except LlmUnavailable as exc:
        log.info("kaizen.vision.unavailable", error=str(exc))
        return VisionResult(
            ran=False, note="Photo saved. The camera analysis could not run just now."
        )

    findings = _clean_findings(_parse_json(result, list_key="findings"))
    return VisionResult(
        findings=findings,
        ran=True,
        model=result.model,
        note=None
        if findings
        else "I looked at your photo but could not make anything out clearly.",
    )


def _parse_json(result: Generation, *, list_key: str | None = None) -> dict:
    """Pull a JSON object out of a reply, tolerating whatever the model did.

    Local models are not reliably schema-obedient. Observed in testing, all from
    prompts that asked plainly for one object: a fenced code block, prose either
    side of the JSON, and a bare top-level array instead of the object. Each of
    those is a 500 if the caller assumes a dict, so every shape is normalised
    here and an unparseable reply degrades to "no findings" rather than an error.
    """
    text = result.text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()

    payload: object = None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        for pattern in (r"\{.*\}", r"\[.*\]"):
            match = re.search(pattern, text, re.DOTALL)
            if not match:
                continue
            try:
                payload = json.loads(match.group(0))
                break
            except json.JSONDecodeError:
                continue

    if isinstance(payload, dict):
        return payload
    # A bare array is the model answering the question but skipping the wrapper.
    if isinstance(payload, list) and list_key:
        return {list_key: payload}
    return {}


#: Rule 2, enforced after the fact. A model that wrote "Oil leak detected"
#: despite the prompt gets its wording corrected rather than its finding shown.
_CERTAIN_WORDS = (
    ("detected", "possible"),
    ("confirmed", "possible"),
    ("definitely", "possibly"),
    ("clearly", "possibly"),
    ("is leaking", "may be leaking"),
    ("there is a leak", "there may be a leak"),
)
_HEDGES = ("possible", "possibly", "may ", "might ", "appears", "looks like", "seems")


def _clean_findings(payload: dict | list) -> list[dict]:
    """Keep only the five categories, and only hedged wording."""
    allowed = {c.value for c in VisionCategory}
    out: list[dict] = []
    seen: set[str] = set()

    rows = payload if isinstance(payload, list) else (payload.get("findings") or [])
    if not isinstance(rows, list):
        return []

    for row in rows:
        if not isinstance(row, dict):
            continue
        category = str(row.get("category", "")).strip().lower()
        text = re.sub(r"\s+", " ", str(row.get("text", "")).strip())
        if category not in allowed or not text:
            continue

        lowered = text.lower()
        for certain, hedged in _CERTAIN_WORDS:
            if certain in lowered:
                text = re.sub(re.escape(certain), hedged, text, flags=re.IGNORECASE)
                lowered = text.lower()
        if not any(hedge in lowered for hedge in _HEDGES):
            text = f"Possible: {text[0].lower()}{text[1:]}"

        key = f"{category}:{text.lower()}"
        if key in seen:
            continue
        seen.add(key)
        out.append({"category": category, "text": text[:280], "accepted": False})
        if len(out) >= MAX_VISION_FINDINGS:
            break
    return out


# ===========================================================================
# Step 7 — the AI Challenge
# ===========================================================================

_CHALLENGE_SYSTEM = """You suggest ONE cheaper alternative to an improvement idea from a factory operator.

ABSOLUTE RULES:
1. Exactly ONE alternative. Two feels like an argument.
2. Call it "an alternative", never "a better idea". The operator's idea is not
   being corrected; they are being offered a second option.
3. You do NOT know this plant's layout, so your suggestion may be physically
   impossible. End with a QUESTION that checks exactly that, never an
   instruction. The question must be about YOUR suggestion and this machine.
4. Suggest something genuinely cheaper or faster. If you cannot think of one
   that is clearly cheaper, say so by returning an empty alternative.
5. Invent no rupee figures beyond a rough cost for your own suggestion, and no
   savings figures at all.
6. Reply with JSON only, in exactly this shape. The values below are
   placeholders describing the SHAPE, not an answer — never repeat them:
{"alternative":"<one concrete cheaper change, specific to the problem above>","rough_cost_rupees":<number>,"question":"<one question checking whether it is possible here>"}
Return {"alternative":""} if you have nothing cheaper to offer."""


@dataclass(slots=True)
class Challenge:
    alternative: str = ""
    rough_cost_paise: int = 0
    question: str = ""
    offered: bool = False

    def as_dict(self) -> dict:
        return {
            "alternative": self.alternative,
            "rough_cost_paise": self.rough_cost_paise,
            "question": self.question,
            "offered": self.offered,
            "chosen": None,
            "dismissed_reason": None,
        }


async def propose_alternative(
    *,
    problem: str,
    proposed_fix: str,
    investment_paise: int,
    station_code: str | None = None,
    provider: OllamaProvider | None = None,
) -> Challenge:
    """Step 7: argue back with one cheaper option (never two, never binding).

    *"Any competitor can write a Kaizen report with AI. Very few will build
    something that argues back with a cheaper option. This is the demo moment
    that sells the product in a boardroom."*

    Silent when there is nothing to challenge — an idea that costs nothing has
    no cheaper alternative, and inventing one to fill the slot is exactly how
    this feature backfires.
    """
    if investment_paise <= 0 or not proposed_fix.strip():
        return Challenge()

    client = provider or OllamaProvider()
    if not client.enabled:
        return Challenge()

    where = f" at {station_code}" if station_code else ""
    user = (
        f"PROBLEM{where}: {problem}\n"
        f"OPERATOR'S FIX: {proposed_fix}\n"
        f"THEIR ESTIMATED COST: ₹{investment_paise // 100:,}"
    )
    try:
        result = await client.generate(
            system=_CHALLENGE_SYSTEM, user=user, temperature=0.4, max_tokens=300
        )
    except LlmUnavailable as exc:
        log.info("kaizen.challenge.unavailable", error=str(exc))
        return Challenge()

    payload = _parse_json(result)
    alternative = re.sub(r"\s+", " ", str(payload.get("alternative", "")).strip())
    if not alternative:
        return Challenge()

    try:
        rough = int(float(payload.get("rough_cost_rupees") or 0) * 100)
    except (TypeError, ValueError):
        rough = 0
    # An "alternative" that costs more than the operator's own idea is not an
    # alternative — showing it makes the feature look like second-guessing.
    if rough >= investment_paise:
        return Challenge()

    question = re.sub(r"\s+", " ", str(payload.get("question", "")).strip())
    if not question:
        question = "Would that work here, or is there something in the way?"
    if not question.endswith("?"):
        question = f"{question.rstrip('.')}?"

    return Challenge(
        alternative=alternative[:400],
        rough_cost_paise=max(0, rough),
        question=question[:240],
        offered=True,
    )


# ===========================================================================
# §10 — meaning fingerprints for pattern detection
# ===========================================================================

#: Words that carry no meaning for clustering. Kept short on purpose: an
#: aggressive stop list merges unrelated problems, which is worse than missing
#: one — a false pattern sends a plant head to a meeting about nothing.
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "to",
        "of",
        "in",
        "on",
        "at",
        "for",
        "from",
        "with",
        "by",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "we",
        "i",
        "you",
        "they",
        "he",
        "she",
        "them",
        "us",
        "our",
        "my",
        "your",
        "there",
        "here",
        "not",
        "no",
        "yes",
        "do",
        "does",
        "did",
        "done",
        "have",
        "has",
        "had",
        "will",
        "would",
        "can",
        "could",
        "should",
        "shall",
        "may",
        "might",
        "must",
        "if",
        "then",
        "than",
        "so",
        "such",
        "very",
        "more",
        "most",
        "much",
        "many",
        "every",
        "each",
        "some",
        "any",
        "all",
        "one",
        "two",
        "three",
        "other",
        "another",
        "same",
        "time",
        "when",
        "while",
        "because",
        "about",
        "into",
        "over",
        "under",
        "again",
        "once",
        "now",
        "day",
        "days",
        "shift",
        "shifts",
    ]
)
_TOKEN = re.compile(r"[a-z][a-z0-9\-]{2,}")


def fingerprint(*parts: str) -> list[str]:
    """A stable token set standing in for the meaning of a submission.

    Used directly when no embedding model is available, and as the join key
    when one is — an embedding cannot be indexed usefully without pgvector, and
    a plant's weekly cluster job is small enough to compare in memory.
    """
    words = _TOKEN.findall(" ".join(p or "" for p in parts).lower())
    return sorted({w for w in words if w not in _STOPWORDS})[:48]


def jaccard(left: Sequence[str], right: Sequence[str]) -> float:
    """Overlap between two fingerprints, 0-1."""
    a, b = set(left or ()), set(right or ())
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """Similarity between two embeddings, 0-1 (negatives clamped to 0)."""
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(x * y for x, y in zip(left, right, strict=True))
    norm = math.sqrt(sum(x * x for x in left)) * math.sqrt(sum(y * y for y in right))
    if norm == 0:
        return 0.0
    return max(0.0, dot / norm)


async def embed_all(
    texts: Sequence[str], *, provider: OllamaProvider | None = None
) -> list[list[float]] | None:
    """Embeddings for a batch, or ``None`` when the local model is unavailable."""
    client = provider or OllamaProvider()
    if not client.enabled or not texts:
        return None
    try:
        return await client.embed(texts)
    except LlmUnavailable as exc:
        log.info("kaizen.embed.unavailable", error=str(exc))
        return None
