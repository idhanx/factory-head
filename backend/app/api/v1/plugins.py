"""Layer 5 — the three plugins, on one router.

They share a router because they share everything else: the same Core data, the
same Actions engine, the same rupee ledger. §3 Layer 5 is explicit that they are
"never separate products — no separate logins, no separate data entry".
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Identity, get_identity, get_plant, get_session, require
from app.api.scoping import in_plant
from app.kernel import format_inr
from app.models import (
    AuditPack,
    ClauseStatus,
    ComplianceClause,
    KaizenIdea,
    KaizenStatus,
    Operator,
    Plant,
    Role,
    Standard,
    Station,
    TrainingNeed,
)
from app.schemas import (
    AuditPackOut,
    ClauseOut,
    ComplianceOverview,
    KaizenDecision,
    KaizenIdeaOut,
    KaizenSubmission,
    SkillMatrixOut,
    SkillUpdate,
    TrainingNeedOut,
)
from app.services import compliance as compliance_service
from app.services import kaizen as kaizen_service
from app.services import skills as skills_service

router = APIRouter(prefix="/plugins", tags=["plugins"])


# ===========================================================================
# Compliance / Audit AI
# ===========================================================================


@router.get("/compliance", response_model=ComplianceOverview)
async def compliance_overview(
    standard: Standard | None = None,
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Audit readiness as last scored from the plant's own Core data.

    A read never writes. The worker re-scores every plant hourly, and
    ``POST /plugins/compliance/refresh`` re-scores on demand — so the number is
    current without every page view rewriting the clause table.
    """
    clauses = (
        await session.scalars(
            select(ComplianceClause).where(ComplianceClause.plant_id == plant.id)
        )
    ).all()
    return _overview(clauses, standard)


@router.post(
    "/compliance/refresh",
    response_model=ComplianceOverview,
    dependencies=[Depends(require(Role.OWNER, Role.CONSULTANT, Role.QUALITY))],
)
async def refresh_compliance(
    standard: Standard | None = None,
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Re-score every clause now, seeding the clause library on first use."""
    return _overview(await compliance_service.refresh(session, plant=plant), standard)


def _overview(clauses, standard: Standard | None) -> ComplianceOverview:
    scoped = [c for c in clauses if standard is None or c.standard is standard]
    scoped.sort(key=lambda c: (c.standard.value, c.display_order))

    return ComplianceOverview(
        overall_score=compliance_service.readiness_score(clauses, standard),
        by_standard={
            s.value: compliance_service.readiness_score(clauses, s)
            for s in Standard
            if any(c.standard is s for c in clauses)
        },
        ready=sum(1 for c in scoped if c.status is ClauseStatus.READY),
        partial=sum(1 for c in scoped if c.status is ClauseStatus.PARTIAL),
        gaps=sum(1 for c in scoped if c.status is ClauseStatus.GAP),
        clauses=[_clause_out(c) for c in scoped],
    )


@router.get("/compliance/{clause_id}/evidence")
async def clause_evidence(
    clause_id: uuid.UUID,
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """The actual records behind a clause — what you hand the auditor."""
    clause = await session.get(ComplianceClause, clause_id)
    if clause is None or clause.plant_id != plant.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Clause not found")

    return {
        "clause": _clause_out(clause).model_dump(mode="json"),
        "evidence": await compliance_service.collect_evidence(
            session, plant=plant, clause=clause
        ),
    }


@router.post(
    "/compliance/pack",
    response_model=AuditPackOut,
    dependencies=[Depends(require(Role.OWNER, Role.CONSULTANT, Role.QUALITY))],
)
async def generate_pack(
    standard: Standard,
    period_start: date,
    period_end: date,
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Freeze an evidence pack for a period.

    Frozen deliberately: an auditor asked for evidence on a date, and the plant
    must be able to show exactly what was handed over — not a live view that has
    since moved on.
    """
    clauses = await compliance_service.score_readiness(session, plant=plant)
    scoped = [c for c in clauses if c.standard is standard]

    evidence = {}
    for clause in scoped:
        evidence[clause.clause_ref] = await compliance_service.collect_evidence(
            session, plant=plant, clause=clause
        )

    pack = AuditPack(
        plant_id=plant.id,
        standard=standard,
        period_start=period_start,
        period_end=period_end,
        readiness_score=compliance_service.readiness_score(clauses, standard),
        clause_summary=[_clause_out(c).model_dump(mode="json") for c in scoped],
        evidence=evidence,
        generated_by_id=identity.user.id,
    )
    session.add(pack)
    await session.flush()

    return AuditPackOut(
        id=pack.id,
        standard=pack.standard.value,
        period_start=pack.period_start,
        period_end=pack.period_end,
        readiness_score=pack.readiness_score,
        clause_count=len(scoped),
        evidence_rows=sum(len(rows) for rows in evidence.values()),
        created_at=pack.created_at,
    )


# ===========================================================================
# Kaizen AI
# ===========================================================================


@router.get("/kaizen", response_model=list[KaizenIdeaOut])
async def list_ideas(
    status_filter: str | None = None,
    identity: Identity = Depends(get_identity),
    session: AsyncSession = Depends(get_session),
):
    query = select(KaizenIdea).where(KaizenIdea.plant_id == identity.plant_id)
    if status_filter:
        try:
            wanted = KaizenStatus(status_filter)
        except ValueError:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown status {status_filter!r}"
            ) from None
        query = query.where(KaizenIdea.status == wanted)
    rows = (
        await session.scalars(
            query.order_by(KaizenIdea.estimated_annual_saving.desc()).limit(200)
        )
    ).all()
    return [await _kaizen_out(session, idea) for idea in rows]


