"""Traceability and stage tracking — §3's job-order spine, §14.2's stages.

    "Scenario: an OEM customer complains about burrs on lot #4471, three weeks
     after despatch. Today, answering that means two days of digging through
     registers. With the job-order spine, the owner asks and gets the answer in
     seconds: lot #4471 was Job Order 2231, run on CNC-3, Shift A, 14 June,
     operator Kumar, inspected by Kavitha, 362 good / 9 rejected — 7 of them
     burrs — and CNC-3 had a tool-breakage event that same morning."

Nothing in that answer is entered for traceability's sake. Every fact was
captured during a normal shift and tagged by the job it belonged to — which is
why this module mostly *reads*, and the only things it writes are the two
events §14.2 adds: a quantity passing a stage, and a quantity leaving the gate.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.kernel import (
    PartFlow,
    StageTotals,
    combine_part_flows,
    format_inr,
    lot_flow,
    throughput_minutes,
)
from app.models import (
    DataSource,
    DefectCode,
    DispatchRecord,
    DowntimeEvent,
    JobOrder,
    JobSession,
    Lot,
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


class TraceError(ValueError):
    """A stage entry that cannot be right — rejected with a readable reason."""


# ---------------------------------------------------------------------------
# Writes — the two events §14.2 adds
# ---------------------------------------------------------------------------


async def find_lot(session: AsyncSession, plant: Plant, lot_code: str) -> Lot | None:
    """Lot codes are typed or scanned off a paper route card, so match loosely:
    case-insensitive, and a leading '#' as people write it ("lot #4471")."""
    code = lot_code.strip().lstrip("#").strip()
    return await session.scalar(
        select(Lot).where(
            Lot.plant_id == plant.id, func.lower(Lot.lot_code) == code.lower()
        )
    )


async def record_stage(
    session: AsyncSession,
    *,
    plant: Plant,
    lot: Lot,
    operation_code: str,
    quantity_completed: int,
    quantity_rejected: int = 0,
    station: Station | None = None,
    completed_at: datetime | None = None,
    started_at: datetime | None = None,
    source: DataSource = DataSource.MANUAL,
) -> StageCompletion:
    """A quantity passing one operation of the lot's routing.

    §14.2: "At each stage the operator scans it (or taps the lot from a short
    list) and enters the quantity." One number, one tap.
    """
    if quantity_completed < 0 or quantity_rejected < 0:
        raise TraceError("Quantities cannot be negative")
    if quantity_completed == 0 and quantity_rejected == 0:
        raise TraceError("Enter the parts that passed, the parts rejected, or both")

    operation = await _operation(session, plant, lot, operation_code)
    completed_at = completed_at or datetime.now(UTC)

    row = StageCompletion(
        plant_id=plant.id,
        lot_id=lot.id,
        routing_operation_id=operation.id,
        station_id=station.id if station else None,
        business_date=completed_at.date(),
        quantity_completed=quantity_completed,
        quantity_rejected=quantity_rejected,
        started_at=started_at,
        completed_at=completed_at,
        source=source,
    )
    session.add(row)
    await session.flush()
    return row


async def record_dispatch(
    session: AsyncSession,
    *,
    plant: Plant,
    lot: Lot,
    quantity: int,
    vehicle_reference: str | None = None,
    despatched_at: datetime | None = None,
) -> DispatchRecord:
    """Parts leaving the gate — deliberately the final operation (§14.2).

    Written as a dispatch record (with its vehicle or challan reference) *and*
    as a completion of the routing's dispatch stage, so the flow through every
    stage is read from one place and despatch is never a special case.
    """
    if quantity <= 0:
        raise TraceError("Despatched quantity must be more than zero")

    despatched_at = despatched_at or datetime.now(UTC)
    operation = await _dispatch_operation(session, plant, lot)

    record = DispatchRecord(
        plant_id=plant.id,
        lot_id=lot.id,
        business_date=despatched_at.date(),
        quantity=quantity,
        vehicle_reference=vehicle_reference,
        despatched_at=despatched_at,
    )
    session.add(record)
    if operation is not None:
        session.add(
            StageCompletion(
                plant_id=plant.id,
                lot_id=lot.id,
                routing_operation_id=operation.id,
                business_date=despatched_at.date(),
                quantity_completed=quantity,
                quantity_rejected=0,
                started_at=despatched_at,
                completed_at=despatched_at,
            )
        )
    lot.closed_at = despatched_at
    await session.flush()
    return record


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def flow_for(session: AsyncSession, plant: Plant, lot: Lot):
    """``(flow, operations)`` for a lot — the combined flow, run through the kernel.

    Signature kept stable on purpose: the dataset verifier calls it directly.
    Per-part detail for batch lots comes from :func:`_flows`.
    """
    flow, operations, _ = await _flows(session, plant, lot)
    return flow, operations


async def _flows(session: AsyncSession, plant: Plant, lot: Lot):
    """``(combined flow, operations, [(part_number, flow, operations)])``.

    A **job lot** follows its part's routing. A **day-batch or shift-batch lot**
    (§3: "everything Line 2 produces on 14 June is lot L2-140626") has no job
    order and can hold several parts — so each part is walked through *its own*
    routing and the results combined by the kernel. Merging stages by operation
    code instead invents shortfalls: in one batch "Op30" can be despatch for one
    part and inspection for another.
    """
    totals = {
        op_id: (int(completed), int(rejected), first, last)
        for op_id, completed, rejected, first, last in (
            await session.execute(
                select(
                    StageCompletion.routing_operation_id,
                    func.coalesce(func.sum(StageCompletion.quantity_completed), 0),
                    func.coalesce(func.sum(StageCompletion.quantity_rejected), 0),
                    func.min(
                        func.coalesce(
                            StageCompletion.started_at, StageCompletion.completed_at
                        )
                    ),
                    func.max(StageCompletion.completed_at),
                )
                .where(StageCompletion.lot_id == lot.id)
                .group_by(StageCompletion.routing_operation_id)
            )
        ).all()
    }

    tolerance = {
        "tolerance_parts": plant.reconciliation_tolerance_parts,
        "tolerance_fraction": Decimal(plant.reconciliation_tolerance_bp) / Decimal(10_000),
    }

    if lot.job_order_id:
        part_ids = await _lot_part_ids(session, plant, lot)
    elif totals:
        part_ids = list(
            (
                await session.scalars(
                    select(RoutingOperation.part_id)
                    .where(RoutingOperation.id.in_(list(totals)))
                    .distinct()
                )
            ).all()
        )
    else:
        part_ids = []

    parts: list[tuple[str, object, list]] = []
    all_operations: list[RoutingOperation] = []
    earliest, latest_despatch = None, None

    for part_id in part_ids:
        operations = (
            await session.scalars(
                select(RoutingOperation)
                .where(
                    RoutingOperation.plant_id == plant.id,
                    RoutingOperation.part_id == part_id,
                )
                .order_by(RoutingOperation.sequence)
            )
        ).all()
        stages = [
            StageTotals(
                operation_code=op.operation_code,
                section=op.section.value,
                completed=totals[op.id][0] if op.id in totals else 0,
                rejected=totals[op.id][1] if op.id in totals else 0,
                first_started_at=totals[op.id][2] if op.id in totals else None,
                last_completed_at=totals[op.id][3] if op.id in totals else None,
                recorded=op.id in totals,
            )
            for op in operations
        ]
        # A job lot knows how many parts entered it; a batch does not know that
        # per part, so each part's first stage is its own starting count.
        flow = lot_flow(
            stages,
            lot_quantity=(lot.quantity or None) if lot.job_order_id else None,
            **tolerance,
        )
        number = await session.scalar(select(Part.part_number).where(Part.id == part_id))
        parts.append((number or "?", flow, list(operations)))
        all_operations.extend(operations)

        for stage in flow.stages:
            if stage.first_started_at and (
                earliest is None or stage.first_started_at < earliest
            ):
                earliest = stage.first_started_at
            if (
                stage.section == "dispatch"
                and stage.last_completed_at
                and (latest_despatch is None or stage.last_completed_at > latest_despatch)
            ):
                latest_despatch = stage.last_completed_at

    if not parts:
        return lot_flow([], **tolerance), [], []

    combined = combine_part_flows(
        [PartFlow(number, flow) for number, flow, _ in parts],
        throughput_minutes=(
            throughput_minutes(
                first_operation_start=earliest, last_operation_completion=latest_despatch
            )
            if earliest and latest_despatch
            else None
        ),
    )
    return combined, all_operations, parts


async def trace_lot(session: AsyncSession, *, plant: Plant, lot: Lot) -> dict:
    """The whole story of one lot, in the order someone investigating asks for it."""
    job = await session.get(JobOrder, lot.job_order_id) if lot.job_order_id else None
    part = await session.get(Part, job.part_id) if job else None
    flow, operations, parts = await _flows(session, plant, lot)

    # --- where it was made: every job session that ran this job -------------
    sessions = (
        await session.execute(
            select(JobSession, Station, ShiftSession, Shift, Operator)
            .join(Station, Station.id == JobSession.station_id)
            .join(ShiftSession, ShiftSession.id == JobSession.shift_session_id)
            .join(Shift, Shift.id == ShiftSession.shift_id)
            .outerjoin(Operator, Operator.id == JobSession.operator_id)
            .where(
                JobSession.plant_id == plant.id,
                # A batch lot has no job order. Without this guard the second
                # clause would compare against NULL and quietly match nothing —
                # or, on a nullable column, everything.
                or_(
                    JobSession.lot_id == lot.id, JobSession.job_order_id == lot.job_order_id
                )
                if lot.job_order_id
                else JobSession.lot_id == lot.id,
            )
            .order_by(JobSession.started_at)
        )
    ).all()

    runs = []
    for job_session, station, shift_session, shift, operator in sessions:
        # §3's last clause — "and CNC-3 had a tool-breakage event that same
        # morning" — is the whole point: stops on the station during the run,
        # which is where a defect's root cause is usually hiding.
        ended = job_session.ended_at or (job_session.started_at + timedelta(hours=9))
        events = (
            await session.execute(
                select(DowntimeEvent, ReasonCode.label)
                .outerjoin(ReasonCode, ReasonCode.id == DowntimeEvent.reason_code_id)
                .where(
                    DowntimeEvent.plant_id == plant.id,
                    DowntimeEvent.station_id == station.id,
                    DowntimeEvent.business_date == shift_session.business_date,
                    DowntimeEvent.started_at < ended,
                )
                .order_by(DowntimeEvent.started_at)
            )
        ).all()
        produced = await session.scalar(
            select(func.coalesce(func.sum(ProductionCount.count), 0)).where(
                ProductionCount.job_session_id == job_session.id
            )
        )
        runs.append(
            {
                "station": station.code,
                "station_name": station.name,
                "business_date": shift_session.business_date.isoformat(),
                "shift": shift.name,
                "operator": operator.name if operator else None,
                "started_at": job_session.started_at.isoformat(),
                "ended_at": job_session.ended_at.isoformat()
                if job_session.ended_at
                else None,
                "produced": int(produced or 0),
                "events": [
                    {
                        "reason": label or "Reason not set",
                        "started_at": event.started_at.isoformat(),
                        "minutes": float(event.duration_minutes or 0),
                        "cost_paise": event.cost_total,
                        "cost_display": format_inr(event.cost_total),
                    }
                    for event, label in events
                ],
            }
        )

    # --- how it was inspected ---------------------------------------------------
    defect_labels = dict(
        (
            await session.execute(
                select(DefectCode.code, DefectCode.label).where(
                    DefectCode.plant_id == plant.id
                )
            )
        ).all()
    )
    inspections = (
        await session.execute(
            select(QualityRecord, User.full_name, Station.code)
            .outerjoin(User, User.id == QualityRecord.inspector_id)
            .outerjoin(Station, Station.id == QualityRecord.station_id)
            .where(QualityRecord.plant_id == plant.id, QualityRecord.lot_id == lot.id)
            .order_by(QualityRecord.recorded_at)
        )
    ).all()

    quality = [
        {
            "recorded_at": record.recorded_at.isoformat(),
            "business_date": record.business_date.isoformat(),
            "station": station_code,
            "inspector": inspector,
            "good": record.good_count,
            "rejected": record.reject_count,
            "defects": [
                {
                    "code": d.get("code"),
                    "label": defect_labels.get(d.get("code"), d.get("code")),
                    "count": int(d.get("count", 0)),
                }
                for d in (record.defects or [])
            ],
            "scrap_cost_paise": record.cost_total,
            "scrap_cost_display": format_inr(record.cost_total),
            "disposition": record.disposition.value,
        }
        for record, inspector, station_code in inspections
    ]

    despatches = (
        await session.scalars(
            select(DispatchRecord)
            .where(DispatchRecord.lot_id == lot.id)
            .order_by(DispatchRecord.despatched_at)
        )
    ).all()

    good = sum(q["good"] for q in quality)
    rejected = sum(q["rejected"] for q in quality)
    defect_totals: dict[str, int] = {}
    for q in quality:
        for d in q["defects"]:
            defect_totals[d["label"]] = defect_totals.get(d["label"], 0) + d["count"]

    first_run = runs[0] if runs else None
    same_day_stops = [
        {**event, "station": run["station"]} for run in runs for event in run["events"]
    ]

    return {
        "lot": {
            "id": str(lot.id),
            "code": lot.lot_code,
            "granularity": lot.granularity.value,
            "quantity": lot.quantity,
            "opened_at": lot.opened_at.isoformat() if lot.opened_at else None,
            "closed_at": lot.closed_at.isoformat() if lot.closed_at else None,
        },
        "job": {
            "number": job.job_number if job else None,
            "customer": (job.customer_name if job else None)
            or (part.customer_name if part else None),
            "planned_quantity": job.planned_quantity if job else None,
        },
        "part": {
            "number": part.part_number if part else None,
            "description": part.description if part else None,
            "family": part.part_family if part else None,
        },
        # The one-paragraph answer §3 describes, assembled from the facts below
        # rather than written by anyone.
        "summary": _summary(
            lot,
            job,
            part,
            first_run,
            quality,
            good,
            rejected,
            defect_totals,
            same_day_stops,
        ),
        "flow": {
            **_flow_payload(flow, operations),
            # A batch holding several parts: each part's own path through its
            # own routing. Empty for a job lot, whose stages are above.
            "parts": [
                {"part_number": number, **_flow_payload(part_flow, part_ops)}
                for number, part_flow, part_ops in parts
            ]
            if len(parts) > 1
            else [],
        },
        "runs": runs,
        "quality": quality,
        "quality_totals": {
            "good": good,
            "rejected": rejected,
            "defects": [
                {"label": k, "count": v}
                for k, v in sorted(defect_totals.items(), key=lambda kv: -kv[1])
            ],
        },
        "despatches": [
            {
                "quantity": d.quantity,
                "vehicle_reference": d.vehicle_reference,
                "despatched_at": d.despatched_at.isoformat(),
            }
            for d in despatches
        ],
        # §10: names are shown because traceability requires them (it is an
        # IATF clause, and §3's own example names the operator and inspector).
        # The screen says what this record is for while the no-blame period runs.
        "no_blame_active": plant.no_blame_active,
    }


def _flow_payload(flow, operations) -> dict:
    names = {op.operation_code: (op.name or op.section.value) for op in operations}
    return {
        "stages": [
            {
                "operation_code": st.operation_code,
                "name": names.get(st.operation_code),
                "section": st.section,
                "received": st.received,
                "completed": st.completed,
                "rejected": st.rejected,
                "wip_before": st.wip_before,
                "recorded": st.recorded,
                "first_started_at": st.first_started_at.isoformat()
                if st.first_started_at
                else None,
                "last_completed_at": st.last_completed_at.isoformat()
                if st.last_completed_at
                else None,
            }
            for st in flow.stages
        ],
        "flags": [
            {
                "operation_code": f.operation_code,
                "kind": f.kind,
                "gap": f.gap,
                "message": f.message,
            }
            for f in flow.flags
        ],
        "despatched": flow.despatched,
        "complete": flow.complete,
        "position": flow.position,
        "throughput_minutes": float(flow.throughput_minutes)
        if flow.throughput_minutes
        else None,
        "total_rejected": flow.total_rejected,
    }


async def search(
    session: AsyncSession, *, plant: Plant, q: str, limit: int = 20
) -> list[dict]:
    """Find a lot by what the person asking actually has in hand: a lot code off
    a complaint, a job number, a part number, or a customer name."""
    text = q.strip().lstrip("#").strip()
    if not text:
        return []
    pattern = f"%{text}%"

    rows = (
        await session.execute(
            select(Lot, JobOrder, Part)
            .outerjoin(JobOrder, JobOrder.id == Lot.job_order_id)
            .outerjoin(Part, Part.id == JobOrder.part_id)
            .where(
                Lot.plant_id == plant.id,
                or_(
                    Lot.lot_code.ilike(pattern),
                    JobOrder.job_number.ilike(pattern),
                    Part.part_number.ilike(pattern),
                    JobOrder.customer_name.ilike(pattern),
                ),
            )
            # Exact lot-code matches first: someone holding a complaint letter
            # typed the lot number and wants that lot, not twelve near misses.
            .order_by(
                (func.lower(Lot.lot_code) == text.lower()).desc(), Lot.opened_at.desc()
            )
            .limit(limit)
        )
    ).all()

    return [
        {
            "lot_code": lot.lot_code,
            "job_number": job.job_number if job else None,
            "part_number": part.part_number if part else None,
            "customer": job.customer_name if job else None,
            "quantity": lot.quantity,
            "opened_at": lot.opened_at.isoformat() if lot.opened_at else None,
            "closed": lot.closed_at is not None,
        }
        for lot, job, part in rows
    ]


async def wip_board(session: AsyncSession, *, plant: Plant, limit: int = 60) -> list[dict]:
    """Lots still moving, and where each one physically sits.

    Only lots with at least one stage recorded and no despatch yet — a lot
    nobody has touched is a plan, not work in progress.
    """
    lots = (
        await session.scalars(
            select(Lot)
            .where(
                Lot.plant_id == plant.id,
                Lot.closed_at.is_(None),
                select(StageCompletion.id).where(StageCompletion.lot_id == Lot.id).exists(),
            )
            .order_by(Lot.opened_at)
            .limit(limit)
        )
    ).all()

    board = []
    for lot in lots:
        flow, _, parts = await _flows(session, plant, lot)
        job = await session.get(JobOrder, lot.job_order_id) if lot.job_order_id else None
        part = await session.get(Part, job.part_id) if job else None
        # For a batch, show the first part still moving: that is the one someone
        # at a stage has in front of them.
        shown_number, shown = next(
            (
                (number, part_flow)
                for number, part_flow, _ in parts
                if not part_flow.complete
            ),
            (parts[0][0], parts[0][1]) if parts else (None, flow),
        )
        waiting = next(
            (s for s in shown.stages if s.operation_code == shown.position), None
        )
        board.append(
            {
                "lot_code": lot.lot_code,
                "job_number": job.job_number if job else None,
                "part_number": part.part_number if part else shown_number,
                "position": shown.position,
                "section": waiting.section if waiting else None,
                "waiting": waiting.wip_before if waiting else 0,
                "opened_at": lot.opened_at.isoformat() if lot.opened_at else None,
                "age_hours": round(
                    (datetime.now(UTC) - lot.opened_at).total_seconds() / 3600, 1
                )
                if lot.opened_at
                else None,
                "flags": len(flow.flags),
                "stages": [
                    {
                        "operation_code": s.operation_code,
                        "section": s.section,
                        "completed": s.completed,
                        "rejected": s.rejected,
                        "recorded": s.recorded,
                    }
                    for s in shown.stages
                ],
            }
        )
    return board


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _lot_part_ids(session, plant: Plant, lot: Lot) -> list[uuid.UUID]:
    """The parts a lot holds: its job's part, or — for a day/shift batch — every
    part that was produced into it."""
    if lot.job_order_id:
        part_id = await session.scalar(
            select(JobOrder.part_id).where(JobOrder.id == lot.job_order_id)
        )
        return [part_id] if part_id else []
    return list(
        (
            await session.scalars(
                select(JobOrder.part_id)
                .join(JobSession, JobSession.job_order_id == JobOrder.id)
                .where(JobSession.plant_id == plant.id, JobSession.lot_id == lot.id)
                .distinct()
            )
        ).all()
    )


async def _operation(
    session, plant: Plant, lot: Lot, operation_code: str
) -> RoutingOperation:
    part_ids = await _lot_part_ids(session, plant, lot)
    operation = (
        await session.scalar(
            select(RoutingOperation)
            .where(
                RoutingOperation.plant_id == plant.id,
                RoutingOperation.part_id.in_(part_ids),
                func.lower(RoutingOperation.operation_code)
                == operation_code.strip().lower(),
            )
            .order_by(RoutingOperation.sequence)
            .limit(1)
        )
        if part_ids
        else None
    )
    if operation is None:
        raise TraceError(f"{operation_code} is not on this lot's routing")
    return operation


async def _dispatch_operation(session, plant: Plant, lot: Lot) -> RoutingOperation | None:
    part_ids = await _lot_part_ids(session, plant, lot)
    if not part_ids:
        return None
    return await session.scalar(
        select(RoutingOperation)
        .where(
            RoutingOperation.plant_id == plant.id,
            RoutingOperation.part_id.in_(part_ids),
            RoutingOperation.section == Section.DISPATCH,
        )
        .order_by(RoutingOperation.sequence.desc())
        .limit(1)
    )


def _summary(
    lot, job, part, first_run, quality, good, rejected, defect_totals, stops
) -> str:
    """§3's answer, as one sentence. Each clause appears only if its fact exists,
    so a lot with no inspection record never claims one."""
    clauses: list[str] = []

    identity = f"Lot #{lot.lot_code}"
    if job:
        identity += f" was Job Order {job.job_number}"
        if part:
            identity += f" for {part.part_number}"
    clauses.append(identity)

    if first_run:
        day = datetime.fromisoformat(first_run["business_date"]).strftime("%-d %B")
        clauses.append(f"run on {first_run['station']}, {first_run['shift']}, {day}")
        if first_run["operator"]:
            clauses.append(f"operator {first_run['operator']}")

    if quality:
        inspectors = sorted({q["inspector"] for q in quality if q["inspector"]})
        if inspectors:
            clauses.append(f"inspected by {' and '.join(inspectors)}")
        verdict = f"{good} good / {rejected} rejected"
        if defect_totals:
            label, count = max(defect_totals.items(), key=lambda kv: kv[1])
            # "7 for burr", not "7 of them burrs": defect labels such as
            # "Dent / Handling Damage" do not pluralise safely.
            verdict += f" — {count} for {label.lower()}"
        clauses.append(verdict)

    sentence = ", ".join(clauses)

    significant = [s for s in stops if s["minutes"] >= 5]
    if significant:
        worst = max(significant, key=lambda s: s["cost_paise"])
        sentence += (
            f" — and {worst['station']} had a {worst['reason'].lower()} event "
            f"({round(worst['minutes'])} min, {worst['cost_display']}) during the run"
        )
    return sentence + "."
