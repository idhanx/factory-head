"""The Actions & PDCA engine (§5) and Factory Memory capture.

Actions are evaluated **at shift close**, not continuously: an Action born
mid-shift interrupts the person who should be running the shift, and the
severity rules need the whole shift's events to judge a pattern anyway.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.kernel import (
    ActionThresholds,
    EventForTrigger,
    Recoverability,
    WeeklyLoss,
    evaluate_action_triggers,
    fix_failed,
    format_inr,
    freeze_baseline,
    recovered_profit,
    route_by_severity,
)
from app.models import (
    Action,
    ActionEvent,
    ActionTrigger,
    AlertRecipient,
    DefectCode,
    DowntimeEvent,
    DowntimeStatus,
    MemoryEntry,
    Part,
    PdcaState,
    Plant,
    QualityRecord,
    ReasonCode,
    RecoveredProfitEntry,
    RootCauseAnalysis,
    ShiftSession,
    Station,
)
from app.services import notifications
from app.services.clock import local_date

LOSS_DOWNTIME = "downtime"
LOSS_QUALITY = "quality"

#: How long after closure recurrences are counted for an Action with no
#: baseline to measure against — the 2-4 week CHECK window of §5.4.
MONITORING_WINDOW = timedelta(weeks=4)


def thresholds_for(plant: Plant) -> ActionThresholds:
    return ActionThresholds(
        cost_threshold=plant.action_cost_threshold,
        duration_minutes=plant.action_duration_minutes,
        recurrence_count=plant.recurrence_count,
        recurrence_window_shifts=plant.recurrence_window_shifts,
    )


async def evaluate_shift_close(
    session: AsyncSession, *, plant: Plant, shift_session: ShiftSession
) -> list[Action]:
    """Create the Actions this shift earned. Usually zero, one or two.

    The rolling window is the plant's last N *shifts* (§5.2: "3+ times in a
    rolling 7 shifts"), so a third material wait on Wednesday finds the Monday
    and Tuesday ones and becomes a single pattern Action covering all three
    (§6, 3:27 PM). A two-shift plant's seven shifts are three and a half days,
    not a week.
    """
    limits = thresholds_for(plant)
    window = await _shift_window(session, plant, shift_session, limits.recurrence_window_shifts)
    shift_index = {session_id: index for index, session_id in enumerate(window)}

    rows = (
        await session.execute(
            select(DowntimeEvent, Station.code, ReasonCode)
            .join(Station, Station.id == DowntimeEvent.station_id)
            .outerjoin(ReasonCode, ReasonCode.id == DowntimeEvent.reason_code_id)
            .where(
                DowntimeEvent.plant_id == plant.id,
                DowntimeEvent.status == DowntimeStatus.CLOSED,
                DowntimeEvent.shift_session_id.in_(window),
            )
        )
    ).all()

    candidates: list[EventForTrigger] = []
    reasons_by_event: dict[str, ReasonCode] = {}
    for event, station_code, reason in rows:
        if reason is None:
            continue
        reasons_by_event[str(event.id)] = reason
        candidates.append(
            EventForTrigger(
                event_id=str(event.id),
                station_id=station_code,
                reason_code=reason.label,
                minutes=Decimal(event.duration_minutes or 0),
                cost=event.cost_total,
                recoverability=Recoverability(reason.recoverability.value),
                shift_index=shift_index[event.shift_session_id],
                safety_or_quality_escape=reason.safety_flag,
            )
        )

    triggered = evaluate_action_triggers(candidates, limits)
    by_id = {str(event.id): event for event, _, _ in rows}
    created: list[Action] = []

    for candidate in triggered:
        if await _already_covered(session, plant.id, candidate.event_ids):
            continue

        first_event = by_id[candidate.event_ids[0]]
        reason = reasons_by_event[candidate.event_ids[0]]
        verdict = route_by_severity(
            next(c for c in candidates if c.event_id == candidate.event_ids[0]),
            limits,
            occurrences=candidate.occurrence_count,
        )
        route = _route_of(reason)
        owner = await _default_owner(session, plant.id, route)

        action = Action(
            plant_id=plant.id,
            reference=await _next_reference(session, plant.id),
            title=candidate.title,
            description=candidate.rationale,
            trigger=ActionTrigger(candidate.trigger.value),
            trigger_rationale=candidate.rationale,
            state=PdcaState.OPEN,
            station_id=first_event.station_id,
            reason_code_id=first_event.reason_code_id,
            loss_source=LOSS_DOWNTIME,
            linked_cost=candidate.total_cost,
            occurrence_count=candidate.occurrence_count,
            routed_to_rca=verdict.escalate_to_rca,
            severity_reasons=list(verdict.reasons),
            # §5.1: every Action has a named owner and a deadline from birth —
            # the person the reason code already routes to (§3 Layer 2).
            owner_id=owner.user_id if owner else None,
            owner_name=_owner_label(owner, route),
            due_date=shift_session.business_date + timedelta(days=plant.action_due_days or 3),
            surfaced_in_meeting_on=shift_session.business_date,
        )
        session.add(action)
        await session.flush()

        session.add_all(
            ActionEvent(
                plant_id=plant.id,
                action_id=action.id,
                downtime_event_id=uuid.UUID(event_id),
            )
            for event_id in candidate.event_ids
        )

        # §5.6 — the baseline is frozen now, at creation, and never recomputed.
        action.baseline_weekly_loss = await compute_baseline(
            session,
            plant_id=plant.id,
            station_id=action.station_id,
            reason_code_id=action.reason_code_id,
            as_of=shift_session.business_date,
        )
        action.baseline_frozen_at = datetime.now(UTC)

        if verdict.escalate_to_rca:
            await _assign_rca(
                session,
                plant=plant,
                action=action,
                route=route,
                context={
                    "occurrences": candidate.occurrence_count,
                    "total_cost_paise": candidate.total_cost,
                    "reason": candidate.reason_code,
                    "station": candidate.station_id,
                    "event_dates": sorted(
                        {by_id[e].business_date.isoformat() for e in candidate.event_ids}
                    ),
                },
            )

        created.append(action)

    created.extend(await _quality_actions(session, plant=plant, shift_session=shift_session))
    await session.flush()
    return created


async def _shift_window(
    session: AsyncSession, plant: Plant, shift_session: ShiftSession, size: int
) -> list[uuid.UUID]:
    """The last ``size`` shifts up to and including this one, oldest first."""
    ids = (
        await session.scalars(
            select(ShiftSession.id)
            .where(
                ShiftSession.plant_id == plant.id,
                ShiftSession.started_at <= shift_session.started_at,
            )
            .order_by(ShiftSession.started_at.desc())
            .limit(max(1, size))
        )
    ).all()
    return list(reversed(ids))


async def _quality_actions(
    session: AsyncSession, *, plant: Plant, shift_session: ShiftSession
) -> list[Action]:
    """§5.2's big-single-event rule, applied to scrap.

    A batch whose scrap alone costs more than the Action threshold is a loss
    worth fixing on its own, measured afterwards in scrap rupees on that part
    and station. Several such batches in one shift roll into one Action.
    """
    limits = thresholds_for(plant)
    rows = (
        await session.execute(
            select(QualityRecord, Station.code, Part.part_number)
            .join(Part, Part.id == QualityRecord.part_id)
            .outerjoin(Station, Station.id == QualityRecord.station_id)
            .where(
                QualityRecord.plant_id == plant.id,
                QualityRecord.shift_session_id == shift_session.id,
                QualityRecord.cost_total > limits.cost_threshold,
            )
        )
    ).all()

    groups: dict[tuple, list] = {}
    for record, station_code, part_number in rows:
        groups.setdefault((record.station_id, record.part_id), []).append(
            (record, station_code, part_number)
        )

    labels = dict(
        (
            await session.execute(
                select(DefectCode.code, DefectCode.label).where(DefectCode.plant_id == plant.id)
            )
        ).all()
    )
    created: list[Action] = []
    for (station_id, part_id), batch in groups.items():
        open_already = await session.scalar(
            select(Action.id).where(
                Action.plant_id == plant.id,
                Action.loss_source == LOSS_QUALITY,
                Action.station_id == station_id,
                Action.part_id == part_id,
                Action.state.not_in([PdcaState.VERIFIED, PdcaState.CANCELLED]),
            )
        )
        if open_already:
            continue

        cost = sum(record.cost_total for record, _, _ in batch)
        rejected = sum(record.reject_count for record, _, _ in batch)
        station_code, part_number = batch[0][1] or "unassigned station", batch[0][2]
        defect_counts: dict[str, int] = {}
        for record, _, _ in batch:
            for defect in record.defects or []:
                defect_counts[defect.get("code", "?")] = defect_counts.get(
                    defect.get("code", "?"), 0
                ) + int(defect.get("count", 0))
        top = max(defect_counts, key=defect_counts.get) if defect_counts else None
        owner = await _default_owner(session, plant.id, "quality")

        action = Action(
            plant_id=plant.id,
            reference=await _next_reference(session, plant.id),
            title=(
                f"Scrap of {part_number} on {station_code}"
                + (f" — {labels.get(top, top)}" if top else "")
            ),
            description=f"{rejected} parts scrapped in one shift, {format_inr(cost)}.",
            trigger=ActionTrigger.BIG_SINGLE_EVENT,
            trigger_rationale="Scrap cost above the plant's Action threshold.",
            state=PdcaState.OPEN,
            station_id=station_id,
            part_id=part_id,
            loss_source=LOSS_QUALITY,
            linked_cost=cost,
            occurrence_count=len(batch),
            routed_to_rca=True,
            severity_reasons=[f"Scrap cost above the ₹{limits.cost_threshold // 100:,} threshold"],
            owner_id=owner.user_id if owner else None,
            owner_name=_owner_label(owner, "quality"),
            due_date=shift_session.business_date + timedelta(days=plant.action_due_days or 3),
            surfaced_in_meeting_on=shift_session.business_date,
        )
        session.add(action)
        await session.flush()
        action.baseline_weekly_loss = await compute_baseline(
            session,
            plant_id=plant.id,
            station_id=station_id,
            reason_code_id=None,
            as_of=shift_session.business_date,
            loss_source=LOSS_QUALITY,
            part_id=part_id,
        )
        action.baseline_frozen_at = datetime.now(UTC)
        await _assign_rca(
            session,
            plant=plant,
            action=action,
            route="quality",
            context={
                "occurrences": len(batch),
                "total_cost_paise": cost,
                "part": part_number,
                "station": station_code,
                "top_defect": labels.get(top, top) if top else None,
                "event_dates": [shift_session.business_date.isoformat()],
            },
        )
        created.append(action)
    return created


async def _assign_rca(
    session: AsyncSession, *, plant: Plant, action: Action, route: str, context: dict
) -> RootCauseAnalysis:
    """§5.5 — the team gets a 5-Why template pre-filled with the event history."""
    rca = RootCauseAnalysis(
        plant_id=plant.id,
        action_id=action.id,
        assigned_team=route,
        due_date=action.due_date,
        five_whys=[{"why": "", "because": ""} for _ in range(5)],
        prefilled_context=context,
    )
    session.add(rca)
    await session.flush()

    recipients = await notifications.recipients_for_route(session, plant.id, route)
    if recipients:
        dates = ", ".join(context.get("event_dates", [])[-3:])
        await notifications.enqueue(
            session,
            plant=plant,
            kind="rca_assignment",
            recipients=recipients,
            title=f"Root cause needed: {action.reference}",
            body=(
                f"{action.reference} {action.title}. {context.get('occurrences', 1)} event(s), "
                f"{format_inr(int(context.get('total_cost_paise', 0)))} so far"
                + (f" ({dates})" if dates else "")
                + f". Please find the root cause with 5-Why by {action.due_date:%d %b}."
            ),
            url=f"/actions/{action.id}",
            subject_type="action",
            subject_id=action.id,
            dedupe_key=f"rca:{action.id}",
        )
    return rca


def _route_of(reason: ReasonCode | None) -> str:
    route = getattr(getattr(reason, "escalation_route", None), "value", None) or "production"
    return "production" if route == "none" else route


async def _default_owner(
    session: AsyncSession, plant_id: uuid.UUID, route: str
) -> AlertRecipient | None:
    return await session.scalar(
        select(AlertRecipient)
        .where(
            AlertRecipient.plant_id == plant_id,
            AlertRecipient.role_tag == route,
            AlertRecipient.is_active.is_(True),
        )
        .order_by(AlertRecipient.escalation_level, AlertRecipient.name)
        .limit(1)
    )


def _owner_label(owner: AlertRecipient | None, route: str) -> str:
    if owner is not None:
        return owner.name
    # Nobody listed for this route in T5 yet: name the function, not a blank.
    return f"{route.replace('_', ' ').title()} lead"


# ---------------------------------------------------------------------------
# Measuring the loss an Action is fixing
# ---------------------------------------------------------------------------


async def weekly_loss(
    session: AsyncSession,
    *,
    plant_id: uuid.UUID,
    station_id: uuid.UUID | None,
    reason_code_id: uuid.UUID | None,
    start: date,
    end: date,
    loss_source: str = LOSS_DOWNTIME,
    part_id: uuid.UUID | None = None,
) -> int:
    """₹ lost on the loss an Action targets, for ``start <= business_date < end``.

    No station means the reason plant-wide — a Kaizen idea about material
    staging is measured on every station's material waits. No reason code on a
    downtime Action means there is nothing specific to measure.
    """
    if loss_source == LOSS_QUALITY:
        query = select(func.coalesce(func.sum(QualityRecord.cost_total), 0)).where(
            QualityRecord.plant_id == plant_id,
            QualityRecord.business_date >= start,
            QualityRecord.business_date < end,
        )
        if station_id is not None:
            query = query.where(QualityRecord.station_id == station_id)
        if part_id is not None:
            query = query.where(QualityRecord.part_id == part_id)
    else:
        if reason_code_id is None:
            return 0
        query = select(func.coalesce(func.sum(DowntimeEvent.cost_total), 0)).where(
            DowntimeEvent.plant_id == plant_id,
            DowntimeEvent.reason_code_id == reason_code_id,
            DowntimeEvent.business_date >= start,
            DowntimeEvent.business_date < end,
        )
        if station_id is not None:
            query = query.where(DowntimeEvent.station_id == station_id)
    return int(await session.scalar(query) or 0)


async def compute_baseline(
    session: AsyncSession,
    *,
    plant_id: uuid.UUID,
    station_id: uuid.UUID | None,
    reason_code_id: uuid.UUID | None,
    as_of: date,
    loss_source: str = LOSS_DOWNTIME,
    part_id: uuid.UUID | None = None,
) -> int:
    """Average weekly ₹ loss for this loss over the preceding 4 weeks."""
    weeks: list[WeeklyLoss] = []
    for offset in range(4, 0, -1):
        week_start = as_of - timedelta(weeks=offset)
        loss = await weekly_loss(
            session,
            plant_id=plant_id,
            station_id=station_id,
            reason_code_id=reason_code_id,
            start=week_start,
            end=week_start + timedelta(days=7),
            loss_source=loss_source,
            part_id=part_id,
        )
        weeks.append(WeeklyLoss(week_start, loss))
    return freeze_baseline(weeks)


async def _weeks_after_closure(
    session: AsyncSession, *, plant: Plant, action: Action, as_of: date
) -> list[WeeklyLoss]:
    """Complete weeks since the Action closed. A week still in progress is not a result.

    One grouped query, not one per week. This is called twice an hour for every
    Action still in CHECK or ACT, and a fix verified last year has fifty-two
    weeks behind it — a query per week would grow without bound for as long as
    the plant kept using the product, for an answer that only changes when a
    week rolls over or someone corrects a past shift.
    """
    closed_on = local_date(plant, action.closed_at)
    span = (as_of - closed_on).days // 7
    if span <= 0:
        return []

    end = closed_on + timedelta(days=7 * span)
    totals = await _loss_by_week(
        session,
        plant_id=plant.id,
        station_id=action.station_id,
        reason_code_id=action.reason_code_id,
        start=closed_on,
        end=end,
        loss_source=action.loss_source or LOSS_DOWNTIME,
        part_id=action.part_id,
    )
    return [
        WeeklyLoss(closed_on + timedelta(days=7 * index), totals.get(index, 0))
        for index in range(span)
    ]


async def _loss_by_week(
    session: AsyncSession,
    *,
    plant_id: uuid.UUID,
    station_id: uuid.UUID | None,
    reason_code_id: uuid.UUID | None,
    start: date,
    end: date,
    loss_source: str,
    part_id: uuid.UUID | None,
) -> dict[int, int]:
    """``{week index from start: paise}`` for ``start <= business_date < end``.

    The buckets are seven days from ``start``, not calendar weeks: an Action's
    weeks are measured from the day its fix landed. Weeks with no loss are
    absent, and the caller reads them as zero — which is the point of the
    measurement.
    """
    if end <= start:
        return {}

    model = QualityRecord if loss_source == LOSS_QUALITY else DowntimeEvent
    if loss_source != LOSS_QUALITY and reason_code_id is None:
        return {}

    query = (
        select(model.business_date, func.coalesce(func.sum(model.cost_total), 0))
        .where(
            model.plant_id == plant_id,
            model.business_date >= start,
            model.business_date < end,
        )
        .group_by(model.business_date)
    )
    if loss_source == LOSS_QUALITY:
        if part_id is not None:
            query = query.where(model.part_id == part_id)
    else:
        query = query.where(model.reason_code_id == reason_code_id)
    if station_id is not None:
        query = query.where(model.station_id == station_id)

    # Bucketed here rather than in SQL: the grouping key is "days since this
    # Action closed", which is not a calendar week, and the row count is one
    # per day that actually lost money — small, and already in hand.
    totals: dict[int, int] = {}
    for business_date, cost in (await session.execute(query)).all():
        index = (business_date - start).days // 7
        totals[index] = totals.get(index, 0) + int(cost or 0)
    return totals


async def accrue_recovered_profit(
    session: AsyncSession, *, plant: Plant, action: Action, as_of: date
) -> Action:
    """Weekly CHECK/ACT step: did the fix actually hold? (§5.6)

    Two consecutive sustained weeks promote the Action to ``verified``; credit
    accrues from the week after them. A week another Action on the same loss
    was already credited for is not credited twice (§12 risk 11: "never
    counted twice") — it belongs to whichever fix closed first.
    """
    if action.closed_at is None:
        return action

    weeks_after = await _weeks_after_closure(session, plant=plant, action=action, as_of=as_of)
    result = recovered_profit(
        baseline_weekly_loss=action.baseline_weekly_loss,
        weeks_after_closure=weeks_after,
        reopened=action.state is PdcaState.REOPENED,
    )
    claimed = await _weeks_claimed_elsewhere(session, plant=plant, action=action)

    existing = {
        row.week_start: row
        for row in (
            await session.scalars(
                select(RecoveredProfitEntry).where(RecoveredProfitEntry.action_id == action.id)
            )
        ).all()
    }
    credited = 0
    counted_weeks = 0
    for (week, saving), observed in zip(result.weekly_breakdown, weeks_after, strict=True):
        row = existing.get(week) or RecoveredProfitEntry(
            plant_id=plant.id, action_id=action.id, week_start=week
        )
        creditable = week in result.counted_weeks
        counts = creditable and week not in claimed
        row.baseline_weekly_loss = action.baseline_weekly_loss
        row.actual_weekly_loss = observed.loss
        row.saving = saving
        row.counted = counts
        row.note = (
            f"Already credited to {claimed[week]}"
            if creditable and week in claimed
            else None
        )
        session.add(row)
        if counts:
            credited += saving
            counted_weeks += 1

    action.recovered_profit = (action.recovered_profit_prior or 0) + credited
    action.sustained_weeks = counted_weeks
    if result.sustained and action.state is PdcaState.MONITORING:
        action.state = PdcaState.VERIFIED
        action.verified_at = datetime.now(UTC)

    await session.flush()
    return action


async def _weeks_claimed_elsewhere(
    session: AsyncSession, *, plant: Plant, action: Action
) -> dict[date, str]:
    """Weeks already credited to an earlier-closed Action on the same loss."""
    query = (
        select(RecoveredProfitEntry.week_start, Action.reference)
        .join(Action, Action.id == RecoveredProfitEntry.action_id)
        .where(
            RecoveredProfitEntry.plant_id == plant.id,
            RecoveredProfitEntry.counted.is_(True),
            Action.id != action.id,
            Action.loss_source == (action.loss_source or LOSS_DOWNTIME),
            Action.closed_at < action.closed_at,
        )
    )
    for column, value in (
        (Action.station_id, action.station_id),
        (Action.reason_code_id, action.reason_code_id),
        (Action.part_id, action.part_id),
    ):
        query = query.where(column.is_(None) if value is None else column == value)
    return dict((await session.execute(query)).all())


async def reopen_if_recurred(
    session: AsyncSession, *, plant: Plant, action: Action, as_of: date
) -> bool:
    """§5.4 ACT — "a fix that didn't hold is not a fix".

    Judged on the same measure as the saving: a complete week back at or above
    the baseline loss. Reopening stops accrual; the weeks the fix did hold stay
    credited, because they happened.
    """
    if action.closed_at is None or action.state not in (
        PdcaState.MONITORING,
        PdcaState.VERIFIED,
    ):
        return False

    weeks_after = await _weeks_after_closure(session, plant=plant, action=action, as_of=as_of)
    if not fix_failed(
        baseline_weekly_loss=action.baseline_weekly_loss,
        weeks_after_closure=weeks_after,
        events_since_closure=await _events_since_closure(session, plant=plant, action=action),
        recurrence_count=plant.recurrence_count,
    ):
        return False

    action.state = PdcaState.REOPENED
    action.reopened_at = datetime.now(UTC)
    action.reopen_count = (action.reopen_count or 0) + 1
    action.recovered_profit_prior = action.recovered_profit or 0
    await session.flush()
    return True


async def _events_since_closure(session: AsyncSession, *, plant: Plant, action: Action) -> int:
    window_end = action.closed_at + MONITORING_WINDOW
    if (action.loss_source or LOSS_DOWNTIME) == LOSS_QUALITY:
        query = select(func.count(QualityRecord.id)).where(
            QualityRecord.plant_id == plant.id,
            QualityRecord.reject_count > 0,
            QualityRecord.recorded_at > action.closed_at,
            QualityRecord.recorded_at <= window_end,
        )
        if action.station_id is not None:
            query = query.where(QualityRecord.station_id == action.station_id)
        if action.part_id is not None:
            query = query.where(QualityRecord.part_id == action.part_id)
    else:
        if action.reason_code_id is None:
            return 0
        query = select(func.count(DowntimeEvent.id)).where(
            DowntimeEvent.plant_id == plant.id,
            DowntimeEvent.reason_code_id == action.reason_code_id,
            DowntimeEvent.started_at > action.closed_at,
            DowntimeEvent.started_at <= window_end,
        )
        if action.station_id is not None:
            query = query.where(DowntimeEvent.station_id == action.station_id)
    return int(await session.scalar(query) or 0)


@dataclass(slots=True)
class MemoryCapture:
    """§3 Layer 4 / §5.3 — the four questions, asked once per significant fix."""

    what_fixed_it: str
    what_was_replaced: str | None = None
    photo_url: str | None = None
    check_first_next_time: str | None = None


async def close_action(
    session: AsyncSession,
    *,
    plant: Plant,
    action: Action,
    root_cause: str | None,
    countermeasure: str,
    memory: MemoryCapture,
    closed_by_name: str,
    closed_at: datetime | None = None,
) -> Action:
    """Screen 10 — closure moves the Action to CHECK, never straight to done.

    The four Factory Memory questions are captured here because this is the only
    moment the person who fixed it is both available and still remembers. Sixty
    seconds now; fifty closures later the plant owns a private repair playbook
    that survives any resignation.
    """
    closed_at = closed_at or datetime.now(UTC)

    if root_cause:
        action.root_cause = root_cause
    action.countermeasure = countermeasure
    if memory.photo_url:
        action.evidence_photo_url = memory.photo_url
    action.closed_at = closed_at
    # CHECK, not done: the system now watches the linked loss for 2-4 weeks to
    # see whether it actually stopped.
    action.state = PdcaState.MONITORING

    session.add(
        MemoryEntry(
            plant_id=plant.id,
            action_id=action.id,
            station_id=action.station_id,
            part_id=action.part_id,
            reason_code_id=action.reason_code_id,
            what_fixed_it=memory.what_fixed_it,
            what_was_replaced=memory.what_was_replaced,
            photo_url=memory.photo_url,
            check_first_next_time=memory.check_first_next_time,
            problem_summary=action.title,
            fixed_by_name=closed_by_name,
            occurred_on=local_date(plant, closed_at),
        )
    )
    await session.flush()
    return action


async def _already_covered(
    session: AsyncSession, plant_id: uuid.UUID, event_ids: tuple[str, ...]
) -> bool:
    """Never raise a second Action for events an open one already covers."""
    count = await session.scalar(
        select(func.count(ActionEvent.id))
        .join(Action, Action.id == ActionEvent.action_id)
        .where(
            ActionEvent.plant_id == plant_id,
            ActionEvent.downtime_event_id.in_([uuid.UUID(e) for e in event_ids]),
            Action.state.not_in([PdcaState.VERIFIED, PdcaState.CANCELLED]),
        )
    )
    return bool(count)


async def _next_reference(session: AsyncSession, plant_id: uuid.UUID) -> str:
    count = await session.scalar(
        select(func.count(Action.id)).where(Action.plant_id == plant_id)
    )
    return f"ACT-{(count or 0) + 1:04d}"
