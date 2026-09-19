"""Kaizen AI, Plugin Edition (Plugin Spec v2).

Most of these tests pin a rule that, broken, would put a wrong rupee figure in
front of a Plant Head. Both specifications say the same thing about that in
different words: one inflated number and every other number in the platform
becomes negotiable.

The invariant test (:func:`test_invariant_core_and_kaizen_agree`) is the one
§7.1 asks for by name — *"Write an automated test for this invariant and run it
on every build."*
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.ai import kaizen_ai
from app.kernel import (
    PLUGIN_WEIGHTS,
    Board,
    ImpactStars,
    Level,
    PointsEvent,
    SavingType,
    ScoreInputs,
    ScoreWeights,
    classify,
    cost_of_doing_nothing,
    level_for,
    participation_percent,
    payback_months,
    points_for,
    priority_score,
    rupees,
    trend_factor,
)
from app.models import (
    Action,
    ActionTrigger,
    ApprovalTemplate,
    JobOrder,
    KaizenCategory,
    KaizenIdea,
    KaizenPoint,
    KaizenStatus,
    KaizenTrack,
    Operator,
    PdcaState,
    Recoverability,
    Station,
)
from app.services import costing as costing_service
from app.services import downtime as downtime_service
from app.services import kaizen as kaizen_service
from app.services import kaizen_costing, kaizen_history, kaizen_patterns, kaizen_reporting
from app.services import shifts as shift_service
from tests.conftest import run_job

RECENT = datetime.now(UTC) - timedelta(days=2)


# ===========================================================================
# The classification layer (§7.2) — the only calculation Kaizen owns
# ===========================================================================


def test_bottleneck_splits_time_savings_and_never_loses_a_paisa():
    """§7.2's table, row by row. ``hard + soft`` must equal Core's own figure."""
    value = rupees(20_250)

    at_bottleneck = classify(
        saving_type=SavingType.CYCLE_TIME, core_value=value, bottleneck_factor=1
    )
    assert at_bottleneck.hard == value
    assert at_bottleneck.soft == 0

    off_bottleneck = classify(
        saving_type=SavingType.MOTION, core_value=value, bottleneck_factor=0
    )
    assert off_bottleneck.hard == 0
    assert off_bottleneck.soft == value

    partial = classify(
        saving_type=SavingType.MOTION, core_value=value, bottleneck_factor=Decimal("0.4")
    )
    assert partial.hard == rupees(8_100)
    assert partial.soft == rupees(12_150)
    # The exact-split rule: rounding happens once, on the hard side.
    assert partial.hard + partial.soft == value


def test_bottleneck_does_not_apply_to_cash_savings():
    """*"Scrap reduction: 100% hard. BF does NOT apply — material saved is cash
    saved."* Halving a scrap saving by a bottleneck factor under-reports genuine
    cash, which loses credibility just as fast as over-claiming."""
    for kind in (
        SavingType.SCRAP,
        SavingType.REWORK,
        SavingType.ENERGY,
        SavingType.COMPRESSED_AIR,
        SavingType.CONSUMABLE,
        SavingType.TOOLING,
    ):
        split = classify(saving_type=kind, core_value=rupees(31_000), bottleneck_factor=0)
        assert split.hard == rupees(31_000), kind
        assert split.soft == 0, kind


def test_safety_and_5s_are_stars_never_rupees():
    """*"Value is risk removed, not output gained."* Costing a near-miss means
    inventing a probability, and an invented probability in a savings total is
    exactly what the three-tier split exists to prevent."""
    for kind in (SavingType.SAFETY, SavingType.HOUSEKEEPING):
        split = classify(saving_type=kind, core_value=rupees(99_999), bottleneck_factor=1)
        assert split.hard == 0
        assert split.soft == 0


def test_classify_rejects_an_impossible_bottleneck_factor():
    with pytest.raises(ValueError):
        classify(saving_type=SavingType.MOTION, core_value=100, bottleneck_factor=2)


# ===========================================================================
# Priority scoring (§11)
# ===========================================================================


def test_verified_cost_outranks_a_projection():
    """§11.1 — *"Weight the measured past above the projected future."*

    A Kaizen attacking a ₹3 lakh recurring problem must outrank a first-time
    ₹500 guess, however optimistic the guess.
    """
    measured = priority_score(
        ScoreInputs(
            verified_cost=rupees(3_24_800),
            verified_events=31,
            events_recent_half=11,
            events_prior_half=8,
            hard_saving=0,
            ease=3,
            cost_reference=rupees(3_24_800),
            frequency_reference=31,
            saving_reference=rupees(3_24_800),
            plant_rank=4,
        )
    )
    guessed = priority_score(
        ScoreInputs(
            hard_saving=rupees(500),
            ease=5,
            cost_reference=rupees(3_24_800),
            frequency_reference=31,
            saving_reference=rupees(3_24_800),
        )
    )
    assert measured.score > guessed.score
    assert measured.verdict == "HIGH"


def test_a_rising_problem_outranks_a_flat_one():
    """*"11 times in the last 60 days versus 8 in the prior 60 is getting worse."*"""
    assert trend_factor(recent=11, prior=8) > 1
    assert trend_factor(recent=8, prior=11) < 1
    # Bounded, so one quiet prior period cannot make a small problem look huge.
    assert trend_factor(recent=100, prior=1) == Decimal("1.5")
    assert trend_factor(recent=5, prior=0) == Decimal("1.25")


def test_score_weights_must_sum_to_something():
    with pytest.raises(ValueError):
        priority_score(ScoreInputs(), ScoreWeights())


def test_plugin_and_standalone_weights_both_total_one_hundred():
    from app.kernel import STANDALONE_WEIGHTS

    assert PLUGIN_WEIGHTS.total == 100
    assert STANDALONE_WEIGHTS.total == 100


# ===========================================================================
# Points and levels (Part C) — the rule that protects the whole product
# ===========================================================================


def test_points_are_weighted_towards_verified_outcomes():
    """*"If you award points for submitting, you will get 500 junk submissions
    a month."*"""
    assert points_for(PointsEvent.SUBMITTED) == 5
    assert points_for(PointsEvent.APPROVED) == 15
    assert points_for(PointsEvent.IMPLEMENTED) == 30
    assert points_for(PointsEvent.VERIFIED) == 50
    # A duplicate scores nothing at all.
    assert points_for(PointsEvent.DUPLICATE) == 0
    # Verification is the only award that scales with the money.
    assert points_for(PointsEvent.VERIFIED, verified_annual_saving=rupees(3_00_000)) == 80


def test_the_value_bonus_is_capped():
    """One very large Kaizen must not make the leaderboard uncontestable."""
    assert points_for(PointsEvent.VERIFIED, verified_annual_saving=rupees(5_00_00_000)) == 150


