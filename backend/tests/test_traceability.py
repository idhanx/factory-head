"""Traceability (§3) and stage tracking (§14.2), against a real database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import (
    DispatchRecord,
    JobSession,
    Lot,
    LotGranularity,
    QualityRecord,
    RoutingOperation,
    Section,
    StageCompletion,
)
from app.services import shifts as shift_service
from app.services import traceability as trace_service
from tests.conftest import run_job

START = datetime(2026, 8, 24, 1, 30, tzinfo=UTC)  # 07:00 IST


async def lot_with_routing(session, fixtures):
    """AK-447 on CNC-3: Op10 machining → Op20 quality → Op30 dispatch."""
    plant, part = fixtures["plant"], fixtures["part"]
    for sequence, code, section in (
        (20, "Op20", Section.QUALITY),
        (30, "Op30", Section.DISPATCH),
    ):
        session.add(
            RoutingOperation(
                plant_id=plant.id,
                part_id=part.id,
                sequence=sequence,
                operation_code=code,
                section=section,
            )
        )
    await session.flush()

    shift_session = await shift_service.start_shift(
        session, plant=plant, shift=fixtures["shift"], supervisor_id=None, started_at=START
    )
    job_session = await run_job(session, fixtures, shift_session)
    lot = await session.get(Lot, job_session.lot_id)
    lot.quantity = 400
    await session.flush()
    return lot


async def test_a_lot_is_found_the_way_people_write_it(session, fixtures):
    lot = await lot_with_routing(session, fixtures)
    for typed in ("4471", "#4471", " #4471", "4471 "):
        found = await trace_service.find_lot(session, fixtures["plant"], typed)
        assert found is not None and found.id == lot.id, typed


async def test_the_trace_tells_the_story_in_one_sentence(session, fixtures):
    """§3 — job, station, shift, operator, inspector, verdict, the defect that
    dominated, and the stop on the station during the run."""
    plant = fixtures["plant"]
    lot = await lot_with_routing(session, fixtures)

    from app.services import downtime as downtime_service

    shift_session = await shift_service.current_open_session(session, plant.id)
    event = await downtime_service.open_downtime(
        session,
        plant=plant,
        station=fixtures["station"],
        reason_code=fixtures["reasons"]["TOOL_BREAKAGE"],
        shift_session=shift_session,
        started_at=START + timedelta(hours=3, minutes=14),
    )
    await downtime_service.close_downtime(
        session, event=event, ended_at=START + timedelta(hours=4, minutes=1)
    )
    session.add(
        QualityRecord(
            plant_id=plant.id,
            station_id=fixtures["station"].id,
            lot_id=lot.id,
            part_id=fixtures["part"].id,
            business_date=shift_session.business_date,
            good_count=362,
            reject_count=9,
            defects=[{"code": "BURR", "count": 7}, {"code": "UNDERSIZE", "count": 2}],
            recorded_at=START + timedelta(days=1),
        )
    )
    await session.flush()

    story = await trace_service.trace_lot(session, plant=plant, lot=lot)
    summary = story["summary"]
    assert summary.startswith("Lot #4471 was Job Order 2231 for AK-447")
    assert "run on CNC-3, Shift A" in summary
    assert "362 good / 9 rejected — 7 for burr" in summary
    assert "tool breakage event (47 min" in summary
    assert story["runs"][0]["events"][0]["minutes"] == 47.0


async def test_stages_flow_into_the_kernel_and_despatch_closes_the_lot(session, fixtures):
    plant = fixtures["plant"]
    lot = await lot_with_routing(session, fixtures)

    await trace_service.record_stage(
        session, plant=plant, lot=lot, operation_code="Op10", quantity_completed=400
    )
    await trace_service.record_stage(
        session,
        plant=plant,
        lot=lot,
        operation_code="op20",
        quantity_completed=380,
        quantity_rejected=20,
    )
    mid = (await trace_service.trace_lot(session, plant=plant, lot=lot))["flow"]
    assert mid["position"] == "Op30" and not mid["complete"] and mid["flags"] == []

    await trace_service.record_dispatch(
        session, plant=plant, lot=lot, quantity=380, vehicle_reference="TN 70 AB 1234"
    )

    done = (await trace_service.trace_lot(session, plant=plant, lot=lot))["flow"]
    assert done["complete"] and done["despatched"] == 380 and done["position"] is None
    assert lot.closed_at is not None
    # Despatch is written once as a dispatch record and once as the routing's
    # dispatch stage, so the flow never treats it as a special case.
    assert (
        await session.scalar(
            select(DispatchRecord.quantity).where(DispatchRecord.lot_id == lot.id)
        )
        == 380
    )
    stages = (
        await session.scalars(
            select(StageCompletion).where(StageCompletion.lot_id == lot.id)
        )
    ).all()
    assert len(stages) == 3


async def test_a_shortfall_at_the_gate_is_flagged_after_despatch(session, fixtures):
    plant = fixtures["plant"]
    lot = await lot_with_routing(session, fixtures)
    await trace_service.record_stage(
        session, plant=plant, lot=lot, operation_code="Op10", quantity_completed=400
    )
    await trace_service.record_stage(
        session,
        plant=plant,
        lot=lot,
        operation_code="Op20",
        quantity_completed=380,
        quantity_rejected=20,
    )
    await trace_service.record_dispatch(session, plant=plant, lot=lot, quantity=340)

    flow = (await trace_service.trace_lot(session, plant=plant, lot=lot))["flow"]
    assert [f["kind"] for f in flow["flags"]] == ["unaccounted_at_gate"]
    assert flow["flags"][0]["gap"] == 40


async def test_an_operation_not_on_the_routing_is_refused(session, fixtures):
    lot = await lot_with_routing(session, fixtures)
    with pytest.raises(trace_service.TraceError, match="not on this lot's routing"):
        await trace_service.record_stage(
            session,
            plant=fixtures["plant"],
            lot=lot,
            operation_code="Op90",
            quantity_completed=10,
        )


async def test_an_empty_stage_entry_is_refused(session, fixtures):
    lot = await lot_with_routing(session, fixtures)
    with pytest.raises(trace_service.TraceError):
        await trace_service.record_stage(
            session,
            plant=fixtures["plant"],
            lot=lot,
            operation_code="Op10",
            quantity_completed=0,
        )
    with pytest.raises(trace_service.TraceError):
        await trace_service.record_dispatch(
            session, plant=fixtures["plant"], lot=lot, quantity=0
        )


async def test_search_puts_the_exact_lot_first(session, fixtures):
    await lot_with_routing(session, fixtures)
    results = await trace_service.search(session, plant=fixtures["plant"], q="#4471")
    assert results[0]["lot_code"] == "4471"
    by_part = await trace_service.search(session, plant=fixtures["plant"], q="ak-447")
    assert by_part and by_part[0]["part_number"] == "AK-447"


async def test_the_wip_board_shows_moving_lots_only(session, fixtures):
    plant = fixtures["plant"]
    lot = await lot_with_routing(session, fixtures)
    assert (
        await trace_service.wip_board(session, plant=plant) == []
    )  # untouched lot = plan, not WIP

    await trace_service.record_stage(
        session, plant=plant, lot=lot, operation_code="Op10", quantity_completed=400
    )
    board = await trace_service.wip_board(session, plant=plant)
    assert [(b["lot_code"], b["position"]) for b in board] == [("4471", "Op20")]

    await trace_service.record_stage(
        session, plant=plant, lot=lot, operation_code="Op20", quantity_completed=400
    )
    await trace_service.record_dispatch(session, plant=plant, lot=lot, quantity=400)
    assert await trace_service.wip_board(session, plant=plant) == []


async def test_a_day_batch_lot_with_no_job_still_traces(session, fixtures):
    """§3 — a plant with no lot discipline starts coarse: the day-batch is the
    lot. It has no job order, but it must still trace and still move through
    stages, or the plant's first rung on the ladder gets no traceability."""
    plant = fixtures["plant"]
    job_lot = await lot_with_routing(session, fixtures)
    batch = Lot(
        plant_id=plant.id,
        lot_code="L1-240826",
        granularity=LotGranularity.DAY_BATCH,
        quantity=400,
    )
    session.add(batch)
    await session.flush()

    # Production for the day went into the batch rather than a job lot.
    job_session = await session.scalar(
        select(JobSession).where(JobSession.lot_id == job_lot.id)
    )
    job_session.lot_id = batch.id
    await session.flush()

    await trace_service.record_stage(
        session, plant=plant, lot=batch, operation_code="Op10", quantity_completed=400
    )
    await trace_service.record_stage(
        session,
        plant=plant,
        lot=batch,
        operation_code="Op20",
        quantity_completed=390,
        quantity_rejected=10,
    )
    await trace_service.record_dispatch(session, plant=plant, lot=batch, quantity=390)

    story = await trace_service.trace_lot(session, plant=plant, lot=batch)
    assert [s["operation_code"] for s in story["flow"]["stages"]] == [
        "Op10",
        "Op20",
        "Op30",
    ]
    assert story["flow"]["complete"] and story["flow"]["flags"] == []
    assert story["runs"] and story["runs"][0]["station"] == "CNC-3"
    assert story["summary"].startswith("Lot #L1-240826")


