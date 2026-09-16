"""Factory Copilot (Layer 3) — plain-language questions over the plant's own data.

    "The Copilot is not a new intelligence — it is the same Brain data,
     delivered through conversation instead of screens."

Two tiers, enforced in the product rather than promised in the pitch:

* **factual** — 'What happened today?', 'How much downtime this week?' Correct
  from day one, because they are a SQL query away.
* **pattern** — 'Why does CNC-3 keep failing?' Withheld until enough events
  exist to support an answer. Until then it says so, in as many words.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.facts import build_question_facts
from app.ai.llm import GROUNDING_SYSTEM_PROMPT, LlmProvider, LlmUnavailable
from app.models import CopilotQuery, Plant

LANGUAGES = {"en": "English", "ta": "Tamil"}


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
) -> CopilotAnswer:
    started = time.perf_counter()
    as_of = as_of or date.today()

    bundle, intent = await build_question_facts(
        session, plant=plant, question=question, as_of=as_of
    )
    tier_available = not bundle.limits or intent != "pattern"

    answer, answered_by = await _answer(
        bundle=bundle, question=question, language=language, use_llm=use_llm
    )
    latency_ms = int((time.perf_counter() - started) * 1000)

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
                "limits": bundle.limits,
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


async def _answer(
    *, bundle, question: str, language: str, use_llm: bool
) -> tuple[str, str]:
    deterministic = _deterministic_answer(bundle)

    if not use_llm:
        return (deterministic, "deterministic")

    try:
        provider = LlmProvider()
        generation = await provider.generate(
            system=GROUNDING_SYSTEM_PROMPT.format(
                language=LANGUAGES.get(language, "English")
            ),
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


def _deterministic_answer(bundle) -> str:
    """The answer the product gives with no model at all.

    Deliberately plain. It is never wrong, which matters more than it reading
    well — one confidently wrong answer to an owner kills trust in every number.
    """
    if bundle.limits and not bundle.facts:
        return " ".join(bundle.limits)

    parts = [
        f"{_humanise(key)}: {value}"
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
            f"{top['station']} has {top['occurrences']} {top['reason'].lower()} "
            f"events in the last 4 weeks costing {top['total_cost']} — "
            f"roughly one every {top['interval_days']} days"
        )

    defects = bundle.facts.get("top_defects") or []
    if defects:
        parts.append(
            "Top defects: " + ", ".join(f"{d['label']} ({d['count']})" for d in defects)
        )

    overdue = bundle.facts.get("overdue_actions") or []
    if overdue:
        parts.append(f"{len(overdue)} action(s) overdue, biggest {overdue[0]['cost']}")

    answer = ". ".join(parts) if parts else "No data recorded for that period."

    if bundle.evidence:
        top = bundle.evidence[0]
        if "station" in top and "reason" in top:
            answer += (
                f". Biggest single item: {top['reason']} on {top['station']}"
                f" ({top.get('cost_display') or top.get('cost')})"
            )
        elif "what_fixed_it" in top:
            answer += f". Last time ({top['when']}): {top['what_fixed_it']}" + (
                f" — check {top['check_first']} first." if top.get("check_first") else ""
            )

    if bundle.limits:
        answer += " " + " ".join(bundle.limits)
    return answer


def _humanise(key: str) -> str:
    return key.replace("_", " ").capitalize()