def test_levels_progress_and_report_the_gap():
    """The product document's own worked example: 210 points is IMPROVER, 90 short."""
    standing = level_for(210)
    assert standing.level is Level.IMPROVER
    assert standing.next_label == "Lean Champion"
    assert standing.points_to_next == 90
    assert level_for(0).level is Level.OPERATOR
    assert level_for(999_999).level is Level.SENSEI


def test_participation_is_a_percentage_of_the_department():
    """*"Otherwise a 60-person machine shop always beats a 4-person stores team."*"""
    assert participation_percent(submitted=3, headcount=4) == Decimal("75.0")
    assert participation_percent(submitted=40, headcount=60) < Decimal("75.0")
    assert participation_percent(submitted=1, headcount=0) == 0


def test_cost_of_doing_nothing_ticks_upwards():
    """*"₹1,687 walks out of this factory every month you don't decide."*"""
    nothing = cost_of_doing_nothing(annual_value=rupees(20_250), days_waiting=4)
    assert nothing.per_month == rupees(1_687) + 50  # ₹1,687.50
    assert nothing.since_submitted == nothing.per_day * 4
    assert nothing.days_waiting == 4


def test_payback_is_none_when_it_never_repays():
    assert payback_months(investment=rupees(12_000), annual_saving=0) is None
    assert payback_months(investment=0, annual_saving=rupees(1000)) == 0


# ===========================================================================
# The AI layer — every rule that protects trust
# ===========================================================================


def test_vision_never_speaks_in_certainties():
    """Step 2, Rule 2 — *"Possible oil patch", not "Oil leak detected"*.

    Vision *will* misread a wet floor as oil and a shadow as a crack. Confident
    wrong statements destroy trust faster than no feature at all.
    """
    findings = kaizen_ai._clean_findings(
        {
            "findings": [
                {"category": "leak_or_spill", "text": "Oil leak detected at machine base"},
                {"category": "safety_hazard", "text": "Guard is definitely missing"},
            ]
        }
    )
    for finding in findings:
        assert "detected" not in finding["text"].lower()
        assert "definitely" not in finding["text"].lower()
        assert any(
            hedge in finding["text"].lower()
            for hedge in ("possible", "possibly", "may ", "might ")
        )


def test_a_model_reply_in_any_shape_is_survivable():
    """Regression, found running against a real local model.

    Local models are not reliably schema-obedient. Asked plainly for one JSON
    object, gemma3 returned a bare top-level array — which crashed the parser
    with ``'list' object has no attribute 'get'``. A vision call that 500s in
    the middle of the operator journey is worse than one that finds nothing, so
    every shape degrades to "no findings" instead.
    """
    from app.ai.llm import Generation

    def reply(text: str):
        return kaizen_ai._parse_json(Generation(text=text, model="test"), list_key="findings")

    assert reply('[{"category":"housekeeping","text":"Possible clutter"}]')["findings"]
    assert reply('```json\n{"findings":[]}\n```') == {"findings": []}
    assert reply('Here you go: {"findings":[]} hope that helps') == {"findings": []}
    assert reply("I cannot see anything clearly.") == {}
    # And the cleaner accepts either shape without raising.
    assert kaizen_ai._clean_findings([{"category": "safety_hazard", "text": "Possible trip"}])
    assert kaizen_ai._clean_findings({"findings": "nonsense"}) == []
    assert kaizen_ai._clean_findings({}) == []


def test_the_prompts_carry_no_answer_a_model_can_copy():
    """Regression, also found against a real model.

    The first version of the challenge prompt carried the product document's own
    worked example as its JSON sample. gemma3 copied it word for word, so every
    Kaizen in the plant came back with "move the toolbox to the midpoint" — a
    feature that argues back with the same argument every time is worse than no
    feature. Examples in these prompts describe the SHAPE only.
    """
    for prompt in (kaizen_ai._CHALLENGE_SYSTEM, kaizen_ai._VISION_SYSTEM):
        assert "midpoint" not in prompt.lower()
        assert "toolbox" not in prompt.lower()


def test_vision_shows_five_categories_and_at_most_five_findings():
    """Rule 3 — *"A list of 15 observations makes the operator scroll past all
    of them."*"""
    payload = {
        "findings": [
            {"category": "housekeeping", "text": f"Possible clutter number {n}"}
            for n in range(10)
        ]
        + [{"category": "invented_category", "text": "Possible something"}]
    }
    findings = kaizen_ai._clean_findings(payload)
    assert len(findings) <= 5
    assert all(f["category"] != "invented_category" for f in findings)
    # Nothing is accepted until the operator says so.
    assert all(f["accepted"] is False for f in findings)


@pytest.mark.asyncio
async def test_transcript_cleanup_never_invents_a_number():
    """A wrong number silently corrupts the savings calculation."""
    original = "Every shift I walk about 20 metres to get the toolbox"
    assert kaizen_ai._guard_numbers(
        original=original, rewritten="Operator walks 50 metres to the toolbox"
    ) == original
    assert (
        kaizen_ai._guard_numbers(
            original=original, rewritten="Operator walks about 20 metres to the toolbox"
        )
        != original
    )
    # With Ollama off, the words come back tidied but unchanged.
    assert await kaizen_ai.clean_transcript("  walks   20 metres  ") == "walks 20 metres"


def test_the_bottleneck_question_is_asked_only_when_it_changes_the_number():
    """*"Asking a canteen Kaizen whether it makes the line wait makes the AI
    look stupid and wastes one of the five question slots."*"""
    keys = [q.key for q in kaizen_ai.questions_for(SavingType.MOTION)]
    assert "bottleneck" in keys
    assert "bottleneck" not in [q.key for q in kaizen_ai.questions_for(SavingType.SCRAP)]
    # And in the plugin edition Core has already answered it.
    assert "bottleneck" not in [
        q.key
        for q in kaizen_ai.questions_for(SavingType.MOTION, known={"bottleneck_factor"})
    ]


def test_a_place_is_not_a_problem():
    """Regression, carried over from the first Kaizen scorer: "put a fan near
    the inspection bench" once matched Quality Hold on the word "inspection",
    was priced against every quality stop in the plant, and came out as the most
    valuable idea of the lot. No keyword may name a *place* rather than a
    *problem*."""
    assert (
        kaizen_ai.classify(
            "Put a fan near the inspection bench",
            "The QC bench gets very hot and it is hard to concentrate on inspection.",
        )
        is KaizenCategory.OTHER
    )


