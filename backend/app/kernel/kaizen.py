"""Kaizen AI — the classification layer (Plugin Spec §7.2, §11, Part C).

    "Kaizen does not calculate savings. Core calculates savings; Kaizen
     classifies them and presents them."  — Plugin Spec §7

So every rupee that arrives here has already come out of :mod:`app.kernel.cost`.
This module decides which *column* it belongs in, ranks it against the rest of
the plant's queue, and turns an outcome into points. Nothing in it touches I/O.

THE INVARIANT (§7.1): for the same event, Core's loss screen and Kaizen's impact
card must show the same rupee figure. A supervisor who can open the downtime log
and the Kaizen card and see two different numbers for one tool breakage makes
every number in the platform negotiable. :func:`classify` therefore never
recomputes a value — it only splits the one Core handed it, and the split is
exact: ``hard + soft == core_value``, always.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from .money import D, Paise, round_paise

# ---------------------------------------------------------------------------
# What kind of saving is being claimed
# ---------------------------------------------------------------------------


class SavingType(str, Enum):
    """The saving types §7.2's table prices, and nothing else.

    The split that matters is not "which lean waste is this" but "does the
    bottleneck test apply to it" — see :data:`TIME_BASED` and :data:`CASH_BASED`.
    """

    CYCLE_TIME = "cycle_time"
    CHANGEOVER = "changeover"  # SMED
    MOTION = "motion"
    WAITING = "waiting"
    TRANSPORT = "transport"
    HANDLING = "handling"

    SCRAP = "scrap"
    REWORK = "rework"
    ENERGY = "energy"
    COMPRESSED_AIR = "compressed_air"
    CONSUMABLE = "consumable"
    TOOLING = "tooling"

    SAFETY = "safety"
    HOUSEKEEPING = "housekeeping"  # 5S with no time claim
    OTHER = "other"


#: Time recovered. Only money if that time was limiting the plant's output, so
#: the bottleneck factor splits it into hard and soft (§7.2).
TIME_BASED: frozenset[SavingType] = frozenset(
    {
        SavingType.CYCLE_TIME,
        SavingType.CHANGEOVER,
        SavingType.MOTION,
        SavingType.WAITING,
        SavingType.TRANSPORT,
        SavingType.HANDLING,
    }
)

#: Money not spent. Hard from the moment it is verified, bottleneck irrelevant:
#: *"material saved is cash saved"*. Applying the hard/soft split here would
#: under-report genuine cash savings, which loses credibility in the other
#: direction just as fast as over-claiming.
CASH_BASED: frozenset[SavingType] = frozenset(
    {
        SavingType.SCRAP,
        SavingType.REWORK,
        SavingType.ENERGY,
        SavingType.COMPRESSED_AIR,
        SavingType.CONSUMABLE,
        SavingType.TOOLING,
    }
)

#: Value is risk removed or order restored — never costed in rupees, shown as
#: stars on the Business Impact Card.
STARS_ONLY: frozenset[SavingType] = frozenset(
    {SavingType.SAFETY, SavingType.HOUSEKEEPING, SavingType.OTHER}
)


#: §6 of the Standalone Edition — the bottleneck question is asked only when the
#: answer changes the number, i.e. for a time-based claim on a real station.
def bottleneck_question_applies(saving_type: SavingType, *, has_station: bool) -> bool:
    """Whether "does this station ever make the line wait?" is worth asking.

    Asking a canteen Kaizen whether it makes the line wait wastes one of five
    question slots and makes the AI look stupid. In the plugin edition this
    question is usually skipped anyway — Core carries a Bottleneck Factor per
    machine — but it stays here because the same code runs standalone.
    """
    return has_station and saving_type in TIME_BASED


# ---------------------------------------------------------------------------
# The hard / soft / intangible split (§7.2) — the only calculation Kaizen owns
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SavingSplit:
    """One Core value, in the column it belongs in.

    ``hard + soft == core_value`` exactly. The two are reported side by side and
    never summed into one headline (§7.2, Standalone "Three tiers"): the owner's
    plant total counts ``hard`` only.
    """

    hard: Paise
    soft: Paise
    core_value: Paise
    saving_type: SavingType
    bottleneck_factor: Decimal
    basis: str

    @property
    def total(self) -> Paise:
        """Both columns added — for display beside the split, never *as* it."""
        return self.hard + self.soft


def classify(
    *,
    saving_type: SavingType,
    core_value: Paise,
    bottleneck_factor: Decimal | float | int | str = 1,
) -> SavingSplit:
    """Split one Core-calculated value into hard cash and soft capacity (§7.2).

    ``core_value`` is whatever Core's formula module returned for this claim —
    contribution value for a cycle-time claim, labour value for a motion claim,
    material value for a scrap claim. This function never recomputes it.

    The bottleneck factor is Core's BF for the affected machine (Core §8.2), and
    it applies to time-based claims only:

    * ``BF = 1`` — the station limits the plant, so time saved there becomes
      output that ships. All hard.
    * ``BF = 0`` — the machine simply idles longer and the plant ships exactly
      the same number of parts. All soft: real hours, not yet cash.
    * ``0 < BF < 1`` — partial buffer absorption, split pro rata.

    Rounding happens once, on the hard side; soft takes the remainder. That
    keeps the two columns summing to the figure Core reported, which is the
    whole point of §7.1's invariant.
    """
    value = int(core_value)
    if value < 0:
        raise ValueError("core_value cannot be negative")

    bf = D(bottleneck_factor)
    if not (D(0) <= bf <= D(1)):
        raise ValueError("bottleneck_factor must be between 0 and 1")

    if saving_type in STARS_ONLY:
        return SavingSplit(
            hard=0,
            soft=0,
            core_value=value,
            saving_type=saving_type,
            bottleneck_factor=bf,
            basis=(
                "Shown as stars, not rupees — the value here is risk removed and "
                "order restored, and costing that invents a number."
            ),
        )

    if saving_type in CASH_BASED:
        return SavingSplit(
            hard=value,
            soft=0,
            core_value=value,
            saving_type=saving_type,
            bottleneck_factor=bf,
            basis=(
                "Money not spent is money saved, whether or not this station is "
                "the bottleneck — so it counts in full once verified."
            ),
        )

    hard = round_paise(D(value) * bf)
    soft = value - hard
    if bf >= D(1):
        basis = (
            "This station limits the plant's output, so the time recovered "
            "becomes parts that ship — it counts as cash."
        )
    elif bf <= D(0):
        basis = (
            "This station is not the bottleneck. The time saved is real, but the "
            "plant ships the same number of parts today — so it is capacity, not "
            "cash, until it is redeployed or this station becomes the constraint."
        )
    else:
        basis = (
            f"This station is a partial constraint (bottleneck factor {bf}), so "
            "the recovered time is split: the part that adds output counts as "
            "cash, the rest as released capacity."
        )
    return SavingSplit(
        hard=hard,
        soft=soft,
        core_value=value,
        saving_type=saving_type,
        bottleneck_factor=bf,
        basis=basis,
    )


def combine(splits: Sequence[SavingSplit]) -> tuple[Paise, Paise]:
    """Total hard and total soft across several claims on one Kaizen."""
    return (
        sum(s.hard for s in splits),
        sum(s.soft for s in splits),
    )


# ---------------------------------------------------------------------------
# The Business Impact Card (§8 Standalone, §6.3 plugin)
# ---------------------------------------------------------------------------

#: Stars are 0-5 per dimension. Money is in rupees; anything that cannot be
#: costed is in stars; the two are never mixed into one figure.
STAR_DIMENSIONS: tuple[str, ...] = (
    "safety",
    "quality",
    "delivery",
    "morale",
    "compliance",
)


@dataclass(frozen=True, slots=True)
class ImpactStars:
    safety: int = 0
    quality: int = 0
    delivery: int = 0
    morale: int = 0
    compliance: int = 0

    def __post_init__(self) -> None:
        for name in STAR_DIMENSIONS:
            value = getattr(self, name)
            if not 0 <= value <= 5:
                raise ValueError(f"{name} stars must be 0-5, got {value}")

    def as_dict(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in STAR_DIMENSIONS}

    @classmethod
    def from_dict(cls, data: dict | None) -> ImpactStars:
        data = data or {}
        return cls(**{k: int(data.get(k, 0) or 0) for k in STAR_DIMENSIONS})


@dataclass(frozen=True, slots=True)
class CostOfDoingNothing:
    """Same number, opposite framing (§8).

    *"Annual saving ₹20,250" is a nice-to-have. "₹1,687 walks out of this
    factory every month you don't decide" is a decision.*
    """

    per_year: Paise
    per_month: Paise
    per_day: Paise
    since_submitted: Paise
    days_waiting: int


def cost_of_doing_nothing(
    *, annual_value: Paise, days_waiting: int = 0
) -> CostOfDoingNothing:
    """What the delay itself costs, growing daily on the approver's screen.

    Built on the *total* claim — hard plus soft — because the hours are being
    lost whether or not they have converted to cash yet, and the approver is
    being asked to decide about the hours.
    """
    annual = max(0, int(annual_value))
    days = max(0, int(days_waiting))
    per_day = round_paise(D(annual) / D(365))
    return CostOfDoingNothing(
        per_year=annual,
        per_month=round_paise(D(annual) / D(12)),
        per_day=per_day,
        since_submitted=per_day * days,
        days_waiting=days,
    )


def payback_months(*, investment: Paise, annual_saving: Paise) -> Decimal | None:
    """Months for the cash saving to repay the spend. ``None`` when it never does."""
    if investment <= 0:
        return D(0)
    if annual_saving <= 0:
        return None
    return (D(investment) / (D(annual_saving) / D(12))).quantize(D("0.1"))


def roi_percent(*, investment: Paise, annual_saving: Paise) -> Decimal | None:
    """First-year return on the spend, as a percentage."""
    if investment <= 0:
        return None
    return ((D(annual_saving) / D(investment)) * D(100)).quantize(D("0.1"))


# ---------------------------------------------------------------------------
# Priority scoring (§11) — never shown as a headline, only used to rank
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScoreWeights:
    """§11's weights, kept as data so a plant can be tuned in an afternoon.

    *"MAKE THE WEIGHTS CONFIGURATION, NOT CODE. These are a starting point. Tune
    after 20+ real Kaizens in a live plant... A weight that lives in a config
    table can be corrected in an afternoon; a weight compiled into the ranking
    service cannot."*
    """

    verified_cost: int = 0
    verified_frequency: int = 0
    hard_saving: int = 0
    ease: int = 0
    safety: int = 0
    quality: int = 0
    compliance: int = 0

    @property
    def total(self) -> int:
        return (
            self.verified_cost
            + self.verified_frequency
            + self.hard_saving
            + self.ease
            + self.safety
            + self.quality
            + self.compliance
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "verified_cost": self.verified_cost,
            "verified_frequency": self.verified_frequency,
            "hard_saving": self.hard_saving,
            "ease": self.ease,
            "safety": self.safety,
            "quality": self.quality,
            "compliance": self.compliance,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> ScoreWeights:
        if not data:
            return PLUGIN_WEIGHTS
        known = cls().as_dict()
        return cls(**{k: int(data.get(k, v) or 0) for k, v in known.items()})


#: §11 — the plugin's weights. Core-verified cost outranks projected savings,
#: because what the plant has already lost is a harder fact than what an
#: operator estimates it might save.
PLUGIN_WEIGHTS = ScoreWeights(
    verified_cost=25,
    verified_frequency=15,
    hard_saving=20,
    ease=15,
    safety=15,
    quality=10,
)

#: §11 — the standalone weights, used when there is no Core event history to
#: score against (a plant in its first weeks, or an office Kaizen).
STANDALONE_WEIGHTS = ScoreWeights(
    hard_saving=30,
    ease=20,
    safety=20,
    quality=15,
    compliance=15,
)


def _clamp01(value: Decimal) -> Decimal:
    return min(D(1), max(D(0), value))


def normalise(value: Paise | int, reference: Paise | int) -> Decimal:
    """Scale against this plant's own distribution, not an absolute rupee scale.

    §11.1: *"so the score means the same thing in a 30-machine plant and a
    300-machine plant."* The reference is the plant's top-decile recurring loss.
    """
    if reference <= 0:
        return D(0)
    return _clamp01(D(value) / D(reference))


def trend_factor(*, recent: int, prior: int) -> Decimal:
    """How much a rising problem outranks a flat one of the same total (§11.1).

    *"A problem occurring 11 times in the last 60 days versus 8 in the prior 60
    is getting worse and should outrank a flat-frequency problem of similar
    total cost."*

    Bounded to ±50% so one quiet prior period cannot make a small problem look
    like the plant's biggest. A first appearance with no prior period is treated
    as mildly rising, not as infinitely rising.
    """
    if prior > 0:
        ratio = D(recent) / D(prior)
        return min(D("1.5"), max(D("0.5"), ratio))
    return D("1.25") if recent > 0 else D(1)


@dataclass(frozen=True, slots=True)
class ScoreInputs:
    """Each component is a raw quantity; :func:`priority_score` normalises."""

    #: Core-verified rupees this problem has already cost, over the window.
    verified_cost: Paise = 0
    #: Core-verified event count over the window, and the two halves of it.
    verified_events: int = 0
    events_recent_half: int = 0
    events_prior_half: int = 0
    #: Projected hard saving — a projection, weighted below measured history.
    hard_saving: Paise = 0
    #: 1 (very hard) to 5 (very easy). Inverted below: easy scores high.
    ease: int = 3
    safety_stars: int = 0
    quality_stars: int = 0
    compliance_stars: int = 0

    #: The plant's own yardsticks — top-decile recurring loss and event count.
    cost_reference: Paise = 0
    frequency_reference: int = 0
    saving_reference: Paise = 0
    #: Where this problem sits in the plant's ranked recurring losses, 1 being
    #: the biggest. Drives the VERDICT the operator is shown, not the score.
    plant_rank: int | None = None


@dataclass(frozen=True, slots=True)
class PriorityScore:
    score: int  # 0-100
    verdict: str  # HIGH | MEDIUM | LOW — what the operator is told
    components: dict[str, Decimal] = field(default_factory=dict)
    weights: ScoreWeights = PLUGIN_WEIGHTS


def priority_score(
    inputs: ScoreInputs, weights: ScoreWeights = PLUGIN_WEIGHTS
) -> PriorityScore:
    """The hidden 0-100 score that ranks the queue (§11).

    Never shown as a headline: *"A numeric score invites arguments about the
    score. A verdict backed by the plant's own recorded history does not."* It
    exists to sort 400 Kaizens and to power the Quick Wins filter.
    """
    if weights.total <= 0:
        raise ValueError("score weights must sum to more than zero")

    frequency = normalise(
        inputs.verified_events, inputs.frequency_reference
    ) * trend_factor(recent=inputs.events_recent_half, prior=inputs.events_prior_half)

    components = {
        "verified_cost": normalise(inputs.verified_cost, inputs.cost_reference),
        "verified_frequency": _clamp01(frequency),
        "hard_saving": normalise(inputs.hard_saving, inputs.saving_reference),
        # Effort is rated 1 (hard) to 5 (easy); an easy fix scores high.
        "ease": _clamp01(D(max(1, min(5, inputs.ease)) - 1) / D(4)),
        "safety": _clamp01(D(inputs.safety_stars) / D(5)),
        "quality": _clamp01(D(inputs.quality_stars) / D(5)),
        "compliance": _clamp01(D(inputs.compliance_stars) / D(5)),
    }

    weighted = sum(components[name] * D(getattr(weights, name)) for name in components)
    score = int((weighted / D(weights.total) * D(100)).quantize(D(1)))
    score = max(0, min(100, score))

    return PriorityScore(
        score=score,
        verdict=verdict_for(score=score, plant_rank=inputs.plant_rank),
        components=components,
        weights=weights,
    )


#: A problem inside the plant's top few recurring losses is high priority however
#: the weighted components land. Everything else reads off the score.
TOP_RECURRING_RANK = 5
NOTABLE_RECURRING_RANK = 10


def verdict_for(*, score: int, plant_rank: int | None = None) -> str:
    """What the operator is told — a verdict, never a number (§11.2).

    *"Your idea is HIGH PRIORITY — this problem is the #4 recurring loss in the
    plant."* The rank is what makes that sentence persuasive, and it is a harder
    fact than any weighted score, so it can raise the verdict on its own. A
    numeric score invites arguments about the score; a verdict backed by the
    plant's own recorded history does not.
    """
    if score >= 70 or (plant_rank is not None and plant_rank <= TOP_RECURRING_RANK):
        return "HIGH"
    if score >= 40 or (plant_rank is not None and plant_rank <= NOTABLE_RECURRING_RANK):
        return "MEDIUM"
    return "LOW"


def is_quick_win(
    *, score: int, investment: Paise, needs_shutdown: bool, threshold: Paise
) -> bool:
    """§12.2's first dashboard view — high score, low cost, no shutdown.

    *"Quick Wins is the view that gets used in week 1. The rest is reporting,
    and reporting has no value until there's volume."*
    """
    return score >= 60 and investment <= threshold and not needs_shutdown


# ---------------------------------------------------------------------------
# Points, levels and the anti-gaming rules (Part C, Feature 4)
# ---------------------------------------------------------------------------


class PointsEvent(str, Enum):
    SUBMITTED = "submitted"
    APPROVED = "approved"
    IMPLEMENTED = "implemented"
    VERIFIED = "verified"
    SAFETY_IMPLEMENTED = "safety_implemented"
    REUSED_BY_ANOTHER_DEPARTMENT = "reused"
    DUPLICATE = "duplicate"


#: Part C's table, unchanged. The weighting towards *verified* outcomes is the
#: rule that protects the whole product: *"If you award points for submitting,
#: you will get 500 junk submissions a month — every one of which costs money to
#: process, clogs the approval queue, and buries the good ideas."*
POINTS: dict[PointsEvent, int] = {
    PointsEvent.SUBMITTED: 5,
    PointsEvent.APPROVED: 15,
    PointsEvent.IMPLEMENTED: 30,
    PointsEvent.VERIFIED: 50,
    PointsEvent.SAFETY_IMPLEMENTED: 25,
    PointsEvent.REUSED_BY_ANOTHER_DEPARTMENT: 40,
    #: A near-identical repeat of something already in the plant's history
    #: scores nothing *and does not clear the month's requirement*.
    PointsEvent.DUPLICATE: 0,
}

#: The value bonus on verification: +10 points per ₹1 lakh of verified annual
#: saving, capped so one very large Kaizen cannot make the leaderboard
#: uncontestable for a year.
VALUE_BONUS_PER_LAKH = 10
VALUE_BONUS_CAP = 100
LAKH = 100_000_00  # ₹1,00,000 in paise


def value_bonus(verified_annual_saving: Paise) -> int:
    """Bonus points for a verified saving, by size."""
    if verified_annual_saving <= 0:
        return 0
    lakhs = int(D(verified_annual_saving) / D(LAKH))
    return min(VALUE_BONUS_CAP, lakhs * VALUE_BONUS_PER_LAKH)


def points_for(event: PointsEvent, *, verified_annual_saving: Paise = 0) -> int:
    """Points for one outcome. Verification is the only one that scales."""
    base = POINTS[event]
    if event is PointsEvent.VERIFIED:
        return base + value_bonus(verified_annual_saving)
    return base


class Level(str, Enum):
    """Progression beats a points number (Feature 4)."""

    OPERATOR = "operator"
    IMPROVER = "improver"
    LEAN_CHAMPION = "lean_champion"
    PROBLEM_SOLVER = "problem_solver"
    INNOVATION_LEADER = "innovation_leader"
    SENSEI = "sensei"


#: Thresholds in points. The first two match the product document's worked
#: example ("IMPROVER, 210 pts... next level Lean Champion, 210/300").
LEVEL_THRESHOLDS: tuple[tuple[Level, int], ...] = (
    (Level.OPERATOR, 0),
    (Level.IMPROVER, 100),
    (Level.LEAN_CHAMPION, 300),
    (Level.PROBLEM_SOLVER, 700),
    (Level.INNOVATION_LEADER, 1500),
    (Level.SENSEI, 3000),
)

LEVEL_LABELS: dict[Level, str] = {
    Level.OPERATOR: "Operator",
    Level.IMPROVER: "Improver",
    Level.LEAN_CHAMPION: "Lean Champion",
    Level.PROBLEM_SOLVER: "Problem Solver",
    Level.INNOVATION_LEADER: "Innovation Leader",
    Level.SENSEI: "Sensei",
}


@dataclass(frozen=True, slots=True)
class LevelStanding:
    level: Level
    label: str
    points: int
    next_level: Level | None
    next_label: str | None
    points_to_next: int
    floor: int
    ceiling: int | None


def level_for(points: int) -> LevelStanding:
    """Where someone stands, and how far the next badge is."""
    points = max(0, int(points))
    index = 0
    for position, (_, threshold) in enumerate(LEVEL_THRESHOLDS):
        if points >= threshold:
            index = position
    level, floor = LEVEL_THRESHOLDS[index]
    nxt = LEVEL_THRESHOLDS[index + 1] if index + 1 < len(LEVEL_THRESHOLDS) else None
    return LevelStanding(
        level=level,
        label=LEVEL_LABELS[level],
        points=points,
        next_level=nxt[0] if nxt else None,
        next_label=LEVEL_LABELS[nxt[0]] if nxt else None,
        points_to_next=max(0, nxt[1] - points) if nxt else 0,
        floor=floor,
        ceiling=nxt[1] if nxt else None,
    )


class Board(str, Enum):
    """Two rankings, always shown side by side (Standalone, "DECIDED").

    *"If a quality engineer competes with a machine operator on the same
    ranking, the engineer wins every month... The operator sees a leaderboard
    they can never top and stops submitting."*
    """

    SHOPFLOOR = "shopfloor"  # operators, technicians
    SUPPORT = "support"  # supervisors, engineers, office


def participation_percent(*, submitted: int, headcount: int) -> Decimal:
    """Share of a department's people who submitted at least one this month.

    Ranked on this, never on raw count — otherwise a 60-person machine shop
    always beats a 4-person stores team, and stores stops trying.
    """
    if headcount <= 0:
        return D(0)
    return (D(min(submitted, headcount)) / D(headcount) * D(100)).quantize(D("0.1"))


__all__ = [
    "CASH_BASED",
    "LEVEL_LABELS",
    "LEVEL_THRESHOLDS",
    "PLUGIN_WEIGHTS",
    "POINTS",
    "STANDALONE_WEIGHTS",
    "STARS_ONLY",
    "STAR_DIMENSIONS",
    "TIME_BASED",
    "Board",
    "CostOfDoingNothing",
    "ImpactStars",
    "Level",
    "LevelStanding",
    "PointsEvent",
    "PriorityScore",
    "SavingSplit",
    "SavingType",
    "ScoreInputs",
    "ScoreWeights",
    "bottleneck_question_applies",
    "classify",
    "combine",
    "cost_of_doing_nothing",
    "is_quick_win",
    "level_for",
    "normalise",
    "participation_percent",
    "payback_months",
    "points_for",
    "priority_score",
    "roi_percent",
    "trend_factor",
    "value_bonus",
    "verdict_for",
]