async def test_a_batch_of_parts_with_different_routings_raises_no_false_flags(
    session, fixtures
):
    """Regression (found by the dataset session): a day batch holding a
    three-step part and a two-step part. Merged by operation code, Op30 was
    "despatch" for one and "inspection" for the other, and the batch showed
    parts unaccounted at the gate that were never missing."""
    plant = fixtures["plant"]
    await lot_with_routing(session, fixtures)  # AK-447: Op10 → Op20 QC → Op30 dispatch

    from app.models import JobOrder, JobOrderStatus, Part

    pin = Part(plant_id=plant.id, part_number="PIN-8", description="Dowel pin")
    session.add(pin)
    await session.flush()
    for sequence, code, section in (
        (10, "Op10", Section.MACHINING),
        (20, "Op20", Section.DISPATCH),
    ):
        session.add(
            RoutingOperation(
                plant_id=plant.id,
                part_id=pin.id,
                sequence=sequence,
                operation_code=code,
                section=section,
            )
        )
    batch = Lot(
        plant_id=plant.id, lot_code="L1-250826", granularity=LotGranularity.DAY_BATCH
    )
    session.add(batch)
    await session.flush()

    shift_session = await shift_service.current_open_session(session, plant.id)
    pin_job = JobOrder(
        plant_id=plant.id, job_number="2232", part_id=pin.id, status=JobOrderStatus.RUNNING
    )
    session.add(pin_job)
    await session.flush()
    for job_id in (pin_job.id,):
        session.add(
            JobSession(
                plant_id=plant.id,
                job_order_id=job_id,
                shift_session_id=shift_session.id,
                station_id=fixtures["station"].id,
                lot_id=batch.id,
                started_at=START,
            )
        )
    ak_session = await session.scalar(
        select(JobSession).where(JobSession.job_order_id != pin_job.id)
    )
    ak_session.lot_id = batch.id
    await session.flush()

    async def op(part, code):
        return await session.scalar(
            select(RoutingOperation).where(
                RoutingOperation.part_id == part, RoutingOperation.operation_code == code
            )
        )

    def done(operation, completed, rejected=0):
        return StageCompletion(
            plant_id=plant.id,
            lot_id=batch.id,
            routing_operation_id=operation.id,
            business_date=START.date(),
            quantity_completed=completed,
            quantity_rejected=rejected,
            completed_at=START,
        )

    ak = fixtures["part"].id
    session.add_all(
        [
            done(await op(ak, "Op10"), 400),
            done(await op(ak, "Op20"), 390, 10),
            done(await op(ak, "Op30"), 390),
            done(await op(pin.id, "Op10"), 1000),
            done(await op(pin.id, "Op20"), 1000),
        ]
    )
    await session.flush()

    story = await trace_service.trace_lot(session, plant=plant, lot=batch)
    assert story["flow"]["flags"] == []
    assert story["flow"]["complete"]
    assert story["flow"]["despatched"] == 1390
    assert sorted(p["part_number"] for p in story["flow"]["parts"]) == ["AK-447", "PIN-8"]
