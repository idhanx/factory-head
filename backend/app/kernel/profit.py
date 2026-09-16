"""Recovered Profit — the north-star metric (§5.6, §8.1).

    "Recovered Profit for an Action = (baseline weekly loss for that
     reason/machine, averaged over the 4 weeks before the Action) minus
     (actual weekly loss after closure), summed weekly — counted only after
     the fix has held for 2 consecutive weeks, and never below zero."

This is the number that renews contracts, so it is measured conservatively or
it becomes a dispute. Four guards, all in this module:

  * the baseline is **frozen** at Action creation and never recomputed;
  * the two weeks that prove the fix held are the proof, not the payout —
    credit accrues from the week after them, exactly as §5.6's example does
    (₹4,400 baseline, ₹1,100 weeks: "accrues at ₹3,300/week from week 3");
  * a week that got worse contributes **zero**, never a negative;
  * if the Action reopens, accrual **stops** — weeks already credited stay.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .money import D, Paise, round_paise

SUSTAIN_WEEKS = 2
BASELINE_WEEKS = 4


@dataclass(frozen=True, slots=True)
class WeeklyLoss:
    """Actual recoverable loss for one linked reason+machine, one week."""

    week_start: date
    loss: Paise


@dataclass(frozen=True, slots=True)
class RecoveredProfitResult:
    baseline_weekly_loss: Paise
    weeks_counted: int
    weeks_observed: int
    sustained: bool
    recovered: Paise
    annualised: Paise
    weekly_breakdown: tuple[tuple[date, Paise], ...]
    reason: str = ""
    #: The weeks whose saving is credited — never the two that proved the fix.
    counted_weeks: tuple[date, ...] = ()


def freeze_baseline(weeks_before: Sequence[WeeklyLoss]) -> Paise:
    """Average weekly ₹ loss for the linked reason+machine over the 4 weeks
    before the Action. Frozen at Action creation (§8.1) — if the plant's mix
    changes later, the baseline this Action is measured against does not move.
    """
    window = list(weeks_before)[-BASELINE_WEEKS:]
    if not window:
        return 0
    return round_paise(D(sum(w.loss for w in window)) / D(len(window)))


def recovered_profit(
    *,
    baseline_weekly_loss: Paise,
    weeks_after_closure: Sequence[WeeklyLoss],
    reopened: bool = False,
) -> RecoveredProfitResult:
    """Accrue savings for one closed Action (§5.6, §8.1).

    Weeks are complete weeks after closure, oldest first. Nothing is credited
    until two consecutive weeks have come in under the baseline; from the week
    after those two, every week under the baseline is credited by how far under
    it came.
    """
    weeks = list(weeks_after_closure)
    per_week = [(w.week_start, max(0, baseline_weekly_loss - w.loss)) for w in weeks]

    def result(**kwargs) -> RecoveredProfitResult:
        base = {
            "baseline_weekly_loss": baseline_weekly_loss,
            "weeks_counted": 0,
            "weeks_observed": len(weeks),
            "sustained": False,
            "recovered": 0,
            "annualised": 0,
            "weekly_breakdown": tuple(per_week),
        }
        return RecoveredProfitResult(**{**base, **kwargs})

    if reopened:
        return result(reason="Action reopened — the fix did not hold, so accrual has stopped.")
    if baseline_weekly_loss <= 0:
        return result(
            reason="No baseline loss was measured before the Action, so there is nothing to recover."
        )

    proven_at = _sustain_index(per_week)
    if proven_at is None:
        return result(
            reason=(
                f"Monitoring — needs {SUSTAIN_WEEKS} consecutive sustained weeks "
                f"({len(weeks)} observed)."
            )
        )

    credited = [(week, saving) for week, saving in per_week[proven_at + 1 :] if saving > 0]
    run_rate_weeks = credited or per_week[proven_at + 1 - SUSTAIN_WEEKS : proven_at + 1]
    return result(
        sustained=True,
        weeks_counted=len(credited),
        recovered=sum(saving for _, saving in credited),
        annualised=_weekly_run_rate(run_rate_weeks) * 52,
        counted_weeks=tuple(week for week, _ in credited),
        reason=(
            "Verified — fix sustained."
            if credited
            else "Verified — fix sustained; credit starts with the next week under baseline."
        ),
    )


def _sustain_index(per_week: Sequence[tuple[date, Paise]]) -> int | None:
    """Index of the week that completes the first run of sustained weeks."""
    run = 0
    for index, (_, saving) in enumerate(per_week):
        run = run + 1 if saving > 0 else 0
        if run >= SUSTAIN_WEEKS:
            return index
    return None


def fix_failed(
    *,
    baseline_weekly_loss: Paise,
    weeks_after_closure: Sequence[WeeklyLoss],
    events_since_closure: int,
    recurrence_count: int,
) -> bool:
    """§5.4 ACT — did the fix stop holding?

    Measured the same way the saving is: a complete week back at or above the
    baseline loss means the loss rate did not stay down. An Action with no
    baseline has no rate to compare against, so it falls back to the event
    rule — the reason recurring as often as the pattern that raised it.
    """
    if baseline_weekly_loss > 0:
        return any(week.loss >= baseline_weekly_loss for week in weeks_after_closure)
    return events_since_closure >= recurrence_count


def _weekly_run_rate(per_week: Sequence[tuple[date, Paise]]) -> Paise:
    """Current sustained saving rate: the mean of the counted weeks."""
    counted = [saving for _, saving in per_week if saving > 0]
    if not counted:
        return 0
    return round_paise(D(sum(counted)) / D(len(counted)))


def plant_recovered_profit(verified_actions: Sequence[Paise]) -> Paise:
    """``Σ across verified Actions`` — the number on the owner's home screen."""
    return sum(verified_actions)


def running_roi(*, cumulative_recovered: Paise, cumulative_fees_paid: Paise) -> Decimal:
    """``cumulative Recovered Profit / cumulative fees paid``.

    Shown monthly. This is the renewal argument: nobody cancels a system whose
    home screen shows 19x ROI.
    """
    if cumulative_fees_paid <= 0:
        return D(0)
    return (D(cumulative_recovered) / D(cumulative_fees_paid)).quantize(D("0.01"))
