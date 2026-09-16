"""Role pages (§8) and the monthly owner report (§4.4)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.models import (
    EscalationRoute,
    OeeTreatment,
    QualityRecord,
    ReasonCode,
    Recoverability,
)
from app.services import capture as capture_service
from app.services import downtime as downtime_service
from app.services import role_kpis
from app.services import shifts as shift_service
from tests.conftest import run_job

DAY = date.today() - timedelta(days=2)
START = datetime.combine(DAY, datetime.min.time(), tzinfo=UTC) + timedelta(
    hours=1, minutes=30
)


async def closed_shift(session, fixtures, *, breakdown_minutes=0, produced=400, rejected=4):
    plant = fixtures["plant"]
    shift_session = await shift_service.start_shift(
        session, plant=plant, shift=fixtures["shift"], supervisor_id=None, started_at=START
    )
    job_session = await run_job(session, fixtures, shift_session)
    await capture_service.record_production(
        session,
        plant=plant,
        station=fixtures["station"],
        shift_session=shift_session,
        count=produced,
        recorded_at=START + timedelta(hours=7),
    )
    if breakdown_minutes:
        reason = ReasonCode(
            plant_id=plant.id,
            code="MACHINE_BREAKDOWN",
            label="Machine Breakdown",
            recoverability=Recoverability.RECOVERABLE,
            oee_treatment=OeeTreatment.UNPLANNED,
            escalation_route=EscalationRoute.MAINTENANCE,
            is_breakdown=True,
        )
        session.add(reason)
        await session.flush()
        event = await downtime_service.open_downtime(
            session,
            plant=plant,
            station=fixtures["station"],
            reason_code=reason,
            shift_session=shift_session,
            started_at=START + timedelta(hours=2),
        )
        await downtime_service.close_downtime(
            session,
            event=event,
            ended_at=START + timedelta(hours=2, minutes=breakdown_minutes),
        )
    session.add(
        QualityRecord(
            plant_id=plant.id,
            station_id=fixtures["station"].id,
            shift_session_id=shift_session.id,
            lot_id=job_session.lot_id,
            part_id=fixtures["part"].id,
            business_date=shift_session.business_date,
            good_count=produced - rejected,
            reject_count=rejected,
            defects=[{"code": "BURR", "count": rejected}],
            recorded_at=START + timedelta(hours=6),
        )
    )
    await shift_service.close_shift(
        session,
        shift_session=shift_session,
        closed_by_id=None,
        closed_at=START + timedelta(hours=8, minutes=30),
    )
    return shift_session


async def test_supervisor_oee_uses_the_same_aggregate_as_the_shift(session, fixtures):
    """A line's OEE on the supervisor page cannot disagree with its shift."""
    from app.services import reports

    shift_session = await closed_shift(session, fixtures, breakdown_minutes=30)
    page = await role_kpis.supervisor(session, plant=fixtures["plant"], start=DAY, end=DAY)
    shift = await reports.shift_kpis(
        session, plant=fixtures["plant"], shift_session=shift_session
    )

    assert page["plant"]["oee"] == shift.oee
    assert page["lines"][0]["line"] == "Line 1"
    assert page["lines"][0]["oee"] == shift.oee
    assert page["plant"]["availability"] > 0.9


async def test_a_machine_with_no_breakdowns_has_no_mtbf_rather_than_zero(session, fixtures):
    await closed_shift(session, fixtures, breakdown_minutes=0)
    page = await role_kpis.maintenance(session, plant=fixtures["plant"], start=DAY, end=DAY)

    assert page["breakdowns"] == 0
    assert page["mtbf_minutes"] is None and page["mttr_minutes"] is None
    assert page["machines"][0]["mtbf_minutes"] is None


async def test_breakdown_counts_come_from_the_reason_code_flag(session, fixtures):
    await closed_shift(session, fixtures, breakdown_minutes=45)
    page = await role_kpis.maintenance(session, plant=fixtures["plant"], start=DAY, end=DAY)

    assert page["breakdowns"] == 1
    assert page["mttr_minutes"] == 45.0
    assert page["machines"][0]["station"] == "CNC-3"


async def test_quality_ppm_is_rejects_per_million_inspected(session, fixtures):
    await closed_shift(session, fixtures, produced=400, rejected=4)
    page = await role_kpis.quality(session, plant=fixtures["plant"], start=DAY, end=DAY)

    assert page["inspected"] == 400
    assert page["ppm"] == 10000.0
    assert page["top_defects"][0]["code"] == "BURR"
    assert page["by_machine"][0]["station"] == "CNC-3"


async def test_the_monthly_report_leads_with_money_and_watches_discipline(
    session, fixtures
):
    await closed_shift(session, fixtures, breakdown_minutes=40)
    report = await role_kpis.monthly_report(
        session, plant=fixtures["plant"], month=DAY.replace(day=1)
    )

    assert report["discipline"]["shifts_closed"] == 1
    assert report["discipline"]["closed_clean_share"] == 1.0
    assert report["recoverable_loss_paise"] > 0
    assert report["annualised_projection_display"].startswith("₹")
    assert "running_roi" in report
