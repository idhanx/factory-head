"""Reporting — one digest, not two (Plugin Spec §12, Part C Feature 4).

    "The Standalone Edition's Friday Executive Digest is its retention
     mechanism — the one page a Plant Head reads. In the plugin, that page
     already exists: Core sends the owner an end-of-shift report daily and a
     weekly digest. A second WhatsApp digest from the Kaizen plugin competes
     with it and both get ignored."

So Kaizen contributes a *block* to Core's weekly owner digest, and its savings
appear inside Core's Recovered Profit figure broken down by source — never as a
separate additive total. Two rules this module exists to enforce:

* **The rupee is counted once.** :func:`digest_block` reports Kaizen-sourced
  Recovered Profit as a *share of* the plant total, and the caller must never
  add the two. "Core savings + Kaizen savings" as two additive lines produces an
  inflated headline, which is the exact failure mode both specifications are
  built to prevent.
* **Participation is never the headline.** *"A plant at 95% participation and
  ₹40,000 of verified savings has a compliance culture, not an improvement
  culture. Report both, always together."*
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.kernel import Board, LevelStanding, format_inr, level_for, participation_percent
from app.models import (
    Action,
    ActionTrigger,
    KaizenIdea,
    KaizenPoint,
    KaizenStatus,
    KaizenTrack,
    Operator,
    OperatorSkill,
    Plant,
    RecoveredProfitEntry,
    Station,
)
from app.services import kaizen as kaizen_service
from app.services import kaizen_patterns

#: §12.2's "Delayed" view, and the digest's red line.
DELAYED_DAYS = 14


def month_bounds(day: date) -> tuple[date, date]:
    """First of the month, and the first of the next. Half-open, as everywhere."""
    start = day.replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1)
    return (start, end)


# ---------------------------------------------------------------------------
# Part C Feature 4 — levels, points and the two leaderboards
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BoardRow:
    name: str
    board: str
    points: int
    submissions: int
    verified_saving_paise: int
    level: str
    operator_id: str | None = None

    def as_dict(self) -> dict:
        return {
            **asdict(self),
            "verified_saving_display": format_inr(self.verified_saving_paise),
        }


async def leaderboards(
    session: AsyncSession,
    *,
    plant: Plant,
    since: date | None = None,
    limit: int = 10,
) -> dict[str, list[dict]]:
    """Two rankings, always returned together (Standalone, "DECIDED").

    *"If a quality engineer competes with a machine operator on the same
    ranking, the engineer wins every month. They have better vocabulary, more
    context, more exposure to the whole plant and more time at a desk. The
    operator sees a leaderboard they can never top and stops submitting."*

    Measured on points from verified outcomes, so the top of either board is the
    person whose ideas actually held — not the person who submitted most.
    """
    query = (
        select(
            KaizenPoint.person_name,
            KaizenPoint.board,
            KaizenPoint.operator_id,
            func.sum(KaizenPoint.points),
            func.count(func.distinct(KaizenPoint.idea_id)),
        )
        .where(KaizenPoint.plant_id == plant.id)
        .group_by(KaizenPoint.person_name, KaizenPoint.board, KaizenPoint.operator_id)
    )
    if since is not None:
        query = query.where(KaizenPoint.awarded_on >= since)

    verified = dict(
        (
            await session.execute(
                select(
                    KaizenIdea.credited_name,
                    func.coalesce(func.sum(KaizenIdea.verified_saving_paise), 0),
                )
                .where(
                    KaizenIdea.plant_id == plant.id,
                    KaizenIdea.status == KaizenStatus.VERIFIED,
                )
                .group_by(KaizenIdea.credited_name)
            )
        ).all()
    )

    boards: dict[str, list[BoardRow]] = {b.value: [] for b in Board}
    for name, board, operator_id, points, ideas in (await session.execute(query)).all():
        standing = level_for(int(points or 0))
        boards.setdefault(board, []).append(
            BoardRow(
                name=name,
                board=board,
                points=int(points or 0),
                submissions=int(ideas or 0),
                verified_saving_paise=int(verified.get(name, 0) or 0),
                level=standing.label,
                operator_id=str(operator_id) if operator_id else None,
            )
        )

    return {
        board: [
            row.as_dict()
            for row in sorted(
                rows,
                key=lambda r: (r.points, r.verified_saving_paise, r.submissions),
                reverse=True,
            )[:limit]
        ]
        for board, rows in boards.items()
    }


async def standing(
    session: AsyncSession,
    *,
    plant: Plant,
    person_name: str,
    operator_id: uuid.UUID | None = None,
    today: date | None = None,
) -> dict:
    """One person's home screen: level, points, and this month's counter.

    The month counter is the *floor*, not the leaderboard — one 30-second Quick
    Observation clears it. The two are separate systems on purpose: quantity
    clears the floor, only quality earns points.
    """
    today = today or date.today()
    start, end = month_bounds(today)

    scope = [KaizenPoint.plant_id == plant.id]
    scope.append(
        KaizenPoint.operator_id == operator_id
        if operator_id is not None
        else KaizenPoint.person_name == person_name
    )
    points = int(await session.scalar(select(func.sum(KaizenPoint.points)).where(*scope)) or 0)

    config = await kaizen_service.settings_for(session, plant=plant)
    idea_scope = [KaizenIdea.plant_id == plant.id, KaizenIdea.submitted_on >= start,
                  KaizenIdea.submitted_on < end,
                  # A duplicate counts for nothing and does not clear the month.
                  KaizenIdea.status != KaizenStatus.DUPLICATE]
    idea_scope.append(
        KaizenIdea.credited_operator_id == operator_id
        if operator_id is not None
        else KaizenIdea.credited_name == person_name
    )
    this_month = int(
        await session.scalar(select(func.count(KaizenIdea.id)).where(*idea_scope)) or 0
    )

    verified = int(
        await session.scalar(
            select(func.coalesce(func.sum(KaizenIdea.verified_saving_paise), 0)).where(
                KaizenIdea.plant_id == plant.id,
                KaizenIdea.credited_name == person_name,
            )
        )
        or 0
    )

    level: LevelStanding = level_for(points)
    return {
        "name": person_name,
        "points": points,
        "level": level.level.value,
        "level_label": level.label,
        "next_level_label": level.next_label,
        "points_to_next": level.points_to_next,
        "level_floor": level.floor,
        "level_ceiling": level.ceiling,
        "this_month": this_month,
        "monthly_minimum": config.monthly_minimum,
        "days_left_in_month": (end - today).days,
        "verified_saving_paise": verified,
        "verified_saving_display": format_inr(verified),
    }


# ---------------------------------------------------------------------------
# Participation — a leading indicator, never the headline
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DepartmentParticipation:
    department: str
    headcount: int
    submitted: int
    percent: float

    def as_dict(self) -> dict:
        return asdict(self)


async def _operator_departments(
    session: AsyncSession, *, plant: Plant
) -> dict[uuid.UUID, str]:
    """Which department each operator belongs to, derived from Core.

    Core's T5.B carries names, not departments — so an operator's department is
    the section of the stations they are certified on. Derived rather than
    entered, because §3's rule is that Kaizen keeps no employee register of its
    own, and asking a plant to maintain a second one would break it.
    """
    sections = dict(
        (
            await session.execute(
                select(Station.id, Station.section).where(Station.plant_id == plant.id)
            )
        ).all()
    )
    # The Skill Matrix plugin's own table is the richer source — it is what a
    # supervisor actually maintains — with T5.B's inline list as the fallback
    # for a plant that has not started the matrix yet.
    certified: dict[uuid.UUID, list[uuid.UUID]] = {}
    for operator_id, station_id in (
        await session.execute(
            select(OperatorSkill.operator_id, OperatorSkill.station_id).where(
                OperatorSkill.plant_id == plant.id, OperatorSkill.level > 0
            )
        )
    ).all():
        certified.setdefault(operator_id, []).append(station_id)

    out: dict[uuid.UUID, str] = {}
    operators = (
        await session.scalars(
            select(Operator).where(Operator.plant_id == plant.id, Operator.is_active.is_(True))
        )
    ).all()
    for operator in operators:
        counts: dict[str, int] = {}
        for station_id in certified.get(operator.id) or operator.certified_station_ids or ():
            section = sections.get(station_id)
            if section is None:
                continue
            key = section.value if hasattr(section, "value") else str(section)
            counts[key] = counts.get(key, 0) + 1
        if counts:
            # Where an operator is certified on stations in several sections,
            # the one they are certified on most is their department.
            out[operator.id] = max(counts, key=lambda k: counts[k])
    return out


async def participation(
    session: AsyncSession, *, plant: Plant, today: date | None = None
) -> list[dict]:
    """Participation % per department this month — how many of that department's
    people submitted at least one.

    Ranked on percentage, never raw count: otherwise a 60-person machine shop
    always beats a 4-person stores team, and stores stops trying.
    """
    today = today or date.today()
    start, end = month_bounds(today)

    departments = await _operator_departments(session, plant=plant)
    headcount: dict[str, int] = {}
    for department in departments.values():
        headcount[department] = headcount.get(department, 0) + 1

    rows = (
        await session.execute(
            select(func.distinct(KaizenIdea.credited_operator_id)).where(
                KaizenIdea.plant_id == plant.id,
                KaizenIdea.submitted_on >= start,
                KaizenIdea.submitted_on < end,
                KaizenIdea.credited_operator_id.is_not(None),
                KaizenIdea.status != KaizenStatus.DUPLICATE,
            )
        )
    ).all()
    submitted: dict[str, int] = {}
    for (operator_id,) in rows:
        department = departments.get(operator_id)
        if department:
            submitted[department] = submitted.get(department, 0) + 1

    out = [
        DepartmentParticipation(
            department=department,
            headcount=count,
            submitted=submitted.get(department, 0),
            percent=float(
                participation_percent(submitted=submitted.get(department, 0), headcount=count)
            ),
        )
        for department, count in headcount.items()
    ]
    out.sort(key=lambda row: row.percent, reverse=True)
    return [row.as_dict() for row in out]


async def nudge_list(
    session: AsyncSession, *, plant: Plant, today: date | None = None, limit: int = 100
) -> list[dict]:
    """Who has not submitted yet this month — *a nudge list, not a punishment list*."""
    today = today or date.today()
    start, end = month_bounds(today)

    submitted = {
        row[0]
        for row in (
            await session.execute(
                select(func.distinct(KaizenIdea.credited_operator_id)).where(
                    KaizenIdea.plant_id == plant.id,
                    KaizenIdea.submitted_on >= start,
                    KaizenIdea.submitted_on < end,
                    KaizenIdea.status != KaizenStatus.DUPLICATE,
                )
            )
        ).all()
        if row[0] is not None
    }
    departments = await _operator_departments(session, plant=plant)
    operators = (
        await session.scalars(
            select(Operator)
            .where(Operator.plant_id == plant.id, Operator.is_active.is_(True))
            .order_by(Operator.name)
        )
    ).all()
    return [
        {
            "operator_id": str(operator.id),
            "name": operator.name,
            "employee_code": operator.employee_code,
            "department": departments.get(operator.id),
            "days_left": (end - today).days,
        }
        for operator in operators
        if operator.id not in submitted
    ][:limit]


async def end_of_month_spike(
    session: AsyncSession, *, plant: Plant, today: date | None = None
) -> list[dict]:
    """Anti-gaming rule 4 — departments that filed most of their month at the end.

    *"The point isn't to punish anyone — it's to tell the Plant Head their scheme
    has become a tick-box exercise on that line, which is exactly what they'd
    otherwise find out a year too late."*
    """
    today = today or date.today()
    start, end = month_bounds(today)
    last_two_days = end - timedelta(days=2)
    if today < last_two_days:
        return []

    departments = await _operator_departments(session, plant=plant)
    rows = (
        await session.execute(
            select(KaizenIdea.credited_operator_id, KaizenIdea.submitted_on).where(
                KaizenIdea.plant_id == plant.id,
                KaizenIdea.submitted_on >= start,
                KaizenIdea.submitted_on < end,
                KaizenIdea.credited_operator_id.is_not(None),
            )
        )
    ).all()

    totals: dict[str, int] = {}
    late: dict[str, int] = {}
    for operator_id, submitted_on in rows:
        department = departments.get(operator_id)
        if not department:
            continue
        totals[department] = totals.get(department, 0) + 1
        if submitted_on >= last_two_days:
            late[department] = late.get(department, 0) + 1

    return [
        {
            "department": department,
            "total": total,
            "in_last_two_days": late.get(department, 0),
            "share": round(late.get(department, 0) / total * 100, 1),
        }
        for department, total in totals.items()
        if total >= 5 and late.get(department, 0) / total >= 0.8
    ]


# ---------------------------------------------------------------------------
# §12.1 — the Kaizen block inside Core's weekly owner digest
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DigestBlock:
    submitted: int = 0
    submitted_change_percent: float | None = None
    approved: int = 0
    implemented: int = 0
    waiting_your_action: int = 0
    delayed: int = 0

    #: Kaizen's share OF Core's Recovered Profit, never a separate total.
    recovered_profit_ytd_paise: int = 0
    plant_recovered_profit_ytd_paise: int = 0
    pending_approval_value_paise: int = 0
    cost_of_doing_nothing_monthly_paise: int = 0

    participation: list[dict] = field(default_factory=list)
    top_shopfloor: dict | None = None
    top_support: dict | None = None
    biggest_opportunity: dict | None = None
    pattern: dict | None = None
    spikes: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            **asdict(self),
            "recovered_profit_ytd_display": format_inr(self.recovered_profit_ytd_paise),
            "plant_recovered_profit_ytd_display": format_inr(
                self.plant_recovered_profit_ytd_paise
            ),
            "kaizen_share_percent": (
                round(
                    self.recovered_profit_ytd_paise
                    / self.plant_recovered_profit_ytd_paise
                    * 100,
                    1,
                )
                if self.plant_recovered_profit_ytd_paise
                else 0.0
            ),
            "pending_approval_value_display": format_inr(self.pending_approval_value_paise),
            "cost_of_doing_nothing_monthly_display": format_inr(
                self.cost_of_doing_nothing_monthly_paise
            ),
        }


async def digest_block(
    session: AsyncSession, *, plant: Plant, today: date | None = None
) -> DigestBlock:
    """One block for Core's owner digest. Kaizen sends no digest of its own."""
    today = today or date.today()
    week_start = today - timedelta(days=7)
    prior_start = today - timedelta(days=14)
    year_start = today.replace(month=1, day=1)

    block = DigestBlock()

    async def count(*where) -> int:
        return int(await session.scalar(select(func.count(KaizenIdea.id)).where(*where)) or 0)

    scope = KaizenIdea.plant_id == plant.id
    block.submitted = await count(
        scope, KaizenIdea.submitted_on >= week_start, KaizenIdea.submitted_on <= today
    )
    prior = await count(
        scope, KaizenIdea.submitted_on >= prior_start, KaizenIdea.submitted_on < week_start
    )
    if prior:
        block.submitted_change_percent = round((block.submitted - prior) / prior * 100, 1)

    block.approved = await count(
        scope,
        KaizenIdea.decided_at.is_not(None),
        KaizenIdea.status.in_(
            [
                KaizenStatus.APPROVED,
                KaizenStatus.ACCEPTED,
                KaizenStatus.IMPLEMENTED,
                KaizenStatus.VERIFIED,
            ]
        ),
    )
    block.implemented = await count(
        scope, KaizenIdea.status.in_([KaizenStatus.IMPLEMENTED, KaizenStatus.VERIFIED])
    )

    waiting = await kaizen_service.open_queue(session, plant=plant)
    block.waiting_your_action = len(waiting)
    block.pending_approval_value_paise = sum(i.hard_saving_paise for i in waiting)
    # The ticking counter, aggregated: what the whole queue costs per month it
    # is not decided. Nothing clears an approval backlog faster.
    block.cost_of_doing_nothing_monthly_paise = sum(
        (i.hard_saving_paise + i.soft_saving_paise) // 12 for i in waiting
    )
    block.delayed = len(await kaizen_service.delayed(session, plant=plant, days=DELAYED_DAYS))

    # Kaizen's share of Core's ledger — read from Core, never recomputed.
    block.plant_recovered_profit_ytd_paise = int(
        await session.scalar(
            select(func.coalesce(func.sum(RecoveredProfitEntry.saving), 0)).where(
                RecoveredProfitEntry.plant_id == plant.id,
                RecoveredProfitEntry.counted.is_(True),
                RecoveredProfitEntry.week_start >= year_start,
            )
        )
        or 0
    )
    block.recovered_profit_ytd_paise = int(
        await session.scalar(
            select(func.coalesce(func.sum(RecoveredProfitEntry.saving), 0))
            .join(Action, Action.id == RecoveredProfitEntry.action_id)
            .where(
                RecoveredProfitEntry.plant_id == plant.id,
                RecoveredProfitEntry.counted.is_(True),
                RecoveredProfitEntry.week_start >= year_start,
                Action.trigger == ActionTrigger.KAIZEN,
            )
        )
        or 0
    )

    block.participation = await participation(session, plant=plant, today=today)
    boards = await leaderboards(session, plant=plant, limit=1)
    block.top_shopfloor = next(iter(boards.get(Board.SHOPFLOOR.value) or []), None)
    block.top_support = next(iter(boards.get(Board.SUPPORT.value) or []), None)

    best = next(iter(waiting), None)
    if best is not None:
        block.biggest_opportunity = {
            "reference": best.reference,
            "title": best.title,
            "hard_saving_display": format_inr(best.hard_saving_paise),
            "priority_verdict": best.priority_verdict,
            "id": str(best.id),
        }

    patterns = await kaizen_patterns.open_patterns(session, plant=plant, limit=1)
    if patterns:
        top = patterns[0]
        block.pattern = {
            "id": str(top.id),
            "label": top.label,
            "kaizen_count": top.kaizen_count,
            "event_count": top.event_count,
            "verified_cost_display": format_inr(top.verified_cost_paise),
        }

    block.spikes = await end_of_month_spike(session, plant=plant, today=today)
    return block