@router.post("/kaizen", response_model=KaizenIdeaOut)
async def submit_idea(
    payload: KaizenSubmission,
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Submit an idea and get it scored immediately.

    Anyone in the plant can submit. §5.2's third Action trigger is "anyone can
    raise an Action from any observation — this is also where Kaizen ideas
    enter", so the door is deliberately wide.
    """
    await in_plant(session, Station, payload.station_id, plant.id, "Station")
    await in_plant(session, Operator, payload.operator_id, plant.id, "Operator")
    idea = await kaizen_service.submit(
        session,
        plant=plant,
        title=payload.title,
        description=payload.description,
        submitted_by_name=payload.submitted_by_name or identity.display_name,
        station_id=payload.station_id,
        operator_id=payload.operator_id,
    )
    return await _kaizen_out(session, idea)


@router.post(
    "/kaizen/{idea_id}/decide",
    response_model=KaizenIdeaOut,
    dependencies=[Depends(require(Role.OWNER, Role.SUPERVISOR, Role.CONSULTANT))],
)
async def decide_idea(
    idea_id: uuid.UUID,
    payload: KaizenDecision,
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Accept an idea into the Actions engine, or decline it with a reason."""
    idea = await session.get(KaizenIdea, idea_id)
    if idea is None or idea.plant_id != plant.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Idea not found")
    if idea.decided_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already decided")

    if payload.accept:
        await kaizen_service.accept(
            session,
            plant=plant,
            idea=idea,
            owner_name=payload.owner_name,
            due_date=payload.due_date,
            note=payload.note,
        )
    else:
        if not payload.note:
            # An idea that disappears without an answer kills the scheme.
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Declining an idea needs a reason the operator can read.",
            )
        await kaizen_service.reject(session, idea=idea, note=payload.note)

    return await _kaizen_out(session, idea)


