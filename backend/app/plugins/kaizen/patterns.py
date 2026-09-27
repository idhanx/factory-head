"""Pattern detection v2 — two sources, one cluster (Plugin Spec §10).

    "17 Kaizens across Machining, Assembly and Welding + 42 Core downtime events
     tagged 'tool search / tool missing' all point to ONE root cause: 'No
     standard tool storage at point of use'. Combined lost time 1,240 hrs/year.
     Core-verified cost ₹2,86,400/year (recorded, not estimated)."

The standalone edition clusters Kaizen submissions against each other and needs
60-90 days of volume before it fires. The plugin clusters across Core's event
log *and* Kaizen submissions, and is useful immediately.

Why both sources, when Core already detects recurring reason codes: a great many
plant problems never appear in a downtime log at all. Nobody logs a downtime
event for an awkward reach, a badly placed bin or an unlabelled valve. Those live
only in the operator's head until the Kaizen app gives them somewhere to go.

Two disciplines this module keeps:

* **Only Core events contribute to the rupee figure.** A cluster's value is
  "recorded, not estimated" — Kaizen's own projections never inflate it, or the
  alert stops being the thing that gets a plant head into a room.
* **A plant-wide project is ONE Core Action**, with the constituent Kaizens and
  events linked as children. Not seventeen separate Actions.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import kaizen_ai
from app.kernel import format_inr
from app.models import (
    Action,
    ActionTrigger,
    DowntimeEvent,
    KaizenIdea,
    KaizenPattern,
    KaizenSettings,
    KaizenStatus,
    KaizenTrack,
    PdcaState,
    Plant,
    ReasonCode,
    Station,
)
from app.services import actions as action_service
from app.services import kaizen as kaizen_service

#: How far back the weekly cluster job looks. Long enough that a problem
#: reported once a month in three departments still forms a cluster.
WINDOW_DAYS = 180

#: Two Kaizens join a cluster at this fingerprint overlap. Lower than the
#: duplicate threshold on purpose: a cluster is looking for a shared *root
#: cause* across different words, not for the same idea twice.
CLUSTER_THRESHOLD = 0.26

#: An event's note joins a cluster at this overlap. Higher, because downtime
#: notes are terse and a loose match would sweep in unrelated stoppages, and
#: every one of those adds rupees to a figure that must stay defensible.
EVENT_THRESHOLD = 0.34

#: Never scan the whole history into memory. A plant at 3,000 submissions a
#: year stays well inside this; anything larger is paged by the window.
MAX_ROWS = 2000


@dataclass(slots=True)
class Cluster:
    signature: list[str] = field(default_factory=list)
    ideas: list[KaizenIdea] = field(default_factory=list)
    events: list[tuple[uuid.UUID, int, int]] = field(default_factory=list)
    departments: set[str] = field(default_factory=set)

    @property
    def verified_cost(self) -> int:
        """Core-verified rupees only — recorded, never estimated."""
        return sum(cost for _, cost, _ in self.events)

    @property
    def lost_minutes(self) -> int:
        return sum(minutes for _, _, minutes in self.events)


async def detect(
    session: AsyncSession, *, plant: Plant, as_of: date | None = None
) -> list[KaizenPattern]:
    """The weekly cluster job. Returns every pattern currently over threshold.

    Idempotent: a cluster that was already raised is updated in place, so an
    alert a plant head has been looking at all week does not reappear as a new
    one on Friday with a different id.
    """
    as_of = as_of or date.today()
    config = await kaizen_service.settings_for(session, plant=plant)
    since = as_of - timedelta(days=WINDOW_DAYS)

    ideas = await _candidate_ideas(session, plant=plant, config=config, since=since)
    clusters = _cluster(ideas)
    if not clusters:
        return []

    stations = {
        row.id: row
        for row in (
            await session.scalars(select(Station).where(Station.plant_id == plant.id))
        ).all()
    }
    events = await _candidate_events(session, plant=plant, since=since)

    raised: list[KaizenPattern] = []
    for cluster in clusters:
        _attach_events(cluster, events)
        for idea in cluster.ideas:
            station = stations.get(idea.station_id)
            label = idea.department or (station.section.value if station else None)
            if label:
                cluster.departments.add(label)

        if not _over_threshold(cluster, config):
            continue
        raised.append(await _upsert(session, plant=plant, cluster=cluster, as_of=as_of))

    await session.flush()
    return raised


def _over_threshold(cluster: Cluster, config: KaizenSettings) -> bool:
    """Default 5+ related items OR ₹50,000+ combined Core-verified value.

    Quick Observations count towards the item threshold (they often carry the
    clearest description of a recurring nuisance) but carry no cost data, so
    they never move the value threshold.
    """
    items = len(cluster.ideas) + len(cluster.events)
    return (
        items >= config.pattern_min_items
        or cluster.verified_cost >= config.pattern_min_value_paise
    )


# ---------------------------------------------------------------------------
# Source 1 — Kaizen submissions
# ---------------------------------------------------------------------------


async def _candidate_ideas(
    session: AsyncSession, *, plant: Plant, config: KaizenSettings, since: date
) -> list[KaizenIdea]:
    query = select(KaizenIdea).where(
        KaizenIdea.plant_id == plant.id,
        KaizenIdea.submitted_on >= since,
        KaizenIdea.fingerprint.is_not(None),
        KaizenIdea.status != KaizenStatus.REJECTED,
    )
    if not config.patterns_include_quick:
        query = query.where(KaizenIdea.track == KaizenTrack.FULL)
    rows = (
        await session.scalars(query.order_by(KaizenIdea.submitted_on.desc()).limit(MAX_ROWS))
    ).all()
    return list(rows)


def _cluster(ideas: list[KaizenIdea]) -> list[Cluster]:
    """Greedy agglomeration on the meaning fingerprint.

    Greedy rather than k-means because the number of root causes in a plant is
    not known in advance and must not be guessed: a wrong k either merges two
    real problems into one project or splits one into two.

    Ideas are visited newest first, so a cluster's signature reflects the
    vocabulary the plant is using now rather than what it used six months ago.
    """
    clusters: list[Cluster] = []
    for idea in ideas:
        marks = idea.fingerprint or []
        if not marks:
            continue

        best: Cluster | None = None
        best_score = 0.0
        for cluster in clusters:
            score = _similarity(idea, cluster)
            if score > best_score:
                best, best_score = cluster, score

        if best is not None and best_score >= CLUSTER_THRESHOLD:
            best.ideas.append(idea)
            # The signature is the vocabulary the cluster genuinely shares.
            best.signature = sorted(set(best.signature) & set(marks)) or best.signature
        else:
            clusters.append(Cluster(signature=list(marks), ideas=[idea]))

    return [c for c in clusters if len(c.ideas) > 1]


def _similarity(idea: KaizenIdea, cluster: Cluster) -> float:
    """Cosine on embeddings where both sides have one; token overlap otherwise."""
    anchor = cluster.ideas[0]
    if idea.embedding and anchor.embedding:
        return kaizen_ai.cosine(
            [float(v) for v in idea.embedding], [float(v) for v in anchor.embedding]
        )
    return kaizen_ai.jaccard(idea.fingerprint or [], cluster.signature)


# ---------------------------------------------------------------------------
# Source 2 — Core's event log
# ---------------------------------------------------------------------------


async def _candidate_events(
    session: AsyncSession, *, plant: Plant, since: date
) -> list[tuple[uuid.UUID, int, int, list[str]]]:
    """Costed downtime events with something to match on, in the window."""
    rows = (
        await session.execute(
            select(
                DowntimeEvent.id,
                DowntimeEvent.cost_total,
                DowntimeEvent.duration_minutes,
                DowntimeEvent.note,
                ReasonCode.label,
            )
            .outerjoin(ReasonCode, ReasonCode.id == DowntimeEvent.reason_code_id)
            .where(
                DowntimeEvent.plant_id == plant.id,
                DowntimeEvent.business_date >= since,
            )
            .limit(MAX_ROWS * 4)
        )
    ).all()
    return [
        (
            row.id,
            int(row.cost_total or 0),
            int(row.duration_minutes or 0),
            kaizen_ai.fingerprint(row.note or "", row.label or ""),
        )
        for row in rows
    ]


def _attach_events(cluster: Cluster, events: list) -> None:
    """Core events that describe the same thing as the cluster."""
    for event_id, cost, minutes, marks in events:
        if not marks:
            continue
        if kaizen_ai.jaccard(cluster.signature, marks) >= EVENT_THRESHOLD:
            cluster.events.append((event_id, cost, minutes))


# ---------------------------------------------------------------------------
# Naming and persistence
# ---------------------------------------------------------------------------


def label_for(cluster: Cluster) -> str:
    """A plain description of the shared root cause.

    Deterministic, from the words the cluster actually shares. A generated
    label would read better and would occasionally be confidently wrong about
    what seventeen people reported, which is not a trade worth making on the
    screen that asks a plant head to spend ₹46,000.
    """
    counts = Counter()
    for idea in cluster.ideas:
        counts.update(idea.fingerprint or [])
    shared = [word for word, n in counts.most_common(6) if n >= max(2, len(cluster.ideas) // 2)]
    if not shared:
        shared = cluster.signature[:4]
    return " · ".join(shared[:5]) or "Related reports"


def recommendation_for(cluster: Cluster) -> str:
    """What the alert asks the plant head to decide.

    *"You are currently approving these one by one."* — which is the sentence
    that makes the pattern worth surfacing at all.
    """
    departments = ", ".join(sorted(cluster.departments)) or "several areas"
    parts = [
        f"{len(cluster.ideas)} Kaizen submissions across {departments}",
    ]
    if cluster.events:
        parts.append(
            f"{len(cluster.events)} recorded Core downtime events "
            f"costing {format_inr(cluster.verified_cost)}"
        )
    return (
        f"{' and '.join(parts)} point at one root cause. You are currently "
        "approving these one by one — a single plant-wide standard would close "
        "all of them together."
    )


async def _upsert(
    session: AsyncSession, *, plant: Plant, cluster: Cluster, as_of: date
) -> KaizenPattern:
    """Create or refresh the pattern row, matched on its signature."""
    existing = (
        await session.scalars(
            select(KaizenPattern).where(
                KaizenPattern.plant_id == plant.id,
                KaizenPattern.dismissed.is_(False),
            )
        )
    ).all()

    row = next(
        (
            candidate
            for candidate in existing
            if kaizen_ai.jaccard(candidate.signature or [], cluster.signature) >= 0.5
        ),
        None,
    )
    if row is None:
        row = KaizenPattern(plant_id=plant.id, detected_on=as_of)
        session.add(row)

    row.label = label_for(cluster)
    row.signature = cluster.signature
    row.idea_ids = [idea.id for idea in cluster.ideas]
    row.downtime_event_ids = [event_id for event_id, _, _ in cluster.events]
    row.departments = sorted(cluster.departments)
    row.kaizen_count = len(cluster.ideas)
    row.event_count = len(cluster.events)
    row.verified_cost_paise = cluster.verified_cost
    row.lost_minutes = cluster.lost_minutes
    row.recommendation = recommendation_for(cluster)
    await session.flush()
    return row


# ---------------------------------------------------------------------------
# Turning a pattern into ONE Core Action
# ---------------------------------------------------------------------------


async def create_project(
    session: AsyncSession,
    *,
    plant: Plant,
    pattern: KaizenPattern,
    owner_name: str | None = None,
    due_date: date | None = None,
    estimated_cost_paise: int = 0,
) -> Action:
    """*"[ Create Plant-Wide Project ]"* — one Action, with everything linked.

    Not seventeen Actions. The constituent Kaizens are marked accepted against
    this one Action, so their originators all receive credit when Core verifies
    the saving, and the Actions Board gains one row rather than seventeen.
    """
    if pattern.action_id is not None:
        raise ValueError("This pattern already has a plant-wide project.")

    reference = await action_service._next_reference(session, plant.id)
    action = Action(
        plant_id=plant.id,
        reference=reference,
        title=f"Plant-wide: {pattern.label}",
        description=pattern.recommendation,
        trigger=ActionTrigger.KAIZEN,
        trigger_rationale=(
            f"Pattern detected across {pattern.kaizen_count} Kaizen submissions and "
            f"{pattern.event_count} recorded Core events, costing "
            f"{format_inr(pattern.verified_cost_paise)} over the last six months."
        ),
        state=PdcaState.OPEN,
        linked_cost=pattern.verified_cost_paise,
        owner_name=owner_name or "Plant Head",
        due_date=due_date or (date.today() + timedelta(days=30)),
        surfaced_in_meeting_on=date.today(),
    )
    session.add(action)
    await session.flush()

    pattern.action_id = action.id

    ideas = (
        await session.scalars(
            select(KaizenIdea).where(
                KaizenIdea.plant_id == plant.id,
                KaizenIdea.id.in_(pattern.idea_ids or []),
            )
        )
    ).all()
    now = datetime.now(UTC)
    for idea in ideas:
        if idea.action_id is not None:
            continue
        idea.action_id = action.id
        idea.status = KaizenStatus.ACCEPTED
        idea.decided_at = idea.decided_at or now
        idea.decision_note = (
            f"Rolled into the plant-wide project {reference} with "
            f"{pattern.kaizen_count - 1} other reports of the same root cause."
        )
        await kaizen_service.award(
            session,
            plant=plant,
            idea=idea,
            event=kaizen_service.PointsEvent.APPROVED,
        )

    # The baseline is frozen here as for any other Action, so the plant-wide
    # project's saving is measured the same conservative way (§9).
    action.baseline_weekly_loss = await action_service.compute_baseline(
        session,
        plant_id=plant.id,
        station_id=None,
        reason_code_id=None,
        as_of=date.today(),
    )
    action.baseline_frozen_at = now
    if estimated_cost_paise:
        action.description = (
            f"{action.description}\n\nEstimated cost: {format_inr(estimated_cost_paise)}."
        )

    await session.flush()
    return action


async def open_patterns(
    session: AsyncSession, *, plant: Plant, limit: int = 20
) -> list[KaizenPattern]:
    """Everything the detector has raised and nobody has acted on or dismissed."""
    return list(
        (
            await session.scalars(
                select(KaizenPattern)
                .where(
                    KaizenPattern.plant_id == plant.id,
                    KaizenPattern.dismissed.is_(False),
                    KaizenPattern.action_id.is_(None),
                )
                .order_by(KaizenPattern.verified_cost_paise.desc())
                .limit(limit)
            )
        ).all()
    )


async def count_open(session: AsyncSession, *, plant: Plant) -> int:
    return int(
        await session.scalar(
            select(func.count(KaizenPattern.id)).where(
                KaizenPattern.plant_id == plant.id,
                KaizenPattern.dismissed.is_(False),
                KaizenPattern.action_id.is_(None),
            )
        )
        or 0
    )
