"""Factory Copilot (Layer 3) — plain-language questions over the plant's own data.

    "The Copilot is not a new intelligence — it is the same Brain data,
     delivered through conversation instead of screens."

Two tiers, enforced in the product rather than promised in the pitch:

* **factual** — 'What happened today?', 'How much downtime this week?' Correct
  from day one, because they are a SQL query away.
* **pattern** — 'Why does CNC-3 keep failing?' Withheld until enough events
  exist to support an answer. Until then it says so, in as many words.

Alongside both sits the company itself — 'what do we make?', 'who are our
customers?', 'how many machines do we run?'. Those are answered from the setup
data the plant entered at onboarding, which makes them as grounded as a loss
figure and no business being refused as "not a factory question".

Every one of them answers in English, Tamil or Hindi, and the fallback answer
is translated as carefully as the model's is: see `phrases.py` for why that
matters more than it sounds like it should.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.facts import COMPANY_WORDS, build_question_facts, limit_note, mentions
from app.ai.llm import GROUNDING_SYSTEM_PROMPT, LlmUnavailable, OllamaProvider
from app.ai.phrases import fact_label, join_sentences, language_name, say
from app.models import CopilotQuery, Plant

NO_DATA_ANSWER = (
    "Sorry, I do not have an answer for this question. Please ask me a "
    "professional or core-based question."
)


@dataclass(slots=True)
class CopilotAnswer:
    answer: str
    intent: str
    tier_available: bool
    facts: dict
    evidence: list[dict]
    answered_by: str
    latency_ms: int


async def ask(
    session: AsyncSession,
    *,
    plant: Plant,
    question: str,
    user_id=None,
    language: str = "en",
    channel: str = "web",
    as_of: date | None = None,
    use_llm: bool = True,
    record: bool = True,
) -> CopilotAnswer:
    """Answer one question from this plant's own rows.

    ``record`` is off for one caller only: showing an answer again in another
    language. That is the same question asked once, re-read — logging it a
    second time would turn a supervisor switching the screen to Hindi into two
    questions in the audit trail and in every usage figure drawn from it.
    """
    started = time.perf_counter()
    as_of = as_of or date.today()

    bundle, intent = await build_question_facts(
        session, plant=plant, question=question, as_of=as_of
    )
    tier_available = not bundle.limits or intent != "pattern"

    answer, answered_by = await _answer(
        bundle=bundle,
        question=question,
        intent=intent,
        language=language,
        use_llm=use_llm,
    )
    latency_ms = int((time.perf_counter() - started) * 1000)

    if record:
        session.add(
            CopilotQuery(
                plant_id=plant.id,
                user_id=user_id,
                channel=channel,
                question=question,
                language=language,
                intent=intent,
                tier_required="pattern" if intent == "pattern" else "factual",
                tier_available=tier_available,
                fact_bundle={
                    "facts": bundle.facts,
                    "evidence": bundle.evidence,
                    # Stored in English: the audit trail of what the product
                    # could not answer must read the same for every reviewer,
                    # whatever language the asker was working in.
                    "limits": [str(limit) for limit in bundle.limits],
                },
                answer=answer,
                answered_by=answered_by,
                latency_ms=latency_ms,
            )
        )
        await session.flush()

    return CopilotAnswer(
        answer=answer,
        intent=intent,
        tier_available=tier_available,
        facts=bundle.facts,
        evidence=bundle.evidence,
        answered_by=answered_by,
        latency_ms=latency_ms,
    )


#: What makes a question one this product can be expected to answer at all.
#: Anything matching none of these — and none of the company words — is refused
#: rather than answered with today's numbers, because a confident reply to
#: "what is the apple colour" is how an owner learns to distrust the rest.
FACTORY_KEYWORDS = (
    "plant",
    "shift",
    "station",
    "machine",
    "downtime",
    "loss",
    "scrap",
    "defect",
    "quality",
    "ppm",
    "maintenance",
    "stores",
    "material",
    "operator",
    "production",
    "reason",
    "cost",
    "profit",
    "performance",
    "today",
    "week",
    "month",
    "issue",
    "fix",
    "action",
    "job",
    "line",
    "breakdown",
)


def _is_off_topic(question: str) -> bool:
    text = question.lower()
    return not (
        any(keyword in text for keyword in FACTORY_KEYWORDS)
        # The company's own setup data is a fair thing to ask this product
        # about: "who are our customers" is answered from the part master, not
        # from outside knowledge, so it is as grounded as a loss figure.
        or mentions(text, COMPANY_WORDS)
    )


async def _answer(
    *,
    bundle,
    question: str,
    language: str,
    use_llm: bool,
    intent: str = "unrecognised",
) -> tuple[str, str]:
    deterministic = _deterministic_answer(bundle, language)

    # A question that landed in a real branch is on topic by construction: the
    # classifier found a period, a machine, a defect, a past fix or the company
    # itself in it. The keyword gate is the second chance given to everything
    # else, and it is the only thing standing between an owner and a confident
    # answer about the colour of apples.
    if intent == "unrecognised" and _is_off_topic(question):
        return (say(language, NO_DATA_ANSWER), "deterministic")

    if not bundle.facts and not bundle.evidence:
        return (say(language, NO_DATA_ANSWER), "deterministic")

    if not use_llm:
        return (deterministic, "deterministic")

    try:
        provider = OllamaProvider()
        generation = await provider.generate(
            system=GROUNDING_SYSTEM_PROMPT.format(language=language_name(language)),
            user=(
                f"{bundle.to_prompt()}\n\n"
                f"QUESTION: {question}\n\n"
                "Answer in two or three sentences using only the FACTS above."
            ),
            max_tokens=300,
        )
        return (generation.text, "llm")
    except LlmUnavailable:
        return (deterministic, "deterministic")


def _deterministic_answer(bundle, language: str = "en") -> str:
    """The answer the product gives with no model at all.

    Deliberately plain. It is never wrong, which matters more than it reading
    well — one confidently wrong answer to an owner kills trust in every number.

    It is assembled from the phrase book rather than written out in English
    here, because this is the answer most plants actually get: the model is
    optional, every caller falls back to this, and an answer that stayed in
    English while the screen around it turned Hindi would make the language
    picker a lie. What is never translated is what comes out of the kernel —
    money, counts, PPM, machine codes, reason labels.
    """
    notes = [limit_note(limit, language) for limit in bundle.limits]

    if bundle.limits and not bundle.facts:
        return join_sentences(language, notes)

    parts = [
        f"{fact_label(language, key)}: {value}"
        for key, value in bundle.facts.items()
        if not isinstance(value, (list, dict))
    ]

    # List-valued facts carry the actual answer to a pattern or defect question,
    # so they need rendering rather than skipping — dropping them is how
    # "Why does Drill-2 keep failing?" ends up answered with "no data".
    patterns = bundle.facts.get("recurring_patterns") or []
    if patterns:
        top = patterns[0]
        parts.append(
            say(
                language,
                "{station} has {occurrences} {reason} events in the last 4 weeks "
                "costing {cost} — roughly one every {interval_days} days",
                station=top["station"],
                occurrences=top["occurrences"],
                reason=top["reason"].lower(),
                cost=top["total_cost"],
                interval_days=top["interval_days"],
            )
        )

    defects = bundle.facts.get("top_defects") or []
    if defects:
        parts.append(
            say(
                language,
                "Top defects: {defects}",
                defects=", ".join(f"{d['label']} ({d['count']})" for d in defects),
            )
        )

    overdue = bundle.facts.get("overdue_actions") or []
    if overdue:
        parts.append(
            say(
                language,
                "{count} action(s) overdue, biggest {cost}",
                count=len(overdue),
                cost=overdue[0]["cost"],
            )
        )

    if bundle.evidence:
        top = bundle.evidence[0]
        if "station" in top and "reason" in top:
            parts.append(
                say(
                    language,
                    "Biggest single item: {reason} on {station} ({cost})",
                    reason=top["reason"],
                    station=top["station"],
                    cost=top.get("cost_display") or top.get("cost"),
                )
            )
        elif "what_fixed_it" in top:
            parts.append(
                say(
                    language,
                    "Last time ({when}): {fix} — check {check_first} first",
                    when=top["when"],
                    fix=top["what_fixed_it"],
                    check_first=top["check_first"],
                )
                if top.get("check_first")
                else say(
                    language,
                    "Last time ({when}): {fix}",
                    when=top["when"],
                    fix=top["what_fixed_it"],
                )
            )

    if not parts:
        parts.append(say(language, "No data recorded for that period"))

    return join_sentences(language, parts + notes)
