"""Role pages (§8) and the monthly owner report (§4.4).

    "No KPI zoo. Launch with roughly a dozen KPIs across all roles — one
     executive page, drill-down per role. A new KPI is added only when a real
     user asks for it twice."

    "Every KPI is derived from data already being entered. If a proposed KPI
     would require new daily data entry, it is rejected."

Nothing here reads a table that capture screens don't already fill, and every
OEE figure goes through :func:`app.services.reports.station_runs` and
:func:`app.kernel.aggregate_oee` — the same path as the shift KPIs — so a line's
OEE on the supervisor page cannot disagree with the shift it came from.

§10's no-blame rule holds on every page: rows are machines, lines, reasons and
defects. Never people.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.kernel import (
    OeeResult,
    Recoverability,
    aggregate_oee,
    annualised_projection,
    first_pass_yield,
    format_inr,
    mtbf,
    mttr,
    ppm,
    running_roi,
    schedule_adherence,
)
from app.models import (
    Action,
    DefectCode,
    DowntimeEvent,
    PdcaState,
    Plant,
    QualityRecord,
    ReasonCode,
    RecoveredProfitEntry,
    ShiftSession,
    ShiftSessionStatus,
    Station,
)
from app.services.clock import plant_today
from app.services.reports import (
    man_hours,
    production_rates,
    ranked_loss_report,
    resolve_targets,
    station_runs,
)

OPEN_STATES = [
    PdcaState.OPEN,
    PdcaState.ROOT_CAUSE,
    PdcaState.PLANNED,
    PdcaState.IN_PROGRESS,
    PdcaState.REOPENED,
]


def _oee(result: OeeResult) -> dict:
    return {
        "oee": float(result.oee),
        "availability": float(result.availability),
        "performance": float(result.performance),
        "quality": float(result.quality),
        "planned_minutes": float(result.planned_minutes),
        "run_minutes": float(result.run_minutes),
        "produced": result.total_count,
        "flags": list(result.flags),
    }


async def _closed_shifts(
    session: AsyncSession, plant: Plant, start: date, end: date
) -> list[ShiftSession]:
    return list(
        (
            await session.scalars(
                select(ShiftSession)
                .where(
                    ShiftSession.plant_id == plant.id,
                    ShiftSession.status == ShiftSessionStatus.CLOSED,
                    ShiftSession.business_date >= start,
                    ShiftSession.business_date <= end,
                )
                .order_by(ShiftSession.business_date)
            )
        ).all()
    )


# ===========================================================================
# Supervisor — "what they control this shift, nothing they don't"
# ===========================================================================


async def supervisor(
    session: AsyncSession, *, plant: Plant, start: date, end: date
) -> dict:
    """OEE per line, production vs target, downtime minutes by reason, open actions."""
    shifts = await _closed_shifts(session, plant, start, end)
    shift_dates = {s.id: s.business_date for s in shifts}
    runs = await station_runs(session, plant=plant, shift_session_ids=list(shift_dates))

    plant_oee, _ = aggregate_oee([run for _, _, run in runs])

    by_line: dict[str, list] = defaultdict(list)
    for station, shift_id, run in runs:
        by_line[station.line or "Unassigned"].append((station, shift_id, run))

    # Targets are per shift (T7), so a line's target over a period is its
    # target for each shift it actually ran — resolved per shift, because a
    # target can change mid-period and differ by shift.
    shift_targets = {shift.id: await resolve_targets(session, plant.id, shift) for shift in shifts}

    lines = []
    for line, members in sorted(by_line.items()):
        result, _ = aggregate_oee([run for _, _, run in members])
        by_shift: dict[uuid.UUID, list] = defaultdict(list)
        for station, shift_id, _ in members:
            by_shift[shift_id].append(station)
        target = sum(
            shift_targets[shift_id].total_for(stations) for shift_id, stations in by_shift.items()
        )
        lines.append(
            {
                "line": line,
                **_oee(result),
                "target": target,
                "schedule_adherence": float(
                    schedule_adherence(
                        actual_output=result.total_count, planned_output=target
                    )
                ),
                "stations": len({station.id for station, _, _ in members}),
            }
        )

    daily: dict[date, list] = defaultdict(list)
    for _, shift_id, run in runs:
        daily[shift_dates[shift_id]].append(run)
    trend = [
        {"date": day.isoformat(), **_oee(aggregate_oee(day_runs)[0])}
        for day, day_runs in sorted(daily.items())
    ]

    reasons: dict[tuple[str, str], dict] = {}
    for _, _, run in runs:
        for event in run.downtime:
            key = (event.reason_code, event.recoverability.value)
            row = reasons.setdefault(
                key,
                {
                    "reason": event.reason_code,
                    "recoverability": event.recoverability.value,
                    "minutes": 0.0,
                    "events": 0,
                    "cost_paise": 0,
                },
            )
            row["minutes"] += float(event.minutes)
            row["events"] += 1
            row["cost_paise"] += event.cost
    by_reason = sorted(reasons.values(), key=lambda r: r["minutes"], reverse=True)
    for row in by_reason:
        row["cost_display"] = format_inr(row["cost_paise"])

    open_actions = await session.scalar(
        select(func.count(Action.id)).where(
            Action.plant_id == plant.id, Action.state.in_(OPEN_STATES)
        )
    )
    overdue = await session.scalar(
        select(func.count(Action.id)).where(
            Action.plant_id == plant.id,
            Action.state.in_(OPEN_STATES),
            Action.due_date < end,
        )
    )

    rates = production_rates(runs, await man_hours(session, list(shift_dates)))
    return {
        "plant": _oee(plant_oee),
        "shifts": len(shifts),
        "productivity": {key: value for key, value in rates.items() if key != "downtime_percent"},
        "downtime_percent": rates["downtime_percent"],
        "lines": lines,
        "trend": trend,
        "downtime_by_reason": by_reason,
        "open_actions": int(open_actions or 0),
        "overdue_actions": int(overdue or 0),
    }


# ===========================================================================
# Quality — "points them at the machine causing tomorrow's complaint"
# ===========================================================================


async def quality(session: AsyncSession, *, plant: Plant, start: date, end: date) -> dict:
    """PPM, first-pass yield, top 3 defects with trend, rejects by machine."""
    span = (end - start).days + 1
    prev_start, prev_end = start - timedelta(days=span), start - timedelta(days=1)

    async def records(a: date, b: date):
        return (
            await session.execute(
                select(QualityRecord, Station.code)
                .outerjoin(Station, Station.id == QualityRecord.station_id)
                .where(
                    QualityRecord.plant_id == plant.id,
                    QualityRecord.business_date >= a,
                    QualityRecord.business_date <= b,
                )
            )
        ).all()

    current = await records(start, end)
    previous = await records(prev_start, prev_end)

    good = sum(r.good_count for r, _ in current)
    rejected = sum(r.reject_count for r, _ in current)
    reworked = sum(r.rework_count for r, _ in current)
    inspected = good + rejected

    labels = dict(
        (
            await session.execute(
                select(DefectCode.code, DefectCode.label).where(
                    DefectCode.plant_id == plant.id
                )
            )
        ).all()
    )

    def defect_counts(rows) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for record, _ in rows:
            for defect in record.defects or []:
                counts[defect.get("code", "?")] += int(defect.get("count", 0))
        return counts

    now_counts, then_counts = defect_counts(current), defect_counts(previous)
    previous_inspected = sum(r.good_count + r.reject_count for r, _ in previous)
    top_defects = [
        {
            "code": code,
            "label": labels.get(code, code),
            "count": count,
            "ppm": float(ppm(rejected_parts=count, total_inspected=inspected)),
            # Trend as PPM, not raw counts: two periods rarely inspect the same
            # number of parts, and a count rising with volume is not a problem.
            "previous_ppm": float(
                ppm(
                    rejected_parts=then_counts.get(code, 0),
                    total_inspected=previous_inspected,
                )
            ),
        }
        for code, count in sorted(now_counts.items(), key=lambda kv: kv[1], reverse=True)[
            :3
        ]
    ]

    machines: dict[str, dict] = {}
    for record, code in current:
        row = machines.setdefault(
            code or "Unassigned",
            {
                "station": code or "Unassigned",
                "inspected": 0,
                "rejected": 0,
                "scrap_cost_paise": 0,
            },
        )
        row["inspected"] += record.good_count + record.reject_count
        row["rejected"] += record.reject_count
        row["scrap_cost_paise"] += record.cost_total
    by_machine = sorted(machines.values(), key=lambda r: r["rejected"], reverse=True)
    for row in by_machine:
        row["ppm"] = float(
            ppm(rejected_parts=row["rejected"], total_inspected=row["inspected"])
        )
        row["scrap_cost_display"] = format_inr(row["scrap_cost_paise"])

    daily: dict[date, list[int]] = defaultdict(lambda: [0, 0])
    for record, _ in current:
        daily[record.business_date][0] += record.good_count + record.reject_count
        daily[record.business_date][1] += record.reject_count
    trend = [
        {
            "date": day.isoformat(),
            "ppm": float(ppm(rejected_parts=rej, total_inspected=insp)),
        }
        for day, (insp, rej) in sorted(daily.items())
    ]

    scrap = sum(r.cost_total for r, _ in current)
    return {
        "inspected": inspected,
        "rejected": rejected,
        "ppm": float(ppm(rejected_parts=rejected, total_inspected=inspected)),
        "previous_ppm": float(
            ppm(
                rejected_parts=sum(r.reject_count for r, _ in previous),
                total_inspected=previous_inspected,
            )
        ),
        # Reworked-then-good parts do not count as first-pass (§8.1).
        "first_pass_yield": float(
            first_pass_yield(
                good_first_time=max(0, good - reworked), parts_started=inspected
            )
        ),
        "scrap_cost_paise": scrap,
        "scrap_cost_display": format_inr(scrap),
        "top_defects": top_defects,
        "by_machine": by_machine,
        "trend": trend,
    }


# ===========================================================================
# Maintenance — "turns 'CNC-3 keeps breaking' into a number that ranks their day"
# ===========================================================================


async def maintenance(
    session: AsyncSession, *, plant: Plant, start: date, end: date
) -> dict:
    """Breakdown count per machine, MTBF and MTTR.

    A breakdown is any stop whose reason code carries ``is_breakdown`` — never a
    string match on a label (§8.1).
    """
    shifts = await _closed_shifts(session, plant, start, end)
    runs = await station_runs(
        session, plant=plant, shift_session_ids=[s.id for s in shifts]
    )

    per_station: dict[uuid.UUID, dict] = {}
    for station, _, run in runs:
        row = per_station.setdefault(
            station.id,
            {
                "station": station.code,
                "line": station.line,
                "is_bottleneck": station.is_bottleneck,
                "runs": [],
            },
        )
        row["runs"].append(run)

    rows = []
    plant_run = Decimal(0)
    plant_breakdowns = 0
    plant_breakdown_minutes = Decimal(0)
    for row in per_station.values():
        result, _ = aggregate_oee(row["runs"])
        events = [e for run in row["runs"] for e in run.downtime if e.is_breakdown]
        count = len(events)
        minutes = sum((e.minutes for e in events), Decimal(0))
        cost = sum(e.cost for e in events)
        plant_run += result.run_minutes
        plant_breakdowns += count
        plant_breakdown_minutes += minutes
        rows.append(
            {
                "station": row["station"],
                "line": row["line"],
                "is_bottleneck": row["is_bottleneck"],
                "breakdowns": count,
                "breakdown_minutes": float(minutes),
                "breakdown_cost_paise": cost,
                "breakdown_cost_display": format_inr(cost),
                # No breakdowns means no MTBF to report — not an MTBF of zero,
                # which would read as a machine that fails the moment it starts.
                "mtbf_minutes": _between_failures(result.run_minutes, count),
                "mttr_minutes": _to_repair(minutes, count),
                "availability": float(result.availability),
            }
        )

    # Ranked by what the breakdowns cost, then how often: that is the order a
    # maintenance in-charge should walk the floor in.
    rows.sort(key=lambda r: (r["breakdown_cost_paise"], r["breakdowns"]), reverse=True)

    return {
        "breakdowns": plant_breakdowns,
        "mtbf_minutes": _between_failures(plant_run, plant_breakdowns),
        "mttr_minutes": _to_repair(plant_breakdown_minutes, plant_breakdowns),
        "machines": rows,
    }


def _between_failures(run_minutes: Decimal, breakdowns: int) -> float | None:
    if not breakdowns:
        return None
    return float(mtbf(total_run_minutes=run_minutes, breakdown_count=breakdowns))


def _to_repair(minutes: Decimal, breakdowns: int) -> float | None:
    if not breakdowns:
        return None
    return float(mttr(total_breakdown_minutes=minutes, breakdown_count=breakdowns))


# ===========================================================================
# Executive — the §8 owner KPIs not already on the Money screen
# ===========================================================================


async def executive(session: AsyncSession, *, plant: Plant, start: date, end: date) -> dict:
    """Recoverable loss for the period and month, ₹ recovered vs last month,
    top 5 losses, quality PPM. Money first; no OEE."""
    month_start = end.replace(day=1)
    last_month_end = month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    period = await ranked_loss_report(session, plant=plant, start=start, end=end)
    month = await ranked_loss_report(session, plant=plant, start=month_start, end=end)

    async def recovered(a: date, b: date) -> int:
        return int(
            await session.scalar(
                select(func.coalesce(func.sum(RecoveredProfitEntry.saving), 0)).where(
                    RecoveredProfitEntry.plant_id == plant.id,
                    RecoveredProfitEntry.counted.is_(True),
                    RecoveredProfitEntry.week_start >= a,
                    RecoveredProfitEntry.week_start <= b,
                )
            )
            or 0
        )

    this_month = await recovered(month_start, end)
    last_month = await recovered(last_month_start, last_month_end)
    q = await quality(session, plant=plant, start=start, end=end)

    shifts = await _closed_shifts(session, plant, start, end)
    shift_ids = [shift.id for shift in shifts]
    rates = production_rates(
        await station_runs(session, plant=plant, shift_session_ids=shift_ids),
        await man_hours(session, shift_ids),
    )

    return {
        "recoverable_loss_paise": period.recoverable_total,
        "recoverable_loss_display": format_inr(period.recoverable_total),
        "month_recoverable_loss_paise": month.recoverable_total,
        "month_recoverable_loss_display": format_inr(month.recoverable_total),
        "recovered_this_month_paise": this_month,
        "recovered_this_month_display": format_inr(this_month),
        "recovered_last_month_paise": last_month,
        "recovered_last_month_display": format_inr(last_month),
        "top_losses": period.recoverable[:5],
        "ppm": q["ppm"],
        "previous_ppm": q["previous_ppm"],
        "productivity": {key: value for key, value in rates.items() if key != "downtime_percent"},
        "downtime_percent": rates["downtime_percent"],
    }


# ===========================================================================
# §4.4 — the monthly owner report
# ===========================================================================


async def monthly_report(session: AsyncSession, *, plant: Plant, month: date) -> dict:
    """What the owner reads once a month.

    §5.6 sets the order: "The owner's monthly report leads with: total ₹
    recovered this month, cumulative ₹ recovered vs annual fee (the running
    ROI), and the top open opportunities." §4.4 adds the recoverable-loss trend,
    the annualised projection and the top recurring problems.
    """
    start = month.replace(day=1)
    next_month = (start + timedelta(days=32)).replace(day=1)
    end = min(next_month - timedelta(days=1), plant_today(plant))
    prev_end = start - timedelta(days=1)
    prev_start = prev_end.replace(day=1)

    async def recovered(a: date | None, b: date) -> int:
        query = select(func.coalesce(func.sum(RecoveredProfitEntry.saving), 0)).where(
            RecoveredProfitEntry.plant_id == plant.id,
            RecoveredProfitEntry.counted.is_(True),
            RecoveredProfitEntry.week_start <= b,
        )
        if a is not None:
            query = query.where(RecoveredProfitEntry.week_start >= a)
        return int(await session.scalar(query) or 0)

    recovered_month = await recovered(start, end)
    recovered_cumulative = await recovered(None, end)

    months_live = 0
    if plant.go_live_date and plant.go_live_date <= end:
        months_live = (
            (end.year - plant.go_live_date.year) * 12
            + end.month
            - plant.go_live_date.month
            + 1
        )
    fees = plant.monthly_fee * months_live

    losses = await ranked_loss_report(session, plant=plant, start=start, end=end)
    previous = await ranked_loss_report(
        session, plant=plant, start=prev_start, end=prev_end
    )

    weeks = []
    cursor = start - timedelta(days=start.weekday())
    while cursor <= end:
        week_end = cursor + timedelta(days=6)
        total = await session.scalar(
            select(func.coalesce(func.sum(DowntimeEvent.cost_total), 0))
            .join(ReasonCode, ReasonCode.id == DowntimeEvent.reason_code_id)
            .where(
                DowntimeEvent.plant_id == plant.id,
                DowntimeEvent.business_date >= max(cursor, start),
                DowntimeEvent.business_date <= min(week_end, end),
                ReasonCode.recoverability == Recoverability.RECOVERABLE,
            )
        )
        weeks.append(
            {
                "week_start": cursor.isoformat(),
                "recoverable_paise": int(total or 0),
                "recoverable_display": format_inr(int(total or 0)),
                "partial": cursor < start or week_end > end,
            }
        )
        cursor += timedelta(days=7)

    four_weeks = await ranked_loss_report(
        session, plant=plant, start=end - timedelta(days=27), end=end
    )

    # "Top recurring problems": the same reason on the same machine at least
    # three times this month, ranked by what the repetition cost.
    recurring = [line for line in losses.recoverable if line["event_count"] >= 3][:5]

    opportunities = (
        await session.scalars(
            select(Action)
            .where(Action.plant_id == plant.id, Action.state.in_(OPEN_STATES))
            .order_by(Action.linked_cost.desc())
            .limit(5)
        )
    ).all()

    async def count_actions(column) -> int:
        return int(
            await session.scalar(
                select(func.count(Action.id)).where(
                    Action.plant_id == plant.id,
                    column >= start,
                    column <= end,
                )
            )
            or 0
        )

    # "Raised" is the business day the Action surfaced in the morning meeting,
    # not the moment its row was written — those differ whenever data is
    # imported, back-filled or entered after the fact.
    raised_on = func.coalesce(Action.surfaced_in_meeting_on, func.date(Action.created_at))

    shifts = await _closed_shifts(session, plant, start, end)
    overridden = sum(1 for s in shifts if s.override_reason)
    q = await quality(session, plant=plant, start=start, end=end)

    change = (
        (losses.recoverable_total - previous.recoverable_total) / previous.recoverable_total
        if previous.recoverable_total
        else None
    )

    return {
        "month": start.isoformat()[:7],
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "plant": plant.name,
        "recovered_this_month_paise": recovered_month,
        "recovered_this_month_display": format_inr(recovered_month),
        "recovered_cumulative_paise": recovered_cumulative,
        "recovered_cumulative_display": format_inr(recovered_cumulative),
        "fees_cumulative_paise": fees,
        "fees_cumulative_display": format_inr(fees),
        "running_roi": float(
            running_roi(
                cumulative_recovered=recovered_cumulative, cumulative_fees_paid=fees
            )
        ),
        "recoverable_loss_paise": losses.recoverable_total,
        "recoverable_loss_display": format_inr(losses.recoverable_total),
        "structural_loss_paise": losses.structural_total,
        "structural_loss_display": format_inr(losses.structural_total),
        "previous_recoverable_loss_paise": previous.recoverable_total,
        "previous_recoverable_loss_display": format_inr(previous.recoverable_total),
        "change_vs_previous": float(change) if change is not None else None,
        "weekly_trend": weeks,
        # §8.1: "always labelled as projection, never as fact".
        "annualised_projection_paise": annualised_projection(four_weeks.recoverable_total),
        "annualised_projection_display": format_inr(
            annualised_projection(four_weeks.recoverable_total), compact=True
        ),
        "top_recurring": recurring,
        "top_opportunities": [
            {
                "reference": a.reference,
                "title": a.title,
                "state": a.state.value,
                "owner": a.owner_name,
                "linked_cost_paise": a.linked_cost,
                "linked_cost_display": format_inr(a.linked_cost),
                "due_date": a.due_date.isoformat() if a.due_date else None,
            }
            for a in opportunities
        ],
        "actions": {
            "raised": await count_actions(raised_on),
            "closed": await count_actions(func.date(Action.closed_at)),
            "verified": await count_actions(func.date(Action.verified_at)),
        },
        # §12 risk 1 — "Watch: % of shifts closed without override." The early
        # warning that data-entry discipline is slipping.
        "discipline": {
            "shifts_closed": len(shifts),
            "closed_with_override": overridden,
            "closed_clean_share": ((len(shifts) - overridden) / len(shifts))
            if shifts
            else None,
        },
        "quality_ppm": q["ppm"],
    }
