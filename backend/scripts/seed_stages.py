"""Seed stage completions, despatch and §3's traceability story — additively.

Safe to run on a database somebody is already using: it never deletes, and each
step checks whether its work is already done.

Three things happen here:

1. **Routing repair.** Earlier seeds stored every routing's last operation as a
   *quality* step, because it is costed at the QC bench's rate. §14.2 makes it
   Dispatch. As stored, inspection and despatch were indistinguishable.
2. **Stage flow for every lot**, derived from what the plant actually recorded:
   machining output from the job's production counts, the reject rate from its
   inspection records. Recent lots are left part-way through the routing — that
   is what work in progress looks like — and a handful of despatched lots are
   short at the gate, so the flag §14.2 describes has something to find.
3. **Lot #4471**, the spec's own worked example: Job Order 2231, CNC-3, Shift A,
   operator Kumar, inspected by Kavitha, 362 good / 9 rejected — 7 of them burrs
   — and a 47-minute tool breakage on CNC-3 that same morning.
"""

from __future__ import annotations

import asyncio
import random
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import Integer, func, select, update

from app.core.db import AdminSessionFactory as SessionFactory
from app.kernel import downtime_event_cost, scrap_cost
from app.models import (
    DataSource,
    DispatchRecord,
    Disposition,
    DowntimeEvent,
    DowntimeStatus,
    JobOrder,
    JobOrderStatus,
    JobSession,
    Lot,
    LotGranularity,
    Operator,
    Part,
    Plant,
    ProductionCount,
    QualityRecord,
    ReasonCode,
    RoutingOperation,
    Section,
    Shift,
    ShiftSession,
    StageCompletion,
    Station,
    User,
)
from app.services.costing import apply_snapshot, resolve_snapshot

random.seed(20260826)
IST = timezone(timedelta(hours=5, minutes=30))

STORY_DATE = date(2026, 8, 26)  # a Shift A when CNC-3 ran nothing else
STORY_JOB = "2231"
STORY_LOT = "4471"


async def seed_stages() -> None:
    async with SessionFactory() as session:
        plant = await session.scalar(select(Plant).where(Plant.code == "KUMARAN"))
        if plant is None:
            print("  No KUMARAN plant found — run scripts/seed.py first.")
            return

        repaired = await _repair_routings(session, plant)
        print(f"  routings ....... {repaired} final operations set to dispatch")

        story = await _story_lot(session, plant)
        print(f"  lot #4471 ...... {story}")

        stages, despatched, short = await _stage_flow(session, plant)
        print(f"  stages ......... {stages} stage completions")
        print(f"  despatch ....... {despatched} lots despatched, {short} short at the gate")

        await session.commit()


# ---------------------------------------------------------------------------


async def _repair_routings(session, plant: Plant) -> int:
    last = (
        select(RoutingOperation.part_id, func.max(RoutingOperation.sequence).label("seq"))
        .where(RoutingOperation.plant_id == plant.id)
        .group_by(RoutingOperation.part_id)
        .subquery()
    )
    ids = (
        await session.scalars(
            select(RoutingOperation.id)
            .join(
                last,
                (last.c.part_id == RoutingOperation.part_id)
                & (last.c.seq == RoutingOperation.sequence),
            )
            .where(RoutingOperation.section != Section.DISPATCH)
        )
    ).all()
    if ids:
        await session.execute(
            update(RoutingOperation)
            .where(RoutingOperation.id.in_(ids))
            .values(section=Section.DISPATCH.value)
        )
    await session.flush()
    return len(ids)


def _next_working_day(day: date, offset: int = 1) -> date:
    """Stages move on working days. The plant is off on Sundays (T7)."""
    current = day
    for _ in range(offset):
        current += timedelta(days=1)
        if current.weekday() == 6:
            current += timedelta(days=1)
    return current


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=IST)


