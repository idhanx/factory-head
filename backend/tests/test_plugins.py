"""Layer 5 plugins — Compliance/Audit AI, Kaizen AI, Skill Matrix AI.

Several of these tests pin bugs found while building the demo data. Each one is
the kind of mistake that puts an inflated rupee figure on a screen, which §8.2
warns discredits every other number the product shows.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.kernel import downtime_event_cost, rupees
from app.models import (
    ActionTrigger,
    ClauseStatus,
    ComplianceClause,
    EscalationRoute,
    KaizenStatus,
    OeeTreatment,
    Operator,
    ReasonCode,
    Recoverability,
    Standard,
    TrainingNeed,
)
from app.services import compliance as compliance_service
from app.services import costing as costing_service
from app.services import downtime as downtime_service
from app.services import kaizen as kaizen_service
from app.services import shifts as shift_service
from app.services import skills as skills_service
from tests.conftest import run_job

RECENT = datetime.now(UTC) - timedelta(days=2)


async def stop(session, fixtures, reason, *, minutes: int, with_job: bool = True):
    """Log and close one stop on CNC-3, two days ago."""
    shift_session = await shift_service.start_shift(
        session,
        plant=fixtures["plant"],
        shift=fixtures["shift"],
        supervisor_id=None,
        started_at=RECENT,
    )
    if with_job:
        await run_job(session, fixtures, shift_session)
    event = await downtime_service.open_downtime(
        session,
        plant=fixtures["plant"],
        station=fixtures["station"],
        reason_code=reason,
        shift_session=shift_session,
        started_at=RECENT,
    )
    await downtime_service.close_downtime(
        session, event=event, ended_at=RECENT + timedelta(minutes=minutes)
    )
    return event


async def no_operator_reason(session, fixtures) -> ReasonCode:
    reason = ReasonCode(
        plant_id=fixtures["plant"].id,
        code="NO_OPERATOR",
        label="No Operator",
        recoverability=Recoverability.RECOVERABLE,
        oee_treatment=OeeTreatment.UNPLANNED,
        escalation_route=EscalationRoute.PRODUCTION,
        display_order=7,
    )
    session.add(reason)
    await session.flush()
    return reason


# ===========================================================================
# Kaizen AI
# ===========================================================================


def test_a_place_is_not_a_problem():
    """Regression: 'put a fan near the inspection bench' once matched Quality
    Hold on the word "inspection", was priced against every quality stop in the
    plant, and came out as the most valuable idea of the lot."""
    assert (
        kaizen_service.match_reason(
            "Put a fan near the inspection bench",
            "The QC bench gets very hot and it is hard to concentrate on inspection.",
        )
        is None
    )


def test_keywords_match_whole_words_not_fragments():
    """Regression: as substrings, 'oil' matched 'soil' and 'tool' matched 'stool'."""
    assert kaizen_service.match_reason("Sweep the soil", "the stool in the kitchen") is None
    assert (
        kaizen_service.match_reason("Coolant oils", "") is not None
    )  # prefix still matches


def test_the_title_outranks_an_incidental_word_in_the_description():
    assert (
        kaizen_service.match_reason(
            "Stage the material trolley early",
            "so the tool setter is not waiting either",
        )
        == "MATERIAL_WAIT"
    )


async def test_an_idea_is_priced_against_this_plants_own_losses(session, fixtures):
    await stop(session, fixtures, fixtures["reasons"]["TOOL_BREAKAGE"], minutes=47)

    score = await kaizen_service.score_idea(
        session,
        plant=fixtures["plant"],
        title="Change the insert at a fixed count",
        description="Stop the tool breaking mid-shift.",
        station_id=fixtures["station"].id,
    )

    assert score.reason_code == "TOOL_BREAKAGE"
    assert score.linked_loss > 0
    annualised = int(score.linked_loss * (365 / 90))
    # A quarter of the annualised loss — ideas reduce a loss, rarely remove it.
    assert score.annual_saving == int(annualised * kaizen_service.SCOPED_SHARE)


async def test_an_idea_that_names_no_machine_is_credited_more_cautiously(session, fixtures):
    await stop(session, fixtures, fixtures["reasons"]["TOOL_BREAKAGE"], minutes=47)

    kwargs = {
        "plant": fixtures["plant"],
        "title": "Change the insert at a fixed count",
        "description": "Stop the tool breaking mid-shift.",
    }
    scoped = await kaizen_service.score_idea(
        session, station_id=fixtures["station"].id, **kwargs
    )
    unscoped = await kaizen_service.score_idea(session, **kwargs)

    assert unscoped.annual_saving < scoped.annual_saving
    assert "names no machine" in unscoped.rationale


async def test_an_unpriced_idea_says_so_rather_than_inventing_a_saving(session, fixtures):
    score = await kaizen_service.score_idea(
        session,
        plant=fixtures["plant"],
        title="Paint the walkway lines again",
        description="People keep walking into the forklift lane.",
    )
    assert score.reason_code is None
    assert score.annual_saving == 0
    assert score.impact == 1
    assert "on its merits" in score.rationale


async def test_accepting_an_idea_creates_a_real_action_with_a_frozen_baseline(
    session, fixtures
):
    """§3 Layer 5: one Actions engine — never a parallel Kaizen workflow."""
    await stop(session, fixtures, fixtures["reasons"]["TOOL_BREAKAGE"], minutes=47)
    idea = await kaizen_service.submit(
        session,
        plant=fixtures["plant"],
        title="Change the insert at a fixed count",
        description="Stop the tool breaking mid-shift.",
        submitted_by_name="Kumar M.",
        station_id=fixtures["station"].id,
    )

    action = await kaizen_service.accept(session, plant=fixtures["plant"], idea=idea)

    assert action.trigger is ActionTrigger.KAIZEN
    assert action.baseline_frozen_at is not None
    assert idea.status is KaizenStatus.ACCEPTED
    assert idea.action_id == action.id


# ===========================================================================
# Skill Matrix AI
# ===========================================================================


async def test_one_no_operator_stop_is_not_a_training_gap(session, fixtures):
    """A single late arrival in ninety days is noise. A training list padded with
    noise is a list nobody reads."""
    reason = await no_operator_reason(session, fixtures)
    await stop(session, fixtures, reason, minutes=20)

    needs = await skills_service.detect_needs(session, plant=fixtures["plant"])
    assert not [n for n in needs if n.kind == "gap"]


async def test_a_single_operator_bottleneck_claims_no_annual_loss(session, fixtures):
    """Regression: coverage risk was once reported as ALL of the station's
    downtime, annualised, times 1.5 — ₹1.6 lakh a year for CNC-3, none of which
    had anything to do with an operator being absent.

    Nothing has been lost yet, so no annual loss is claimed. The exposure is the
    cost of one idle shift, from the same kernel formula as every stop.
    """
    plant, station = fixtures["plant"], fixtures["station"]

    # A job gives the station a real part, so lost contribution is priced.
    shift_session = await shift_service.start_shift(
        session, plant=plant, shift=fixtures["shift"], supervisor_id=None, started_at=RECENT
    )
    await run_job(session, fixtures, shift_session)

    operator = Operator(plant_id=plant.id, name="Kumar M.")
    session.add(operator)
    await session.flush()
    await skills_service.set_skill(
        session, plant=plant, operator_id=operator.id, station_id=station.id, level=2
    )

    needs = await skills_service.detect_needs(session, plant=plant)
    coverage = next(n for n in needs if n.kind == "coverage")

    assert coverage.annual_loss_paise == 0
    assert coverage.evidence["is_bottleneck"] is True

    snapshot, _ = await costing_service.resolve_snapshot(
        session,
        plant=plant,
        station=station,
        part=fixtures["part"],
        on_date=datetime.now(UTC).date(),
    )
    shift_minutes = shift_service.planned_minutes(fixtures["shift"])
    expected = downtime_event_cost(shift_minutes, snapshot).total
    assert coverage.evidence["exposure_per_day_paise"] == expected
    # 465 min: ₹6,277.50 of time plus ⌊465/2.4⌋ = 193 parts × ₹38.
    assert expected == rupees("6277.50") + rupees(38) * 193


async def test_two_certified_operators_is_enough_coverage(session, fixtures):
    plant, station = fixtures["plant"], fixtures["station"]
    for name in ("Kumar M.", "Ravi S."):
        operator = Operator(plant_id=plant.id, name=name)
        session.add(operator)
        await session.flush()
        await skills_service.set_skill(
            session, plant=plant, operator_id=operator.id, station_id=station.id, level=2
        )

    needs = await skills_service.detect_needs(session, plant=plant)
    assert not [n for n in needs if n.kind == "coverage"]


async def test_an_operator_in_training_does_not_count_as_coverage(session, fixtures):
    """Level 1 can run the machine only under supervision — not alone at 3 AM."""
    plant, station = fixtures["plant"], fixtures["station"]
    operator = Operator(plant_id=plant.id, name="Kumar M.")
    trainee = Operator(plant_id=plant.id, name="Anbu R.")
    session.add_all([operator, trainee])
    await session.flush()
    await skills_service.set_skill(
        session, plant=plant, operator_id=operator.id, station_id=station.id, level=2
    )
    await skills_service.set_skill(
        session, plant=plant, operator_id=trainee.id, station_id=station.id, level=1
    )

    grid = await skills_service.matrix(session, plant=plant)
    assert grid["stations"][0]["coverage"] == 1
    assert grid["stations"][0]["at_risk"] is True


def test_gaps_and_coverage_risks_are_never_sorted_against_each_other():
    """Different units — ₹ lost per year vs ₹ per idle shift — so gaps come first
    by measured loss, then coverage risks, bottlenecks leading."""
    big_gap = TrainingNeed(kind="gap", annual_loss_paise=rupees(4000), evidence={})
    small_gap = TrainingNeed(kind="gap", annual_loss_paise=rupees(900), evidence={})
    bottleneck = TrainingNeed(
        kind="coverage",
        annual_loss_paise=0,
        evidence={"is_bottleneck": True, "exposure_per_day_paise": rupees(5000)},
    )
    buffered = TrainingNeed(
        kind="coverage",
        annual_loss_paise=0,
        evidence={"is_bottleneck": False, "exposure_per_day_paise": rupees(99000)},
    )

    ordered = sorted(
        [buffered, small_gap, bottleneck, big_gap], key=skills_service.rank_key
    )
    assert ordered == [big_gap, small_gap, bottleneck, buffered]


async def test_a_skill_level_outside_the_scale_is_rejected(session, fixtures):
    with pytest.raises(ValueError):
        await skills_service.set_skill(
            session,
            plant=fixtures["plant"],
            operator_id=None,
            station_id=fixtures["station"].id,
            level=4,
        )


# ===========================================================================
# Compliance / Audit AI
# ===========================================================================


async def test_a_plant_with_no_history_is_not_audit_ready(session, fixtures):
    """Readiness is scored from Core data, never asserted. No data, no score."""
    await compliance_service.seed_clauses(session, plant=fixtures["plant"])
    clauses = await compliance_service.score_readiness(session, plant=fixtures["plant"])

    assert len(clauses) == len(compliance_service.CLAUSE_LIBRARY)
    assert compliance_service.readiness_score(clauses) == 0
    assert all(c.gap_note for c in clauses if c.status is ClauseStatus.GAP)


async def test_seeding_clauses_twice_adds_nothing(session, fixtures):
    first = await compliance_service.seed_clauses(session, plant=fixtures["plant"])
    second = await compliance_service.seed_clauses(session, plant=fixtures["plant"])
    assert first == len(compliance_service.CLAUSE_LIBRARY)
    assert second == 0


async def test_logged_breakdowns_become_maintenance_evidence(session, fixtures):
    """§3 Layer 5: 'a downtime log with corrective actions IS audit evidence'."""
    breakdown = ReasonCode(
        plant_id=fixtures["plant"].id,
        code="MACHINE_BREAKDOWN",
        label="Machine Breakdown",
        recoverability=Recoverability.RECOVERABLE,
        oee_treatment=OeeTreatment.UNPLANNED,
        escalation_route=EscalationRoute.MAINTENANCE,
        is_breakdown=True,
    )
    session.add(breakdown)
    await session.flush()
    await stop(session, fixtures, breakdown, minutes=90)

    await compliance_service.seed_clauses(session, plant=fixtures["plant"])
    await compliance_service.score_readiness(session, plant=fixtures["plant"])

    clause = await session.scalar(
        select(ComplianceClause).where(
            ComplianceClause.plant_id == fixtures["plant"].id,
            ComplianceClause.standard == Standard.ISO_9001,
            ComplianceClause.clause_ref == "7.1.3",
        )
    )
    assert clause.status is ClauseStatus.PARTIAL
    assert clause.evidence_count == 1

    evidence = await compliance_service.collect_evidence(
        session, plant=fixtures["plant"], clause=clause
    )
    assert evidence[0]["station"] == "CNC-3"
    assert evidence[0]["minutes"] == 90.0


def test_partial_evidence_earns_half_credit():
    """Thin-but-real evidence everywhere is closer to an audit than perfect
    evidence on half the clauses and nothing on the rest."""
    clauses = [
        ComplianceClause(standard=Standard.ISO_9001, status=ClauseStatus.READY),
        ComplianceClause(standard=Standard.ISO_9001, status=ClauseStatus.PARTIAL),
        ComplianceClause(standard=Standard.ISO_9001, status=ClauseStatus.GAP),
        ComplianceClause(standard=Standard.ISO_9001, status=ClauseStatus.NOT_APPLICABLE),
    ]
    # (1 + 0.5 + 0) / 3 applicable
    assert compliance_service.readiness_score(clauses) == 50


def test_every_clause_names_evidence_the_product_can_actually_produce():
    """A clause scored from data nobody captures would pad the readiness number."""
    scorable = {
        "downtime_maintenance",
        "production_records",
        "lot_traceability",
        "quality_records",
        "kpi_history",
        "actions_closed",
        "skill_matrix",
        "maintenance_kpis",
        "oee_history",
        "rca_verified",
        "kaizen_pipeline",
        "recovered_profit",
    }
    for clause in compliance_service.CLAUSE_LIBRARY:
        assert clause["evidence_source"] in scorable, clause["clause_ref"]


async def test_a_gap_clause_never_shows_evidence_it_did_not_count(session, fixtures):
    """Regression: the root-cause clause once scored GAP (no completed RCA) while
    listing every closed Action as its evidence. Status and evidence must agree."""
    await compliance_service.seed_clauses(session, plant=fixtures["plant"])
    await compliance_service.score_readiness(session, plant=fixtures["plant"])
    clauses = (
        await session.scalars(
            select(ComplianceClause).where(
                ComplianceClause.plant_id == fixtures["plant"].id
            )
        )
    ).all()

    for clause in clauses:
        evidence = await compliance_service.collect_evidence(
            session, plant=fixtures["plant"], clause=clause
        )
        if clause.status is ClauseStatus.GAP:
            assert evidence == [], f"{clause.clause_ref} is a gap but lists evidence"


async def test_an_idea_naming_a_station_in_its_text_is_scoped_to_it(session, fixtures):
    """An operator who writes 'CNC-3' has named the machine, dropdown or not."""
    await stop(session, fixtures, fixtures["reasons"]["TOOL_BREAKAGE"], minutes=47)

    named = await kaizen_service.score_idea(
        session,
        plant=fixtures["plant"],
        title="Keep spare inserts at CNC-3",
        description="Walking to stores for a new tool insert stretches every breakage.",
    )
    picked = await kaizen_service.score_idea(
        session,
        plant=fixtures["plant"],
        title="Keep spare inserts at the machine",
        description="Walking to stores for a new tool insert stretches every breakage.",
        station_id=fixtures["station"].id,
    )
    assert named.annual_saving == picked.annual_saving
    assert "CNC-3" in named.rationale
