"""Recovered Profit — the four conservatism guards that keep it out of dispute."""

from datetime import date, timedelta
from decimal import Decimal

from app.kernel import (
    WeeklyLoss,
    freeze_baseline,
    plant_recovered_profit,
    recovered_profit,
    running_roi,
    rupees,
)

MONDAY = date(2026, 6, 1)


def weeks(*amounts, start=MONDAY):
    return [
        WeeklyLoss(start + timedelta(weeks=i), rupees(a)) for i, a in enumerate(amounts)
    ]


def test_baseline_averages_the_four_weeks_before_the_action():
    """§5.6's worked example: material waits on Line 2 at ₹4,400/week."""
    assert freeze_baseline(weeks(4400, 4400, 4400, 4400)) == rupees(4400)
    assert freeze_baseline(weeks(4000, 4400, 4800, 4400)) == rupees(4400)


def test_baseline_uses_only_the_last_four_weeks():
    """A longer history does not dilute the baseline the Action is measured against."""
    assert freeze_baseline(weeks(99999, 4400, 4400, 4400, 4400)) == rupees(4400)


def test_spec_worked_example_accrues_from_week_three():
    """§5.6: baseline ₹4,400/week, actual ₹1,100/week → ₹3,300/week "from week 3 onwards"."""
    result = recovered_profit(
        baseline_weekly_loss=rupees(4400),
        weeks_after_closure=weeks(1100, 1100, 1100),
    )
    assert result.sustained
    assert result.recovered == rupees(3300)  # weeks 1-2 prove the fix; week 3 is credited
    assert result.counted_weeks == (MONDAY + timedelta(weeks=2),)
    assert result.annualised == rupees(3300) * 52  # ~₹1.7L, as the spec states


def test_the_two_proof_weeks_verify_the_fix_but_credit_nothing():
    result = recovered_profit(
        baseline_weekly_loss=rupees(4400), weeks_after_closure=weeks(1100, 1100)
    )
    assert result.sustained
    assert result.recovered == 0
    assert result.annualised == rupees(3300) * 52


def test_nothing_accrues_until_the_fix_has_held_two_consecutive_weeks():
    """A fix that lasted one week is not a fix. Credit nothing yet."""
    result = recovered_profit(
        baseline_weekly_loss=rupees(4400), weeks_after_closure=weeks(1100)
    )
    assert not result.sustained
    assert result.recovered == 0
    assert "2 consecutive sustained weeks" in result.reason


def test_a_worse_week_contributes_zero_never_a_negative():
    """Never below zero (§5.6). An Action cannot lose the plant money on paper."""
    result = recovered_profit(
        baseline_weekly_loss=rupees(4400),
        weeks_after_closure=weeks(1100, 1100, 1100, 9000),
    )
    assert result.weekly_breakdown[3][1] == 0
    assert result.recovered == rupees(3300)


def test_a_reopened_action_stops_accruing_entirely():
    """The system reopens an Action when events recur — a fix that didn't hold."""
    result = recovered_profit(
        baseline_weekly_loss=rupees(4400),
        weeks_after_closure=weeks(1100, 1100, 1100),
        reopened=True,
    )
    assert result.recovered == 0
    assert "did not hold" in result.reason


def test_intermittent_weeks_never_reach_the_sustain_bar():
    result = recovered_profit(
        baseline_weekly_loss=rupees(4400),
        weeks_after_closure=weeks(1100, 5000, 1100, 5000),
    )
    assert not result.sustained
    assert result.recovered == 0


def test_running_roi_is_the_renewal_argument():
    """Nobody cancels a system whose home screen shows 19× ROI."""
    assert running_roi(
        cumulative_recovered=rupees(1900000), cumulative_fees_paid=rupees(100000)
    ) == Decimal("19.00")
    assert running_roi(
        cumulative_recovered=rupees(5000), cumulative_fees_paid=0
    ) == Decimal(0)


def test_plant_total_sums_verified_actions_only():
    assert plant_recovered_profit([rupees(9900), rupees(4200)]) == rupees(14100)


def test_an_action_with_no_baseline_recovers_nothing():
    result = recovered_profit(baseline_weekly_loss=0, weeks_after_closure=weeks(0, 0, 0))
    assert not result.sustained
    assert result.recovered == 0


def test_a_week_back_at_baseline_means_the_fix_failed():
    from app.kernel import fix_failed

    assert fix_failed(
        baseline_weekly_loss=rupees(4400),
        weeks_after_closure=weeks(1100, 4400),
        events_since_closure=1,
        recurrence_count=3,
    )
    assert not fix_failed(
        baseline_weekly_loss=rupees(4400),
        weeks_after_closure=weeks(1100, 1200, 900),
        events_since_closure=9,  # still happening, but far less often: the fix held
        recurrence_count=3,
    )


def test_without_a_baseline_recurrence_decides_whether_the_fix_failed():
    from app.kernel import fix_failed

    assert fix_failed(
        baseline_weekly_loss=0, weeks_after_closure=[], events_since_closure=3, recurrence_count=3
    )
    assert not fix_failed(
        baseline_weekly_loss=0, weeks_after_closure=[], events_since_closure=2, recurrence_count=3
    )
