"""Compliance / Audit AI (§3 Layer 5).

    "Generates audit evidence (ISO 9001, IATF 16949, ZED, customer/OEM formats)
     automatically from data the plant already captures daily, and scores live
     audit readiness against the relevant clauses. A downtime log with
     corrective actions IS audit evidence — the plugin formats it against the
     right clause and tracks CAPA to closure through the same Actions engine.
     Cross-standard mapping means one captured fact satisfies multiple
     standards at once."

Nothing here asks the plant for anything new. Every clause is scored from rows
Core already holds, which is why audit readiness can be a live number rather
than a three-week scramble before the auditor arrives.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Action,
    ClauseStatus,
    ComplianceClause,
    DowntimeEvent,
    Operator,
    OperatorSkill,
    PdcaState,
    Plant,
    ProductionCount,
    QualityRecord,
    ReasonCode,
    RootCauseAnalysis,
    ShiftSession,
    ShiftSessionStatus,
    Standard,
    Station,
)

# ---------------------------------------------------------------------------
# The clause library.
#
# Deliberately small. These are the clauses an Indian auto-ancillary plant is
# actually challenged on, and — critically — the ones this product's own data
# can evidence. A clause the system cannot prove from Core data does not belong
# here, because a readiness score padded with clauses nobody is measuring is
# worse than no score.
#
# `also_satisfies` is the cross-standard mapping: the same captured fact closes
# a clause under several standards at once, so the plant does the work once.
# ---------------------------------------------------------------------------

CLAUSE_LIBRARY: list[dict] = [
    # --- ISO 9001:2015 ------------------------------------------------------
    {
        "standard": Standard.ISO_9001,
        "clause_ref": "7.1.3",
        "title": "Infrastructure — equipment maintained and available",
        "requirement": "Determine, provide and maintain the infrastructure needed for process operation.",
        "evidence_source": "downtime_maintenance",
        "also_satisfies": ["iatf_16949:8.5.1.5", "zed:EM-2"],
        "display_order": 10,
    },
    {
        "standard": Standard.ISO_9001,
        "clause_ref": "8.5.1",
        "title": "Control of production — monitored under controlled conditions",
        "requirement": "Implement production under controlled conditions, including monitoring of outputs.",
        "evidence_source": "production_records",
        "also_satisfies": ["iatf_16949:8.5.1", "zed:PR-1"],
        "display_order": 20,
    },
    {
        "standard": Standard.ISO_9001,
        "clause_ref": "8.5.2",
        "title": "Identification and traceability",
        "requirement": "Identify outputs and control unique identification where traceability is a requirement.",
        "evidence_source": "lot_traceability",
        "also_satisfies": ["iatf_16949:8.5.2.1", "zed:PR-3"],
        "display_order": 30,
    },
    {
        "standard": Standard.ISO_9001,
        "clause_ref": "8.7",
        "title": "Control of nonconforming output",
        "requirement": "Identify and control outputs that do not conform, to prevent unintended use.",
        "evidence_source": "quality_records",
        "also_satisfies": ["iatf_16949:8.7.1", "zed:QM-2"],
        "display_order": 40,
    },
    {
        "standard": Standard.ISO_9001,
        "clause_ref": "9.1.1",
        "title": "Monitoring, measurement, analysis and evaluation",
        "requirement": "Determine what needs to be monitored and measured, and evaluate performance.",
        "evidence_source": "kpi_history",
        "also_satisfies": ["iatf_16949:9.1.1.1", "zed:PM-1"],
        "display_order": 50,
    },
    {
        "standard": Standard.ISO_9001,
        "clause_ref": "10.2",
        "title": "Nonconformity and corrective action",
        "requirement": "React to nonconformity, evaluate the need for action to eliminate causes, and review effectiveness.",
        "evidence_source": "actions_closed",
        "also_satisfies": ["iatf_16949:10.2.3", "zed:QM-4"],
        "display_order": 60,
    },
    # --- IATF 16949:2016 ----------------------------------------------------
    {
        "standard": Standard.IATF_16949,
        "clause_ref": "6.2.2.1",
        "title": "Competence — on-the-job training",
        "requirement": "Personnel performing work affecting conformity shall be competent; training records maintained.",
        "evidence_source": "skill_matrix",
        "also_satisfies": ["iso_9001:7.2", "zed:HR-1"],
        "display_order": 10,
    },
    {
        "standard": Standard.IATF_16949,
        "clause_ref": "8.5.1.5",
        "title": "Total productive maintenance",
        "requirement": "Develop and implement a TPM system; document MTBF and MTTR.",
        "evidence_source": "maintenance_kpis",
        "also_satisfies": ["iso_9001:7.1.3"],
        "display_order": 20,
    },
    {
        "standard": Standard.IATF_16949,
        "clause_ref": "9.1.1.1",
        "title": "Monitoring and measurement of manufacturing processes",
        "requirement": "Perform process studies on new processes and maintain process capability over time.",
        "evidence_source": "oee_history",
        "display_order": 30,
    },
    {
        "standard": Standard.IATF_16949,
        "clause_ref": "10.2.3",
        "title": "Problem solving — defined process with root cause",
        "requirement": "A documented process for problem solving including root-cause analysis and verification of effectiveness.",
        "evidence_source": "rca_verified",
        "also_satisfies": ["iso_9001:10.2"],
        "display_order": 40,
    },
    {
        "standard": Standard.IATF_16949,
        "clause_ref": "10.3.1",
        "title": "Continual improvement",
        "requirement": "A documented continual-improvement process, prioritised by risk and benefit.",
        "evidence_source": "kaizen_pipeline",
        "also_satisfies": ["zed:CI-1"],
        "display_order": 50,
    },
    # --- ZED (MSME) ---------------------------------------------------------
    {
        "standard": Standard.ZED,
        "clause_ref": "PR-1",
        "title": "Production planning and daily monitoring",
        "requirement": "Daily production plan against actual, with variance review.",
        "evidence_source": "production_records",
        "display_order": 10,
    },
    {
        "standard": Standard.ZED,
        "clause_ref": "QM-2",
        "title": "Defect recording and analysis",
        "requirement": "Record rejections by defect type and analyse trends.",
        "evidence_source": "quality_records",
        "display_order": 20,
    },
    {
        "standard": Standard.ZED,
        "clause_ref": "EM-2",
        "title": "Machine maintenance records",
        "requirement": "Preventive maintenance schedule and breakdown history per machine.",
        "evidence_source": "downtime_maintenance",
        "display_order": 30,
    },
    {
        "standard": Standard.ZED,
        "clause_ref": "CI-1",
        "title": "Continual improvement with measured benefit",
        "requirement": "Improvement projects with recorded before/after benefit.",
        "evidence_source": "recovered_profit",
        "display_order": 40,
    },
]


@dataclass(slots=True)
class ClauseScore:
    status: ClauseStatus
    count: int
    last_at: datetime | None
    note: str | None = None


async def seed_clauses(session: AsyncSession, *, plant: Plant) -> int:
    """Install the clause library for a plant. Idempotent."""
    created = 0
    for entry in CLAUSE_LIBRARY:
        existing = await session.scalar(
            select(ComplianceClause).where(
                ComplianceClause.plant_id == plant.id,
                ComplianceClause.standard == entry["standard"],
                ComplianceClause.clause_ref == entry["clause_ref"],
            )
        )
        if existing is not None:
            continue
        session.add(
            ComplianceClause(
                plant_id=plant.id,
                standard=entry["standard"],
                clause_ref=entry["clause_ref"],
                title=entry["title"],
                requirement=entry.get("requirement"),
                evidence_source=entry["evidence_source"],
                also_satisfies=entry.get("also_satisfies"),
                display_order=entry.get("display_order", 100),
            )
        )
        created += 1
    await session.flush()
    return created


async def refresh(session: AsyncSession, *, plant: Plant) -> list[ComplianceClause]:
    """Seed the clause library if this plant has none, then re-score every clause."""
    existing = await session.scalar(
        select(func.count(ComplianceClause.id)).where(ComplianceClause.plant_id == plant.id)
    )
    if not existing:
        await seed_clauses(session, plant=plant)
    return await score_readiness(session, plant=plant)


async def score_readiness(
    session: AsyncSession, *, plant: Plant, window_days: int = 90
) -> list[ComplianceClause]:
    """Re-score every clause from Core data.

    Evidence must be *recent* as well as present: an auditor asking about the
    last quarter is not satisfied by a register that stopped in March. So each
    scorer counts inside the window and records when the last evidence landed.
    """
    since = date.today() - timedelta(days=window_days)
    clauses = (
        await session.scalars(
            select(ComplianceClause).where(ComplianceClause.plant_id == plant.id)
        )
    ).all()

    scorers = {
        "downtime_maintenance": _score_downtime_maintenance,
        "production_records": _score_production,
        "lot_traceability": _score_traceability,
        "quality_records": _score_quality,
        "kpi_history": _score_kpi_history,
        "actions_closed": _score_actions_closed,
        "skill_matrix": _score_skill_matrix,
        "maintenance_kpis": _score_maintenance_kpis,
        "oee_history": _score_kpi_history,
        "rca_verified": _score_rca,
        "kaizen_pipeline": _score_kaizen,
        "recovered_profit": _score_recovered_profit,
    }

    for clause in clauses:
        scorer = scorers.get(clause.evidence_source)
        if scorer is None:
            continue
        result = await scorer(session, plant, since)
        clause.status = result.status
        clause.evidence_count = result.count
        clause.last_evidence_at = result.last_at
        clause.gap_note = result.note

    await session.flush()
    return clauses


def readiness_score(
    clauses: list[ComplianceClause], standard: Standard | None = None
) -> int:
    """Percentage of applicable clauses with evidence. Partial counts as half.

    Half-credit for partial is deliberate: a plant with thin-but-real evidence
    everywhere is genuinely closer to an audit than one with perfect evidence on
    half the clauses and nothing on the rest, and the score should say so.
    """
    scope = [c for c in clauses if standard is None or c.standard is standard]
    applicable = [c for c in scope if c.status is not ClauseStatus.NOT_APPLICABLE]
    if not applicable:
        return 0
    earned = sum(
        1.0
        if c.status is ClauseStatus.READY
        else 0.5
        if c.status is ClauseStatus.PARTIAL
        else 0.0
        for c in applicable
    )
    return round(earned * 100 / len(applicable))


# ---------------------------------------------------------------------------
# Scorers. Each reads Core data only.
# ---------------------------------------------------------------------------


def _verdict(count: int, last_at, *, ready_at: int, gap_note: str) -> ClauseScore:
    if count >= ready_at:
        return ClauseScore(ClauseStatus.READY, count, last_at)
    if count > 0:
        return ClauseScore(ClauseStatus.PARTIAL, count, last_at, gap_note)
    return ClauseScore(ClauseStatus.GAP, 0, None, gap_note)


async def _score_downtime_maintenance(session, plant, since) -> ClauseScore:
    row = (
        await session.execute(
            select(func.count(DowntimeEvent.id), func.max(DowntimeEvent.started_at))
            .join(ReasonCode, ReasonCode.id == DowntimeEvent.reason_code_id)
            .where(
                DowntimeEvent.plant_id == plant.id,
                DowntimeEvent.business_date >= since,
                ReasonCode.is_breakdown.is_(True),
            )
        )
    ).one()
    return _verdict(
        int(row[0] or 0),
        row[1],
        ready_at=5,
        gap_note="Log breakdowns with a reason code so maintenance history builds.",
    )


async def _score_production(session, plant, since) -> ClauseScore:
    row = (
        await session.execute(
            select(
                func.count(ProductionCount.id), func.max(ProductionCount.recorded_at)
            ).where(
                ProductionCount.plant_id == plant.id, ProductionCount.business_date >= since
            )
        )
    ).one()
    return _verdict(
        int(row[0] or 0),
        row[1],
        ready_at=100,
        gap_note="Production counts are thin for this period — an auditor will ask for daily records.",
    )


async def _score_traceability(session, plant, since) -> ClauseScore:
    from app.models import Lot

    row = (
        await session.execute(
            select(func.count(Lot.id), func.max(Lot.opened_at)).where(
                Lot.plant_id == plant.id
            )
        )
    ).one()
    return _verdict(
        int(row[0] or 0),
        row[1],
        ready_at=20,
        gap_note="Start lot identification — day-batch lots are enough to satisfy this clause.",
    )


async def _score_quality(session, plant, since) -> ClauseScore:
    row = (
        await session.execute(
            select(func.count(QualityRecord.id), func.max(QualityRecord.recorded_at)).where(
                QualityRecord.plant_id == plant.id, QualityRecord.business_date >= since
            )
        )
    ).one()
    return _verdict(
        int(row[0] or 0),
        row[1],
        ready_at=20,
        gap_note="Record rejections with defect codes at batch completion.",
    )


async def _score_kpi_history(session, plant, since) -> ClauseScore:
    row = (
        await session.execute(
            select(func.count(ShiftSession.id), func.max(ShiftSession.locked_at)).where(
                ShiftSession.plant_id == plant.id,
                ShiftSession.status == ShiftSessionStatus.CLOSED,
                ShiftSession.business_date >= since,
            )
        )
    ).one()
    return _verdict(
        int(row[0] or 0),
        row[1],
        ready_at=30,
        gap_note="Close shifts consistently — locked shifts are what make the KPI history defensible.",
    )


async def _score_actions_closed(session, plant, since) -> ClauseScore:
    row = (
        await session.execute(
            select(func.count(Action.id), func.max(Action.closed_at)).where(
                Action.plant_id == plant.id,
                Action.closed_at.is_not(None),
            )
        )
    ).one()
    return _verdict(
        int(row[0] or 0),
        row[1],
        ready_at=5,
        gap_note="Close actions with a root cause and countermeasure — that record is the corrective-action evidence.",
    )


async def _score_rca(session, plant, since) -> ClauseScore:
    row = (
        await session.execute(
            select(
                func.count(RootCauseAnalysis.id), func.max(RootCauseAnalysis.completed_at)
            ).where(
                RootCauseAnalysis.plant_id == plant.id,
                RootCauseAnalysis.completed_at.is_not(None),
            )
        )
    ).one()
    return _verdict(
        int(row[0] or 0),
        row[1],
        ready_at=3,
        gap_note="Complete the 5-Why on escalated actions — auditors ask to see root cause, not just the fix.",
    )


async def _score_skill_matrix(session, plant, since) -> ClauseScore:
    count = await session.scalar(
        select(func.count(OperatorSkill.id)).where(OperatorSkill.plant_id == plant.id)
    )
    operators = await session.scalar(
        select(func.count(Operator.id)).where(
            Operator.plant_id == plant.id, Operator.is_active.is_(True)
        )
    )
    target = max(1, int(operators or 0))
    return _verdict(
        int(count or 0),
        None,
        ready_at=target,
        gap_note="Record which operator is certified on which station — the Skill Matrix is the competence evidence.",
    )


async def _score_maintenance_kpis(session, plant, since) -> ClauseScore:
    row = (
        await session.execute(
            select(func.count(DowntimeEvent.id), func.max(DowntimeEvent.started_at))
            .join(ReasonCode, ReasonCode.id == DowntimeEvent.reason_code_id)
            .where(
                DowntimeEvent.plant_id == plant.id,
                DowntimeEvent.business_date >= since,
                ReasonCode.is_breakdown.is_(True),
                DowntimeEvent.duration_minutes.is_not(None),
            )
        )
    ).one()
    return _verdict(
        int(row[0] or 0),
        row[1],
        ready_at=10,
        gap_note="MTBF and MTTR need closed breakdown events with durations.",
    )


async def _score_kaizen(session, plant, since) -> ClauseScore:
    from app.models import KaizenIdea

    row = (
        await session.execute(
            select(func.count(KaizenIdea.id), func.max(KaizenIdea.created_at)).where(
                KaizenIdea.plant_id == plant.id
            )
        )
    ).one()
    return _verdict(
        int(row[0] or 0),
        row[1],
        ready_at=5,
        gap_note="Capture improvement ideas — a continual-improvement clause needs a visible pipeline.",
    )


async def _score_recovered_profit(session, plant, since) -> ClauseScore:
    row = (
        await session.execute(
            select(func.count(Action.id), func.max(Action.verified_at)).where(
                Action.plant_id == plant.id,
                Action.state == PdcaState.VERIFIED,
            )
        )
    ).one()
    return _verdict(
        int(row[0] or 0),
        row[1],
        ready_at=3,
        gap_note="Verified actions with measured savings are the before/after benefit this clause asks for.",
    )


async def collect_evidence(
    session: AsyncSession,
    *,
    plant: Plant,
    clause: ComplianceClause,
    limit: int = 25,
    window_days: int = 90,
) -> list[dict]:
    """The actual rows behind a clause, ready to hand to an auditor.

    Every branch returns **the same rows its scorer counted**, over the same
    window. That is not a nicety. An earlier version listed every closed Action
    as evidence for the root-cause clause, while the scorer — correctly — counted
    only completed RCAs; the clause showed as a GAP with 25 rows of "evidence"
    under it. A clause's status and its evidence must never disagree, or the
    plugin is telling an auditor two different things.
    """
    since = date.today() - timedelta(days=window_days)
    source = clause.evidence_source

    if source == "actions_closed":
        rows = (
            await session.scalars(
                select(Action)
                .where(Action.plant_id == plant.id, Action.closed_at.is_not(None))
                .order_by(Action.closed_at.desc())
                .limit(limit)
            )
        ).all()
        return [_action_row(a) for a in rows]

    if source == "recovered_profit":
        rows = (
            await session.scalars(
                select(Action)
                .where(Action.plant_id == plant.id, Action.state == PdcaState.VERIFIED)
                .order_by(Action.verified_at.desc())
                .limit(limit)
            )
        ).all()
        return [
            {
                **_action_row(a),
                "recovered_paise": a.recovered_profit,
                "sustained_weeks": a.sustained_weeks,
            }
            for a in rows
        ]

    if source == "rca_verified":
        rows = (
            await session.execute(
                select(RootCauseAnalysis, Action.reference, Action.title)
                .join(Action, Action.id == RootCauseAnalysis.action_id)
                .where(
                    RootCauseAnalysis.plant_id == plant.id,
                    RootCauseAnalysis.completed_at.is_not(None),
                )
                .order_by(RootCauseAnalysis.completed_at.desc())
                .limit(limit)
            )
        ).all()
        return [
            {
                "action": reference,
                "title": title,
                "conclusion": rca.conclusion,
                "five_whys": [w for w in (rca.five_whys or []) if w.get("why")],
                "completed": rca.completed_at.date().isoformat(),
            }
            for rca, reference, title in rows
        ]

    if source in {"downtime_maintenance", "maintenance_kpis"}:
        query = (
            select(DowntimeEvent, Station.code, ReasonCode.label)
            .join(Station, Station.id == DowntimeEvent.station_id)
            .join(ReasonCode, ReasonCode.id == DowntimeEvent.reason_code_id)
            .where(
                DowntimeEvent.plant_id == plant.id,
                DowntimeEvent.business_date >= since,
                ReasonCode.is_breakdown.is_(True),
            )
        )
        if source == "maintenance_kpis":
            query = query.where(DowntimeEvent.duration_minutes.is_not(None))
        rows = (
            await session.execute(
                query.order_by(DowntimeEvent.started_at.desc()).limit(limit)
            )
        ).all()
        return [
            {
                "date": e.business_date.isoformat(),
                "station": code,
                "reason": label,
                "minutes": float(e.duration_minutes or 0),
                "cost_paise": e.cost_total,
            }
            for e, code, label in rows
        ]

    if source == "production_records":
        rows = (
            await session.execute(
                select(
                    ProductionCount.business_date,
                    func.count(ProductionCount.id),
                    func.sum(ProductionCount.count),
                    func.count(func.distinct(ProductionCount.station_id)),
                )
                .where(
                    ProductionCount.plant_id == plant.id,
                    ProductionCount.business_date >= since,
                )
                .group_by(ProductionCount.business_date)
                .order_by(ProductionCount.business_date.desc())
                .limit(limit)
            )
        ).all()
        return [
            {
                "date": day.isoformat(),
                "entries": int(entries),
                "parts": int(parts or 0),
                "stations": int(stations),
            }
            for day, entries, parts, stations in rows
        ]

    if source in {"kpi_history", "oee_history"}:
        rows = (
            await session.scalars(
                select(ShiftSession)
                .where(
                    ShiftSession.plant_id == plant.id,
                    ShiftSession.status == ShiftSessionStatus.CLOSED,
                    ShiftSession.business_date >= since,
                )
                .order_by(ShiftSession.business_date.desc())
                .limit(limit)
            )
        ).all()
        return [
            {
                "date": row.business_date.isoformat(),
                "locked_at": row.locked_at.isoformat() if row.locked_at else None,
                "planned_minutes": row.planned_minutes,
                # An override is disclosed, not hidden: an auditor should see it.
                "closed_with_override": bool(row.override_reason),
            }
            for row in rows
        ]

    if source == "quality_records":
        rows = (
            await session.scalars(
                select(QualityRecord)
                .where(
                    QualityRecord.plant_id == plant.id,
                    QualityRecord.business_date >= since,
                )
                .order_by(QualityRecord.recorded_at.desc())
                .limit(limit)
            )
        ).all()
        return [
            {
                "date": r.business_date.isoformat(),
                "good": r.good_count,
                "rejected": r.reject_count,
                "defects": r.defects,
                "disposition": r.disposition.value,
            }
            for r in rows
        ]

    if source == "skill_matrix":
        rows = (
            await session.execute(
                select(OperatorSkill, Operator.name, Station.code)
                .join(Operator, Operator.id == OperatorSkill.operator_id)
                .join(Station, Station.id == OperatorSkill.station_id)
                .where(OperatorSkill.plant_id == plant.id)
                .order_by(Station.code, OperatorSkill.level.desc())
                .limit(limit)
            )
        ).all()
        return [
            {
                "operator": name,
                "station": code,
                "level": skill.level,
                "certified_on": skill.certified_on.isoformat()
                if skill.certified_on
                else None,
            }
            for skill, name, code in rows
        ]

    if source == "lot_traceability":
        from app.models import JobOrder, Lot

        rows = (
            await session.execute(
                select(Lot, JobOrder.job_number)
                .outerjoin(JobOrder, JobOrder.id == Lot.job_order_id)
                .where(Lot.plant_id == plant.id)
                .order_by(Lot.opened_at.desc())
                .limit(limit)
            )
        ).all()
        return [
            {
                "lot": lot.lot_code,
                "job": job_number,
                "quantity": lot.quantity,
                "granularity": lot.granularity.value,
            }
            for lot, job_number in rows
        ]

    if source == "kaizen_pipeline":
        from app.models import KaizenIdea

        rows = (
            await session.scalars(
                select(KaizenIdea)
                .where(KaizenIdea.plant_id == plant.id)
                .order_by(KaizenIdea.created_at.desc())
                .limit(limit)
            )
        ).all()
        return [
            {
                "reference": idea.reference,
                "title": idea.title,
                "status": idea.status.value,
                "estimated_annual_saving_paise": idea.estimated_annual_saving,
                "submitted": idea.created_at.date().isoformat(),
            }
            for idea in rows
        ]

    return []


def _action_row(action: Action) -> dict:
    return {
        "reference": action.reference,
        "title": action.title,
        "root_cause": action.root_cause,
        "countermeasure": action.countermeasure,
        "closed": action.closed_at.date().isoformat() if action.closed_at else None,
        "verified": action.state is PdcaState.VERIFIED,
        "owner": action.owner_name,
    }