async def _stage_flow(session, plant: Plant) -> tuple[int, int, int]:
    already = await session.scalar(
        select(func.count(StageCompletion.id))
        .join(Lot, Lot.id == StageCompletion.lot_id)
        .where(Lot.plant_id == plant.id, Lot.lot_code != STORY_LOT)
    )
    if already:
        despatched = await session.scalar(
            select(func.count(func.distinct(DispatchRecord.lot_id))).where(
                DispatchRecord.plant_id == plant.id
            )
        )
        return int(already), int(despatched or 0), 0

    today = date.today()

    routings: dict = defaultdict(list)
    for op in (
        await session.scalars(
            select(RoutingOperation)
            .where(RoutingOperation.plant_id == plant.id)
            .order_by(RoutingOperation.part_id, RoutingOperation.sequence)
        )
    ).all():
        routings[op.part_id].append(op)

    produced = dict(
        (
            await session.execute(
                select(
                    JobSession.job_order_id,
                    func.coalesce(func.sum(ProductionCount.count), 0),
                )
                .join(ProductionCount, ProductionCount.job_session_id == JobSession.id)
                .where(JobSession.plant_id == plant.id)
                .group_by(JobSession.job_order_id)
            )
        ).all()
    )
    first_session = {}
    for job_id, station_id, started, ended, business_date in (
        await session.execute(
            select(
                JobSession.job_order_id,
                JobSession.station_id,
                JobSession.started_at,
                JobSession.ended_at,
                ShiftSession.business_date,
            )
            .join(ShiftSession, ShiftSession.id == JobSession.shift_session_id)
            .where(JobSession.plant_id == plant.id)
            .order_by(JobSession.started_at)
        )
    ).all():
        first_session.setdefault(job_id, (station_id, started, ended, business_date))

    reject_rates = {
        lot_id: (int(rej or 0) / max(1, int(good or 0) + int(rej or 0)))
        for lot_id, good, rej in (
            await session.execute(
                select(
                    QualityRecord.lot_id,
                    func.sum(QualityRecord.good_count),
                    func.sum(QualityRecord.reject_count),
                )
                .where(
                    QualityRecord.plant_id == plant.id, QualityRecord.lot_id.is_not(None)
                )
                .group_by(QualityRecord.lot_id)
            )
        ).all()
    }

    assembly_stations = [
        s.id
        for s in (
            await session.scalars(
                select(Station).where(
                    Station.plant_id == plant.id, Station.section == Section.ASSEMBLY
                )
            )
        ).all()
    ]
    qc_station = await session.scalar(
        select(Station.id)
        .where(Station.plant_id == plant.id, Station.section == Section.QUALITY)
        .limit(1)
    )

    lots = (
        await session.execute(
            select(Lot, JobOrder.part_id)
            .join(JobOrder, JobOrder.id == Lot.job_order_id)
            .where(Lot.plant_id == plant.id, Lot.lot_code != STORY_LOT)
        )
    ).all()

    rows: list[StageCompletion] = []
    despatch_rows: list[DispatchRecord] = []
    short = 0

    for lot, part_id in lots:
        made = int(produced.get(lot.job_order_id, 0) or 0)
        start = first_session.get(lot.job_order_id)
        ops = routings.get(part_id, [])
        if made <= 0 or start is None or not ops:
            continue

        station_id, started_at, ended_at, day = start
        lot.quantity = made
        age = (today - day).days
        rate = reject_rates.get(lot.id, 0.012)

        upstream = made
        stage_day = day
        despatched_lot = False

        for index, op in enumerate(ops):
            if index == 0:
                completed_at = ended_at or (started_at + timedelta(hours=8))
                rows.append(
                    StageCompletion(
                        plant_id=plant.id,
                        lot_id=lot.id,
                        routing_operation_id=op.id,
                        station_id=station_id,
                        business_date=day,
                        quantity_completed=made,
                        quantity_rejected=0,
                        started_at=started_at,
                        completed_at=completed_at,
                    )
                )
                continue

            # Each later stage happens on the next working day — and only once
            # enough days have passed. A lot from yesterday is still moving.
            stage_day = _next_working_day(stage_day)
            if stage_day > today:
                break
            if op.section == Section.DISPATCH and age < 3:
                break

            if op.section == Section.ASSEMBLY:
                # A buffer between stages is normal: the most recent lots still
                # have parts waiting ahead of assembly.
                waiting = int(upstream * random.uniform(0.05, 0.2)) if age <= 2 else 0
                completed, rejected = upstream - waiting, 0
                at_station = random.choice(assembly_stations) if assembly_stations else None
                hour = 10
            elif op.section == Section.QUALITY:
                rejected = round(upstream * rate)
                waiting = int(upstream * random.uniform(0.05, 0.15)) if age <= 3 else 0
                completed = max(0, upstream - rejected - waiting)
                at_station = qc_station
                hour = 13
            else:  # dispatch
                completed = upstream
                # A few despatched lots are short at the gate — the gap §14.2
                # says is "where parts silently disappear in most plants today".
                if random.random() < 0.03 and upstream > 120:
                    completed = upstream - random.randint(18, 45)
                    short += 1
                rejected, at_station, hour = 0, None, 11
                despatched_lot = True
                despatch_rows.append(
                    DispatchRecord(
                        plant_id=plant.id,
                        lot_id=lot.id,
                        business_date=stage_day,
                        quantity=completed,
                        vehicle_reference=f"TN 70 AB {random.randint(1000, 9999)} / DC-{random.randint(40000, 49999)}",
                        despatched_at=_at(stage_day, hour),
                    )
                )

            if completed <= 0 and rejected <= 0:
                break

            rows.append(
                StageCompletion(
                    plant_id=plant.id,
                    lot_id=lot.id,
                    routing_operation_id=op.id,
                    station_id=at_station,
                    business_date=stage_day,
                    quantity_completed=completed,
                    quantity_rejected=rejected,
                    started_at=_at(stage_day, hour - 1),
                    completed_at=_at(stage_day, hour + 2),
                )
            )
            upstream = completed

        if despatched_lot:
            lot.closed_at = _at(stage_day, 11)

    session.add_all(rows)
    session.add_all(despatch_rows)
    await session.flush()
    return len(rows), len(despatch_rows), short