@router.get("/kaizen/leaderboard")
async def kaizen_leaderboard(
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    rows = await kaizen_service.leaderboard(session, plant=plant)
    for row in rows:
        row["estimated_annual_saving_display"] = format_inr(
            row["estimated_annual_saving_paise"]
        )
    return rows


# ===========================================================================
# Skill Matrix AI
# ===========================================================================


@router.get("/skills/matrix", response_model=SkillMatrixOut)
async def skill_matrix(
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    return await skills_service.matrix(session, plant=plant)


@router.put(
    "/skills",
    dependencies=[Depends(require(Role.OWNER, Role.SUPERVISOR, Role.CONSULTANT))],
)
async def update_skill(
    payload: SkillUpdate,
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    await in_plant(session, Operator, payload.operator_id, plant.id, "Operator")
    await in_plant(session, Station, payload.station_id, plant.id, "Station")
    try:
        skill = await skills_service.set_skill(
            session,
            plant=plant,
            operator_id=payload.operator_id,
            station_id=payload.station_id,
            level=payload.level,
            certified_on=payload.certified_on or date.today(),
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"id": str(skill.id), "level": skill.level}


@router.get("/skills/needs", response_model=list[TrainingNeedOut])
async def training_needs(
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Training needs, ranked by the rupee loss they would remove, as last detected.

    Detection runs hourly in the worker and on ``POST /skills/needs/refresh``; a
    read never regenerates the list, so the ids a supervisor is looking at stay
    valid while they decide which need to raise an Action for.
    """
    needs = sorted(
        (
            await session.scalars(
                select(TrainingNeed).where(
                    TrainingNeed.plant_id == plant.id,
                    TrainingNeed.resolved.is_(False),
                )
            )
        ).all(),
        key=skills_service.rank_key,
    )
    return await _needs_out(session, plant, needs)


@router.post(
    "/skills/needs/refresh",
    response_model=list[TrainingNeedOut],
    dependencies=[Depends(require(Role.OWNER, Role.SUPERVISOR, Role.CONSULTANT))],
)
async def refresh_training_needs(
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    await skills_service.detect_needs(session, plant=plant)
    return await training_needs(plant=plant, session=session)


async def _needs_out(session: AsyncSession, plant: Plant, needs) -> list[TrainingNeedOut]:
    stations = {
        s.id: s
        for s in (
            await session.scalars(select(Station).where(Station.plant_id == plant.id))
        ).all()
    }
    return [
        TrainingNeedOut(
            id=n.id,
            station_code=stations[n.station_id].code if n.station_id in stations else "—",
            kind=n.kind,
            annual_loss_paise=n.annual_loss_paise,
            annual_loss_display=format_inr(n.annual_loss_paise),
            exposure_per_day_paise=int((n.evidence or {}).get("exposure_per_day_paise", 0)),
            exposure_per_day_display=format_inr(
                int((n.evidence or {}).get("exposure_per_day_paise", 0))
            ),
            is_bottleneck=bool((n.evidence or {}).get("is_bottleneck", False)),
            recommendation=n.recommendation,
            evidence=n.evidence or {},
            has_action=n.action_id is not None,
        )
        for n in needs
    ]


@router.post(
    "/skills/needs/{need_id}/action",
    dependencies=[Depends(require(Role.OWNER, Role.SUPERVISOR, Role.CONSULTANT))],
)
async def raise_training_action(
    need_id: uuid.UUID,
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Promote a training need into the same Actions engine everything else uses."""
    need = await session.get(TrainingNeed, need_id)
    if need is None or need.plant_id != plant.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Training need not found")
    if need.action_id is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Action already raised")

    action = await skills_service.raise_action(session, plant=plant, need=need)
    return {"action_id": str(action.id), "reference": action.reference}


# ---------------------------------------------------------------------------


def _clause_out(clause: ComplianceClause) -> ClauseOut:
    return ClauseOut(
        id=clause.id,
        standard=clause.standard.value,
        clause_ref=clause.clause_ref,
        title=clause.title,
        requirement=clause.requirement,
        status=clause.status.value,
        evidence_count=clause.evidence_count,
        last_evidence_at=clause.last_evidence_at,
        gap_note=clause.gap_note,
        also_satisfies=list(clause.also_satisfies or []),
    )


async def _kaizen_out(session: AsyncSession, idea: KaizenIdea) -> KaizenIdeaOut:
    station = await session.get(Station, idea.station_id) if idea.station_id else None
    return KaizenIdeaOut(
        id=idea.id,
        reference=idea.reference,
        title=idea.title,
        description=idea.description,
        submitted_by_name=idea.submitted_by_name,
        station_code=station.code if station else None,
        status=idea.status.value,
        estimated_annual_saving_paise=idea.estimated_annual_saving,
        estimated_annual_saving_display=format_inr(idea.estimated_annual_saving),
        impact_score=idea.impact_score,
        effort_score=idea.effort_score,
        score_rationale=idea.score_rationale,
        action_id=idea.action_id,
        decision_note=idea.decision_note,
        created_at=idea.created_at,
    )
