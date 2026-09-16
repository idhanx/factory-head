"""Trigger, routing, tier-gating and shift-lock rules (§5.2, §5.3, §14.6, §2)."""

from decimal import Decimal

from app.kernel import (
    ActionThresholds,
    ActionTrigger,
    CoachTier,
    DataSufficiency,
    EventForTrigger,
    Recoverability,
    check_shift_closure,
    coach_tier,
    evaluate_action_triggers,
    route_by_severity,
    rupees,
)


def event(eid, station, reason, minutes, cost_rupees, *, shift=1, safety=False):
    return EventForTrigger(
        event_id=eid,
        station_id=station,
        reason_code=reason,
        minutes=Decimal(minutes),
        cost=rupees(cost_rupees),
        recoverability=Recoverability.RECOVERABLE,
        shift_index=shift,
        safety_or_quality_escape=safety,
    )


def test_a_big_single_event_creates_an_action():
    """§5.2: the 47-min ₹3,290 tool breakage auto-creates an Action at close."""
    actions = evaluate_action_triggers([event("e1", "CNC-3", "Tool Breakage", 47, 3290)])
    assert len(actions) == 1
    assert actions[0].trigger is ActionTrigger.BIG_SINGLE_EVENT


def test_small_unrelated_losses_stay_logged_and_generate_no_work():
    """§6: six small sub-₹500 losses stay logged, visible and costed — but they
    are not sent to RCA. A system spawning forty open actions a week dies."""
    smalls = [
        event("e1", "VMC-1", "Minor Adjustment", 4, 300),
        event("e2", "CNC-3", "Tool Change", 6, 480),
        event("e3", "Press-2", "Operator Break", 5, 200),
        event("e4", "VMC-2", "Quality Hold", 3, 150),
        event("e5", "CNC-5", "Minor Adjustment", 4, 320),
        event("e6", "Line 2", "Meeting", 8, 410),
    ]
    assert evaluate_action_triggers(smalls) == []


def test_small_losses_that_keep_repeating_on_one_station_do_earn_an_action():
    """Cheap but relentless is exactly what the pattern rule exists to catch —
    six ₹300 stops on the same machine is ₹1,800 nobody would otherwise chase."""
    smalls = [
        event(f"e{i}", "VMC-1", "Minor Adjustment", 4, 300, shift=i) for i in range(6)
    ]
    actions = evaluate_action_triggers(smalls)
    assert len(actions) == 1
    assert actions[0].trigger is ActionTrigger.RECURRING_PATTERN
    assert actions[0].total_cost == rupees(1800)


def test_occurrences_spread_beyond_the_window_are_not_a_pattern():
    """Three material waits across two months are not a pattern; they are Tuesdays."""
    spread = [
        event("e1", "Press-2", "Material Wait", 15, 1500, shift=0),
        event("e2", "Press-2", "Material Wait", 15, 1500, shift=20),
        event("e3", "Press-2", "Material Wait", 15, 1500, shift=48),
    ]
    assert evaluate_action_triggers(spread) == []


def test_three_recurrences_roll_into_one_pattern_action_not_three():
    """§12 risk 10 — recurring events roll into ONE Action covering the pattern."""
    repeats = [
        event(f"m{i}", "Press-2", "Material Wait", 15, 1500, shift=i) for i in range(3)
    ]
    actions = evaluate_action_triggers(repeats)
    assert len(actions) == 1
    assert len(actions) == 1
    assert actions[0].trigger is ActionTrigger.RECURRING_PATTERN
    assert actions[0].occurrence_count == 3
    assert actions[0].total_cost == rupees(4500)


def test_two_recurrences_are_below_the_pattern_bar():
    repeats = [event(f"m{i}", "Press-2", "Material Wait", 15, 1500) for i in range(2)]
    assert evaluate_action_triggers(repeats) == []


def test_actions_are_ranked_by_money():
    actions = evaluate_action_triggers(
        [
            event("e1", "VMC-1", "Tool Change", 35, 1540),
            event("e2", "CNC-3", "Tool Breakage", 47, 3290),
        ]
    )
    assert [a.station_id for a in actions] == ["CNC-3", "VMC-1"]