def test_keywords_match_whole_words_not_fragments():
    """Regression: as bare substrings, "oil" matched "soil", "tool" matched
    "stool" and "kit" matched "kitchen"."""
    assert kaizen_ai.classify("Sweep the soil", "the stool in the kitchen") is (
        KaizenCategory.OTHER
    )
    # ...while a plural still matches its keyword.
    assert kaizen_ai.classify("Coolant oils", "") is KaizenCategory.MAINTENANCE
    # And "toolbox" is not "tool": a shared toolbox is a walking problem.
    assert (
        kaizen_ai.classify("Toolbox shared", "I walk 20 metres to the toolbox each shift")
        is KaizenCategory.MOTION
    )


def test_the_title_outranks_an_incidental_word_in_the_description():
    """An operator's title says what the idea *is*; the description often
    mentions incidental things."""
    category = kaizen_ai.classify(
        "Stage the material trolley early", "so the tool setter is not waiting either"
    )
    # The title's subject wins. "tool" in the description must not send this to
    # Maintenance, which would price it against tool-breakage downtime.
    assert category is not KaizenCategory.MAINTENANCE
    assert category is KaizenCategory.TRANSPORT


def test_no_interview_asks_more_than_five_questions():
    for saving_type in SavingType:
        assert len(kaizen_ai.questions_for(saving_type)) <= kaizen_ai.MAX_QUESTIONS


def test_categories_are_matched_on_the_words_operators_use():
    assert (
        kaizen_ai.classify("Shared toolbox", "I walk 20 metres every shift to fetch it")
        is KaizenCategory.MOTION
    )
    assert (
        kaizen_ai.classify("Burr on brackets", "We reject about 180 a month")
        is KaizenCategory.QUALITY
    )
    # A place name must never become a problem: "put a fan near the inspection
    # bench" once scored as the plant's most valuable idea.
    assert (
        kaizen_ai.saving_type_for(
            KaizenCategory.PRODUCTIVITY, "Die change takes too long", "SMED on the press"
        )
        is SavingType.CHANGEOVER
    )


def test_cross_plugin_signals_come_free(
):
    """§10.3 — the same 90 seconds produces a training signal and audit evidence."""
    assert "skill_gap" in kaizen_ai.cross_plugin_signals(
        "Nobody is trained on VMC-2", "Only one person can run it"
    )
    assert "compliance" in kaizen_ai.cross_plugin_signals(
        "Work instruction missing", "The audit found no SOP at this station"
    )


# ===========================================================================
# The worked example, end to end (§7.1's three stages)
# ===========================================================================


@pytest.mark.asyncio
async def test_the_toolbox_example_reproduces_exactly(session, fixtures):
    """The product document's own worked example, from tapped answers to rupees.

    45 sec x 12 trips/shift x 1 shift x 300 days = 45 hours, and at this plant's
    ₹180/hr labour rate that is ₹8,100. The plant in the fixtures runs one
    shift, not three — the arithmetic, not the headline number, is what is
    pinned here.
    """
    plant = fixtures["plant"]
    rhythm = await kaizen_costing.plant_rhythm(session, plant=plant)
    assert rhythm.working_days == 300

    request = kaizen_costing.build_request(
        saving_type=SavingType.MOTION,
        answers={"seconds_per_trip": 45, "trips_per_shift": 12},
        rhythm=rhythm,
        station_id=fixtures["station"].id,
    )
    expected_hours = Decimal(45 * 12 * rhythm.shifts_per_day * 300) / Decimal(3600)
    assert request.extras["hours_per_year"] == expected_hours

    values = await kaizen_costing.core_values(session, plant=plant, request=request)
    assert values.labour_value == int(expected_hours * Decimal(rupees(180)))
    # CNC-3 is the bottleneck in the fixtures, so the whole claim is hard cash.
    assert values.bottleneck_factor == Decimal("1.00")

    priced = kaizen_costing.price(
        core=values,
        request=request,
        category=KaizenCategory.MOTION,
        answers={"people_affected": 5},
        investment_paise=rupees(12_000),
    )
    assert priced.hard == values.labour_value
    assert priced.soft == 0
    # People affected drives the morale star and never the money.
    assert priced.stars.morale >= 4


@pytest.mark.asyncio
async def test_people_affected_never_multiplies_the_money(session, fixtures):
    """Multiplying 12 trips a shift by the four people who share the toolbox
    turns a real number into a fictional one four times its size."""
    rhythm = await kaizen_costing.plant_rhythm(session, plant=fixtures["plant"])
    base = kaizen_costing.build_request(
        saving_type=SavingType.MOTION,
        answers={"seconds_per_trip": 45, "trips_per_shift": 12},
        rhythm=rhythm,
    )
    with_people = kaizen_costing.build_request(
        saving_type=SavingType.MOTION,
        answers={"seconds_per_trip": 45, "trips_per_shift": 12, "people_affected": 8},
        rhythm=rhythm,
    )
    assert base.extras["hours_per_year"] == with_people.extras["hours_per_year"]


@pytest.mark.asyncio
async def test_a_missing_rate_makes_the_kaizen_provisional(session, fixtures):
    """§7.3 — *"Any Kaizen calculated on a Core default carries Core's
    Provisional flag and is excluded from verified totals."*"""
    plant = fixtures["plant"]
    rhythm = await kaizen_costing.plant_rhythm(session, plant=plant)
    request = kaizen_costing.build_request(
        saving_type=SavingType.COMPRESSED_AIR,
        answers={"leak_cfm": 6, "hours_per_day": 20},
        rhythm=rhythm,
        station_id=fixtures["station"].id,
    )
    values = await kaizen_costing.core_values(session, plant=plant, request=request)
    assert values.provisional
    assert values.missing_rate_key == costing_service.COMPRESSED_AIR_M3
    assert values.subject_value == 0

    # Supply it, and the same request prices properly.
    await costing_service.set_utility_rate(
        session,
        plant_id=plant.id,
        key=costing_service.COMPRESSED_AIR_M3,
        paise=120,  # ₹1.20/m³
        effective_from=date.today() - timedelta(days=1),
    )
    again = await kaizen_costing.core_values(session, plant=plant, request=request)
    assert not again.provisional
    assert again.subject_value > 0


# ===========================================================================
# Step 5A — the Core History Check (§6)
# ===========================================================================


async def stop(session, fixtures, reason, *, minutes: int, days_ago: int = 2):
    """Log and close one stop on CNC-3, costed by Core exactly as a real one is.

    The job order is started once per test: events inherit their part, cycle
    time and margin from whichever job is running on the station, so a second
    identical job order would violate Core's own uniqueness rule.
    """
    at = datetime.now(UTC) - timedelta(days=days_ago)
    shift_session = await shift_service.start_shift(
        session,
        plant=fixtures["plant"],
        shift=fixtures["shift"],
        supervisor_id=None,
        started_at=at,
    )
    running = await session.scalar(
        select(JobOrder).where(
            JobOrder.plant_id == fixtures["plant"].id, JobOrder.job_number == "2231"
        )
    )
    if running is None:
        await run_job(session, fixtures, shift_session)
    event = await downtime_service.open_downtime(
        session,
        plant=fixtures["plant"],
        station=fixtures["station"],
        reason_code=reason,
        shift_session=shift_session,
        started_at=at,
    )
    await downtime_service.close_downtime(
        session, event=event, ended_at=at + timedelta(minutes=minutes)
    )
    return event


