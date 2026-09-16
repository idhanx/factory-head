"""HTTP-level tests for the Phase 1 audit blockers.

Each test drives the real FastAPI app over ASGI with real tokens, because every
defect these pin down lived above the service layer: a missing role guard, an
id from another plant accepted in a request body, a field the handler never
set, a read endpoint that wrote.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.kernel import rupees
from app.models import (
    Action,
    ComplianceClause,
    CostFamily,
    Escalation,
    EscalationChannel,
    EscalationStatus,
    JobOrder,
    JobOrderStatus,
    JobSession,
    PdcaState,
)


async def _job(session, fixtures, number: str = "2231") -> JobOrder:
    job = JobOrder(
        plant_id=fixtures["plant"].id,
        job_number=number,
        part_id=fixtures["part"].id,
        customer_name="Rane TRW",
        planned_quantity=500,
        status=JobOrderStatus.PLANNED,
    )
    session.add(job)
    await session.flush()
    return job


async def _start_shift(api, fixtures, job: JobOrder | None = None):
    assignments = (
        [{"station_id": str(fixtures["station"].id), "job_order_id": str(job.id), "lot_code": "4471"}]
        if job
        else []
    )
    response = await api["client"].post(
        "/api/v1/shifts/start",
        headers=api["as"]("supervisor"),
        json={"shift_id": str(fixtures["shift"].id), "assignments": assignments},
    )
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# OEE — the routing operation is linked when a job goes on a station
# ---------------------------------------------------------------------------


async def test_shift_start_links_the_routing_operation_so_performance_is_measured(
    api, session, fixtures
):
    job = await _job(session, fixtures)
    shift = await _start_shift(api, fixtures, job)

    job_session = await session.scalar(
        select(JobSession).where(JobSession.shift_session_id == uuid.UUID(shift["id"]))
    )
    assert job_session.routing_operation_id is not None
    assert job_session.lot_id is not None

    response = await api["client"].post(
        "/api/v1/production",
        headers=api["as"]("supervisor"),
        json={"station_id": str(fixtures["station"].id), "count": 150},
    )
    assert response.status_code == 200, response.text

    kpis = (
        await api["client"].get(
            f"/api/v1/reports/kpis/{shift['id']}", headers=api["as"]("owner")
        )
    ).json()
    assert kpis["performance"] > 0
    assert kpis["oee"] > 0


async def test_shift_start_refuses_a_station_from_another_plant(api, session, fixtures):
    job = await _job(session, fixtures)
    response = await api["client"].post(
        "/api/v1/shifts/start",
        headers=api["as"]("supervisor"),
        json={
            "shift_id": str(fixtures["shift"].id),
            "assignments": [
                {"station_id": str(api["foreign_station"].id), "job_order_id": str(job.id)}
            ],
        },
    )
    assert response.status_code == 404


async def test_change_job_carries_lot_and_routing_to_the_new_job(api, session, fixtures):
    first = await _job(session, fixtures, "2231")
    second = await _job(session, fixtures, "4482")
    await _start_shift(api, fixtures, first)

    response = await api["client"].post(
        "/api/v1/change-job",
        headers=api["as"]("supervisor"),
        json={"station_id": str(fixtures["station"].id), "new_job_order_id": str(second.id)},
    )
    assert response.status_code == 200, response.text
    new_session = await session.get(JobSession, uuid.UUID(response.json()["job_session_id"]))
    assert new_session.lot_id is not None
    assert new_session.routing_operation_id is not None


async def test_a_refused_change_job_leaves_the_running_job_untouched(api, session, fixtures):
    job = await _job(session, fixtures)
    await _start_shift(api, fixtures, job)

    response = await api["client"].post(
        "/api/v1/change-job",
        headers=api["as"]("supervisor"),
        json={"station_id": str(fixtures["station"].id), "new_job_order_id": str(uuid.uuid4())},
    )
    assert response.status_code == 404
    running = await session.scalar(
        select(func.count(JobSession.id)).where(
            JobSession.station_id == fixtures["station"].id, JobSession.ended_at.is_(None)
        )
    )
    assert running == 1


# ---------------------------------------------------------------------------
# Actions — the board loads, the lifecycle is enforced
# ---------------------------------------------------------------------------


async def _action(api, fixtures, **extra) -> dict:
    response = await api["client"].post(
        "/api/v1/actions",
        headers=api["as"]("supervisor"),
        json={"title": "Eliminate short-staging on Line 2", **extra},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_the_board_loads_with_a_cancelled_action_on_it(api, fixtures):
    action = await _action(api, fixtures)
    cancel = await api["client"].patch(
        f"/api/v1/actions/{action['id']}",
        headers=api["as"]("supervisor"),
        json={"state": "cancelled"},
    )
    assert cancel.status_code == 200, cancel.text

    board = await api["client"].get("/api/v1/actions", headers=api["as"]("maintenance"))
    assert board.status_code == 200
    assert any(a["state"] == "cancelled" for a in board.json())

    single = await api["client"].get(
        f"/api/v1/actions/{action['id']}", headers=api["as"]("maintenance")
    )
    assert single.status_code == 200
    assert single.json()["allowed_transitions"] == []  # only a lead may restore it


async def test_nobody_can_declare_an_action_verified(api, fixtures):
    action = await _action(api, fixtures)
    for role in ("stores", "owner"):
        response = await api["client"].patch(
            f"/api/v1/actions/{action['id']}",
            headers=api["as"](role),
            json={"state": "verified"},
        )
        assert response.status_code == 409


async def test_an_assignee_moves_work_along_but_cannot_cancel_it(api, fixtures):
    action = await _action(api, fixtures)
    client = api["client"]
    moved = await client.patch(
        f"/api/v1/actions/{action['id']}", headers=api["as"]("stores"), json={"state": "in_progress"}
    )
    assert moved.status_code == 200
    assert "cancelled" not in moved.json()["allowed_transitions"]

    refused = await client.patch(
        f"/api/v1/actions/{action['id']}", headers=api["as"]("stores"), json={"state": "cancelled"}
    )
    assert refused.status_code == 409


async def test_closing_needs_real_memory_answers_and_the_root_cause_when_routed(
    api, session, fixtures
):
    action = await _action(api, fixtures)
    client = api["client"]

    blank = await client.post(
        f"/api/v1/actions/{action['id']}/close",
        headers=api["as"]("maintenance"),
        json={"countermeasure": "", "memory": {"what_fixed_it": ""}},
    )
    assert blank.status_code == 422

    row = await session.get(Action, uuid.UUID(action["id"]))
    row.routed_to_rca = True
    await session.flush()
    no_cause = await client.post(
        f"/api/v1/actions/{action['id']}/close",
        headers=api["as"]("maintenance"),
        json={
            "countermeasure": "Kitting list signed at 6:45",
            "memory": {"what_fixed_it": "Stores kits the job the night before"},
        },
    )
    assert no_cause.status_code == 409

    closed = await client.post(
        f"/api/v1/actions/{action['id']}/close",
        headers=api["as"]("maintenance"),
        json={
            "countermeasure": "Kitting list signed at 6:45",
            "root_cause": "Job released after Stores' last pick run",
            "memory": {"what_fixed_it": "Stores kits the job the night before"},
        },
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["state"] == PdcaState.MONITORING.value


async def test_a_manual_action_refuses_another_plants_station(api, fixtures):
    response = await api["client"].post(
        "/api/v1/actions",
        headers=api["as"]("supervisor"),
        json={"title": "Cross-plant", "station_id": str(api["foreign_station"].id)},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Escalations — a fired rung can be answered, and a running machine resolves it
# ---------------------------------------------------------------------------


async def test_a_fired_escalation_stops_blocking_once_the_machine_runs(api, session, fixtures):
    shift = await _start_shift(api, fixtures, await _job(session, fixtures))
    client = api["client"]
    started = datetime.now(UTC) - timedelta(minutes=20)
    stop = (
        await client.post(
            "/api/v1/downtime/start",
            headers=api["as"]("supervisor"),
            json={
                "station_id": str(fixtures["station"].id),
                "reason_code_id": str(fixtures["reasons"]["TOOL_BREAKAGE"].id),
                "started_at": started.isoformat(),
            },
        )
    ).json()

    rung = await session.scalar(
        select(Escalation).where(
            Escalation.downtime_event_id == uuid.UUID(stop["id"]), Escalation.level == 2
        )
    )
    rung.status = EscalationStatus.SENT
    rung.channel = EscalationChannel.IN_APP
    rung.sent_at = started + timedelta(minutes=15)
    await session.flush()

    listed = await client.get("/api/v1/escalations", headers=api["as"]("maintenance"))
    assert [e["id"] for e in listed.json()] == [str(rung.id)]

    closed = await client.post(
        f"/api/v1/downtime/{stop['id']}/close", headers=api["as"]("supervisor"), json={}
    )
    assert closed.status_code == 200
    await session.refresh(rung)
    assert rung.resolved_at is not None

    check = (
        await client.get(
            f"/api/v1/shifts/{shift['id']}/closure-check", headers=api["as"]("supervisor")
        )
    ).json()
    assert not any(b["kind"] == "open_escalation" for b in check["blockers"])


async def test_an_escalation_can_be_acknowledged_by_name(api, session, fixtures):
    await _start_shift(api, fixtures, await _job(session, fixtures))
    stop = (
        await api["client"].post(
            "/api/v1/downtime/start",
            headers=api["as"]("supervisor"),
            json={"station_id": str(fixtures["station"].id)},
        )
    ).json()
    rung = await session.scalar(
        select(Escalation).where(
            Escalation.downtime_event_id == uuid.UUID(stop["id"]), Escalation.level == 2
        )
    )
    pending = await api["client"].post(
        f"/api/v1/escalations/{rung.id}/acknowledge", headers=api["as"]("maintenance")
    )
    assert pending.status_code == 409  # not sent yet

    rung.status = EscalationStatus.SENT
    await session.flush()
    acked = await api["client"].post(
        f"/api/v1/escalations/{rung.id}/acknowledge", headers=api["as"]("maintenance")
    )
    assert acked.status_code == 200
    assert acked.json()["acknowledged_by_name"].startswith("Maintenance")
    assert acked.json()["is_open"] is False


# ---------------------------------------------------------------------------
# Capture validation and quality
# ---------------------------------------------------------------------------


async def test_a_stop_cannot_start_in_the_future(api, session, fixtures):
    await _start_shift(api, fixtures, await _job(session, fixtures))
    response = await api["client"].post(
        "/api/v1/downtime/start",
        headers=api["as"]("supervisor"),
        json={
            "station_id": str(fixtures["station"].id),
            "started_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
        },
    )
    assert response.status_code == 422


async def test_a_batch_with_scrap_and_rework_is_recorded_and_costed(api, session, fixtures):
    await _start_shift(api, fixtures, await _job(session, fixtures))
    client = api["client"]
    await client.post(
        "/api/v1/production",
        headers=api["as"]("supervisor"),
        json={"station_id": str(fixtures["station"].id), "count": 374},
    )
    response = await client.post(
        "/api/v1/quality",
        headers=api["as"]("quality"),
        json={
            "part_id": str(fixtures["part"].id),
            "station_id": str(fixtures["station"].id),
            "good_count": 362,
            "reject_count": 9,
            "rework_count": 4,
            "rework_minutes": 60,
            "disposition": "scrap",
            "defects": [{"code": "BURR", "count": 9}],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scrap_cost_paise"] > 0
    # (60/60) x (420 + 180 + 210) = ₹810 at CNC-3's burden rate.
    assert body["rework_cost_paise"] == rupees(810)
    assert body["reconciliation_flagged"] is False


async def test_quality_refuses_unknown_defects_and_impossible_rework(api, session, fixtures):
    await _start_shift(api, fixtures, await _job(session, fixtures))
    base = {"part_id": str(fixtures["part"].id), "good_count": 10, "reject_count": 1}
    client = api["client"]

    unknown = await client.post(
        "/api/v1/quality",
        headers=api["as"]("quality"),
        json={**base, "defects": [{"code": "NOT_A_CODE", "count": 1}]},
    )
    assert unknown.status_code == 422

    too_much = await client.post(
        "/api/v1/quality", headers=api["as"]("quality"), json={**base, "rework_count": 11}
    )
    assert too_much.status_code == 422


async def test_the_second_batch_of_a_healthy_shift_is_not_flagged(api, session, fixtures):
    """§6: counts and inspections reconcile cumulatively, batch after batch."""
    await _start_shift(api, fixtures, await _job(session, fixtures))
    client = api["client"]
    station = str(fixtures["station"].id)

    async def count(n):
        await client.post(
            "/api/v1/production", headers=api["as"]("supervisor"), json={"station_id": station, "count": n}
        )

    async def inspect(good, rejected):
        return (
            await client.post(
                "/api/v1/quality",
                headers=api["as"]("quality"),
                json={
                    "part_id": str(fixtures["part"].id),
                    "station_id": station,
                    "good_count": good,
                    "reject_count": rejected,
                },
            )
        ).json()

    await count(374)
    assert (await inspect(362, 9))["reconciliation_flagged"] is False
    await count(300)
    second = await inspect(290, 8)
    assert second["reconciliation_flagged"] is False
    assert second["reconciliation_gap"] == 5

    padded = await inspect(200, 0)  # 200 inspected parts nobody counted
    assert padded["reconciliation_flagged"] is True


# ---------------------------------------------------------------------------
# Administration, job orders and master data
# ---------------------------------------------------------------------------


async def test_a_supervisor_creates_and_repeats_todays_job_orders(api, fixtures):
    client = api["client"]
    created = await client.post(
        "/api/v1/masters/job-orders",
        headers=api["as"]("supervisor"),
        json={"part_id": str(fixtures["part"].id), "planned_quantity": 380},
    )
    assert created.status_code == 201, created.text
    job = created.json()
    assert job["customer_name"] == "Rane TRW"  # inherited from the part
    assert job["job_number"].startswith("JO-")

    repeat = await client.post(
        f"/api/v1/masters/job-orders/{job['id']}/repeat",
        headers=api["as"]("supervisor"),
        json={},
    )
    assert repeat.status_code == 201
    assert repeat.json()["job_number"] != job["job_number"]

    listed = await client.get("/api/v1/masters/job-orders", headers=api["as"]("supervisor"))
    assert {job["id"], repeat.json()["id"]} <= {j["id"] for j in listed.json()}


async def test_only_the_owner_configures_stations_and_bottleneck_factors(api, session, fixtures):
    client = api["client"]
    payload = {
        "code": "VMC-1",
        "name": "VMC 1",
        "section": "machining",
        "bottleneck_factor": 0.5,
        "cost_family_code": "CNC-family",
    }
    refused = await client.post(
        "/api/v1/masters/stations", headers=api["as"]("supervisor"), json=payload
    )
    assert refused.status_code == 403

    created = await client.post(
        "/api/v1/masters/stations", headers=api["as"]("owner"), json=payload
    )
    assert created.status_code == 201, created.text
    assert created.json()["bottleneck_factor"] == 0.5
    families = await session.scalar(
        select(func.count(CostFamily.id)).where(CostFamily.plant_id == fixtures["plant"].id)
    )
    assert families == 1  # reused the existing family, did not duplicate it


async def test_part_margins_are_the_owners_alone(api, fixtures):
    client = api["client"]
    assert (
        await client.get("/api/v1/masters/parts?detail=true", headers=api["as"]("supervisor"))
    ).status_code == 403
    detail = await client.get("/api/v1/masters/parts?detail=true", headers=api["as"]("owner"))
    assert detail.json()[0]["contribution_margin_rupees"] == 38.0


async def test_an_owner_adds_people_but_cannot_grant_platform_roles(api, fixtures):
    client = api["client"]
    consultant = await client.post(
        "/api/v1/admin/users",
        headers=api["as"]("owner"),
        json={"full_name": "X", "phone": "+919876500001", "password": "long-enough", "roles": ["consultant"]},
    )
    assert consultant.status_code == 403

    supervisor = await client.post(
        "/api/v1/admin/users",
        headers=api["as"]("owner"),
        json={"full_name": "Murugan V.", "phone": "+919876500002", "password": "long-enough", "roles": ["supervisor"]},
    )
    assert supervisor.status_code == 201, supervisor.text
    assert supervisor.json()["roles"] == ["supervisor"]

    owner_id = api["tokens"]["owner"]["user_id"]
    lockout = await client.patch(
        f"/api/v1/admin/users/{owner_id}", headers=api["as"]("owner"), json={"roles": ["supervisor"]}
    )
    assert lockout.status_code == 409


# ---------------------------------------------------------------------------
# Money is the owner's; reads never write
# ---------------------------------------------------------------------------


async def test_owner_money_reports_are_closed_to_the_shopfloor(api, fixtures):
    client = api["client"]
    for path in ("/api/v1/reports/monthly", "/api/v1/reports/roles/executive", "/api/v1/reports/trend"):
        assert (await client.get(path, headers=api["as"]("maintenance"))).status_code == 403
    assert (
        await client.get("/api/v1/reports/roles/maintenance", headers=api["as"]("maintenance"))
    ).status_code == 200


async def test_reading_the_audit_and_coach_screens_writes_nothing(api, session, fixtures):
    client = api["client"]
    compliance = await client.get("/api/v1/plugins/compliance", headers=api["as"]("quality"))
    assert compliance.status_code == 200
    clauses = await session.scalar(
        select(func.count(ComplianceClause.id)).where(
            ComplianceClause.plant_id == fixtures["plant"].id
        )
    )
    assert clauses == 0

    coach = await client.get("/api/v1/ai/coach/today", headers=api["as"]("supervisor"))
    assert coach.status_code == 404

    refreshed = await client.post("/api/v1/plugins/compliance/refresh", headers=api["as"]("quality"))
    assert refreshed.status_code == 200
    assert refreshed.json()["clauses"]


async def test_sensor_ingest_refuses_a_missing_or_wrong_token(api, fixtures, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "mqtt_enabled", True)
    monkeypatch.setattr(settings, "ingest_token", "device-secret")
    url = f"/api/v1/ingest/{fixtures['plant'].code}/telemetry"
    body = {"station_code": "CNC-3", "kind": "production", "value": 5}

    assert (await api["client"].post(url, json=body)).status_code == 401
    assert (
        await api["client"].post(url, json=body, headers={"X-Ingest-Token": "guess"})
    ).status_code == 401
    ok = await api["client"].post(url, json=body, headers={"X-Ingest-Token": "device-secret"})
    assert ok.status_code == 200