async def _story_lot(session, plant: Plant) -> str:
    """§3, verbatim — built through the real costing path so its rupee figures
    are the kernel's, not typed in."""
    if await session.scalar(
        select(Lot.id).where(Lot.plant_id == plant.id, Lot.lot_code == STORY_LOT)
    ):
        return "already present"

    part = await session.scalar(
        select(Part).where(Part.plant_id == plant.id, Part.part_number == "AK-447")
    )
    station = await session.scalar(
        select(Station).where(Station.plant_id == plant.id, Station.code == "CNC-3")
    )
    assembly = await session.scalar(
        select(Station).where(Station.plant_id == plant.id, Station.code == "Assy-L1")
    )
    qc = await session.scalar(
        select(Station).where(
            Station.plant_id == plant.id, Station.section == Section.QUALITY
        )
    )
    kumar = await session.scalar(
        select(Operator).where(Operator.plant_id == plant.id, Operator.name == "Kumar M.")
    )
    kavitha = await session.scalar(select(User).where(User.full_name == "Kavitha R."))
    breakage = await session.scalar(
        select(ReasonCode).where(
            ReasonCode.plant_id == plant.id, ReasonCode.code == "TOOL_BREAKAGE"
        )
    )
    shift_session = await session.scalar(
        select(ShiftSession)
        .join(Shift, Shift.id == ShiftSession.shift_id)
        .where(
            ShiftSession.plant_id == plant.id,
            Shift.name == "Shift A",
            ShiftSession.business_date == STORY_DATE,
        )
    )
    if not all([part, station, kumar, kavitha, breakage, shift_session]):
        return "skipped — demo masters or the 26 Aug shift are missing"

    busy = await session.scalar(
        select(func.count(JobSession.id)).where(
            JobSession.shift_session_id == shift_session.id,
            JobSession.station_id == station.id,
        )
    )
    if busy:
        return "skipped — CNC-3 already ran a job that shift"

    # The spec's number is 2231. An earlier seed generated a random job with that
    # number; move it aside rather than give the story a different number from
    # the document the demo is built on.
    clash = await session.scalar(
        select(JobOrder).where(
            JobOrder.plant_id == plant.id, JobOrder.job_number == STORY_JOB
        )
    )
    renumbered = ""
    if clash is not None and clash.part_id != part.id:
        highest = await session.scalar(
            select(func.max(func.cast(JobOrder.job_number, Integer))).where(
                JobOrder.plant_id == plant.id, JobOrder.job_number.op("~")(r"^\d+$")
            )
        )
        clash.job_number = str(int(highest or 3000) + 1)
        renumbered = f" (random seed job {STORY_JOB} renumbered to {clash.job_number})"
        await session.flush()

    operation = await session.scalar(
        select(RoutingOperation)
        .where(RoutingOperation.part_id == part.id)
        .order_by(RoutingOperation.sequence)
    )
    ops = (
        await session.scalars(
            select(RoutingOperation)
            .where(RoutingOperation.part_id == part.id)
            .order_by(RoutingOperation.sequence)
        )
    ).all()

    start, end = _at(STORY_DATE, 7), _at(STORY_DATE, 15, 30)
    job = JobOrder(
        plant_id=plant.id,
        job_number=STORY_JOB,
        part_id=part.id,
        customer_name=part.customer_name,
        planned_quantity=400,
        status=JobOrderStatus.COMPLETED,
        opened_at=start,
        completed_at=_at(date(2026, 8, 28), 11),
    )
    session.add(job)
    await session.flush()

    lot = Lot(
        plant_id=plant.id,
        lot_code=STORY_LOT,
        job_order_id=job.id,
        granularity=LotGranularity.JOB_LOT,
        quantity=371,
        opened_at=start,
    )
    session.add(lot)
    await session.flush()

    job_session = JobSession(
        plant_id=plant.id,
        job_order_id=job.id,
        shift_session_id=shift_session.id,
        station_id=station.id,
        lot_id=lot.id,
        routing_operation_id=operation.id,
        operator_id=kumar.id,
        started_at=start,
        ended_at=end,
    )
    session.add(job_session)
    await session.flush()

    # 371 parts across the shift — the hour with the breakage made the fewest.
    for hour, count in enumerate([52, 50, 18, 34, 50, 56, 55, 56]):
        session.add(
            ProductionCount(
                plant_id=plant.id,
                station_id=station.id,
                job_session_id=job_session.id,
                shift_session_id=shift_session.id,
                business_date=STORY_DATE,
                count=count,
                recorded_at=start + timedelta(hours=hour + 1),
                covers_from=start + timedelta(hours=hour),
            )
        )

    snapshot, _ = await resolve_snapshot(
        session,
        plant=plant,
        station=station,
        part=part,
        on_date=STORY_DATE,
        routing_operation=operation,
    )

    # 10:14 AM. CNC-3's tool snaps. At 11:01 the new tool is fitted (§3, Layer 1).
    stopped = _at(STORY_DATE, 10, 14)
    event = DowntimeEvent(
        plant_id=plant.id,
        station_id=station.id,
        job_session_id=job_session.id,
        shift_session_id=shift_session.id,
        business_date=STORY_DATE,
        reason_code_id=breakage.id,
        started_at=stopped,
        ended_at=stopped + timedelta(minutes=47),
        duration_minutes=Decimal(47),
        status=DowntimeStatus.CLOSED,
        note="Tool snapped mid-cut",
    )
    apply_snapshot(event, snapshot, downtime_event_cost(47, snapshot))
    session.add(event)

    inspected = _at(date(2026, 8, 27), 11, 30)
    record = QualityRecord(
        plant_id=plant.id,
        station_id=station.id,
        job_session_id=job_session.id,
        shift_session_id=shift_session.id,
        lot_id=lot.id,
        part_id=part.id,
        business_date=date(2026, 8, 27),
        good_count=362,
        reject_count=9,
        disposition=Disposition.SCRAP,
        defects=[{"code": "BURR", "count": 7}, {"code": "UNDERSIZE", "count": 2}],
        recorded_at=inspected,
        inspector_id=kavitha.id,
    )
    apply_snapshot(record, snapshot)
    cost = scrap_cost(
        quantity=9,
        snapshot=snapshot,
        completed_operations=[(operation.cycle_time_min, snapshot.rates)],
    )
    record.cost_total = cost.total
    record.cost_breakdown = {
        "material": cost.material_cost,
        "processing": cost.processing_cost,
        "lost_contribution": cost.lost_contribution,
        "quantity": cost.quantity,
    }
    session.add(record)

    flow = {
        Section.MACHINING: (371, 0, station.id, start, end),
        Section.ASSEMBLY: (
            371,
            0,
            assembly.id if assembly else None,
            _at(date(2026, 8, 27), 8),
            _at(date(2026, 8, 27), 10, 30),
        ),
        Section.QUALITY: (
            362,
            9,
            qc.id if qc else None,
            _at(date(2026, 8, 27), 10, 45),
            inspected,
        ),
    }
    for op in ops:
        if op.section == Section.DISPATCH:
            continue
        completed, rejected, at_station, began, finished = flow[op.section]
        session.add(
            StageCompletion(
                plant_id=plant.id,
                lot_id=lot.id,
                routing_operation_id=op.id,
                station_id=at_station,
                business_date=finished.date(),
                quantity_completed=completed,
                quantity_rejected=rejected,
                started_at=began,
                completed_at=finished,
                source=DataSource.MANUAL,
            )
        )

    await session.flush()
    from app.services.traceability import record_dispatch

    await record_dispatch(
        session,
        plant=plant,
        lot=lot,
        quantity=362,
        vehicle_reference="TN 70 AB 4471 / DC-44718",
        despatched_at=_at(date(2026, 8, 28), 11),
    )
    return "created" + renumbered


if __name__ == "__main__":
    print("Seeding stages, despatch and the lot #4471 story...")
    asyncio.run(seed_stages())
    print("Done.")
