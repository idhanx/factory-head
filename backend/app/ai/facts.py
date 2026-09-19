"""Fact bundles — the only thing an LLM in this product is ever allowed to see.

Facts are assembled here by deterministic SQL and the kernel. The model then
phrases them (C5). This split is the difference between an assistant an owner
trusts and one wrong rupee figure that poisons every number on every screen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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
    PdcaState,
    Plant,
    ReasonCode,
    Recoverability,
    ShiftSession,
    ShiftSessionStatus,
    Station,
)
from app.services.reports import ranked_loss_report


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
            "Not enough history for pattern analysis (needs about 4 weeks). "
            "Do not claim a machine 'usually' or 'always' does anything."
        )

    overdue = await _overdue_actions(session, plant=plant, as_of=as_of)
    if overdue:
        bundle.facts["overdue_actions"] = overdue

    return bundle


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

    if any(word in text for word in ("week", "month", "so far", "total")):
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
    elif any(
        phrase in text
        for phrase in (
            "ever",
            "before",
            "seen",
            "last time",
            "previously",
            "how do we fix",
            "what fixed",
        )
    ):
        intent = "memory"
        bundle.evidence = await _memory_matches(session, plant=plant, query=question)
        if not bundle.evidence:
            bundle.limits.append(
                "Factory Memory has no recorded fix matching this. Say so plainly."
            )

    elif any(
        word in text for word in ("defect", "reject", "quality", "ppm", "burr", "scrap")
    ):
        intent = "quality"
        bundle.facts.update(await _quality_facts(session, plant=plant, as_of=as_of))

    elif any(word in text for word in ("why", "keep", "always", "usually", "pattern")):
        intent = "pattern"
        if tier < CoachTier.PATTERN:
            # §3 Layer 3 — the tier is enforced, not left to a disclaimer.
            bundle.limits.append(
                f"Only {sufficiency.complete_shifts} shifts of history exist. "
                "Pattern questions cannot be answered yet. Say: 'I've seen "
                f"{sufficiency.distinct_events} events like this; I'll give you "
                "pattern analysis once I've seen more.'"
            )
        else:
            patterns = await _recurring_patterns(
                session, plant=plant, as_of=as_of, station_code=station_code
            )
            bundle.facts["recurring_patterns"] = patterns
            if station_code and not patterns:
                bundle.limits.append(
                    f"{station_code} has no recurring pattern in the last 4 weeks "
                    "(nothing repeated three or more times). Say that plainly "
                    "rather than describing a different machine."
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
                "That question was not understood, so this is today's position "
                "rather than an answer to it. Losses for a period, defects and "
                "quality, why a machine keeps stopping, and what fixed something "
                "last time can all be asked for by name."
            )

    return bundle, intent


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