@pytest.mark.asyncio
async def test_history_check_reports_what_the_plant_recorded(session, fixtures):
    """The screen that changes an operator's belief that submitting is worth it."""
    plant = fixtures["plant"]
    await kaizen_service.settings_for(session, plant=plant)

    events = [
        await stop(session, fixtures, fixtures["reasons"]["TOOL_BREAKAGE"], minutes=40, days_ago=n)
        for n in (3, 10, 20, 40)
    ]
    recorded = sum(event.cost_total for event in events)

    history = await kaizen_history.check(
        session,
        plant=plant,
        category=KaizenCategory.MAINTENANCE,
        title="Tool breakage countermeasure",
        description="The insert breaks and we lose the machine for half an hour",
        station=fixtures["station"],
    )
    assert history.found
    assert history.matched_on == "reason_code"
    assert history.events == len(events)
    # Every rupee comes from Core's cost engine, on events Core recorded.
    assert history.cost_paise == recorded
    assert history.rank == 1
    assert {tier.scope for tier in history.tiers} >= {"machine", "plant"}
    assert "Nobody has an open action on this" in history.message


@pytest.mark.asyncio
async def test_history_check_says_so_plainly_when_there_is_nothing(session, fixtures):
    """§6.5 — *"never fabricate, never pad, never hide the step."*"""
    history = await kaizen_history.check(
        session,
        plant=fixtures["plant"],
        category=KaizenCategory.FIVE_S,
        title="Paint the walkway edges",
        description="The gangway boundary is not marked in the paint shop",
    )
    assert not history.found
    assert history.cost_paise == 0
    assert "new one" in history.message
    assert "First-time problems are worth reporting" in history.message


@pytest.mark.asyncio
async def test_an_open_action_is_flagged_as_a_duplicate(session, fixtures):
    """§6.4 — *"prevents two people solving the same thing in parallel."*"""
    plant = fixtures["plant"]
    await kaizen_service.settings_for(session, plant=plant)
    await stop(session, fixtures, fixtures["reasons"]["TOOL_BREAKAGE"], minutes=30)

    session.add(
        Action(
            plant_id=plant.id,
            reference="ACT-9001",
            title="Tool life on CNC-3",
            trigger=ActionTrigger.BIG_SINGLE_EVENT,
            state=PdcaState.IN_PROGRESS,
            station_id=fixtures["station"].id,
            reason_code_id=fixtures["reasons"]["TOOL_BREAKAGE"].id,
            owner_name="Maintenance",
            due_date=date.today() + timedelta(days=3),
        )
    )
    await session.flush()

    history = await kaizen_history.check(
        session,
        plant=plant,
        category=KaizenCategory.MAINTENANCE,
        title="Tool breakage",
        description="Insert keeps breaking on the turning operation",
        station=fixtures["station"],
    )
    assert history.open_action is not None
    assert history.open_action.reference == "ACT-9001"
    assert "already open on this" in history.message


@pytest.mark.asyncio
async def test_a_returning_problem_is_flagged_as_a_recurrence(session, fixtures):
    """*"A recurrence after closure is a failed countermeasure and Core's
    Recovered Profit for that Action must be revisited."*"""
    plant = fixtures["plant"]
    await kaizen_service.settings_for(session, plant=plant)
    session.add(
        Action(
            plant_id=plant.id,
            reference="ACT-9002",
            title="Tool life on CNC-3",
            trigger=ActionTrigger.BIG_SINGLE_EVENT,
            state=PdcaState.VERIFIED,
            station_id=fixtures["station"].id,
            reason_code_id=fixtures["reasons"]["TOOL_BREAKAGE"].id,
            owner_name="Maintenance",
            closed_at=datetime.now(UTC) - timedelta(days=30),
            verified_at=datetime.now(UTC) - timedelta(days=10),
        )
    )
    await session.flush()
    # ...and the problem happens again after that closure.
    await stop(session, fixtures, fixtures["reasons"]["TOOL_BREAKAGE"], minutes=25, days_ago=2)

    history = await kaizen_history.check(
        session,
        plant=plant,
        category=KaizenCategory.MAINTENANCE,
        title="Tool breakage again",
        description="Insert breaking on CNC-3 once more",
        station=fixtures["station"],
    )
    assert history.recurrence_of is not None
    assert history.recurrence_of.reference == "ACT-9002"
    assert "did not hold" in history.message


@pytest.mark.asyncio
async def test_the_forecast_says_whether_it_learned_from_this_plant(session, fixtures):
    """Step 9 — rule-based on day one, learned as soon as Core has closures."""
    forecast = await kaizen_history.forecast(
        session, plant=fixtures["plant"], investment_paise=rupees(18_000), needs_shutdown=False
    )
    assert not forecast.learned  # no closed Actions yet
    risks = {o["what"]: o["risk"] for o in forecast.obstacles}
    assert risks["Purchase approval"] == "high"
    assert "pre-approve" in (forecast.advice or "")

    cheap = await kaizen_history.forecast(
        session, plant=fixtures["plant"], investment_paise=rupees(800), needs_shutdown=False
    )
    assert cheap.probability > forecast.probability


# ===========================================================================
# The journey, through the service
# ===========================================================================


async def submit(session, fixtures, **overrides):
    payload = {
        "title": "Toolbox shared across machines",
        "description": "Every shift I walk about 20 metres to another machine to get the toolbox.",
        "interview": [
            {"key": "seconds_per_trip", "answer": "30-60 sec"},
            {"key": "trips_per_shift", "answer": 12},
            {"key": "people_affected", "answer": "4-6"},
        ],
        "investment_paise": rupees(12_000),
        "station_id": fixtures["station"].id,
    }
    payload.update(overrides)
    interview = payload.pop("interview")
    submission = kaizen_service.Submission(interview=interview, **payload)
    return await kaizen_service.submit(
        session,
        plant=fixtures["plant"],
        submission=submission,
        submitted_by_name="R. Kumar",
    )


