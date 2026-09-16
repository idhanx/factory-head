"""Production counts (Screen 4) and quality records (Screen 5).

Both are deliberately thin. The intelligence lives in the kernel and the Actions
engine; these functions exist to make recording reality take fifteen seconds.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.kernel import CostRates, count_reconciliation, rework_cost, scrap_cost
from app.models import (
    DataSource,
    Disposition,
    DowntimeEvent,
    DowntimeStatus,
    JobSession,
    Part,
    Plant,
    ProductionCount,
    QualityRecord,
    RoutingOperation,
    ShiftSession,
    Station,
)
from app.services.costing import _rate_for, apply_snapshot, resolve_snapshot


async def record_production(
    session: AsyncSession,
    *,
    plant: Plant,
    station: Station,
    shift_session: ShiftSession,
    count: int,
    recorded_at: datetime | None = None,
    logged_by_id: uuid.UUID | None = None,
    source: DataSource = DataSource.MANUAL,
    source_ref: str | None = None,
    client_ref: str | None = None,
) -> ProductionCount:
    """Count *since the last entry* — the number a supervisor actually knows.

    ``covers_from`` is filled from the previous entry so the interval is
    explicit, which is what makes hourly counts usable for the Performance term
    of OEE without anyone typing a time range.

    ``client_ref`` makes the call idempotent: a tablet that retries a sync gets
    the row it already created, never a second count.
    """
    if count < 0:
        raise ValueError("Production count cannot be negative")

    if client_ref:
        existing = await session.scalar(
            select(ProductionCount).where(
                ProductionCount.plant_id == plant.id, ProductionCount.client_ref == client_ref
            )
        )
        if existing is not None:
            return existing

    recorded_at = recorded_at or datetime.now(UTC)
    job_session = await _active_job_session(session, plant.id, station.id, at=recorded_at)

    previous = await session.scalar(
        select(func.max(ProductionCount.recorded_at)).where(
            ProductionCount.plant_id == plant.id,
            ProductionCount.station_id == station.id,
            ProductionCount.shift_session_id == shift_session.id,
        )
    )

    row = ProductionCount(
        plant_id=plant.id,
        station_id=station.id,
        job_session_id=job_session.id if job_session else None,
        shift_session_id=shift_session.id,
        business_date=shift_session.business_date,
        count=count,
        recorded_at=recorded_at,
        covers_from=previous or shift_session.started_at,
        source=source,
        source_ref=source_ref,
        client_ref=client_ref,
        logged_by_id=logged_by_id,
    )
    session.add(row)
    await session.flush()
    await _end_changeover(session, plant=plant, station=station, at=recorded_at)
    return row


async def _end_changeover(
    session: AsyncSession, *, plant: Plant, station: Station, at: datetime
) -> None:
    """§14.3 step 2 — the changeover clock stops at the first entry on the new job.

    The gap between 'Job A paused' and the first count on Job B is the
    changeover, so nobody has to remember to tap 'Running' for it.
    """
    from app.services import downtime as downtime_service

    changeover = await session.scalar(
        select(DowntimeEvent).where(
            DowntimeEvent.plant_id == plant.id,
            DowntimeEvent.station_id == station.id,
            DowntimeEvent.status == DowntimeStatus.OPEN,
            DowntimeEvent.source_ref == CHANGEOVER_REF,
            DowntimeEvent.started_at <= at,
        )
    )
    if changeover is not None:
        await downtime_service.close_downtime(session, event=changeover, ended_at=at)


#: Marks the downtime a Change Job opens, so the first count can close it.
CHANGEOVER_REF = "change-job"


async def record_quality(
    session: AsyncSession,
    *,
    plant: Plant,
    part: Part,
    shift_session: ShiftSession,
    good_count: int,
    reject_count: int,
    station: Station | None = None,
    lot_id: uuid.UUID | None = None,
    rework_count: int = 0,
    rework_minutes: Decimal | None = None,
    disposition: Disposition = Disposition.SCRAP,
    defects: list | None = None,
    inspector_id: uuid.UUID | None = None,
    recorded_at: datetime | None = None,
    client_ref: str | None = None,
) -> QualityRecord:
    """The inspector's independent entry, costed and cross-checked on save.

    Inspected = good + rejected; ``rework_count`` is how many of the good parts
    needed rework first, so a reworked part is never also scrap (§8.1). Three
    things happen here that the inspector never sees:

    * scrapped rejections are costed (§8.1) — material plus the processing the
      part already consumed plus the contribution it will never earn;
    * rework is costed at the inspecting station's burden rate for the rework
      time recorded;
    * the count is reconciled against the supervisor's production entries. If
      they disagree beyond tolerance the record is flagged for the owner,
      because two independent counts that don't meet is the one signal manual
      data can give that someone is padding (§2).
    """
    if good_count < 0 or reject_count < 0 or rework_count < 0:
        raise ValueError("Counts cannot be negative")

    if client_ref:
        existing = await session.scalar(
            select(QualityRecord).where(
                QualityRecord.plant_id == plant.id, QualityRecord.client_ref == client_ref
            )
        )
        if existing is not None:
            return existing

    recorded_at = recorded_at or datetime.now(UTC)
    job_session = (
        await _active_job_session(session, plant.id, station.id, at=recorded_at)
        if station
        else None
    )

    record = QualityRecord(
        plant_id=plant.id,
        station_id=station.id if station else None,
        job_session_id=job_session.id if job_session else None,
        shift_session_id=shift_session.id,
        lot_id=lot_id or (job_session.lot_id if job_session else None),
        part_id=part.id,
        business_date=shift_session.business_date,
        good_count=good_count,
        reject_count=reject_count,
        rework_count=rework_count,
        rework_minutes=rework_minutes,
        disposition=disposition,
        defects=defects or [],
        recorded_at=recorded_at,
        inspector_id=inspector_id,
        client_ref=client_ref,
    )
    await cost_and_reconcile(
        session, plant=plant, record=record, part=part, station=station, shift_session=shift_session
    )
    session.add(record)
    await session.flush()
    return record


async def cost_and_reconcile(
    session: AsyncSession,
    *,
    plant: Plant,
    record: QualityRecord,
    part: Part,
    station: Station | None,
    shift_session: ShiftSession,
) -> None:
    """Price a quality record and cross-check it — on entry, and again on correction."""
    if station is None:
        return
    snapshot, _ = await resolve_snapshot(
        session,
        plant=plant,
        station=station,
        part=part,
        on_date=shift_session.business_date,
    )
    completed_ops = await _completed_operations(
        session,
        plant_id=plant.id,
        part_id=part.id,
        station=station,
        on_date=shift_session.business_date,
    )
    cost = scrap_cost(
        quantity=record.reject_count if record.disposition is Disposition.SCRAP else 0,
        snapshot=snapshot,
        completed_operations=completed_ops,
    )
    rework = (
        rework_cost(rework_minutes=record.rework_minutes, station_rates=snapshot.rates)
        if record.rework_count and record.rework_minutes
        else 0
    )
    apply_snapshot(record, snapshot)
    record.cost_total = cost.total + rework
    record.cost_breakdown = {
        "material": cost.material_cost,
        "processing": cost.processing_cost,
        "lost_contribution": cost.lost_contribution,
        "quantity": cost.quantity,
        "rework": rework,
        "rework_quantity": record.rework_count,
    }

    flagged, gap, _ = await reconcile_counts(
        session,
        plant=plant,
        station=station,
        shift_session=shift_session,
        job_session_id=record.job_session_id,
        inspected_now=record.good_count + record.reject_count,
        exclude_record_id=record.id,
    )
    record.reconciliation_gap = gap
    record.reconciliation_flagged = flagged


async def reconcile_counts(
    session: AsyncSession,
    *,
    plant: Plant,
    station: Station,
    shift_session: ShiftSession,
    inspected_now: int,
    job_session_id: uuid.UUID | None = None,
    exclude_record_id: uuid.UUID | None = None,
) -> tuple[bool, int, Decimal]:
    """Everything inspected so far against everything counted so far (§6, 2:30 PM).

    Like is compared with like. A batch is a slice of the running production,
    so the inspector's batches are summed — this one plus the ones already
    saved — and compared with the supervisor's running total for the same job
    on the same station in the same shift. Comparing one batch with the whole
    shift's count would flag every second batch of a healthy shift.
    """
    count_scope = [
        ProductionCount.plant_id == plant.id,
        ProductionCount.station_id == station.id,
        ProductionCount.shift_session_id == shift_session.id,
    ]
    quality_scope = [
        QualityRecord.plant_id == plant.id,
        QualityRecord.station_id == station.id,
        QualityRecord.shift_session_id == shift_session.id,
    ]
    if job_session_id is not None:
        count_scope.append(ProductionCount.job_session_id == job_session_id)
        quality_scope.append(QualityRecord.job_session_id == job_session_id)
    if exclude_record_id is not None:
        quality_scope.append(QualityRecord.id != exclude_record_id)

    supervisor_total = await session.scalar(
        select(func.coalesce(func.sum(ProductionCount.count), 0)).where(*count_scope)
    )
    inspected_before = await session.scalar(
        select(
            func.coalesce(
                func.sum(QualityRecord.good_count + QualityRecord.reject_count),
                0,
            )
        ).where(*quality_scope)
    )
    return count_reconciliation(
        supervisor_count=int(supervisor_total or 0),
        inspector_good=int(inspected_before or 0) + inspected_now,
        inspector_rejected=0,
        tolerance_parts=plant.reconciliation_tolerance_parts,
        tolerance_fraction=Decimal(plant.reconciliation_tolerance_bp) / Decimal(10_000),
    )


async def _completed_operations(
    session: AsyncSession,
    *,
    plant_id: uuid.UUID,
    part_id: uuid.UUID,
    station: Station,
    on_date,
) -> list[tuple[Decimal, CostRates]]:
    """Operations this part passed through, up to and including this station.

    A part scrapped at Op10 has not consumed Op20's machine time, and claiming
    it had would inflate the scrap number. Each operation is priced at the rate
    effective on the day the rejection happened, not today's rate.
    """
    operations = (
        await session.scalars(
            select(RoutingOperation)
            .where(
                RoutingOperation.plant_id == plant_id,
                RoutingOperation.part_id == part_id,
            )
            .order_by(RoutingOperation.sequence)
        )
    ).all()

    completed: list[tuple[Decimal, CostRates]] = []
    for operation in operations:
        if operation.cycle_time_min:
            family_ref = str(operation.cost_family_id) if operation.cost_family_id else ""
            machine, _ = await _rate_for(
                session, plant_id, "cost_family", family_ref, on_date, "machine_rate"
            )
            labour, _ = await _rate_for(
                session,
                plant_id,
                "section",
                operation.section.value,
                on_date,
                "labour_rate",
            )
            overhead, _ = await _rate_for(
                session, plant_id, "plant", "plant", on_date, "overhead_rate"
            )
            completed.append(
                (
                    Decimal(operation.cycle_time_min),
                    CostRates(
                        machine_rate=machine,
                        labour_rate=labour,
                        overhead_rate=overhead,
                    ),
                )
            )
        # Stop at the station where the rejection was found.
        if station.cost_family_id and operation.cost_family_id == station.cost_family_id:
            break
    return completed


async def _active_job_session(
    session: AsyncSession,
    plant_id: uuid.UUID,
    station_id: uuid.UUID,
    at: datetime | None = None,
) -> JobSession | None:
    """The job a station was running at ``at`` — now, when not given.

    An entry synced from an offline tablet an hour late belongs to the job that
    was on the station when it happened, not to whatever replaced it since.
    """
    query = select(JobSession).where(
        JobSession.plant_id == plant_id, JobSession.station_id == station_id
    )
    if at is None:
        return await session.scalar(
            query.where(JobSession.ended_at.is_(None))
            .order_by(JobSession.started_at.desc())
            .limit(1)
        )
    running = await session.scalar(
        query.where(
            JobSession.started_at <= at,
            (JobSession.ended_at.is_(None)) | (JobSession.ended_at >= at),
        )
        .order_by(JobSession.started_at.desc())
        .limit(1)
    )
    if running is not None:
        return running
    # A stop logged from just before the supervisor put the job on the station
    # — the machine was being set up for it — belongs to that job.
    return await session.scalar(
        query.where(
            JobSession.started_at > at,
            JobSession.started_at <= at + JOB_LOOKAHEAD,
        )
        .order_by(JobSession.started_at)
        .limit(1)
    )


#: How far ahead an entry recorded before any job was running looks for its job.
JOB_LOOKAHEAD = timedelta(minutes=60)
