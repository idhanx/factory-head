"""Screens 7 and 8 — the morning meeting and the owner's money screen."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Identity, get_identity, get_plant, get_session, require
from app.kernel import format_inr
from app.models import (
    Action,
    DefectCode,
    PdcaState,
    Plant,
    QualityRecord,
    Role,
    ShiftSession,
    ShiftSessionStatus,
)
from app.schemas import (
    LossLineOut,
    LossReportOut,
    MeetingPackOut,
    OwnerHomeOut,
    ShiftKpiOut,
)
from app.services import reports as service
from app.services import role_kpis as role_service
from app.services.clock import plant_today

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get(
    "/owner-home",
    response_model=OwnerHomeOut,
    dependencies=[Depends(require(Role.OWNER, Role.CONSULTANT))],
)
async def owner_home(
    as_of: date | None = None,
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Four numbers. Every one of them a door.

    §7: "An owner opening the app sees four numbers and nothing else:
    Recovered Profit yesterday, Lost Profit yesterday, Actions completed,
    running ROI." The owner should never need to understand OEE to use this.
    """
    home = await service.owner_home(session, plant=plant, as_of=as_of)
    return OwnerHomeOut(
        recovered_profit_paise=home.recovered_profit,
        recovered_profit_display=format_inr(home.recovered_profit, compact=True),
        lost_yesterday_paise=home.lost_yesterday,
        lost_yesterday_display=format_inr(home.lost_yesterday),
        lost_month_to_date_paise=home.lost_month_to_date,
        lost_month_to_date_display=format_inr(home.lost_month_to_date, compact=True),
        actions_completed=home.actions_completed,
        actions_open=home.actions_open,
        running_roi=home.running_roi,
        annualised_projection_paise=home.annualised_projection,
        annualised_projection_display=format_inr(home.annualised_projection, compact=True),
        structural_yesterday_paise=home.structural_yesterday,
        top_losses=[LossLineOut(**line) for line in home.top_losses],
        as_of=home.as_of,
    )