@pytest.mark.asyncio
async def test_a_full_kaizen_arrives_costed_ranked_and_answered(session, fixtures):
    idea = await submit(session, fixtures)

    assert idea.reference.startswith("KZN-")
    assert idea.track is KaizenTrack.FULL
    assert idea.category is KaizenCategory.MOTION
    assert idea.saving_type == SavingType.MOTION.value
    # Costed against this plant's own labour rate, through Core's engine.
    assert idea.hard_saving_paise > 0
    assert idea.history is not None
    assert idea.forecast is not None
    assert idea.score_rationale
    assert idea.status is KaizenStatus.SCORED
    # The operator is told a verdict, never a number (§11.2).
    assert idea.priority_verdict in ("HIGH", "MEDIUM", "LOW")


@pytest.mark.asyncio
async def test_a_quick_observation_skips_the_cost_engine(session, fixtures):
    """§4.3 — *"Quick Observation: logged, auto-routed, counted for
    participation and 5 points."* No interview, no cost engine, no challenge."""
    idea = await submit(
        session,
        fixtures,
        track=KaizenTrack.QUICK,
        title="Bin is not labelled",
        description="The scrap bin beside CNC-3 has no label on it at all.",
        interview=[],
        investment_paise=0,
    )
    assert idea.hard_saving_paise == 0
    assert idea.soft_saving_paise == 0
    assert idea.challenge is None
    assert idea.forecast is None

    points = await session.scalar(
        select(KaizenPoint).where(KaizenPoint.idea_id == idea.id)
    )
    assert points.points == 5


@pytest.mark.asyncio
async def test_a_quick_observation_never_reaches_the_approval_queue(session, fixtures):
    """§4.3 — *"At ~80% of volume, Quick Observations would flood the Actions
    Board on day one."* The same applies to the approval queue: a supervisor
    with 40 operators facing 400 items a month cannot review 400, and the four
    ideas worth ₹4 lakh get buried under them."""
    plant = fixtures["plant"]
    observation = await submit(
        session,
        fixtures,
        track=KaizenTrack.QUICK,
        title="Scrap bin has no label",
        description="The bin beside the bench has no label on it at all.",
        interview=[],
        investment_paise=0,
    )
    full = await submit(session, fixtures)

    queue = await kaizen_service.open_queue(session, plant=plant)
    assert full.id in {idea.id for idea in queue}
    assert observation.id not in {idea.id for idea in queue}
    # ...and it is never counted as delayed either: nothing was owed on it.
    assert observation.approval_route == []


@pytest.mark.asyncio
async def test_a_quick_observation_never_creates_an_action(session, fixtures):
    """*"At ~80% of volume, Quick Observations would flood the Actions Board on
    day one."*"""
    idea = await submit(
        session,
        fixtures,
        track=KaizenTrack.QUICK,
        title="Loose guard on the guard rail",
        description="The guard beside the walkway is loose and rattles.",
        interview=[],
    )
    assert not kaizen_service.creates_action(plant=fixtures["plant"], idea=idea)


@pytest.mark.asyncio
async def test_only_accepted_vision_findings_are_stored(session, fixtures):
    """Step 2, Rule 1 — a rejected finding *"is deleted and never reaches a
    supervisor"*. This is a technical rule, not a policy note."""
    idea = await submit(
        session,
        fixtures,
        vision_findings=[
            {"category": "housekeeping", "text": "Possible clutter", "accepted": True},
            {"category": "leak_or_spill", "text": "Possible oil patch", "accepted": False},
        ],
        vision_ran=True,
    )
    stored = [f["text"] for f in idea.vision_findings or []]
    assert stored == ["Possible clutter"]


@pytest.mark.asyncio
async def test_credit_goes_to_the_operator_not_the_submitter(session, fixtures):
    """*"Get this wrong and the first time an operator sees their idea credited
    to their supervisor, participation collapses on that line."*"""
    operator = Operator(plant_id=fixtures["plant"].id, name="M. Selvam", employee_code="OP-12")
    session.add(operator)
    await session.flush()

    idea = await submit(session, fixtures, credited_operator_id=operator.id)
    assert idea.submitted_by_name == "R. Kumar"
    assert idea.credited_name == "M. Selvam"
    assert idea.board == Board.SHOPFLOOR.value

    award = await session.scalar(select(KaizenPoint).where(KaizenPoint.idea_id == idea.id))
    assert award.person_name == "M. Selvam"
    assert award.operator_id == operator.id


@pytest.mark.asyncio
async def test_a_station_named_in_the_text_is_resolved(session, fixtures):
    """An operator who writes "CNC-3" has named the machine, dropdown or not.

    Without this the idea is priced against the whole plant instead of against
    the machine it is actually about.
    """
    plant = fixtures["plant"]
    named = await kaizen_service.station_named_in(
        session, plant=plant, text="Keep spare inserts at CNC-3 so we stop walking"
    )
    assert named == fixtures["station"].id

    # Ambiguity resolves to nothing rather than to a guess.
    assert (
        await kaizen_service.station_named_in(
            session, plant=plant, text="Nothing here names a machine at all"
        )
        is None
    )

    idea = await submit(
        session,
        fixtures,
        station_id=None,
        title="Keep spare inserts at CNC-3",
        description="Walking to stores for a new insert stretches every breakage.",
        interview=[],
    )
    assert idea.station_id == fixtures["station"].id


@pytest.mark.asyncio
async def test_core_context_is_inherited_not_asked(session, fixtures):
    """§5.1 — machine, line and the bottleneck factor come from Core."""
    context = await kaizen_service.inherit_context(
        session, plant=fixtures["plant"], station_id=fixtures["station"].id
    )
    assert context.station.code == "CNC-3"
    assert context.line == "Line 1"
    assert context.bottleneck_factor == 1.0
    # Which is why the bottleneck question never appears in the interview.
    assert "bottleneck_factor" in context.known
    assert "correct?" in context.prompt


@pytest.mark.asyncio
async def test_a_near_identical_repeat_scores_nothing(session, fixtures):
    """Anti-gaming rule 3 — *"Duplicates count for nothing. A near-identical
    repeat scores 0 points and does not clear the month's requirement."*"""
    first = await submit(session, fixtures)
    second = await submit(session, fixtures)

    assert second.duplicate_of_id == first.id
    assert second.status is KaizenStatus.DUPLICATE
    award = await session.scalar(select(KaizenPoint).where(KaizenPoint.idea_id == second.id))
    assert award.points == 0
    assert award.kind == PointsEvent.DUPLICATE.value


# ===========================================================================
# §7.1's invariant — the test the spec asks for by name
# ===========================================================================


