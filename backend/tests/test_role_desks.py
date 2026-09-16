"""The two roles §1 names but nothing served: Maintenance and Stores.

§1's "Who uses it" table is the contract these tests hold the product to:

    Maintenance In-charge | Receives escalation alerts. Logs what was done to
                            fix breakdowns.      | When events occur
    Stores In-charge      | Receives material-shortage alerts. Confirms
                            material staging.    | When events occur

Four duties. Each one has a test here, plus the routing rule underneath them
both — §3 Layer 2's "the system picks the recipient from the reason code" —
because a shared inbox is not a routed one, and the whole point of the ladder
is that the right person is told.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.kernel import rupees
from app.models import (
    DowntimeEvent,
    DowntimeStatus,
    Escalation,
    EscalationChannel,
    EscalationStatus,
    JobOrder,
    JobOrderStatus,
    MaterialStaging,
    ShiftSessionStatus,
    StagingStatus,
)


async def _job(session, fixtures, number: str = "2231") -> JobOrder:
    job = JobOrder(
        plant_id=fixtures["plant"].id,
        job_number=number,
        part_id=fixtures["part"].id,
        customer_name="Rane TRW",
        planned_quantity=500,
        status=JobOrderStatus.RUNNING,
    )
    session.add(job)
    await session.flush()
    return job


async def _start_shift(api, fixtures, job: JobOrder | None = None):
    assignments = (
        [
            {
                "station_id": str(fixtures["station"].id),
                "job_order_id": str(job.id),
                "lot_code": f"LOT-{uuid.uuid4().hex[:6]}",
            }
        ]
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


async def _stop(api, fixtures, reason_code: str):
    response = await api["client"].post(
        "/api/v1/downtime/start",
        headers=api["as"]("supervisor"),
        json={
            "station_id": str(fixtures["station"].id),
            "reason_code_id": str(fixtures["reasons"][reason_code].id),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _fire(session, event_id: uuid.UUID, level: int = 2) -> Escalation:
    """Mark a scheduled rung as actually sent — the worker's job, done inline."""
    escalation = await session.scalar(
        select(Escalation).where(
            Escalation.downtime_event_id == event_id, Escalation.level == level
        )
    )
    escalation.status = EscalationStatus.SENT
    escalation.channel = EscalationChannel.WHATSAPP
    escalation.sent_at = datetime.now(UTC)
    escalation.cost_at_send = rupees(1050)
    await session.flush()
    return escalation


# ---------------------------------------------------------------------------
# §3 Layer 2 — "the system picks the recipient from the reason code"
# ---------------------------------------------------------------------------


async def test_the_ladder_records_which_role_each_rung_was_aimed_at(
    api, session, fixtures
):
    """The route is stored, not just the person's name.

    Without it an inbox cannot tell a breakdown from a material wait, and the
    routing the reason code performed is lost the day the recipient list is
    edited.
    """
    await _start_shift(api, fixtures)
    event = await _stop(api, fixtures, "TOOL_BREAKAGE")

    rungs = (
        await session.scalars(
            select(Escalation)
            .where(Escalation.downtime_event_id == uuid.UUID(event["id"]))
            .order_by(Escalation.level)
        )
    ).all()

    # §3 Layer 2's ladder: 5 min to the supervisor already on the screen, 15 to
    # the role the reason code names, 30 to the plant head with the cost.
    assert [r.level for r in rungs] == [1, 2, 3]
    assert [r.route for r in rungs] == ["supervisor", "maintenance", "plant_head"]


async def test_a_material_wait_routes_to_stores_not_maintenance(api, session, fixtures):
    await _start_shift(api, fixtures)
    event = await _stop(api, fixtures, "MATERIAL_WAIT")

    level_two = await session.scalar(
        select(Escalation).where(
            Escalation.downtime_event_id == uuid.UUID(event["id"]), Escalation.level == 2
        )
    )
    assert level_two.route == "stores"


