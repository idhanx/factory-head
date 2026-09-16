"""The stores desk — §1, "Stores In-charge".

    Receives material-shortage alerts. Confirms material staging.
    *When events occur.*

Two duties, and the second is the one that pays for the first. §6's 5:45 AM
briefing ends its second priority with "confirm with Ramesh in Stores that full
quantity is at Line 2 before 8:00", and §6's 10:40 AM scene is what happens
when nobody did: Press-2 stops on a material wait, a rupee counter climbs, and
at minute 15 Ramesh gets the WhatsApp anyway. The confirmation is the cheap
half of that pair, and the plant has never had anywhere to record it.

Two design constraints this module is built against:

**No new daily register (§3, Layer 5's rule; §4.3's 15-minute budget).** No row
is created for Stores to fill in. The queue is today's job orders — which the
supervisor is already creating from the production plan — and a row appears the
moment somebody answers. A plant that never opens this screen is exactly as it
was before, with no half-filled table and no blocked shift closure.

**One standing answer per job per day.** Stores correcting itself ("it was
short at 7, it is staged now") updates the answer. A second row beside the
first could only ever disagree with it, and the morning meeting would then have
two material stories for one line.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    DowntimeEvent,
    DowntimeStatus,
    JobOrder,
    JobOrderStatus,
    JobSession,
    MaterialStaging,
    Part,
    Plant,
    ReasonCode,
    StagingStatus,
    Station,
    User,
)
from app.services.clock import plant_today

#: Job orders Stores can still do something about. A completed or cancelled job
#: needs no material, and listing it is noise on a screen used standing up.
LIVE_JOB_STATUSES = (JobOrderStatus.PLANNED, JobOrderStatus.RUNNING, JobOrderStatus.PAUSED)


@dataclass(slots=True)
class StagingRow:
    """One job order, and Stores' answer about its material — if there is one."""

    job_order: JobOrder
    part: Part
    staging: MaterialStaging | None
    #: Stations this job is running on right now, so 'Line 2' is on the screen
    #: rather than a job number nobody in Stores recognises.
    station_codes: list[str]
    #: Material waits this job has already caused today — the cost of not
    #: having answered, in the plant's own numbers.
    wait_events: int
    wait_cost_paise: int

    @property
    def status(self) -> StagingStatus:
        return self.staging.status if self.staging else StagingStatus.PENDING


def _staging_query(plant_id: uuid.UUID, business_date: date) -> Select:
    return (
        select(JobOrder, Part, MaterialStaging)
        .join(Part, Part.id == JobOrder.part_id)
        .outerjoin(
            MaterialStaging,
            (MaterialStaging.job_order_id == JobOrder.id)
            & (MaterialStaging.business_date == business_date)
            & (MaterialStaging.plant_id == plant_id),
        )
        .where(
            JobOrder.plant_id == plant_id,
            JobOrder.status.in_(LIVE_JOB_STATUSES),
        )
    )


async def staging_queue(
    session: AsyncSession, plant: Plant, *, business_date: date | None = None
) -> list[StagingRow]:
    """Today's jobs, each with Stores' answer and what silence has cost.

    Ordered by what a person standing at a stores counter should deal with
    first: the jobs already costing money in material waits, then the ones
    nobody has answered for, then the settled ones.
    """
    business_date = business_date or plant_today(plant)
    rows = (await session.execute(_staging_query(plant.id, business_date))).all()
    if not rows:
        return []

    job_ids = [job.id for job, _, _ in rows]
    stations = await _stations_by_job(session, plant.id, job_ids)
    waits = await _material_waits_by_job(session, plant.id, job_ids, business_date)

    queue = [
        StagingRow(
            job_order=job,
            part=part,
            staging=staging,
            station_codes=stations.get(job.id, []),
            wait_events=waits.get(job.id, (0, 0))[0],
            wait_cost_paise=waits.get(job.id, (0, 0))[1],
        )
        for job, part, staging in rows
    ]

    order = {StagingStatus.SHORT: 0, StagingStatus.PENDING: 1, StagingStatus.STAGED: 2}
    queue.sort(
        key=lambda row: (
            0 if row.wait_cost_paise else 1,
            -row.wait_cost_paise,
            order[row.status],
            row.job_order.job_number,
        )
    )
    return queue