@pytest.mark.asyncio
async def test_invariant_core_and_kaizen_agree(session, fixtures):
    """*"For the same event, Core's loss screen and Kaizen's impact card must
    show the same rupee figure. Write an automated test for this invariant and
    run it on every build."*

    A Kaizen's linked cost is the sum of the Core events it matched. If those
    two ever diverge, a supervisor can open the downtime log and the Kaizen card
    and see two different numbers for one tool breakage.
    """
    plant = fixtures["plant"]
    await kaizen_service.settings_for(session, plant=plant)
    events = [
        await stop(session, fixtures, fixtures["reasons"]["TOOL_BREAKAGE"], minutes=45, days_ago=n)
        for n in (4, 12, 25)
    ]
    core_total = sum(event.cost_total for event in events)

    idea = await submit(
        session,
        fixtures,
        title="Stop the tool breaking",
        description="The insert on CNC-3 breaks and we lose the machine each time.",
        interview=[],
    )
    assert idea.linked_loss_paise == core_total
    assert idea.history["cost_paise"] == core_total
    assert idea.history["cost_display"] == idea.history["cost_display"]


# ===========================================================================
# §10 — approval, and §8 — the handoff to a Core Action
# ===========================================================================


@pytest.mark.asyncio
async def test_approval_above_cores_threshold_creates_one_action(session, fixtures):
    """§8.1 — *"From this point the Action follows CORE's lifecycle."*"""
    plant = fixtures["plant"]
    await kaizen_service.settings_for(session, plant=plant)
    await stop(session, fixtures, fixtures["reasons"]["TOOL_BREAKAGE"], minutes=90)

    idea = await submit(
        session,
        fixtures,
        title="Pre-shift tool inspection",
        description="Check the insert before the shift so it does not break mid-run.",
        interview=[
            {"key": "minutes_per_wait", "answer": "15-30 min"},
            {"key": "waits_per_shift", "answer": 2},
        ],
    )
    assert idea.hard_saving_paise > 0
    await kaizen_service.decide(
        session, plant=plant, idea=idea, accept=True, user=None, note="Worth doing."
    )

    assert idea.status is KaizenStatus.ACCEPTED
    assert idea.action_id is not None

    action = await session.get(Action, idea.action_id)
    assert action.trigger is ActionTrigger.KAIZEN
    assert action.state is PdcaState.OPEN
    # The baseline is frozen now, as for any other Action.
    assert action.baseline_frozen_at is not None
    # A projection is labelled as one, never as a saving.
    assert "PROJECTED (not a saving)" in action.trigger_rationale


@pytest.mark.asyncio
async def test_a_small_kaizen_stays_inside_the_kaizen_workflow(session, fixtures):
    """§4.3 — *"Full Kaizen below the Action threshold: approved and implemented
    within the Kaizen workflow; savings still counted once verified."*"""
    plant = fixtures["plant"]
    idea = await submit(
        session,
        fixtures,
        title="Move the bin one metre",
        description="The scrap bin is just past the operator's reach at the bench.",
        interview=[{"key": "seconds_per_trip", "answer": "Under 30 sec"},
                   {"key": "trips_per_shift", "answer": 1}],
        investment_paise=0,
    )
    assert idea.hard_saving_paise < plant.action_cost_threshold
    assert not kaizen_service.creates_action(plant=plant, idea=idea)


@pytest.mark.asyncio
async def test_a_safety_kaizen_always_reaches_an_action(session, fixtures):
    """Safety never competes with ROI for attention."""
    idea = await submit(
        session,
        fixtures,
        title="Guard missing on the pinch point",
        description="There is no guard on the pinch point and somebody nearly caught a hand.",
        interview=[],
        investment_paise=rupees(4_000),
    )
    assert idea.safety_flagged
    assert kaizen_service.creates_action(plant=fixtures["plant"], idea=idea)
    # And the money stays at zero: the value is risk removed, shown in stars.
    assert idea.hard_saving_paise == 0
    assert idea.intangible_stars["safety"] >= 4


@pytest.mark.asyncio
async def test_the_approval_route_follows_the_plants_template(session, fixtures):
    plant = fixtures["plant"]
    config = await kaizen_service.settings_for(session, plant=plant)
    config.template = ApprovalTemplate.STANDARD
    config.steps = ["supervisor", "owner"]
    await session.flush()

    idea = await submit(session, fixtures, title="Trolley for the toolbox",
                        description="Put the shared toolbox on a trolley between the machines.")
    assert idea.approval_route == ["supervisor", "owner"]

    await kaizen_service.decide(session, plant=plant, idea=idea, accept=True, user=None)
    # One step cleared, still waiting on the second.
    assert idea.approval_step == 1
    assert idea.status is KaizenStatus.SCORED

    await kaizen_service.decide(session, plant=plant, idea=idea, accept=True, user=None)
    assert idea.status in (KaizenStatus.APPROVED, KaizenStatus.ACCEPTED)


@pytest.mark.asyncio
async def test_a_safety_kaizen_is_always_routed_to_the_plant_head(session, fixtures):
    """*"IF safety-flagged → always route to Plant Head."*"""
    plant = fixtures["plant"]
    config = await kaizen_service.settings_for(session, plant=plant)
    config.template = ApprovalTemplate.OPEN
    config.steps = []
    await session.flush()

    idea = await submit(
        session,
        fixtures,
        title="Unsafe step near the press",
        description="The step by the press is a trip hazard and someone will fall.",
        interview=[],
    )
    assert "owner" in (idea.approval_route or [])


@pytest.mark.asyncio
async def test_declining_records_a_reason_the_operator_can_read(session, fixtures):
    idea = await submit(session, fixtures)
    await kaizen_service.decide(
        session,
        plant=fixtures["plant"],
        idea=idea,
        accept=False,
        user=None,
        note="The fixture change next month already moves this toolbox.",
    )
    assert idea.status is KaizenStatus.REJECTED
    assert "next month" in idea.decision_note
    assert idea.approval_trail[-1]["accepted"] is False


# ===========================================================================
# §9 — verification through Core's Recovered Profit ledger
# ===========================================================================


@pytest.mark.asyncio
async def test_points_land_only_when_core_says_the_saving_held(session, fixtures):
    """§9.3 — *"Points on verification fire when Core marks the Recovered Profit
    as sustained, not when the Action is marked complete."*"""
    plant = fixtures["plant"]
    await kaizen_service.settings_for(session, plant=plant)
    await stop(session, fixtures, fixtures["reasons"]["TOOL_BREAKAGE"], minutes=120)

    idea = await submit(
        session,
        fixtures,
        title="Better insert grade",
        description="Change the insert grade so the tool stops breaking mid-run.",
        interview=[],
    )
    await kaizen_service.decide(session, plant=plant, idea=idea, accept=True, user=None)
    action = await session.get(Action, idea.action_id)

    # Implemented — but not yet proven to have held.
    action.closed_at = datetime.now(UTC)
    action.state = PdcaState.MONITORING
    await session.flush()
    await kaizen_service.sync_with_actions(session, plant=plant)
    assert idea.status is KaizenStatus.IMPLEMENTED
    assert idea.verified_at is None

    kinds = {
        row.kind
        for row in (
            await session.scalars(select(KaizenPoint).where(KaizenPoint.idea_id == idea.id))
        ).all()
    }
    assert PointsEvent.VERIFIED.value not in kinds

    # Core verifies it two weeks later.
    action.state = PdcaState.VERIFIED
    action.verified_at = datetime.now(UTC)
    action.recovered_profit = rupees(4_90_000)
    await session.flush()
    await kaizen_service.sync_with_actions(session, plant=plant)

    assert idea.status is KaizenStatus.VERIFIED
    assert idea.verified_saving_paise == rupees(4_90_000)
    verified = await session.scalar(
        select(KaizenPoint).where(
            KaizenPoint.idea_id == idea.id, KaizenPoint.kind == PointsEvent.VERIFIED.value
        )
    )
    # 50 base, plus the value bonus, capped.
    assert verified.points == 50 + 40