async def test_each_desk_sees_only_the_alerts_addressed_to_it(api, session, fixtures):
    """The failure this pins: one shared inbox for the whole plant.

    Maintenance must not have to read past Stores' material waits to find the
    breakdown that is theirs, and Stores must not be shown a tool breakage it
    can do nothing about.
    """
    await _start_shift(api, fixtures)

    breakdown = await _stop(api, fixtures, "TOOL_BREAKAGE")
    await _fire(session, uuid.UUID(breakdown["id"]))
    await api["client"].post(
        f"/api/v1/downtime/{breakdown['id']}/close",
        headers=api["as"]("supervisor"),
        json={},
    )
    # Closing settles that event's rungs, so raise a second, still-open one.
    wait = await _stop(api, fixtures, "MATERIAL_WAIT")
    await _fire(session, uuid.UUID(wait["id"]))

    stores = (
        await api["client"].get("/api/v1/stores/desk", headers=api["as"]("stores"))
    ).json()
    assert [a["route"] for a in stores["alerts"]] == ["stores"]
    assert stores["alerts"][0]["downtime_event_id"] == wait["id"]

    maintenance = (
        await api["client"].get(
            "/api/v1/maintenance/desk", headers=api["as"]("maintenance")
        )
    ).json()
    # The breakdown's rung was resolved when the machine restarted, so the
    # maintenance inbox is correctly empty — and, crucially, does not contain
    # the stores alert that is live right now.
    assert all(a["route"] == "maintenance" for a in maintenance["alerts"])


async def test_mine_filter_narrows_the_escalation_list_to_the_signed_in_role(
    api, session, fixtures
):
    await _start_shift(api, fixtures)
    wait = await _stop(api, fixtures, "MATERIAL_WAIT")
    await _fire(session, uuid.UUID(wait["id"]))

    everything = (
        await api["client"].get(
            "/api/v1/escalations?open=true", headers=api["as"]("maintenance")
        )
    ).json()
    mine = (
        await api["client"].get(
            "/api/v1/escalations?open=true&mine=true", headers=api["as"]("maintenance")
        )
    ).json()

    assert any(e["route"] == "stores" for e in everything)
    assert all(e["route"] == "maintenance" for e in mine)


# ---------------------------------------------------------------------------
# §1, Maintenance — "Receives escalation alerts"
# ---------------------------------------------------------------------------


async def test_the_maintenance_desk_ranks_live_stops_by_rupees_not_by_time(
    api, session, fixtures
):
    """§3 Layer 2: the plant "always knows the single most valuable thing to fix"."""
    job = await _job(session, fixtures)
    await _start_shift(api, fixtures, job)
    event = await _stop(api, fixtures, "TOOL_BREAKAGE")

    # Back-date the stop so it has a real ticker rather than zero minutes.
    row = await session.get(DowntimeEvent, uuid.UUID(event["id"]))
    row.started_at = datetime.now(UTC) - timedelta(minutes=47)
    await session.flush()

    desk = (
        await api["client"].get(
            "/api/v1/maintenance/desk", headers=api["as"]("maintenance")
        )
    ).json()

    assert len(desk["open_breakdowns"]) == 1
    stop = desk["open_breakdowns"][0]
    assert stop["station_code"] == "CNC-3"
    # It is on this desk because its reason code routes to maintenance — not
    # because of ``is_breakdown``, which §8.1 reserves for MTBF/MTTR and the
    # default library sets on 'Machine Breakdown' alone. A tool breakage is
    # maintenance's work (§6's own Priority 1 assigns one to Suresh K.) while
    # correctly not counting as a machine breakdown.
    assert stop["reason_code"] == "TOOL_BREAKAGE"
    assert stop["is_breakdown"] is False
    assert stop["minutes"] == 47
    # The same kernel the supervisor's grid and the shift report use (§8.1's
    # build rule) — never a second implementation of the cost formula.
    assert stop["cost_paise"] > 0
    assert desk["open_cost_paise"] == stop["cost_paise"]
    assert desk["open_cost_display"].startswith("₹")


async def test_a_material_wait_is_not_listed_as_a_breakdown(api, session, fixtures):
    """MTBF/MTTR read ``is_breakdown``; this list must read the same flag."""
    await _start_shift(api, fixtures)
    await _stop(api, fixtures, "MATERIAL_WAIT")

    desk = (
        await api["client"].get(
            "/api/v1/maintenance/desk", headers=api["as"]("maintenance")
        )
    ).json()
    assert desk["open_breakdowns"] == []
    assert [s["reason_code"] for s in desk["other_open_stops"]] == ["MATERIAL_WAIT"]


# ---------------------------------------------------------------------------
# §1, Maintenance — "Logs what was done to fix breakdowns"
# ---------------------------------------------------------------------------