async def _stations_by_job(
    session: AsyncSession, plant_id: uuid.UUID, job_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    """Where each job is physically running, from the job sessions §3 already keeps."""
    rows = (
        await session.execute(
            select(JobSession.job_order_id, Station.code)
            .join(Station, Station.id == JobSession.station_id)
            .where(
                JobSession.plant_id == plant_id,
                JobSession.job_order_id.in_(job_ids),
                JobSession.ended_at.is_(None),
            )
            .distinct()
        )
    ).all()
    by_job: dict[uuid.UUID, list[str]] = {}
    for job_id, code in rows:
        by_job.setdefault(job_id, []).append(code)
    for codes in by_job.values():
        codes.sort()
    return by_job


async def _material_waits_by_job(
    session: AsyncSession,
    plant_id: uuid.UUID,
    job_ids: list[uuid.UUID],
    business_date: date,
) -> dict[uuid.UUID, tuple[int, int]]:
    """Today's material-wait stops per job — count and cost.

    A stop counts as Stores' business when its reason code routes to stores,
    which is the same rule the escalation ladder uses to decide who gets the
    15-minute WhatsApp. One routing rule, read in two places (§8.1).
    """
    rows = (
        await session.execute(
            select(
                JobSession.job_order_id,
                func.count(DowntimeEvent.id),
                func.coalesce(func.sum(DowntimeEvent.cost_total), 0),
            )
            .join(JobSession, JobSession.id == DowntimeEvent.job_session_id)
            .join(ReasonCode, ReasonCode.id == DowntimeEvent.reason_code_id)
            .where(
                DowntimeEvent.plant_id == plant_id,
                DowntimeEvent.business_date == business_date,
                JobSession.job_order_id.in_(job_ids),
                ReasonCode.escalation_route == "stores",
            )
            .group_by(JobSession.job_order_id)
        )
    ).all()
    return {job_id: (int(count), int(cost)) for job_id, count, cost in rows}


async def open_material_waits(
    session: AsyncSession, plant_id: uuid.UUID
) -> list[tuple[DowntimeEvent, Station, ReasonCode]]:
    """Lines stopped on material right now — §1's "material-shortage alerts".

    Shown whether or not the 15-minute rung has fired yet: a line that has been
    waiting four minutes is still Stores' to fix, and arriving before the
    escalation is the entire point of putting this on a screen.
    """
    rows = (
        await session.execute(
            select(DowntimeEvent, Station, ReasonCode)
            .join(Station, Station.id == DowntimeEvent.station_id)
            .join(ReasonCode, ReasonCode.id == DowntimeEvent.reason_code_id)
            .where(
                DowntimeEvent.plant_id == plant_id,
                DowntimeEvent.status == DowntimeStatus.OPEN,
                ReasonCode.escalation_route == "stores",
            )
            .order_by(DowntimeEvent.started_at)
        )
    ).all()
    return [(event, station, reason) for event, station, reason in rows]


async def confirm_staging(
    session: AsyncSession,
    plant: Plant,
    *,
    job_order: JobOrder,
    status: StagingStatus,
    quantity_staged: int | None,
    note: str | None,
    user: User,
    business_date: date | None = None,
    at: datetime | None = None,
) -> MaterialStaging:
    """Stores' answer for one job, today. One tap, plus a number if it is short.

    ``quantity_required`` is frozen onto the row from the job's plan at the
    moment of answering, so that re-planning the job this afternoon never
    rewrites what Stores was asked for this morning — the same
    freeze-what-you-were-asked discipline §5.6 applies to savings baselines.
    """
    business_date = business_date or plant_today(plant)

    if status is StagingStatus.SHORT and quantity_staged is None:
        raise ValueError("Say how much is staged when the material is short")
    if quantity_staged is not None and quantity_staged < 0:
        raise ValueError("Staged quantity cannot be negative")
    if status is StagingStatus.PENDING:
        raise ValueError("Confirm either 'staged' or 'short' — pending is not an answer")

    staging = await session.scalar(
        select(MaterialStaging).where(
            MaterialStaging.plant_id == plant.id,
            MaterialStaging.job_order_id == job_order.id,
            MaterialStaging.business_date == business_date,
        )
    )
    if staging is None:
        staging = MaterialStaging(
            plant_id=plant.id,
            job_order_id=job_order.id,
            business_date=business_date,
            quantity_required=job_order.planned_quantity,
        )
        session.add(staging)

    staging.status = status
    staging.quantity_staged = (
        quantity_staged if quantity_staged is not None else job_order.planned_quantity
    )
    staging.note = (note or "").strip() or None
    staging.confirmed_by_id = user.id
    staging.confirmed_at = at or datetime.now(UTC)
    await session.flush()
    return staging
