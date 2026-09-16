"""Lot flow — §14.2's worked example, and the WIP-versus-gap distinction."""

from datetime import datetime, timedelta
from decimal import Decimal

from app.kernel import PartFlow, StageTotals, combine_part_flows, lot_flow

MON = datetime(2026, 8, 24, 7, 0)


def stage(code, section, completed, rejected=0, *, day=0, recorded=True):
    at = MON + timedelta(days=day)
    return StageTotals(
        operation_code=code,
        section=section,
        completed=completed,
        rejected=rejected,
        first_started_at=at if recorded else None,
        last_completed_at=at + timedelta(hours=8) if recorded else None,
        recorded=recorded,
    )


def test_the_spec_lot_mid_production_is_wip_not_a_gap():
    """§3: 500 through Op10 (Mon), 430 through Op20 (Tue — 70 in WIP), 380
    through Op30 with 20 rejected, 0 started Op40. Nothing is missing yet."""
    flow = lot_flow(
        [
            stage("Op10", "machining", 500, day=0),
            stage("Op20", "assembly", 430, day=1),
            stage("Op30", "quality", 380, 20, day=2),
            stage("Op40", "dispatch", 0, recorded=False),
        ],
        lot_quantity=500,
    )
    assert [s.wip_before for s in flow.stages] == [0, 70, 30, 380]
    assert flow.flags == ()
    assert flow.position == "Op20"  # the first stage it has not fully passed
    assert not flow.complete
    assert flow.throughput_minutes is None  # not despatched, so no throughput yet


def test_a_fully_despatched_lot_reports_its_throughput_time():
    flow = lot_flow(
        [
            stage("Op10", "machining", 500, day=0),
            stage("Op20", "assembly", 500, day=1),
            stage("Op30", "quality", 480, 20, day=2),
            stage("Op40", "dispatch", 480, day=3),
        ],
        lot_quantity=500,
    )
    assert flow.complete and flow.flags == ()
    assert flow.despatched == 480
    assert flow.total_rejected == 20
    # Mon 07:00 → Thu 15:00, wall clock (§8.2).
    assert flow.throughput_minutes == Decimal("4800.00")


def test_parts_that_passed_qc_but_never_left_the_gate_are_flagged_once_despatched():
    """§14.2 — "that gap is where parts silently disappear in most plants today"."""
    flow = lot_flow(
        [
            stage("Op10", "machining", 400),
            stage("Op20", "quality", 380, 20, day=1),
            stage("Op30", "dispatch", 340, day=2),
        ],
        lot_quantity=400,
    )
    assert len(flow.flags) == 1
    assert flow.flags[0].kind == "unaccounted_at_gate"
    assert flow.flags[0].gap == 40
    # It has left the building: the shortfall is a flag, not a location.
    assert flow.position is None


def test_a_small_shortfall_at_the_gate_is_within_tolerance():
    flow = lot_flow(
        [
            stage("Op10", "machining", 400),
            stage("Op20", "quality", 380, 20, day=1),
            stage("Op30", "dispatch", 377, day=2),
        ],
        lot_quantity=400,
    )
    assert flow.flags == ()


def test_parts_waiting_at_the_gate_before_despatch_are_not_a_loss():
    """Until despatch is recorded, 380 passed parts are simply waiting to go."""
    flow = lot_flow(
        [
            stage("Op10", "machining", 400),
            stage("Op20", "quality", 380, 20, day=1),
            stage("Op30", "dispatch", 0, recorded=False),
        ],
        lot_quantity=400,
    )
    assert flow.flags == ()
    assert flow.position == "Op30"


def test_parts_appearing_from_nowhere_are_always_flagged():
    flow = lot_flow(
        [
            stage("Op10", "machining", 300),
            stage("Op20", "assembly", 360, day=1),
        ],
        lot_quantity=300,
    )
    assert flow.flags[0].kind == "appeared"
    assert flow.flags[0].gap == 60


def test_over_production_against_the_plan_is_not_a_counting_error():
    flow = lot_flow([stage("Op10", "machining", 540)], lot_quantity=500)
    assert flow.flags == ()


def test_parts_with_different_routings_are_not_merged_by_operation_code():
    """Regression (found by the dataset session): in one batch, Op30 is despatch
    for a two-step part and inspection for a three-step one. Merged by code,
    every such batch showed parts "unaccounted at the gate"."""
    short = lot_flow(
        [
            stage("Op10", "machining", 300),
            stage("Op20", "quality", 295, 5, day=1),
            stage("Op30", "dispatch", 295, day=2),
        ]
    )
    long = lot_flow(
        [
            stage("Op10", "machining", 200),
            stage("Op20", "assembly", 200, day=1),
            stage("Op30", "quality", 196, 4, day=2),
            stage("Op40", "dispatch", 196, day=3),
        ]
    )
    batch = combine_part_flows([PartFlow("SV-2040", short), PartFlow("SV-1101", long)])

    assert batch.flags == ()
    assert batch.complete
    assert batch.despatched == 491
    assert batch.total_rejected == 9


def test_a_batch_is_not_complete_until_every_part_has_left():
    done = lot_flow(
        [stage("Op10", "machining", 300), stage("Op20", "dispatch", 300, day=1)]
    )
    moving = lot_flow(
        [
            stage("Op10", "machining", 200),
            stage("Op20", "quality", 0, recorded=False),
            stage("Op30", "dispatch", 0, recorded=False),
        ]
    )
    batch = combine_part_flows([PartFlow("PIN-8", done), PartFlow("BOLT-12", moving)])

    assert not batch.complete
    assert batch.position == "BOLT-12 Op20"
    assert batch.throughput_minutes is None


def test_a_flag_in_a_batch_names_its_part():
    short_at_gate = lot_flow(
        [
            stage("Op10", "machining", 400),
            stage("Op20", "quality", 380, 20, day=1),
            stage("Op30", "dispatch", 330, day=2),
        ]
    )
    clean = lot_flow(
        [stage("Op10", "machining", 100), stage("Op20", "dispatch", 100, day=1)]
    )
    batch = combine_part_flows([PartFlow("NUT-6", short_at_gate), PartFlow("PIN-8", clean)])

    assert [f.operation_code for f in batch.flags] == ["NUT-6 Op30"]
    assert batch.flags[0].message.startswith("NUT-6:")
