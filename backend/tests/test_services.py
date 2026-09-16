"""Integration tests for the service layer, against a real database.

These cover the behaviours that make or break trust in the product: that a
costed event freezes its inputs, that a locked shift cannot be edited, that
Actions are only born when they have earned it, and that Recovered Profit is
measured conservatively.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.kernel import rupees
from app.models import (
    CostRate,
    DataSource,
    PdcaState,
    RateSource,
    ShiftSessionStatus,
)
from app.services import actions as action_service
from app.services import capture as capture_service
from app.services import costing as costing_service
from app.services import downtime as downtime_service
from app.services import shifts as shift_service
from tests.conftest import run_job

NOW = datetime(2026, 9, 12, 8, 0, tzinfo=UTC)


async def open_shift(session, fixtures, *, with_job: bool = False):
    shift_session = await shift_service.start_shift(
        session,
        plant=fixtures["plant"],
        shift=fixtures["shift"],
        supervisor_id=None,
        started_at=NOW,
    )
    if with_job:
        await run_job(session, fixtures, shift_session)
    return shift_session


# ---------------------------------------------------------------------------
# Costing — the frozen snapshot (ARCHITECTURE.md C3)
# ---------------------------------------------------------------------------


async def test_rates_resolve_from_the_cost_family(session, fixtures):
    snapshot, trace = await costing_service.resolve_snapshot(
        session,
        plant=fixtures["plant"],
        station=fixtures["station"],
        part=fixtures["part"],
        on_date=date.today(),
    )
    assert snapshot.rates.machine_rate == rupees(420)
    assert snapshot.rates.labour_rate == rupees(180)
    assert snapshot.rates.overhead_rate == rupees(210)
    assert snapshot.part.cycle_time_min == Decimal("2.4")
    assert snapshot.part.contribution_margin == rupees(38)
    assert trace.warnings == ()


async def test_a_costed_event_freezes_its_inputs_on_the_row(session, fixtures):
    """History never restates. The row carries the 'why' behind its total."""
    shift_session = await open_shift(session, fixtures, with_job=True)
    event = await downtime_service.open_downtime(
        session,
        plant=fixtures["plant"],
        station=fixtures["station"],
        reason_code=fixtures["reasons"]["TOOL_BREAKAGE"],
        shift_session=shift_session,
        started_at=NOW,
    )
    await downtime_service.close_downtime(
        session, event=event, ended_at=NOW + timedelta(minutes=47)
    )

    assert event.snapshot_machine_rate == rupees(420)
    assert event.snapshot_cycle_time_min == Decimal("2.4000")
    assert event.snapshot_contribution_margin == rupees(38)
    assert event.formula_version == 1
    assert event.cost_total == rupees("1356.50")
    assert event.cost_breakdown["parts_not_made"] == 19


async def test_a_later_rate_change_does_not_restate_a_past_event(session, fixtures):
    """If the owner corrects a rate in week 6, yesterday's ₹9,380 must not
    silently become ₹11,200 — that is how trust dies (ARCHITECTURE.md C3)."""
    shift_session = await open_shift(session, fixtures, with_job=True)
    event = await downtime_service.open_downtime(
        session,
        plant=fixtures["plant"],
        station=fixtures["station"],
        reason_code=fixtures["reasons"]["TOOL_BREAKAGE"],
        shift_session=shift_session,
        started_at=NOW,
    )
    await downtime_service.close_downtime(
        session, event=event, ended_at=NOW + timedelta(minutes=47)
    )
    original = event.cost_total

    family_ref = costing_service._family_ref(fixtures["station"])
    session.add(
        CostRate(
            plant_id=fixtures["plant"].id,
            scope="cost_family",
            scope_ref=family_ref,
            machine_rate=rupees(900),
            source=RateSource.MEASURED,
            effective_from=date.today() + timedelta(days=1),
        )
    )
    await session.flush()
    await session.refresh(event)

    assert event.cost_total == original
    assert event.snapshot_machine_rate == rupees(420)


# ---------------------------------------------------------------------------
# Downtime capture
# ---------------------------------------------------------------------------


async def test_a_station_cannot_be_stopped_twice(session, fixtures):
    """Two supervisors on a shared tablet must not double-count one stop."""
    shift_session = await open_shift(session, fixtures)
    first = await downtime_service.open_downtime(
        session,
        plant=fixtures["plant"],
        station=fixtures["station"],
        reason_code=fixtures["reasons"]["MATERIAL_WAIT"],
        shift_session=shift_session,
        started_at=NOW,
    )
    second = await downtime_service.open_downtime(
        session,
        plant=fixtures["plant"],
        station=fixtures["station"],
        reason_code=fixtures["reasons"]["MATERIAL_WAIT"],
        shift_session=shift_session,
        started_at=NOW + timedelta(minutes=1),
    )
    assert first.id == second.id


async def test_closing_a_stop_cancels_its_unfired_escalations(session, fixtures):
    """The machine is running again; chasing Stores at minute 15 is noise."""
    from app.models import Escalation, EscalationStatus

    shift_session = await open_shift(session, fixtures)
    event = await downtime_service.open_downtime(
        session,
        plant=fixtures["plant"],
        station=fixtures["station"],
        reason_code=fixtures["reasons"]["MATERIAL_WAIT"],
        shift_session=shift_session,
        started_at=NOW,
    )
    scheduled = (
        await session.scalars(
            select(Escalation).where(Escalation.downtime_event_id == event.id)
        )
    ).all()
    assert len(scheduled) == 3  # the 5/15/30 ladder

    await downtime_service.close_downtime(
        session, event=event, ended_at=NOW + timedelta(minutes=2)
    )
    after = (
        await session.scalars(
            select(Escalation).where(Escalation.downtime_event_id == event.id)
        )
    ).all()
    assert all(e.status is EscalationStatus.CANCELLED for e in after)


async def test_a_sensor_reading_enters_through_the_same_contract(session, fixtures):
    """§2's data-source seam: the only difference that reaches the database is
    one column. This is what makes the hardware phase a swap, not a rebuild."""
    shift_session = await open_shift(session, fixtures, with_job=True)
    event = await downtime_service.open_downtime(
        session,
        plant=fixtures["plant"],
        station=fixtures["station"],
        reason_code=fixtures["reasons"]["TOOL_BREAKAGE"],
        shift_session=shift_session,
        started_at=NOW,
        source=DataSource.SENSOR,
        source_ref="ct-clamp-003",
    )
    await downtime_service.close_downtime(
        session, event=event, ended_at=NOW + timedelta(minutes=47)
    )

    assert event.source is DataSource.SENSOR
    assert event.source_ref == "ct-clamp-003"
    # Identical cost to the manually-typed equivalent above.
    assert event.cost_total == rupees("1356.50")


# ---------------------------------------------------------------------------
# The shift closure lock (§2)
# ---------------------------------------------------------------------------


async def test_closure_is_blocked_by_a_stop_with_no_reason(session, fixtures):
    shift_session = await open_shift(session, fixtures)
    event = await downtime_service.open_downtime(
        session,
        plant=fixtures["plant"],
        station=fixtures["station"],
        reason_code=None,
        shift_session=shift_session,
        started_at=NOW,
    )
    await downtime_service.close_downtime(
        session, event=event, ended_at=NOW + timedelta(minutes=10)
    )

    check = await shift_service.closure_blockers(session, shift_session)
    assert not check.can_close
    assert any(b.kind == "missing_reason" for b in check.blockers)


async def test_closure_is_blocked_while_a_machine_is_still_stopped(session, fixtures):
    shift_session = await open_shift(session, fixtures)
    await downtime_service.open_downtime(
        session,
        plant=fixtures["plant"],
        station=fixtures["station"],
        reason_code=fixtures["reasons"]["MATERIAL_WAIT"],
        shift_session=shift_session,
        started_at=NOW,
    )
    check = await shift_service.closure_blockers(session, shift_session)
    assert not check.can_close
    assert any(b.kind == "open_downtime" for b in check.blockers)


async def test_a_clean_shift_closes_and_locks(session, fixtures):
    shift_session = await open_shift(session, fixtures)
    check = await shift_service.closure_blockers(session, shift_session)
    assert check.can_close

    await shift_service.close_shift(
        session,
        shift_session=shift_session,
        closed_by_id=None,
        closed_at=NOW + timedelta(hours=8),
    )
    assert shift_session.status is ShiftSessionStatus.CLOSED
    assert shift_session.locked_at is not None
    assert await shift_service.is_locked(session, shift_session.id)


# ---------------------------------------------------------------------------
# Quality cross-verification (§2, §6)
# ---------------------------------------------------------------------------


async def test_matching_counts_reconcile_silently(session, fixtures):
    shift_session = await open_shift(session, fixtures)
    await capture_service.record_production(
        session,
        plant=fixtures["plant"],
        station=fixtures["station"],
        shift_session=shift_session,
        count=374,
        recorded_at=NOW,
    )
    record = await capture_service.record_quality(
        session,
        plant=fixtures["plant"],
        part=fixtures["part"],
        shift_session=shift_session,
        good_count=362,
        reject_count=9,
        station=fixtures["station"],
        recorded_at=NOW,
    )
    # §6: three parts unaccounted in ~374 is within tolerance, no flag.
    assert record.reconciliation_gap == 3
    assert not record.reconciliation_flagged


async def test_a_large_count_gap_alerts_the_owner(session, fixtures):
    shift_session = await open_shift(session, fixtures)
    await capture_service.record_production(
        session,
        plant=fixtures["plant"],
        station=fixtures["station"],
        shift_session=shift_session,
        count=396,
        recorded_at=NOW,
    )
    record = await capture_service.record_quality(
        session,
        plant=fixtures["plant"],
        part=fixtures["part"],
        shift_session=shift_session,
        good_count=362,
        reject_count=9,
        station=fixtures["station"],
        recorded_at=NOW,
    )
    assert record.reconciliation_gap == 25
    assert record.reconciliation_flagged


async def test_rejections_are_costed_as_scrap(session, fixtures):
    shift_session = await open_shift(session, fixtures)
    record = await capture_service.record_quality(
        session,
        plant=fixtures["plant"],
        part=fixtures["part"],
        shift_session=shift_session,
        good_count=362,
        reject_count=9,
        station=fixtures["station"],
        recorded_at=NOW,
    )
    # 9 × (₹72 material + ₹38 contribution) plus the Op10 processing consumed.
    assert record.cost_total > rupees(72 + 38) * 9
    assert record.cost_breakdown["quantity"] == 9


# ---------------------------------------------------------------------------
# Actions (§5)
# ---------------------------------------------------------------------------


async def test_a_big_event_creates_an_action_at_shift_close(session, fixtures):
    shift_session = await open_shift(session, fixtures)
    event = await downtime_service.open_downtime(
        session,
        plant=fixtures["plant"],
        station=fixtures["station"],
        reason_code=fixtures["reasons"]["TOOL_BREAKAGE"],
        shift_session=shift_session,
        started_at=NOW,
    )
    await downtime_service.close_downtime(
        session, event=event, ended_at=NOW + timedelta(minutes=47)
    )

    created = await action_service.evaluate_shift_close(
        session, plant=fixtures["plant"], shift_session=shift_session
    )
    assert len(created) == 1
    action = created[0]
    assert action.state is PdcaState.OPEN
    assert action.routed_to_rca  # 47 min clears the 30-minute severity bar
    assert action.baseline_frozen_at is not None


async def test_a_small_event_creates_no_action(session, fixtures):
    """Below threshold: visible, costed, counted in KPIs — and no work created."""
    shift_session = await open_shift(session, fixtures)
    event = await downtime_service.open_downtime(
        session,
        plant=fixtures["plant"],
        station=fixtures["station"],
        reason_code=fixtures["reasons"]["MATERIAL_WAIT"],
        shift_session=shift_session,
        started_at=NOW,
    )
    await downtime_service.close_downtime(
        session, event=event, ended_at=NOW + timedelta(minutes=6)
    )
    created = await action_service.evaluate_shift_close(
        session, plant=fixtures["plant"], shift_session=shift_session
    )
    assert created == []
    assert event.cost_total > 0  # still costed


async def test_the_same_event_never_spawns_two_actions(session, fixtures):
    """Shift close is evaluated over a rolling window, so it re-sees yesterday's
    events. It must not raise a duplicate Action for them."""
    shift_session = await open_shift(session, fixtures)
    event = await downtime_service.open_downtime(
        session,
        plant=fixtures["plant"],
        station=fixtures["station"],
        reason_code=fixtures["reasons"]["TOOL_BREAKAGE"],
        shift_session=shift_session,
        started_at=NOW,
    )
    await downtime_service.close_downtime(
        session, event=event, ended_at=NOW + timedelta(minutes=47)
    )

    first = await action_service.evaluate_shift_close(
        session, plant=fixtures["plant"], shift_session=shift_session
    )
    second = await action_service.evaluate_shift_close(
        session, plant=fixtures["plant"], shift_session=shift_session
    )
    assert len(first) == 1
    assert second == []


async def test_closing_an_action_captures_factory_memory(session, fixtures):
    from app.models import MemoryEntry

    shift_session = await open_shift(session, fixtures)
    event = await downtime_service.open_downtime(
        session,
        plant=fixtures["plant"],
        station=fixtures["station"],
        reason_code=fixtures["reasons"]["TOOL_BREAKAGE"],
        shift_session=shift_session,
        started_at=NOW,
    )
    await downtime_service.close_downtime(
        session, event=event, ended_at=NOW + timedelta(minutes=47)
    )
    action = (
        await action_service.evaluate_shift_close(
            session, plant=fixtures["plant"], shift_session=shift_session
        )
    )[0]

    await action_service.close_action(
        session,
        plant=fixtures["plant"],
        action=action,
        root_cause="Insert grade too soft for this material",
        countermeasure="Switched to P15 inserts",
        memory=action_service.MemoryCapture(
            what_fixed_it="Replaced the carbide insert grade on Station 3",
            what_was_replaced="Insert grade P25 → P15",
            check_first_next_time="Check insert wear at 240 parts, not 310",
        ),
        closed_by_name="Suresh K.",
    )

    # Closure moves to CHECK, never straight to done.
    assert action.state is PdcaState.MONITORING
    entry = await session.scalar(
        select(MemoryEntry).where(MemoryEntry.action_id == action.id)
    )
    assert entry is not None
    assert entry.check_first_next_time.startswith("Check insert wear")


async def test_recovered_profit_needs_two_sustained_weeks(session, fixtures):
    """A fix that held for one week earns nothing. §5.6 is deliberate about it."""
    shift_session = await open_shift(session, fixtures)
    event = await downtime_service.open_downtime(
        session,
        plant=fixtures["plant"],
        station=fixtures["station"],
        reason_code=fixtures["reasons"]["TOOL_BREAKAGE"],
        shift_session=shift_session,
        started_at=NOW,
    )
    await downtime_service.close_downtime(
        session, event=event, ended_at=NOW + timedelta(minutes=47)
    )
    action = (
        await action_service.evaluate_shift_close(
            session, plant=fixtures["plant"], shift_session=shift_session
        )
    )[0]
    # Closure is dated the day after the stop that raised the action, and the
    # clock is advanced with `as_of` rather than by back-dating `closed_at`
    # against the wall clock. Anchoring both ends to NOW is what keeps the
    # monitoring weeks genuinely clean: back-dating from today drags the
    # window over this test's own 47-minute stop once today drifts within
    # three weeks of NOW, and the assertion below fails by exactly that stop.
    closed_at = NOW + timedelta(days=1)
    action.baseline_weekly_loss = rupees(4400)
    action.closed_at = closed_at
    action.state = PdcaState.MONITORING
    await session.flush()

    await action_service.accrue_recovered_profit(
        session,
        plant=fixtures["plant"],
        action=action,
        as_of=closed_at.date() + timedelta(weeks=1),
    )
    assert action.state is PdcaState.MONITORING
    assert action.recovered_profit == 0

    # Three clean weeks later it verifies, and only the third week is credited:
    # the first two proved the fix held (§5.6, "from week 3 onwards").
    await action_service.accrue_recovered_profit(
        session,
        plant=fixtures["plant"],
        action=action,
        as_of=closed_at.date() + timedelta(weeks=3),
    )
    assert action.state is PdcaState.VERIFIED
    assert action.recovered_profit == rupees(4400)


async def _closed_action(session, fixtures, *, closed_at, baseline=rupees(4400)):
    from app.models import Action, ActionTrigger

    action = Action(
        plant_id=fixtures["plant"].id,
        reference=f"ACT-T{closed_at:%d%H%M%S}",
        title="Tool breakage on CNC-3",
        trigger=ActionTrigger.MANUAL,
        state=PdcaState.MONITORING,
        station_id=fixtures["station"].id,
        reason_code_id=fixtures["reasons"]["TOOL_BREAKAGE"].id,
        baseline_weekly_loss=baseline,
        closed_at=closed_at,
    )
    session.add(action)
    await session.flush()
    return action


async def _stop(session, fixtures, started_at, minutes):
    shift_session = await shift_service.start_shift(
        session,
        plant=fixtures["plant"],
        shift=fixtures["shift"],
        supervisor_id=None,
        started_at=started_at,
    )
    event = await downtime_service.open_downtime(
        session,
        plant=fixtures["plant"],
        station=fixtures["station"],
        reason_code=fixtures["reasons"]["TOOL_BREAKAGE"],
        shift_session=shift_session,
        started_at=started_at,
    )
    await downtime_service.close_downtime(
        session, event=event, ended_at=started_at + timedelta(minutes=minutes)
    )
    await shift_service.close_shift(session, shift_session=shift_session, closed_by_id=None)


async def test_a_reopen_stops_accrual_but_keeps_the_weeks_that_held(session, fixtures):
    plant = fixtures["plant"]
    closed_at = NOW - timedelta(weeks=8)
    action = await _closed_action(session, fixtures, closed_at=closed_at)

    as_of = closed_at.date() + timedelta(weeks=4)
    await action_service.accrue_recovered_profit(session, plant=plant, action=action, as_of=as_of)
    assert action.state is PdcaState.VERIFIED
    credited = action.recovered_profit
    assert credited == rupees(4400) * 2  # weeks 3 and 4

    # Week 5: a long run of breakages brings the weekly loss back above baseline.
    week_five = closed_at + timedelta(weeks=4, days=1)
    for day in range(4):
        await _stop(session, fixtures, week_five + timedelta(days=day), 240)

    as_of = closed_at.date() + timedelta(weeks=5)
    await action_service.accrue_recovered_profit(session, plant=plant, action=action, as_of=as_of)
    assert await action_service.reopen_if_recurred(session, plant=plant, action=action, as_of=as_of)
    assert action.state is PdcaState.REOPENED
    assert action.recovered_profit == credited  # the weeks that held stay credited


async def test_the_same_loss_is_never_credited_to_two_actions(session, fixtures):
    plant = fixtures["plant"]
    first = await _closed_action(session, fixtures, closed_at=NOW - timedelta(weeks=8))
    second = await _closed_action(
        session, fixtures, closed_at=NOW - timedelta(weeks=8) + timedelta(hours=1)
    )
    as_of = first.closed_at.date() + timedelta(weeks=4)

    await action_service.accrue_recovered_profit(session, plant=plant, action=first, as_of=as_of)
    await action_service.accrue_recovered_profit(session, plant=plant, action=second, as_of=as_of)
    assert first.recovered_profit == rupees(4400) * 2
    assert second.recovered_profit == 0


async def test_an_event_with_no_job_running_costs_only_time(session, fixtures):
    """A station with no active job has no part, so no cycle time and no margin.

    That is the job-order spine working as designed (§3): tags are inherited
    from whichever job is active, never typed. The machine, labour and overhead
    still cost money — an idle machine always does — but the product must not
    claim contribution on parts it cannot name.
    """
    shift_session = await open_shift(session, fixtures)  # no job assigned
    event = await downtime_service.open_downtime(
        session,
        plant=fixtures["plant"],
        station=fixtures["station"],
        reason_code=fixtures["reasons"]["TOOL_BREAKAGE"],
        shift_session=shift_session,
        started_at=NOW,
    )
    await downtime_service.close_downtime(
        session, event=event, ended_at=NOW + timedelta(minutes=47)
    )

    assert event.cost_breakdown["lost_contribution"] == 0
    assert event.cost_breakdown["parts_not_made"] == 0
    assert event.cost_total == rupees("634.50")  # time terms only