async def test_maintenance_logs_what_fixed_the_breakdown(api, session, fixtures):
    await _start_shift(api, fixtures)
    event = await _stop(api, fixtures, "TOOL_BREAKAGE")

    response = await api["client"].post(
        f"/api/v1/maintenance/breakdowns/{event['id']}/repair",
        headers=api["as"]("maintenance"),
        json={"note": "Replaced the insert on Station 3", "part_changed": "CNMG 120408 insert"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["repair_note"] == "Replaced the insert on Station 3"
    assert body["repair_part_changed"] == "CNMG 120408 insert"
    assert body["repaired_at"] is not None
    assert body["repaired_by_name"]

    row = await session.get(DowntimeEvent, uuid.UUID(event["id"]))
    assert row.repaired_by_id == api["tokens"]["maintenance"]["user_id"]


async def test_the_repair_note_never_touches_the_shifts_arithmetic(
    api, session, fixtures
):
    """A repair note is testimony beside the record, not an edit to it.

    §2's whole case for trusting manual data is that a closed figure cannot be
    quietly restated. If writing 'changed the bearing' could move a cost, the
    note would be an edit channel wearing a different name.
    """
    job = await _job(session, fixtures)
    await _start_shift(api, fixtures, job)
    event = await _stop(api, fixtures, "TOOL_BREAKAGE")
    await api["client"].post(
        f"/api/v1/downtime/{event['id']}/close", headers=api["as"]("supervisor"), json={}
    )

    row = await session.get(DowntimeEvent, uuid.UUID(event["id"]))
    before = (row.cost_total, row.duration_minutes, row.reason_code_id, row.ended_at)

    response = await api["client"].post(
        f"/api/v1/maintenance/breakdowns/{event['id']}/repair",
        headers=api["as"]("maintenance"),
        json={"note": "Tightened the collet"},
    )
    assert response.status_code == 200, response.text

    await session.refresh(row)
    assert (row.cost_total, row.duration_minutes, row.reason_code_id, row.ended_at) == before


async def test_a_repair_can_be_logged_while_the_machine_is_still_down(
    api, session, fixtures
):
    """The fitter writes it at the machine, not after the supervisor's tap.

    Making them wait for 'Running' is how the note stops being written at all.
    """
    await _start_shift(api, fixtures)
    event = await _stop(api, fixtures, "TOOL_BREAKAGE")

    response = await api["client"].post(
        f"/api/v1/maintenance/breakdowns/{event['id']}/repair",
        headers=api["as"]("maintenance"),
        json={"note": "Fitting a new insert now"},
    )
    assert response.status_code == 200, response.text

    row = await session.get(DowntimeEvent, uuid.UUID(event["id"]))
    assert row.status is DowntimeStatus.OPEN
    assert row.repair_note == "Fitting a new insert now"


async def test_a_closed_shift_refuses_a_repair_note(api, session, fixtures):
    """§2's Shift Closure Lock — "the data locks permanently"."""
    await _start_shift(api, fixtures)
    event = await _stop(api, fixtures, "TOOL_BREAKAGE")
    await api["client"].post(
        f"/api/v1/downtime/{event['id']}/close", headers=api["as"]("supervisor"), json={}
    )

    row = await session.get(DowntimeEvent, uuid.UUID(event["id"]))
    from app.models import ShiftSession

    shift_session = await session.get(ShiftSession, row.shift_session_id)
    shift_session.status = ShiftSessionStatus.CLOSED
    await session.flush()

    response = await api["client"].post(
        f"/api/v1/maintenance/breakdowns/{event['id']}/repair",
        headers=api["as"]("maintenance"),
        json={"note": "Too late"},
    )
    assert response.status_code == 409
    assert "correction" in response.json()["detail"].lower()


async def test_breakdowns_closed_without_a_note_are_surfaced_for_write_up(
    api, session, fixtures
):
    """§3 Layer 4 — knowledge that leaves with whoever fixed it is the loss."""
    await _start_shift(api, fixtures)
    event = await _stop(api, fixtures, "TOOL_BREAKAGE")
    await api["client"].post(
        f"/api/v1/downtime/{event['id']}/close", headers=api["as"]("supervisor"), json={}
    )

    desk = (
        await api["client"].get(
            "/api/v1/maintenance/desk", headers=api["as"]("maintenance")
        )
    ).json()
    assert [b["event_id"] for b in desk["awaiting_repair_note"]] == [event["id"]]

    await api["client"].post(
        f"/api/v1/maintenance/breakdowns/{event['id']}/repair",
        headers=api["as"]("maintenance"),
        json={"note": "New insert fitted"},
    )

    desk = (
        await api["client"].get(
            "/api/v1/maintenance/desk", headers=api["as"]("maintenance")
        )
    ).json()
    assert desk["awaiting_repair_note"] == []


async def test_an_empty_repair_note_is_refused(api, session, fixtures):
    await _start_shift(api, fixtures)
    event = await _stop(api, fixtures, "TOOL_BREAKAGE")

    response = await api["client"].post(
        f"/api/v1/maintenance/breakdowns/{event['id']}/repair",
        headers=api["as"]("maintenance"),
        json={"note": "   "},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# §1, Stores — "Confirms material staging"
# ---------------------------------------------------------------------------


async def test_the_staging_queue_needs_no_row_to_be_created_for_stores_to_fill(
    api, session, fixtures
):
    """§4.3's 15-minute budget — no plugin or desk may add a daily register.

    The queue is today's job orders, which the supervisor already creates from
    the production plan. A plant that never opens this screen is left exactly
    as it was, with no half-filled table behind it.
    """
    job = await _job(session, fixtures, "9001")
    await _start_shift(api, fixtures, job)

    desk = (
        await api["client"].get("/api/v1/stores/desk", headers=api["as"]("stores"))
    ).json()

    row = next(r for r in desk["queue"] if r["job_number"] == "9001")
    assert row["status"] == "pending"
    assert row["part_number"] == "AK-447"
    assert row["station_codes"] == ["CNC-3"]
    assert desk["pending_count"] >= 1

    stored = await session.scalar(
        select(MaterialStaging).where(MaterialStaging.job_order_id == job.id)
    )
    assert stored is None  # nothing was written just by looking


async def test_stores_confirms_staging_in_one_tap(api, session, fixtures):
    job = await _job(session, fixtures, "9002")
    await _start_shift(api, fixtures, job)

    response = await api["client"].post(
        "/api/v1/stores/staging",
        headers=api["as"]("stores"),
        json={"job_order_id": str(job.id), "status": "staged"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "staged"
    # Staged with no number means the whole planned quantity is there.
    assert body["quantity_staged"] == 500
    assert body["quantity_required"] == 500
    assert body["confirmed_by_name"]


async def test_a_short_stage_must_say_how_much_is_actually_there(
    api, session, fixtures
):
    """'Short' without a number is a complaint, not a record §5.2 can count."""
    job = await _job(session, fixtures, "9003")
    await _start_shift(api, fixtures, job)

    response = await api["client"].post(
        "/api/v1/stores/staging",
        headers=api["as"]("stores"),
        json={"job_order_id": str(job.id), "status": "short"},
    )
    assert response.status_code == 400
    assert "how much" in response.json()["detail"].lower()

    response = await api["client"].post(
        "/api/v1/stores/staging",
        headers=api["as"]("stores"),
        json={
            "job_order_id": str(job.id),
            "status": "short",
            "quantity_staged": 320,
            "note": "180 short, arriving 09:30",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["quantity_staged"] == 320
    assert response.json()["note"] == "180 short, arriving 09:30"


async def test_stores_correcting_itself_updates_the_answer_rather_than_adding_one(
    api, session, fixtures
):
    """One standing answer per job per day.

    Two rows could only ever disagree, and the morning meeting would then have
    two material stories for the same line.
    """
    job = await _job(session, fixtures, "9004")
    await _start_shift(api, fixtures, job)

    await api["client"].post(
        "/api/v1/stores/staging",
        headers=api["as"]("stores"),
        json={"job_order_id": str(job.id), "status": "short", "quantity_staged": 200},
    )
    await api["client"].post(
        "/api/v1/stores/staging",
        headers=api["as"]("stores"),
        json={"job_order_id": str(job.id), "status": "staged"},
    )

    rows = (
        await session.scalars(
            select(MaterialStaging).where(MaterialStaging.job_order_id == job.id)
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].status is StagingStatus.STAGED


async def test_the_staged_quantity_freezes_what_stores_was_asked_for(
    api, session, fixtures
):
    """Re-planning the job this afternoon must not rewrite this morning's ask.

    The same freeze-what-you-were-asked discipline §5.6 applies to savings
    baselines: a number that moves under you is a number nobody can be held to.
    """
    job = await _job(session, fixtures, "9005")
    await _start_shift(api, fixtures, job)

    await api["client"].post(
        "/api/v1/stores/staging",
        headers=api["as"]("stores"),
        json={"job_order_id": str(job.id), "status": "staged"},
    )

    job.planned_quantity = 900
    await session.flush()

    row = await session.scalar(
        select(MaterialStaging).where(MaterialStaging.job_order_id == job.id)
    )
    assert row.quantity_required == 500


# ---------------------------------------------------------------------------
# §1, Stores — "Receives material-shortage alerts"
# ---------------------------------------------------------------------------


async def test_a_line_stopped_on_material_reaches_stores_before_the_ladder_does(
    api, session, fixtures
):
    """§6, 10:40 AM — arriving before minute 15 is the point of the screen."""
    job = await _job(session, fixtures, "9006")
    await _start_shift(api, fixtures, job)
    await _stop(api, fixtures, "MATERIAL_WAIT")

    desk = (
        await api["client"].get("/api/v1/stores/desk", headers=api["as"]("stores"))
    ).json()

    assert len(desk["open_waits"]) == 1
    assert desk["open_waits"][0]["station_code"] == "CNC-3"
    # No rung has fired yet — the line is on the screen regardless.
    assert desk["alerts"] == []


async def test_the_queue_puts_the_job_that_is_already_costing_money_first(
    api, session, fixtures
):
    """What a person standing at a stores counter should deal with first."""
    await _job(session, fixtures, "9007")  # quiet: no waits against it today
    costing = await _job(session, fixtures, "9008")
    await _start_shift(api, fixtures, costing)

    event = await _stop(api, fixtures, "MATERIAL_WAIT")
    row = await session.get(DowntimeEvent, uuid.UUID(event["id"]))
    row.started_at = datetime.now(UTC) - timedelta(minutes=31)
    await session.flush()
    await api["client"].post(
        f"/api/v1/downtime/{event['id']}/close", headers=api["as"]("supervisor"), json={}
    )

    desk = (
        await api["client"].get("/api/v1/stores/desk", headers=api["as"]("stores"))
    ).json()
    numbers = [r["job_number"] for r in desk["queue"]]
    assert numbers.index("9008") < numbers.index("9007")

    costing_row = next(r for r in desk["queue"] if r["job_number"] == "9008")
    assert costing_row["wait_events"] == 1
    assert costing_row["wait_cost_paise"] > 0
    assert desk["wait_cost_today_paise"] == costing_row["wait_cost_paise"]


# ---------------------------------------------------------------------------
# Role separation — a desk is not a public page
# ---------------------------------------------------------------------------


async def test_quality_cannot_open_either_desk(api, session, fixtures):
    """§7: "home screens are role-based". Hiding a link is not a lock."""
    for path in ("/api/v1/maintenance/desk", "/api/v1/stores/desk"):
        response = await api["client"].get(path, headers=api["as"]("quality"))
        assert response.status_code == 403, f"{path} -> {response.status_code}"


async def test_stores_cannot_write_a_repair_note_and_maintenance_cannot_stage(
    api, session, fixtures
):
    """Each role writes only what §1 gives it, which is what makes the record
    worth anything: 'Stores confirmed it' has to mean Stores confirmed it."""
    await _start_shift(api, fixtures)
    event = await _stop(api, fixtures, "TOOL_BREAKAGE")
    job = await _job(session, fixtures, "9009")

    response = await api["client"].post(
        f"/api/v1/maintenance/breakdowns/{event['id']}/repair",
        headers=api["as"]("stores"),
        json={"note": "Not mine to write"},
    )
    assert response.status_code == 403

    response = await api["client"].post(
        "/api/v1/stores/staging",
        headers=api["as"]("maintenance"),
        json={"job_order_id": str(job.id), "status": "staged"},
    )
    assert response.status_code == 403


async def test_a_job_order_from_another_plant_is_refused(api, session, fixtures):
    """An id in a request body is an easier leak than a missing WHERE."""
    response = await api["client"].post(
        "/api/v1/stores/staging",
        headers=api["as"]("stores"),
        json={"job_order_id": str(uuid.uuid4()), "status": "staged"},
    )
    assert response.status_code == 404