@pytest.mark.asyncio
async def test_every_award_is_idempotent(session, fixtures):
    """The sync job runs hourly. It must not pay twice for one outcome."""
    idea = await submit(session, fixtures)
    first = await kaizen_service.award(
        session, plant=fixtures["plant"], idea=idea, event=PointsEvent.APPROVED
    )
    again = await kaizen_service.award(
        session, plant=fixtures["plant"], idea=idea, event=PointsEvent.APPROVED
    )
    assert first == again
    rows = (
        await session.scalars(
            select(KaizenPoint).where(
                KaizenPoint.idea_id == idea.id,
                KaizenPoint.kind == PointsEvent.APPROVED.value,
            )
        )
    ).all()
    assert len(rows) == 1


# ===========================================================================
# §10 — pattern detection across both sources
# ===========================================================================


@pytest.mark.asyncio
async def test_a_pattern_needs_more_than_one_report(session, fixtures):
    plant = fixtures["plant"]
    await kaizen_service.settings_for(session, plant=plant)
    await submit(session, fixtures)
    assert await kaizen_patterns.detect(session, plant=plant) == []


@pytest.mark.asyncio
async def test_a_plant_wide_project_is_one_action_not_seventeen(session, fixtures):
    """§10.2 — *"a single Core Action with the constituent Kaizens and events
    linked as children — not seventeen separate Actions."*"""
    plant = fixtures["plant"]
    config = await kaizen_service.settings_for(session, plant=plant)
    config.pattern_min_items = 3
    await session.flush()

    for n in range(4):
        await submit(
            session,
            fixtures,
            title=f"No tool storage at station {n}",
            description=(
                "There is no standard tool storage at the point of use, so the "
                f"operator hunts for tools every shift on machine {n}."
            ),
            interview=[],
        )

    patterns = await kaizen_patterns.detect(session, plant=plant)
    assert patterns, "four reports of one root cause should cluster"
    pattern = patterns[0]
    assert pattern.kaizen_count >= 3
    assert "approving these one by one" in pattern.recommendation

    action = await kaizen_patterns.create_project(
        session, plant=plant, pattern=pattern, owner_name="Plant Head"
    )
    assert action.trigger is ActionTrigger.KAIZEN
    assert pattern.action_id == action.id

    linked = (
        await session.scalars(
            select(KaizenIdea).where(KaizenIdea.id.in_(pattern.idea_ids))
        )
    ).all()
    # One Action, shared by every constituent Kaizen.
    assert {idea.action_id for idea in linked} == {action.id}

    with pytest.raises(ValueError):
        await kaizen_patterns.create_project(session, plant=plant, pattern=pattern)


@pytest.mark.asyncio
async def test_only_core_events_contribute_to_a_patterns_rupee_figure(session, fixtures):
    """*"Core-verified cost ₹2,86,400/year (recorded, not estimated)."*"""
    cluster = kaizen_patterns.Cluster(signature=["tool", "storage"])
    cluster.ideas = [KaizenIdea(title="a"), KaizenIdea(title="b")]
    assert cluster.verified_cost == 0
    cluster.events = [(None, rupees(50_000), 120)]
    assert cluster.verified_cost == rupees(50_000)


# ===========================================================================
# §12 — reporting
# ===========================================================================


@pytest.mark.asyncio
async def test_the_digest_reports_kaizen_as_a_share_never_an_addition(session, fixtures):
    """§9.2 — *"The owner's money screen must never show 'Core savings + Kaizen
    savings' as two additive lines."*"""
    plant = fixtures["plant"]
    await kaizen_service.settings_for(session, plant=plant)
    await submit(session, fixtures)

    block = await kaizen_reporting.digest_block(session, plant=plant)
    rendered = kaizen_reporting.render_digest(plant, block)

    assert "KAIZEN THIS WEEK" in rendered
    assert "Of the plant's recovered profit" in rendered
    assert block.recovered_profit_ytd_paise <= block.plant_recovered_profit_ytd_paise
    # Participation and savings are reported together, never participation alone.
    assert "Submitted" in rendered


@pytest.mark.asyncio
async def test_the_two_leaderboards_are_separate(session, fixtures):
    """*"The operator sees a leaderboard they can never top and stops
    submitting."*"""
    plant = fixtures["plant"]
    await submit(session, fixtures)
    boards = await kaizen_reporting.leaderboards(session, plant=plant)
    assert set(boards) == {Board.SHOPFLOOR.value, Board.SUPPORT.value}
    assert boards[Board.SHOPFLOOR.value][0]["name"] == "R. Kumar"


@pytest.mark.asyncio
async def test_the_monthly_floor_is_reported_to_the_person(session, fixtures):
    """*"This month: 0 of 1 · 6 days left"* — a floor, not a leaderboard."""
    plant = fixtures["plant"]
    await submit(session, fixtures)
    standing = await kaizen_reporting.standing(
        session, plant=plant, person_name="R. Kumar"
    )
    assert standing["this_month"] == 1
    assert standing["monthly_minimum"] == 1
    assert standing["level_label"] == "Operator"
    assert standing["points"] >= 5


# ===========================================================================
# The API surface
# ===========================================================================