def test_thresholds_are_per_plant():
    """Set during onboarding, tightened as the plant matures (§5.2)."""
    strict = ActionThresholds(cost_threshold=rupees(500), duration_minutes=5)
    assert evaluate_action_triggers([event("e1", "A", "R", 10, 800)], strict)
    assert not evaluate_action_triggers([event("e1", "A", "R", 10, 800)])


def test_a_safety_escape_routes_to_rca_regardless_of_cost():
    """§5.3 — severity, not only money, earns a place in the RCA queue."""
    cheap_but_serious = event("e1", "QC", "Quality Hold", 2, 50, safety=True)
    verdict = route_by_severity(cheap_but_serious)
    assert verdict.escalate_to_rca
    assert "Safety or quality-escape" in verdict.reasons[0]


def test_a_cheap_short_event_does_not_reach_rca():
    verdict = route_by_severity(event("e1", "VMC-1", "Minor Adjustment", 4, 300))
    assert not verdict.escalate_to_rca


def test_coach_stays_silent_before_it_has_evidence():
    """§14.6 Tier 0 — saying nothing beats guessing. Enforced in the product,
    by a data-sufficiency check, never by an instruction in a prompt."""
    assert (
        coach_tier(DataSufficiency(complete_shifts=4, days_live=3, distinct_events=20))
        is CoachTier.SILENT
    )
    assert (
        coach_tier(DataSufficiency(complete_shifts=12, days_live=11, distinct_events=90))
        is CoachTier.RULE_BASED
    )
    assert (
        coach_tier(DataSufficiency(complete_shifts=48, days_live=30, distinct_events=400))
        is CoachTier.PATTERN
    )
    assert (
        coach_tier(DataSufficiency(complete_shifts=150, days_live=90, distinct_events=1500))
        is CoachTier.DEEP
    )


def test_shift_closure_blocks_on_a_downtime_event_with_no_reason():
    """§6, 3:25 PM — the lock is what makes manual data honest. Closing the
    shift is how the supervisor goes home, so the data gets filled."""
    check = check_shift_closure(downtime_without_reason=[("dt-9", "Press-2")])
    assert not check.can_close
    assert check.blockers[0].kind == "missing_reason"


def test_shift_closure_passes_when_everything_is_resolved():
    assert check_shift_closure().can_close


def test_shift_closure_blocks_on_a_machine_still_running_a_stop():
    check = check_shift_closure(open_downtime_events=[("dt-4", "CNC-3")])
    assert not check.can_close
    assert check.blockers[0].kind == "open_downtime"


# ---------------------------------------------------------------------------
# §5.4 — the lifecycle a person may walk by hand
# ---------------------------------------------------------------------------


def test_nobody_walks_an_action_into_check_or_act_by_hand():
    """CHECK is entered by closing with a countermeasure; ACT only by measurement."""
    from app.kernel import MANUAL_TRANSITIONS, PdcaState, can_transition

    system_only = {PdcaState.MONITORING, PdcaState.VERIFIED, PdcaState.REOPENED}
    for source, targets in MANUAL_TRANSITIONS.items():
        assert not (targets & system_only), source
    assert not can_transition(PdcaState.OPEN, PdcaState.VERIFIED)
    assert not can_transition(PdcaState.MONITORING, PdcaState.CANCELLED)


def test_every_state_has_a_phase_and_a_transition_rule():
    """A state missing from either table is how the Actions board once returned 400."""
    from app.kernel import MANUAL_TRANSITIONS, PDCA_PHASE, PdcaState

    assert set(PDCA_PHASE) == set(PdcaState)
    assert set(MANUAL_TRANSITIONS) == set(PdcaState)


def test_a_recurring_pattern_is_routed_to_rca_even_when_each_event_is_small():
    """§6, 3:27 PM: the third material wait clears the severity rule and gets an RCA."""
    from decimal import Decimal

    from app.kernel import EventForTrigger, Recoverability, route_by_severity, rupees

    small = EventForTrigger(
        event_id="e3",
        station_id="Press-2",
        reason_code="Material Wait",
        minutes=Decimal(22),
        cost=rupees(1540),
        recoverability=Recoverability.RECOVERABLE,
        shift_index=2,
    )
    assert not route_by_severity(small).escalate_to_rca
    verdict = route_by_severity(small, occurrences=3)
    assert verdict.escalate_to_rca
    assert any("Recurring" in reason for reason in verdict.reasons)