def render_digest(plant: Plant, block: DigestBlock) -> str:
    """The block as the owner reads it, inside Core's weekly digest.

    Participation and savings appear together, and the savings line says
    explicitly that it is a share of the plant total rather than an addition to
    it — a reader who mentally adds the two produces the inflated headline both
    specifications exist to prevent.
    """
    data = block.as_dict()
    change = (
        f" ({block.submitted_change_percent:+.0f}%)"
        if block.submitted_change_percent is not None
        else ""
    )
    lines = [
        "KAIZEN THIS WEEK",
        f"  Submitted {block.submitted}{change} · Approved {block.approved} · "
        f"Implemented {block.implemented}",
        f"  Waiting your action {block.waiting_your_action} · "
        f"Delayed >{DELAYED_DAYS} days {block.delayed}",
        f"  Of the plant's recovered profit YTD "
        f"({data['plant_recovered_profit_ytd_display']}), "
        f"{data['recovered_profit_ytd_display']} came from Kaizen-sourced actions "
        f"({data['kaizen_share_percent']}%).",
    ]
    if block.participation:
        lines.append(
            "  Participation  "
            + " · ".join(
                f"{row['department'].title()} {row['percent']:.0f}%"
                for row in block.participation[:5]
            )
        )
    if block.top_shopfloor:
        top = block.top_shopfloor
        lines.append(f"  Top shopfloor  {top['name']} ({top['points']} pts)")
    if block.top_support:
        top = block.top_support
        lines.append(f"  Top support    {top['name']} ({top['points']} pts)")
    if block.biggest_opportunity:
        best = block.biggest_opportunity
        lines.append(
            f"  Biggest opportunity  {best['reference']} {best['title']} · "
            f"{best['hard_saving_display']}/yr"
        )
    if block.cost_of_doing_nothing_monthly_paise:
        lines.append(
            f"  Cost of not deciding  {data['cost_of_doing_nothing_monthly_display']}/month"
        )
    if block.pattern:
        pattern = block.pattern
        lines.append(
            f"  Recurring pattern    {pattern['kaizen_count']} Kaizens + "
            f"{pattern['event_count']} Core events = one root cause "
            f"({pattern['verified_cost_display']})"
        )
    for spike in block.spikes:
        lines.append(
            f"  ⚠ {spike['department'].title()} filed {spike['share']:.0f}% of its "
            "month in the last two days — that line has become a tick-box exercise."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# §12.2 — the dashboard views, in the order they get built
# ---------------------------------------------------------------------------


async def dashboard(
    session: AsyncSession, *, plant: Plant, today: date | None = None
) -> dict:
    """Quick Wins first, then the rest.

    *"Quick Wins is the view that gets used in week 1. The rest is reporting,
    and reporting has no value until there's volume."*
    """
    today = today or date.today()
    config = await kaizen_service.settings_for(session, plant=plant)
    waiting = await kaizen_service.open_queue(session, plant=plant)

    top_savings = (
        await session.scalars(
            select(KaizenIdea)
            .where(
                KaizenIdea.plant_id == plant.id,
                KaizenIdea.status == KaizenStatus.VERIFIED,
            )
            .order_by(KaizenIdea.verified_saving_paise.desc())
            .limit(20)
        )
    ).all()

    safety = (
        await session.scalars(
            select(KaizenIdea)
            .where(KaizenIdea.plant_id == plant.id, KaizenIdea.safety_flagged.is_(True))
            .order_by(KaizenIdea.created_at.desc())
            .limit(20)
        )
    ).all()

    return {
        "quick_wins": [i for i in waiting if kaizen_service.quick_win(i, config)],
        "pending": waiting,
        "delayed": await kaizen_service.delayed(session, plant=plant, days=DELAYED_DAYS),
        # Verified only. A "top savings" list built on estimates is a list of
        # opinions ranked by confidence.
        "top_savings": list(top_savings),
        # A separate view so safety never competes with ROI for attention.
        "safety": list(safety),
        "patterns": await kaizen_patterns.open_patterns(session, plant=plant),
        "participation": await participation(session, plant=plant, today=today),
        "leaderboards": await leaderboards(session, plant=plant),
    }


async def totals(session: AsyncSession, *, plant: Plant) -> dict:
    """Headline counters for the top of the Kaizen screen."""
    rows = (
        await session.execute(
            select(
                KaizenIdea.status,
                KaizenIdea.track,
                func.count(KaizenIdea.id),
                func.coalesce(func.sum(KaizenIdea.hard_saving_paise), 0),
                func.coalesce(func.sum(KaizenIdea.soft_saving_paise), 0),
                func.coalesce(func.sum(KaizenIdea.verified_saving_paise), 0),
            )
            .where(KaizenIdea.plant_id == plant.id)
            .group_by(KaizenIdea.status, KaizenIdea.track)
        )
    ).all()

    by_status: dict[str, int] = {}
    by_track: dict[str, int] = {t.value: 0 for t in KaizenTrack}
    verified = hard = soft = 0
    for status, track, count, hard_sum, soft_sum, verified_sum in rows:
        by_status[status.value] = by_status.get(status.value, 0) + int(count)
        by_track[track.value] = by_track.get(track.value, 0) + int(count)
        verified += int(verified_sum or 0)
        if status in (
            KaizenStatus.APPROVED,
            KaizenStatus.ACCEPTED,
            KaizenStatus.IMPLEMENTED,
            KaizenStatus.VERIFIED,
        ):
            hard += int(hard_sum or 0)
            soft += int(soft_sum or 0)

    total = sum(by_track.values())
    return {
        "by_status": by_status,
        "by_track": by_track,
        "total": total,
        # ~80% Quick / ~20% Full is the designed split; a plant far off it is
        # either not using the quick track or running everything through it.
        "quick_share_percent": (
            round(by_track.get(KaizenTrack.QUICK.value, 0) / total * 100, 1) if total else 0.0
        ),
        "approved_hard_paise": hard,
        "approved_hard_display": format_inr(hard),
        "approved_soft_paise": soft,
        "approved_soft_display": format_inr(soft),
        "verified_paise": verified,
        "verified_display": format_inr(verified),
    }