@router.get("/losses", response_model=LossReportOut)
async def losses(
    start: date | None = None,
    end: date | None = None,
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """The ranked loss table for any period. Recoverable and Structural, split."""
    end = end or plant_today(plant)
    start = start or end
    report = await service.ranked_loss_report(session, plant=plant, start=start, end=end)
    return _loss_out(report)


OWNER_ONLY = (Role.OWNER, Role.CONSULTANT)


@router.get("/trend", dependencies=[Depends(require(*OWNER_ONLY))])
async def trend(
    days: int = 28,
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Daily loss for the owner's trend chart, plus the recovered-profit curve.

    Two separate series in one payload, never combined into a total — see
    `loss_trend` for why.
    """
    end = plant_today(plant)
    start = end - timedelta(days=max(1, min(days, 180)))
    return {
        "loss": await service.loss_trend(session, plant=plant, start=start, end=end),
        "recovered": await service.recovered_trend(session, plant=plant),
    }


@router.get("/roles/{role}")
async def role_kpis(
    role: str,
    days: int = 7,
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """§8 — one executive page, drill-down per role.

    Every row is a machine, a line, a reason or a defect — never a person (§10).
    The executive page is the owner's: it carries recovered profit against the
    fee and the annualised projection, which are not a shopfloor's business.
    """
    if role == "executive" and not identity.has(Role.ADMIN, *OWNER_ONLY):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The executive page is for the owner")
    builders = {
        "executive": role_service.executive,
        "supervisor": role_service.supervisor,
        "quality": role_service.quality,
        "maintenance": role_service.maintenance,
    }
    builder = builders.get(role)
    if builder is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No KPI page for {role!r}")

    end = plant_today(plant) - timedelta(days=1)
    start = end - timedelta(days=max(1, min(days, 90)) - 1)
    payload = await builder(session, plant=plant, start=start, end=end)
    return {
        "role": role,
        "period": {"start": start, "end": end, "days": (end - start).days + 1},
        **payload,
    }


@router.get("/monthly", dependencies=[Depends(require(*OWNER_ONLY))])
async def monthly(
    month: str | None = None,
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """§4.4 — the monthly owner report. `month` is YYYY-MM; defaults to last month."""
    if month:
        try:
            first = date.fromisoformat(f"{month}-01")
        except ValueError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "month must be YYYY-MM"
            ) from None
    else:
        first = (plant_today(plant).replace(day=1) - timedelta(days=1)).replace(day=1)
    return await role_service.monthly_report(session, plant=plant, month=first)


@router.get("/meeting", response_model=MeetingPackOut)
async def meeting_pack(
    business_date: date | None = None,
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Screen 7 — Morning Meeting Mode. Readable from five metres.

    Yesterday's numbers, locked. Nobody argues about them because nobody can
    edit them. Top 3 downtime and top 3 quality losses by ₹, and the actions
    gate: Today's Plan stays closed until yesterday's escalated Actions are each
    marked Done or Delayed-with-reason.
    """
    today = plant_today(plant)
    business_date = business_date or (today - timedelta(days=1))
    report = await service.ranked_loss_report(
        session, plant=plant, start=business_date, end=business_date
    )

    # Locked means every shift of that day is closed — one closed shift while
    # another is still running is not a set of numbers anyone can stop arguing about.
    statuses = (
        await session.scalars(
            select(ShiftSession.status).where(
                ShiftSession.plant_id == plant.id,
                ShiftSession.business_date == business_date,
            )
        )
    ).all()
    locked = bool(statuses) and all(s is ShiftSessionStatus.CLOSED for s in statuses)

    open_actions = (
        await session.scalars(
            select(Action)
            .where(
                Action.plant_id == plant.id,
                Action.state.in_(
                    [
                        PdcaState.OPEN,
                        PdcaState.ROOT_CAUSE,
                        PdcaState.PLANNED,
                        PdcaState.IN_PROGRESS,
                        PdcaState.REOPENED,
                    ]
                ),
                Action.surfaced_in_meeting_on <= business_date,
            )
            .order_by(Action.linked_cost.desc())
        )
    ).all()

    def status_today(action: Action) -> str | None:
        return action.meeting_status if action.meeting_status_on == today else None

    # Only the escalated ones gate the meeting, and each is asked again every
    # morning it is still open: "Delayed" last Tuesday is not an answer today.
    # Small logged losses never gate it — that is the triage §5.3 asks for.
    blocking = [a for a in open_actions if a.routed_to_rca and status_today(a) is None]

    return MeetingPackOut(
        business_date=business_date,
        locked=locked,
        losses=_loss_out(report),
        top_quality=await _top_quality(session, plant=plant, business_date=business_date),
        open_actions=[
            {
                "id": str(a.id),
                "reference": a.reference,
                "title": a.title,
                "owner": a.owner_name,
                "due_date": a.due_date.isoformat() if a.due_date else None,
                "cost_display": format_inr(a.linked_cost),
                "state": a.state.value,
                "routed_to_rca": a.routed_to_rca,
                "meeting_status": a.meeting_status,
                "meeting_status_today": status_today(a),
                "is_overdue": bool(a.due_date and a.due_date < today),
            }
            for a in open_actions[:50]
        ],
        actions_gate_cleared=not blocking,
        blocking_actions=[
            {"id": str(a.id), "reference": a.reference, "title": a.title} for a in blocking
        ],
    )


@router.get("/kpis/{shift_session_id}", response_model=ShiftKpiOut)
async def shift_kpis(
    shift_session_id: uuid.UUID,
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    shift_session = await session.get(ShiftSession, shift_session_id)
    if shift_session is None or shift_session.plant_id != plant.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shift session not found")

    kpis = await service.shift_kpis(session, plant=plant, shift_session=shift_session)
    # ShiftKpis is a slots dataclass: it has no __dict__, which is what turned
    # this endpoint into a 500. asdict() is the supported way to read it.
    return ShiftKpiOut(
        **{
            **asdict(kpis),
            "recoverable_loss_paise": kpis.recoverable_loss,
            "structural_loss_paise": kpis.structural_loss,
        }
    )


@router.get("/shift-report/{shift_session_id}")
async def shift_report(
    shift_session_id: uuid.UUID,
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """The end-of-shift message the owner reads on their phone in 90 seconds.

    §6, 3:35 PM. Same numbers as every other screen, phrased for someone in a
    customer meeting in Chennai.
    """
    shift_session = await session.get(ShiftSession, shift_session_id)
    if shift_session is None or shift_session.plant_id != plant.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shift session not found")

    return await service.shift_report_text(session, plant=plant, shift_session=shift_session)


def _loss_out(report) -> LossReportOut:
    return LossReportOut(
        business_date=report.business_date,
        recoverable_total_paise=report.recoverable_total,
        recoverable_total_display=format_inr(report.recoverable_total),
        structural_total_paise=report.structural_total,
        structural_total_display=format_inr(report.structural_total),
        total_minutes=float(report.total_minutes),
        recoverable=[LossLineOut(**line) for line in report.recoverable],
        structural=[LossLineOut(**line) for line in report.structural],
    )


async def _top_quality(
    session: AsyncSession, *, plant: Plant, business_date: date
) -> list[dict]:
    """Top defects by cost — §7 Screen 7's 'top 3 quality by ₹'."""
    records = (
        await session.scalars(
            select(QualityRecord).where(
                QualityRecord.plant_id == plant.id,
                QualityRecord.business_date == business_date,
            )
        )
    ).all()

    labels = dict(
        (
            await session.execute(
                select(DefectCode.code, DefectCode.label).where(
                    DefectCode.plant_id == plant.id
                )
            )
        ).all()
    )

    totals: dict[str, dict] = {}
    for record in records:
        # Rejections in a batch share that batch's scrap cost in proportion to
        # how many parts each defect accounted for.
        defects = record.defects or []
        defect_parts = sum(int(d.get("count", 0)) for d in defects) or 1
        for defect in defects:
            code = defect.get("code", "unknown")
            count = int(defect.get("count", 0))
            entry = totals.setdefault(
                code, {"code": code, "label": labels.get(code, code), "count": 0, "cost": 0}
            )
            entry["count"] += count
            entry["cost"] += int(record.cost_total * count / defect_parts)

    ranked = sorted(totals.values(), key=lambda e: e["cost"], reverse=True)[:3]
    for entry in ranked:
        entry["cost_display"] = format_inr(entry["cost"])
    return ranked
