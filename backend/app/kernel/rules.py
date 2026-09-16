"""Decision rules — §5.2 Action triggers, §5.3 severity routing, §14.6 Coach tiers.

These are business rules, not a rules engine: ~15 typed, unit-tested conditions
live here as code. A Drools-style engine would only add a second place for
business logic to hide.

Every threshold is per-plant configurable — the spec is explicit that they are
set during onboarding and tightened as the plant matures.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from enum import Enum

from .money import D, Paise, rupees
from .types import Recoverability


class ActionTrigger(str, Enum):
    BIG_SINGLE_EVENT = "big_single_event"
    RECURRING_PATTERN = "recurring_pattern"
    MANUAL = "manual"


class PdcaState(str, Enum):
    """§5.4 — the Action lifecycle *is* the PDCA cycle, so nothing feels foreign."""

    OPEN = "open"  # PLAN   — problem defined, ₹ cost attached
    ROOT_CAUSE = "root_cause"  # PLAN   — root cause established
    PLANNED = "planned"  # PLAN   — countermeasure, owner and deadline set
    IN_PROGRESS = "in_progress"  # DO     — the fix is executed
    MONITORING = "monitoring"  # CHECK  — did the loss rate actually drop?
    VERIFIED = "verified"  # ACT    — sustained; savings credited
    REOPENED = "reopened"  # ACT    — events recurred; a fix that didn't hold
    CANCELLED = "cancelled"  # outside the cycle — raised in error or superseded


PDCA_PHASE = {
    PdcaState.OPEN: "PLAN",
    PdcaState.ROOT_CAUSE: "PLAN",
    PdcaState.PLANNED: "PLAN",
    PdcaState.IN_PROGRESS: "DO",
    PdcaState.MONITORING: "CHECK",
    PdcaState.VERIFIED: "ACT",
    PdcaState.REOPENED: "ACT",
    PdcaState.CANCELLED: "",
}

#: States from which the fix can still be closed into CHECK.
CLOSABLE_STATES = frozenset(
    {
        PdcaState.OPEN,
        PdcaState.ROOT_CAUSE,
        PdcaState.PLANNED,
        PdcaState.IN_PROGRESS,
        PdcaState.REOPENED,
    }
)

#: The moves a person may make by hand. CHECK is entered only by closing the
#: Action with its countermeasure and the Factory Memory answers, and ACT
#: (verified / reopened) only by the system measuring what happened after —
#: a fix is never declared verified, it is shown to have held (§5.4, §5.6).
MANUAL_TRANSITIONS: dict[PdcaState, frozenset[PdcaState]] = {
    PdcaState.OPEN: frozenset(
        {PdcaState.ROOT_CAUSE, PdcaState.PLANNED, PdcaState.IN_PROGRESS, PdcaState.CANCELLED}
    ),
    PdcaState.ROOT_CAUSE: frozenset(
        {PdcaState.PLANNED, PdcaState.IN_PROGRESS, PdcaState.CANCELLED}
    ),
    PdcaState.PLANNED: frozenset(
        {PdcaState.ROOT_CAUSE, PdcaState.IN_PROGRESS, PdcaState.CANCELLED}
    ),
    PdcaState.IN_PROGRESS: frozenset(
        {PdcaState.ROOT_CAUSE, PdcaState.PLANNED, PdcaState.CANCELLED}
    ),
    PdcaState.REOPENED: frozenset(
        {PdcaState.ROOT_CAUSE, PdcaState.PLANNED, PdcaState.IN_PROGRESS, PdcaState.CANCELLED}
    ),
    PdcaState.MONITORING: frozenset(),
    PdcaState.VERIFIED: frozenset(),
    PdcaState.CANCELLED: frozenset({PdcaState.OPEN}),
}


def can_transition(current: PdcaState, target: PdcaState) -> bool:
    """Whether a person may move an Action from ``current`` to ``target``."""
    return target in MANUAL_TRANSITIONS.get(current, frozenset())


#: §5.2's default: >₹2,500. A module constant rather than a call in the
#: dataclass default, so it is evaluated once and obviously immutable.
DEFAULT_COST_THRESHOLD: Paise = rupees(2500)


@dataclass(frozen=True, slots=True)
class ActionThresholds:
    """§5.2 / §16.5 — defaults if the plant hasn't tuned them yet.

    Too low and the plant drowns in action fatigue; too high and real losses
    are ignored. The spec's default: >₹2,500 or >30 min.
    """

    cost_threshold: Paise = DEFAULT_COST_THRESHOLD
    duration_minutes: int = 30
    recurrence_count: int = 3
    recurrence_window_shifts: int = 7


@dataclass(frozen=True, slots=True)
class EventForTrigger:
    event_id: str
    station_id: str
    reason_code: str
    minutes: Decimal
    cost: Paise
    recoverability: Recoverability
    shift_index: int
    safety_or_quality_escape: bool = False


@dataclass(frozen=True, slots=True)
class TriggeredAction:
    trigger: ActionTrigger
    title: str
    station_id: str
    reason_code: str
    event_ids: tuple[str, ...]
    total_cost: Paise
    occurrence_count: int
    rationale: str


def evaluate_action_triggers(
    events: Sequence[EventForTrigger],
    thresholds: ActionThresholds | None = None,
) -> list[TriggeredAction]:
    """Decide which of a shift's events deserve a tracked Action.

    Evaluated at shift close, not continuously — an Action born mid-shift would
    interrupt the person who should be running the shift.

    Everything below threshold stays a logged event: visible, costed, counted in
    KPIs, but not generating work. A system that spawns forty open actions a
    week dies of action fatigue within a month.
    """
    limits = thresholds or ActionThresholds()
    actions: list[TriggeredAction] = []
    consumed: set[str] = set()

    # Rule 1 — any single event over the cost or duration threshold.
    for event in events:
        if event.cost > limits.cost_threshold or event.minutes > D(limits.duration_minutes):
            consumed.add(event.event_id)
            actions.append(
                TriggeredAction(
                    trigger=ActionTrigger.BIG_SINGLE_EVENT,
                    title=f"{event.reason_code} on {event.station_id}",
                    station_id=event.station_id,
                    reason_code=event.reason_code,
                    event_ids=(event.event_id,),
                    total_cost=event.cost,
                    occurrence_count=1,
                    rationale=(
                        f"Single event of {event.minutes} min costing "
                        f"{event.cost / 100:.0f} rupees, above the plant threshold."
                    ),
                )
            )

    # Rule 2 — the same reason on the same station 3+ times in a rolling window.
    # Recurring events roll into ONE pattern Action, never one per occurrence
    # (§12 risk 10: action fatigue).
    groups: dict[tuple[str, str], list[EventForTrigger]] = {}
    for event in events:
        groups.setdefault((event.station_id, event.reason_code), []).append(event)

    for (station_id, reason_code), group in groups.items():
        window = _densest_window(group, limits)
        if window is None:
            continue
        group = window
        if all(e.event_id in consumed for e in group):
            continue
        actions.append(
            TriggeredAction(
                trigger=ActionTrigger.RECURRING_PATTERN,
                title=f"Eliminate recurring {reason_code} on {station_id}",
                station_id=station_id,
                reason_code=reason_code,
                event_ids=tuple(e.event_id for e in group),
                total_cost=sum(e.cost for e in group),
                occurrence_count=len(group),
                rationale=(
                    f"{len(group)} occurrences in a rolling "
                    f"{limits.recurrence_window_shifts}-shift window."
                ),
            )
        )

    actions.sort(key=lambda a: a.total_cost, reverse=True)
    return actions


def _densest_window(
    group: Sequence[EventForTrigger], limits: ActionThresholds
) -> list[EventForTrigger] | None:
    """The occurrences inside the busiest rolling window, if they clear the bar.

    'Three times in a rolling 7 shifts' is a real constraint: three material
    waits spread over two months are not a pattern, they are Tuesdays. Only the
    events actually inside the window are attached to the Action, so the ₹ total
    the owner reads describes the pattern being claimed.
    """
    ordered = sorted(group, key=lambda e: e.shift_index)
    best: list[EventForTrigger] | None = None
    for i, anchor in enumerate(ordered):
        cutoff = anchor.shift_index + limits.recurrence_window_shifts
        window = [e for e in ordered[i:] if e.shift_index < cutoff]
        if len(window) >= limits.recurrence_count and (
            best is None or len(window) > len(best)
        ):
            best = window
    return best


# ---------------------------------------------------------------------------
# §5.3 — severity routing: which issues go to PDCA/RCA, and which just stay logged
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SeverityVerdict:
    escalate_to_rca: bool
    reasons: tuple[str, ...]


def route_by_severity(
    event: EventForTrigger,
    thresholds: ActionThresholds | None = None,
    *,
    occurrences: int = 1,
) -> SeverityVerdict:
    """The morning meeting does NOT root-cause every issue from yesterday.

    That is how RCA dies — nobody does five 5-Whys before 8 AM. The team spends
    its RCA effort only where the money or the severity justifies it: above
    the Action threshold, a safety or quality-escape flag, or a recurring
    pattern (§5.3, §16.5 default).
    """
    limits = thresholds or ActionThresholds()
    reasons: list[str] = []
    if occurrences >= limits.recurrence_count:
        reasons.append(
            f"Recurring: {occurrences} times in {limits.recurrence_window_shifts} shifts"
        )

    if event.cost > limits.cost_threshold:
        reasons.append(f"Cost above the ₹{limits.cost_threshold // 100:,} threshold")
    if event.minutes > D(limits.duration_minutes):
        reasons.append(f"Duration above {limits.duration_minutes} min")
    if event.safety_or_quality_escape:
        reasons.append("Safety or quality-escape severity flag")

    return SeverityVerdict(escalate_to_rca=bool(reasons), reasons=tuple(reasons))


# ---------------------------------------------------------------------------
# §3 Layer 2 — the escalation ladder
# ---------------------------------------------------------------------------


class EscalationLevel(int, Enum):
    SUPERVISOR = 1  # 5 min  — push notification
    FUNCTION = 2  # 15 min — WhatsApp to Maintenance or Stores
    PLANT_HEAD = 3  # 30 min — alert with the running cost


ESCALATION_LADDER: tuple[tuple[EscalationLevel, timedelta], ...] = (
    (EscalationLevel.SUPERVISOR, timedelta(minutes=5)),
    (EscalationLevel.FUNCTION, timedelta(minutes=15)),
    (EscalationLevel.PLANT_HEAD, timedelta(minutes=30)),
)


def escalation_schedule(opened_at):
    """Absolute due times for one open downtime event.

    Durable *delayed* jobs, never ``setTimeout`` — the process will restart and
    a missed 15-minute WhatsApp is a missed escalation nobody can later prove.
    """
    return [(level, opened_at + delay) for level, delay in ESCALATION_LADDER]


# ---------------------------------------------------------------------------
# §14.6 — AI Coach capability tiers, enforced in the product
# ---------------------------------------------------------------------------


class CoachTier(int, Enum):
    SILENT = 0  # days 1-10      — saying nothing beats guessing
    RULE_BASED = 1  # ~10 shifts     — biggest loss, 3x recurrence, overdue actions
    PATTERN = 2  # 4-6 weeks      — breakage intervals, risk windows
    DEEP = 3  # 12+ weeks      — day-of-week effects, correlation suggestions


@dataclass(frozen=True, slots=True)
class DataSufficiency:
    complete_shifts: int
    days_live: int
    distinct_events: int


def coach_tier(data: DataSufficiency) -> CoachTier:
    """Gate the Coach on evidence, server-side — never by an instruction in a prompt.

    A Copilot with three days of history that confidently answers pattern
    questions will be confidently wrong, and one wrong answer to an owner kills
    trust in every number the product shows.
    """
    if data.days_live >= 84 and data.complete_shifts >= 120:
        return CoachTier.DEEP
    if data.days_live >= 28 and data.complete_shifts >= 40:
        return CoachTier.PATTERN
    if data.complete_shifts >= 10:
        return CoachTier.RULE_BASED
    return CoachTier.SILENT


@dataclass(frozen=True, slots=True)
class ShiftClosureBlocker:
    kind: str
    detail: str
    reference_id: str | None = None


@dataclass(frozen=True, slots=True)
class ClosureCheck:
    can_close: bool
    blockers: tuple[ShiftClosureBlocker, ...] = field(default=())


def check_shift_closure(
    *,
    downtime_without_reason: Sequence[tuple[str, str]] = (),
    stations_without_production: Sequence[str] = (),
    open_downtime_events: Sequence[tuple[str, str]] = (),
    unresolved_escalations: Sequence[tuple[str, str]] = (),
) -> ClosureCheck:
    """The Shift Closure Lock (§2) — the mechanism that makes manual data honest.

    A supervisor cannot close their shift while any downtime event has no
    reason, any production entry is missing, or any escalation is unresolved.
    Closing the shift is how they go home, so the data gets filled.
    """
    blockers: list[ShiftClosureBlocker] = []

    for event_id, station in downtime_without_reason:
        blockers.append(
            ShiftClosureBlocker(
                "missing_reason", f"{station}: downtime with no reason code", event_id
            )
        )
    for station in stations_without_production:
        blockers.append(
            ShiftClosureBlocker(
                "missing_production", f"{station}: no production count entered", station
            )
        )
    for event_id, station in open_downtime_events:
        blockers.append(
            ShiftClosureBlocker(
                "open_downtime", f"{station}: machine still marked stopped", event_id
            )
        )
    for escalation_id, detail in unresolved_escalations:
        blockers.append(ShiftClosureBlocker("open_escalation", detail, escalation_id))

    return ClosureCheck(can_close=not blockers, blockers=tuple(blockers))