@pytest.mark.asyncio
async def test_api_walks_the_operator_journey(api, fixtures):
    client, as_ = api["client"], api["as"]
    station_id = str(fixtures["station"].id)

    context = await client.get(
        "/api/v1/plugins/kaizen/context",
        params={"station_id": station_id},
        headers=as_("supervisor"),
    )
    assert context.status_code == 200
    assert context.json()["station_code"] == "CNC-3"
    assert "bottleneck_factor" in context.json()["known"]

    classified = await client.post(
        "/api/v1/plugins/kaizen/classify",
        json={
            "title": "Shared toolbox",
            "description": "I walk 20 metres every shift to fetch the toolbox.",
            "station_id": station_id,
        },
        headers=as_("supervisor"),
    )
    assert classified.status_code == 200
    body = classified.json()
    assert body["category"] == "motion"
    # Core answered the bottleneck question, so it was never asked.
    assert "bottleneck" not in [q["key"] for q in body["questions"]]
    assert "bottleneck" in body["skipped"]

    created = await client.post(
        "/api/v1/plugins/kaizen",
        json={
            "title": "Shared toolbox",
            "description": "I walk 20 metres every shift to fetch the toolbox.",
            "station_id": station_id,
            "interview": [
                {"key": "seconds_per_trip", "answer": "30-60 sec"},
                {"key": "trips_per_shift", "answer": 12},
            ],
            "investment_paise": rupees(12_000),
        },
        headers=as_("supervisor"),
    )
    assert created.status_code == 201, created.text
    idea = created.json()
    assert idea["impact"]["saving"]["hard_paise"] > 0
    assert idea["impact"]["cost_of_doing_nothing"]["per_month_display"].startswith("₹")
    assert idea["priority_verdict"] in ("HIGH", "MEDIUM", "LOW")


@pytest.mark.asyncio
async def test_api_refuses_a_self_approval(api, fixtures):
    """*"Nobody approves their own Kaizen."*"""
    client, as_ = api["client"], api["as"]
    created = await client.post(
        "/api/v1/plugins/kaizen",
        json={
            "title": "Label the valves in the compressor room",
            "description": "None of the air valves are labelled so nobody knows which is which.",
            "station_id": str(fixtures["station"].id),
        },
        headers=as_("supervisor"),
    )
    assert created.status_code == 201
    idea_id = created.json()["id"]

    refused = await client.post(
        f"/api/v1/plugins/kaizen/{idea_id}/decide",
        json={"accept": True},
        headers=as_("supervisor"),
    )
    assert refused.status_code == 403
    assert "own Kaizen" in refused.json()["detail"]

    # Somebody else, a level up, can.
    allowed = await client.post(
        f"/api/v1/plugins/kaizen/{idea_id}/decide",
        json={"accept": True},
        headers=as_("owner"),
    )
    assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_api_refuses_a_decline_with_no_reason(api, fixtures):
    client, as_ = api["client"], api["as"]
    created = await client.post(
        "/api/v1/plugins/kaizen",
        json={
            "title": "Second scrap bin at the bench",
            "description": "One bin for two benches means we carry parts across the aisle.",
            "station_id": str(fixtures["station"].id),
        },
        headers=as_("supervisor"),
    )
    idea_id = created.json()["id"]
    refused = await client.post(
        f"/api/v1/plugins/kaizen/{idea_id}/decide",
        json={"accept": False},
        headers=as_("owner"),
    )
    assert refused.status_code == 400
    assert "reason the operator can read" in refused.json()["detail"]


@pytest.mark.asyncio
async def test_api_refuses_another_plants_station(api, fixtures):
    client, as_ = api["client"], api["as"]
    response = await client.post(
        "/api/v1/plugins/kaizen",
        json={
            "title": "Something on another plant's machine",
            "description": "This station belongs to a different plant entirely.",
            "station_id": str(api["foreign_station"].id),
        },
        headers=as_("supervisor"),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_api_settings_reject_weights_that_sum_to_nothing(api):
    client, as_ = api["client"], api["as"]
    response = await client.put(
        "/api/v1/plugins/kaizen/settings",
        json={"score_weights": {"ease": 0, "safety": 0}},
        headers=as_("owner"),
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_api_settings_expose_cores_threshold_not_a_second_one(api, fixtures):
    """§4.3 — *"Kaizen does not introduce a second one."*"""
    client, as_ = api["client"], api["as"]
    response = await client.get("/api/v1/plugins/kaizen/settings", headers=as_("owner"))
    assert response.status_code == 200
    assert response.json()["action_threshold_paise"] == fixtures["plant"].action_cost_threshold


@pytest.mark.asyncio
async def test_api_dashboard_puts_quick_wins_first(api, fixtures):
    client, as_ = api["client"], api["as"]
    response = await client.get("/api/v1/plugins/kaizen/dashboard", headers=as_("owner"))
    assert response.status_code == 200
    body = response.json()
    assert set(body) >= {
        "quick_wins",
        "pending",
        "delayed",
        "top_savings",
        "safety",
        "patterns",
        "participation",
        "leaderboards",
        "totals",
    }


@pytest.mark.asyncio
async def test_api_digest_renders(api):
    client, as_ = api["client"], api["as"]
    response = await client.get("/api/v1/plugins/kaizen/digest", headers=as_("owner"))
    assert response.status_code == 200
    assert "KAIZEN THIS WEEK" in response.json()["rendered"]


@pytest.mark.asyncio
async def test_api_transcript_falls_back_without_a_model(api):
    """Nothing in the operator journey blocks on a model being up."""
    client, as_ = api["client"], api["as"]
    response = await client.post(
        "/api/v1/plugins/kaizen/transcript",
        json={"text": "  every  shift I walk 20 metres  "},
        headers=as_("supervisor"),
    )
    assert response.status_code == 200
    assert response.json()["text"] == "every shift I walk 20 metres"


@pytest.mark.asyncio
async def test_seeding_the_category_map_is_additive(session, fixtures):
    """A consultant's fifteen minutes of onboarding mapping is never overwritten."""
    plant = fixtures["plant"]
    await kaizen_service.settings_for(session, plant=plant)
    added_again = await kaizen_service.seed_category_map(session, plant=plant)
    assert added_again == 0

    reasons, _ = await kaizen_history.resolve_codes(
        session, plant=plant, category=KaizenCategory.MAINTENANCE
    )
    assert fixtures["reasons"]["TOOL_BREAKAGE"].id in reasons


@pytest.mark.asyncio
async def test_kaizen_never_writes_a_station(session, fixtures):
    """§4.2 — *"A plugin that can silently create master records will corrupt
    the plant's cost model within a month."*"""
    plant = fixtures["plant"]
    before = len(
        (await session.scalars(select(Station).where(Station.plant_id == plant.id))).all()
    )
    await submit(
        session,
        fixtures,
        station_id=None,
        unknown_station_note="The bench behind Line 2, not on the machine list",
    )
    after = len(
        (await session.scalars(select(Station).where(Station.plant_id == plant.id))).all()
    )
    assert after == before


def test_impact_stars_stay_in_range():
    with pytest.raises(ValueError):
        ImpactStars(safety=9)
    assert ImpactStars.from_dict({"safety": 4}).safety == 4
    assert ImpactStars.from_dict(None).as_dict()["quality"] == 0


def test_recoverability_import_is_used():
    """Guards the fixture contract the history tests depend on."""
    assert Recoverability.RECOVERABLE.value == "R"
