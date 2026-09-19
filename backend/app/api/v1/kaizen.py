"""Kaizen AI — the plugin's API surface (Plugin Spec v2).

Mounted under ``/plugins/kaizen``: one plugin, one prefix, no separate login and
no second source of truth (§1).

The route order in this file matters. Every static path is declared before
``/{idea_id}``, because FastAPI matches in declaration order and would
otherwise try to parse "mine" as a UUID.

Two rules enforced at this boundary rather than deeper down, because this is
where the operator's consent and the operator's credit actually live:

* **Vision findings the operator rejected never arrive.** :func:`analyse_photo`
  returns suggestions; only findings the client marks ``accepted`` are persisted
  by :func:`submit_idea`. Nothing a supervisor sees came from the camera without
  the operator putting it there (Step 2, Rule 1).
* **Nobody approves their own Kaizen.** :func:`decide_idea` refuses a decision
  from the person who submitted it or the operator credited with it — otherwise
  the approval workflow is theatre and the savings are self-certified.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import kaizen_ai
from app.api.deps import Identity, get_identity, get_plant, get_session, require
from app.api.scoping import in_plant
from app.kernel import ScoreWeights, format_inr
from app.models import (
    CATEGORY_LABELS_BY_VALUE,
    TEMPLATE_STEPS,
    ApprovalTemplate,
    KaizenCategory,
    KaizenIdea,
    KaizenPattern,
    KaizenStatus,
    KaizenTrack,
    Operator,
    Plant,
    Role,
    Station,
)
from app.schemas import (
    KaizenClassifyOut,
    KaizenClassifyRequest,
    KaizenContextOut,
    KaizenDashboardOut,
    KaizenDecision,
    KaizenDigestOut,
    KaizenIdeaOut,
    KaizenImpactOut,
    KaizenPatternOut,
    KaizenPreviewOut,
    KaizenPreviewRequest,
    KaizenProjectRequest,
    KaizenPromoteRequest,
    KaizenRateRequest,
    KaizenSavingOut,
    KaizenSettingsOut,
    KaizenSettingsUpdate,
    KaizenStandingOut,
    KaizenSubmission,
    KaizenTranscriptOut,
    KaizenTranscriptRequest,
    KaizenVisionOut,
)
from app.services import (
    attachments_store,
    kaizen_costing,
    kaizen_history,
    kaizen_patterns,
    kaizen_reporting,
)
from app.services import kaizen as kaizen_service

router = APIRouter(prefix="/plugins/kaizen", tags=["kaizen"])

#: Who can decide on a Kaizen. Deliberately the same roles Core uses for an
#: Action — a plant does not want two permission models to maintain.
APPROVERS = (Role.OWNER, Role.SUPERVISOR, Role.CONSULTANT)


# ===========================================================================
# Steps 1-4 — the operator journey, before anything is committed
# ===========================================================================


@router.get("/context", response_model=KaizenContextOut)
async def context(
    station_id: uuid.UUID | None = None,
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """§5.1 — machine, line, part and operation, inherited from Core's Job Order.

    Shown for one-tap confirmation, never silently assumed: an operator may be
    standing at a different machine from the one they are logged into.
    """
    await in_plant(session, Station, station_id, plant.id, "Station")
    resolved = await kaizen_service.inherit_context(
        session, plant=plant, station_id=station_id
    )
    return KaizenContextOut(**resolved.as_dict())


@router.post("/transcript", response_model=KaizenTranscriptOut)
async def tidy_transcript(payload: KaizenTranscriptRequest):
    """Step 1 — clean up what the operator said, changing no facts.

    Phase 1 of the voice roadmap: the device transcribes, the model only tidies.
    A rewrite that introduces a number the operator never said is discarded —
    a wrong number silently corrupts the savings calculation, and a wrong
    savings number is the one thing that kills credibility with a Plant Head.
    """
    cleaned = await kaizen_ai.clean_transcript(payload.text)
    return KaizenTranscriptOut(
        text=cleaned, original=payload.text, cleaned=cleaned != payload.text.strip()
    )


@router.post("/vision", response_model=KaizenVisionOut)
async def analyse_photo(
    file: UploadFile = File(...),
    said: str = Form(default=""),
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Step 2 — the photo, read by a local vision model.

    What comes back is a list of *suggestions for the operator*, every one
    hedged as "possible" and drawn from five categories only. Nothing here is
    stored against the Kaizen: the client shows them, the operator taps "Add to
    this Kaizen" or "Not relevant", and only the accepted ones are submitted.
    That is Rule 1, and it is a technical rule rather than a policy note — the
    moment operators believe the camera reports them to management, they stop
    taking photos and Step 2 dies.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty photo")

    attachment = await attachments_store.store(
        session,
        plant=plant,
        entity_type="kaizen",
        entity_id=uuid.uuid4(),
        filename=file.filename,
        content_type=file.content_type or "image/jpeg",
        data=raw,
        uploaded_by_id=identity.user.id,
    )
    result = await kaizen_ai.analyse_photo(
        raw, media_type=file.content_type or "image/jpeg", said=said
    )
    return KaizenVisionOut(
        findings=result.findings,
        ran=result.ran,
        note=result.note,
        attachment_id=attachment.id if attachment else None,
    )


@router.post("/classify", response_model=KaizenClassifyOut)
async def classify(
    payload: KaizenClassifyRequest,
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Steps 3 and 4 — the category to confirm, and the questions that follow it.

    AI classifies first and the operator confirms in one tap. Asking a
    shop-floor operator to pick the right waste category *before* explaining the
    problem is asking them to do abstract classification, which is exactly the
    form-filling this product exists to kill.

    The question list is already shortened by everything Core knows: the
    bottleneck question disappears because Core carries a Bottleneck Factor per
    machine, and the rejection stage disappears because the Job Order carries
    the routing.
    """
    await in_plant(session, Station, payload.station_id, plant.id, "Station")
    resolved = await kaizen_service.inherit_context(
        session, plant=plant, station_id=payload.station_id
    )
    vision = [f.model_dump() for f in payload.vision_findings if f.accepted]

    category = kaizen_ai.classify(payload.title, payload.description, vision=vision)
    saving_type = kaizen_ai.saving_type_for(category, payload.title, payload.description)

    asked = kaizen_ai.questions_for(saving_type, known=resolved.known)
    everything = kaizen_ai.QUESTION_TREES.get(saving_type, ())
    skipped = [q.key for q in everything if q not in asked]

    config = await kaizen_service.settings_for(session, plant=plant)
    enabled = set(config.enabled_categories or [c.value for c in KaizenCategory])

    return KaizenClassifyOut(
        category=category.value,
        category_label=CATEGORY_LABELS_BY_VALUE[category.value],
        saving_type=saving_type.value,
        safety_flagged=kaizen_ai.looks_safety_related(
            payload.title, payload.description, vision
        ),
        categories=[
            {"value": c.value, "label": CATEGORY_LABELS_BY_VALUE[c.value]}
            for c in KaizenCategory
            if c.value in enabled
        ],
        questions=[q.as_dict() for q in asked],
        context=KaizenContextOut(**resolved.as_dict()),
        skipped=skipped,
    )


@router.post("/preview", response_model=KaizenPreviewOut)
async def preview(
    payload: KaizenPreviewRequest,
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Steps 5A-9 without committing — what the operator sees before submitting.

    The History Check is the moment the plugin earns its keep, and the operator
    should see it *before* they decide whether to go on. Nothing is written.
    """
    await in_plant(session, Station, payload.station_id, plant.id, "Station")
    resolved = await kaizen_service.inherit_context(
        session, plant=plant, station_id=payload.station_id
    )
    config = await kaizen_service.settings_for(session, plant=plant)
    vision = [f.model_dump() for f in payload.vision_findings if f.accepted]

    category = (
        KaizenCategory(payload.category)
        if payload.category
        else kaizen_ai.classify(payload.title, payload.description, vision=vision)
    )
    saving_type = kaizen_ai.saving_type_for(category, payload.title, payload.description)

    history = await kaizen_history.check(
        session,
        plant=plant,
        category=category,
        title=payload.title,
        description=payload.description,
        station=resolved.station,
        part_id=resolved.part.id if resolved.part else None,
        window_days=config.history_window_days,
    )
    priced = await kaizen_costing.quote(
        session,
        plant=plant,
        saving_type=saving_type,
        category=category,
        interview=[a.model_dump() for a in payload.interview],
        station_id=payload.station_id,
        part_id=resolved.part.id if resolved.part else None,
        operation_id=resolved.operation.id if resolved.operation else None,
        vision=vision,
        investment_paise=payload.investment_paise,
    )

    challenge = None
    forecast = None
    if payload.track == KaizenTrack.FULL.value:
        offered = await kaizen_ai.propose_alternative(
            problem=payload.description,
            proposed_fix=payload.proposed_fix or payload.title,
            investment_paise=payload.investment_paise,
            station_code=resolved.station.code if resolved.station else None,
        )
        challenge = offered.as_dict() if offered.offered else None
        forecast = (
            await kaizen_history.forecast(
                session,
                plant=plant,
                investment_paise=payload.investment_paise,
                needs_shutdown=payload.needs_shutdown,
                safety=category is KaizenCategory.SAFETY,
            )
        ).as_dict()

    verdict = "HIGH" if history.rank and history.rank <= 5 else "MEDIUM"
    if not history.found and priced.hard == 0:
        verdict = "LOW"

    return KaizenPreviewOut(
        category=category.value,
        category_label=CATEGORY_LABELS_BY_VALUE[category.value],
        saving_type=saving_type.value,
        context=KaizenContextOut(**resolved.as_dict()),
        history=history.as_dict(),
        similar=[],
        impact=_impact_from_priced(priced, payload.investment_paise),
        challenge=challenge,
        forecast=forecast,
        priority_verdict=verdict,
        rationale=history.message,
    )


# ===========================================================================
# Submission and listing
# ===========================================================================


@router.post("", response_model=KaizenIdeaOut, status_code=status.HTTP_201_CREATED)
async def submit_idea(
    payload: KaizenSubmission,
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Submit, and get the whole answer back immediately.

    Anyone in the plant can submit — operators, supervisors, maintenance,
    quality, stores, office, dispatch. The product is *designed* for the
    operator first, but the door is deliberately wide.
    """
    await in_plant(session, Station, payload.station_id, plant.id, "Station")
    await in_plant(session, Operator, payload.credited_operator_id, plant.id, "Operator")

    submission = kaizen_service.Submission(
        title=payload.title,
        description=payload.description,
        track=KaizenTrack(payload.track),
        voice_transcript=payload.voice_transcript,
        voice_language=payload.voice_language,
        station_id=payload.station_id,
        category=KaizenCategory(payload.category) if payload.category else None,
        # Only what the operator accepted. A rejected finding is deleted here
        # and never reaches a supervisor.
        vision_findings=[f.model_dump() for f in payload.vision_findings if f.accepted],
        vision_ran=payload.vision_ran,
        interview=[a.model_dump() for a in payload.interview],
        investment_paise=payload.investment_paise,
        needs_shutdown=payload.needs_shutdown,
        proposed_fix=payload.proposed_fix,
        credited_operator_id=payload.credited_operator_id,
        context_confirmed=payload.context_confirmed,
        unknown_station_note=payload.unknown_station_note,
    )
    idea = await kaizen_service.submit(
        session,
        plant=plant,
        submission=submission,
        user=identity.user,
        submitted_by_name=payload.submitted_by_name or identity.display_name,
    )
    for attachment_id in payload.attachment_ids:
        await attachments_store.reassign(
            session, plant=plant, attachment_id=attachment_id, entity_id=idea.id
        )
    return await _out(session, idea)


@router.get("", response_model=list[KaizenIdeaOut])
async def list_ideas(
    status_filter: str | None = Query(default=None, alias="status"),
    track: str | None = None,
    safety: bool | None = None,
    limit: int = Query(default=100, ge=1, le=300),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """The queue, ranked by the hidden score — never by date."""
    query = select(KaizenIdea).where(KaizenIdea.plant_id == plant.id)
    if status_filter:
        try:
            query = query.where(KaizenIdea.status == KaizenStatus(status_filter))
        except ValueError:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Unknown status {status_filter!r}",
            ) from None
    if track:
        try:
            query = query.where(KaizenIdea.track == KaizenTrack(track))
        except ValueError:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown track {track!r}"
            ) from None
    if safety is not None:
        query = query.where(KaizenIdea.safety_flagged.is_(safety))

    rows = (
        await session.scalars(
            query.order_by(
                KaizenIdea.priority_score.desc(), KaizenIdea.created_at.desc()
            ).limit(limit)
        )
    ).all()
    return [await _out(session, idea) for idea in rows]


@router.get("/mine", response_model=list[KaizenIdeaOut])
async def my_ideas(
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """"My Kaizens" — the only Kaizen screen most people ever open."""
    rows = await kaizen_service.mine(session, plant=plant, user=identity.user)
    return [await _out(session, idea) for idea in rows]


@router.get("/me", response_model=KaizenStandingOut)
async def my_standing(
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Level, points and this month's counter — the operator's home screen."""
    return KaizenStandingOut(
        **await kaizen_reporting.standing(
            session, plant=plant, person_name=identity.display_name
        )
    )


@router.get("/dashboard", response_model=KaizenDashboardOut)
async def dashboard(
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """§12.2 — Quick Wins first, then the rest."""
    views = await kaizen_reporting.dashboard(session, plant=plant)

    async def out(rows) -> list[KaizenIdeaOut]:
        return [await _out(session, idea) for idea in rows]

    return KaizenDashboardOut(
        quick_wins=await out(views["quick_wins"]),
        pending=await out(views["pending"]),
        delayed=await out(views["delayed"]),
        top_savings=await out(views["top_savings"]),
        safety=await out(views["safety"]),
        patterns=[_pattern_out(p) for p in views["patterns"]],
        participation=views["participation"],
        leaderboards=views["leaderboards"],
        totals=await kaizen_reporting.totals(session, plant=plant),
    )


@router.get("/leaderboard")
async def leaderboard(
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Two boards, always returned together and always shown side by side."""
    return await kaizen_reporting.leaderboards(session, plant=plant)


@router.get("/participation")
async def participation(
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Participation % by department — a leading indicator, never the headline."""
    return {
        "departments": await kaizen_reporting.participation(session, plant=plant),
        "spikes": await kaizen_reporting.end_of_month_spike(session, plant=plant),
    }


@router.get(
    "/nudge-list",
    dependencies=[Depends(require(Role.OWNER, Role.SUPERVISOR, Role.CONSULTANT))],
)
async def nudge_list(
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Who has not submitted yet this month — a nudge list, not a punishment list."""
    return await kaizen_reporting.nudge_list(session, plant=plant)


@router.get("/digest", response_model=KaizenDigestOut)
async def digest(
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """§12.1 — the block Kaizen contributes to CORE's weekly owner digest.

    Kaizen sends no digest of its own. A second weekly blast competes with
    Core's and trains people to ignore both.
    """
    block = await kaizen_reporting.digest_block(session, plant=plant)
    return KaizenDigestOut(
        block=block.as_dict(), rendered=kaizen_reporting.render_digest(plant, block)
    )


# ===========================================================================
# §10 — patterns
# ===========================================================================


@router.get("/patterns", response_model=list[KaizenPatternOut])
async def list_patterns(
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    return [
        _pattern_out(p)
        for p in await kaizen_patterns.open_patterns(session, plant=plant)
    ]


@router.post(
    "/patterns/refresh",
    response_model=list[KaizenPatternOut],
    dependencies=[Depends(require(Role.OWNER, Role.SUPERVISOR, Role.CONSULTANT))],
)
async def refresh_patterns(
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Run the cluster job now. It also runs weekly in the worker."""
    await kaizen_patterns.detect(session, plant=plant)
    return await list_patterns(plant=plant, session=session)


@router.post(
    "/patterns/{pattern_id}/project",
    dependencies=[Depends(require(Role.OWNER, Role.CONSULTANT))],
)
async def create_plant_wide_project(
    pattern_id: uuid.UUID,
    payload: KaizenProjectRequest,
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """One Core Action for the whole pattern — not seventeen separate ones."""
    pattern = await _pattern(session, plant, pattern_id)
    try:
        action = await kaizen_patterns.create_project(
            session,
            plant=plant,
            pattern=pattern,
            owner_name=payload.owner_name,
            due_date=payload.due_date,
            estimated_cost_paise=payload.estimated_cost_paise,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {"action_id": str(action.id), "reference": action.reference}


@router.post(
    "/patterns/{pattern_id}/dismiss",
    dependencies=[Depends(require(Role.OWNER, Role.SUPERVISOR, Role.CONSULTANT))],
)
async def dismiss_pattern(
    pattern_id: uuid.UUID,
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    pattern = await _pattern(session, plant, pattern_id)
    pattern.dismissed = True
    await session.flush()
    return {"dismissed": True}


# ===========================================================================
# K4 / K5 — configuration
# ===========================================================================


@router.get("/settings", response_model=KaizenSettingsOut)
async def read_settings(
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    return _settings_out(await kaizen_service.settings_for(session, plant=plant), plant)


@router.put(
    "/settings",
    response_model=KaizenSettingsOut,
    dependencies=[Depends(require(Role.OWNER, Role.CONSULTANT))],
)
async def update_settings(
    payload: KaizenSettingsUpdate,
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """K4 and K5 — about ten minutes of setup, and the weights that tune it.

    The weights are configuration rather than code precisely so a plant can be
    corrected in an afternoon after twenty real Kaizens.
    """
    config = await kaizen_service.settings_for(session, plant=plant)
    data = payload.model_dump(exclude_unset=True)

    if "template" in data and data["template"] is not None:
        template = ApprovalTemplate(data.pop("template"))
        config.template = template
        # Changing the preset reseeds the step list unless the caller sent one.
        if "steps" not in data:
            config.steps = list(TEMPLATE_STEPS[template])

    if data.get("score_weights") is not None:
        weights = ScoreWeights.from_dict(data["score_weights"])
        if weights.total <= 0:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Score weights must sum to more than zero."
            )
        data["score_weights"] = weights.as_dict()

    if data.get("steps") is not None:
        allowed = {r.value for r in Role}
        unknown = [s for s in data["steps"] if s not in allowed]
        if unknown:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"Unknown approval role(s): {', '.join(unknown)}"
            )

    for key, value in data.items():
        if value is not None:
            setattr(config, key, value)
    await session.flush()
    return _settings_out(config, plant)


@router.post(
    "/settings/seed-codes",
    dependencies=[Depends(require(Role.OWNER, Role.CONSULTANT))],
)
async def seed_codes(
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """§6.1 — wire Kaizen categories to whichever of Core's T6 codes exist.

    Additive only: a consultant's onboarding mapping is never overwritten.
    """
    added = await kaizen_service.seed_category_map(session, plant=plant)
    return {"added": added}


# ===========================================================================
# One Kaizen — must stay last, after every static path
# ===========================================================================


@router.get("/{idea_id}", response_model=KaizenIdeaOut)
async def read_idea(
    idea_id: uuid.UUID,
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    return await _out(session, await _idea(session, plant, idea_id))


@router.post("/{idea_id}/decide", response_model=KaizenIdeaOut)
async def decide_idea(
    idea_id: uuid.UUID,
    payload: KaizenDecision,
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """One approval step. Declining always carries a reason the operator can read.

    Nobody approves their own Kaizen. When a supervisor or manager submits, the
    Kaizen routes one level up — otherwise the approval workflow is theatre, the
    savings numbers are self-certified, and the audit trail is worthless in
    front of a customer.
    """
    if not identity.has(*APPROVERS) and Role.ADMIN not in identity.roles:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Requires one of: {', '.join(r.value for r in APPROVERS)}",
        )

    idea = await _idea(session, plant, idea_id)
    if idea.status in (KaizenStatus.REJECTED, KaizenStatus.VERIFIED):
        raise HTTPException(status.HTTP_409_CONFLICT, "This Kaizen is already closed.")
    if idea.decided_at is not None and idea.approval_step >= len(idea.approval_route or []):
        raise HTTPException(status.HTTP_409_CONFLICT, "Already decided.")
    if idea.submitted_by_user_id == identity.user.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Nobody approves their own Kaizen — this one needs someone a level up.",
        )
    if not payload.accept and not payload.note:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Declining an idea needs a reason the operator can read.",
        )

    await kaizen_service.decide(
        session,
        plant=plant,
        idea=idea,
        accept=payload.accept,
        user=identity.user,
        note=payload.note,
        owner_name=payload.owner_name,
        due_date=payload.due_date,
        investment_paise=payload.investment_paise,
    )
    return await _out(session, idea)


@router.post("/{idea_id}/promote", response_model=KaizenIdeaOut)
async def promote_observation(
    idea_id: uuid.UUID,
    payload: KaizenPromoteRequest,
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """*"This one looks worth a proper analysis. 4 quick questions?"*

    Creates a new Full Kaizen linked to the observation rather than rewriting
    it: the observation already earned its five points and cleared the
    operator's month, and taking that back would teach people not to report
    small things.
    """
    idea = await _idea(session, plant, idea_id)
    if idea.track is not KaizenTrack.QUICK:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Only a Quick Observation can be promoted."
        )
    full = await kaizen_service.promote(
        session,
        plant=plant,
        idea=idea,
        interview=[a.model_dump() for a in payload.interview],
        investment_paise=payload.investment_paise,
        needs_shutdown=payload.needs_shutdown,
        proposed_fix=payload.proposed_fix,
    )
    return await _out(session, full)


@router.post("/{idea_id}/challenge", response_model=KaizenIdeaOut)
async def resolve_challenge(
    idea_id: uuid.UUID,
    payload: dict,
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """Step 7 — record which option went forward, and why one was dismissed.

    Every dismissal is logged so the suggestions improve: AI does not know the
    plant layout and will sometimes propose something physically impossible.
    The operator can always submit their original with one tap; AI advises, it
    never blocks or overrules.
    """
    idea = await _idea(session, plant, idea_id)
    if not idea.challenge:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No alternative was offered on this Kaizen."
        )
    chosen = str(payload.get("chosen", "mine"))
    if chosen not in ("mine", "alternative", "both"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown choice.")

    idea.challenge = {
        **idea.challenge,
        "chosen": chosen,
        "dismissed_reason": payload.get("dismissed_reason"),
    }
    if chosen == "alternative" and idea.challenge.get("rough_cost_paise"):
        idea.investment_paise = int(idea.challenge["rough_cost_paise"])
        await kaizen_service.enrich(session, plant=plant, idea=idea)
    await session.flush()
    return await _out(session, idea)


@router.post(
    "/{idea_id}/rate",
    response_model=KaizenIdeaOut,
    dependencies=[Depends(require(Role.OWNER, Role.SUPERVISOR, Role.CONSULTANT))],
)
async def supply_rate(
    idea_id: uuid.UUID,
    payload: KaizenRateRequest,
    identity: Identity = Depends(get_identity),
    plant: Plant = Depends(get_plant),
    session: AsyncSession = Depends(get_session),
):
    """§7.3 — the approver supplies a rate Core did not have.

    Written into CORE's cost master with a full audit trail, never to a
    Kaizen-local store, and visible in Core's own cost screens immediately. It
    never blocks approval: the approver can skip the question and approve
    anyway, and the Kaizen simply stays Provisional.
    """
    idea = await _idea(session, plant, idea_id)
    try:
        await kaizen_service.supply_rate(
            session,
            plant=plant,
            idea=idea,
            key=payload.key,
            paise=payload.paise,
            user=identity.user,
            effective_from=payload.effective_from or date.today(),
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return await _out(session, idea)


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


async def _idea(session: AsyncSession, plant: Plant, idea_id: uuid.UUID) -> KaizenIdea:
    idea = await session.get(KaizenIdea, idea_id)
    if idea is None or idea.plant_id != plant.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Kaizen not found")
    return idea


async def _pattern(
    session: AsyncSession, plant: Plant, pattern_id: uuid.UUID
) -> KaizenPattern:
    row = await session.get(KaizenPattern, pattern_id)
    if row is None or row.plant_id != plant.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pattern not found")
    return row


def _impact_from_priced(priced, investment_paise: int) -> KaizenImpactOut:
    """The Business Impact Card, straight off a fresh pricing run."""
    return KaizenImpactOut(
        saving=KaizenSavingOut(
            hard_paise=priced.hard,
            hard_display=format_inr(priced.hard),
            soft_paise=priced.soft,
            soft_display=format_inr(priced.soft),
            basis=f"{priced.core.working}\n{priced.split.basis}".strip(),
            provisional=priced.provisional,
            provisional_note=priced.provisional_note,
            missing_rate_key=priced.core.missing_rate_key,
            missing_rate_label=priced.core.missing_rate_label,
        ),
        stars=priced.stars.as_dict(),
        effort_score=3,
        impact_score=1,
        investment_paise=investment_paise,
        investment_display=format_inr(investment_paise),
        investment_is_estimate=True,
        payback_months=float(priced.payback) if priced.payback is not None else None,
        roi_percent=float(priced.roi) if priced.roi is not None else None,
        cost_of_doing_nothing=_nothing_out(priced.nothing),
    )


def _nothing_out(nothing) -> dict:
    """*"₹1,687 walks out of this factory every month you don't decide."*"""
    return {
        "per_year_paise": nothing.per_year,
        "per_year_display": format_inr(nothing.per_year),
        "per_month_paise": nothing.per_month,
        "per_month_display": format_inr(nothing.per_month),
        "per_day_paise": nothing.per_day,
        "per_day_display": format_inr(nothing.per_day),
        "since_submitted_paise": nothing.since_submitted,
        "since_submitted_display": format_inr(nothing.since_submitted),
        "days_waiting": nothing.days_waiting,
    }


async def _out(session: AsyncSession, idea: KaizenIdea) -> KaizenIdeaOut:
    from app.kernel import cost_of_doing_nothing, payback_months, roi_percent

    labels = await kaizen_service.label_for(session, idea)
    days_waiting = max(0, (date.today() - (idea.submitted_on or date.today())).days)
    total = idea.hard_saving_paise + idea.soft_saving_paise

    return KaizenIdeaOut(
        id=idea.id,
        reference=idea.reference,
        track=idea.track.value,
        title=idea.title,
        description=idea.description,
        voice_transcript=idea.voice_transcript,
        submitted_by_name=idea.submitted_by_name,
        credited_name=idea.credited_name,
        credited_operator_id=idea.credited_operator_id,
        board=idea.board,
        department=idea.department,
        submitted_on=idea.submitted_on,
        station_code=labels["station_code"],
        line=labels["line"],
        part_number=labels["part_number"],
        reason_label=labels["reason_label"],
        defect_label=labels["defect_label"],
        unknown_station_note=idea.unknown_station_note,
        category=idea.category.value if idea.category else None,
        category_label=(
            CATEGORY_LABELS_BY_VALUE[idea.category.value] if idea.category else None
        ),
        saving_type=idea.saving_type,
        status=idea.status.value,
        priority_verdict=idea.priority_verdict,
        safety_flagged=idea.safety_flagged,
        is_recurrence=idea.is_recurrence,
        impact=KaizenImpactOut(
            saving=KaizenSavingOut(
                hard_paise=idea.hard_saving_paise,
                hard_display=format_inr(idea.hard_saving_paise),
                soft_paise=idea.soft_saving_paise,
                soft_display=format_inr(idea.soft_saving_paise),
                basis=idea.saving_basis,
                provisional=idea.provisional,
                provisional_note=idea.provisional_note,
            ),
            stars=idea.intangible_stars or {},
            effort_score=idea.effort_score,
            impact_score=idea.impact_score,
            investment_paise=idea.investment_paise,
            investment_display=format_inr(idea.investment_paise),
            investment_is_estimate=idea.investment_is_estimate,
            payback_months=(
                float(p)
                if (
                    p := payback_months(
                        investment=idea.investment_paise,
                        annual_saving=idea.hard_saving_paise,
                    )
                )
                is not None
                else None
            ),
            roi_percent=(
                float(r)
                if (
                    r := roi_percent(
                        investment=idea.investment_paise,
                        annual_saving=idea.hard_saving_paise,
                    )
                )
                is not None
                else None
            ),
            cost_of_doing_nothing=_nothing_out(
                cost_of_doing_nothing(annual_value=total, days_waiting=days_waiting)
            ),
        ),
        history=idea.history,
        similar=idea.similar,
        challenge=idea.challenge,
        forecast=idea.forecast,
        vision_findings=idea.vision_findings or [],
        interview=idea.interview or [],
        signals=list(idea.signals or []),
        score_rationale=idea.score_rationale,
        action_id=idea.action_id,
        duplicate_action_id=idea.duplicate_action_id,
        promoted_from_id=idea.promoted_from_id,
        approval_route=list(idea.approval_route or []),
        approval_step=idea.approval_step,
        approval_trail=list(idea.approval_trail or []),
        decision_note=idea.decision_note,
        verified_saving_paise=idea.verified_saving_paise,
        verified_saving_display=format_inr(idea.verified_saving_paise),
        points_awarded=idea.points_awarded,
        created_at=idea.created_at,
    )


def _pattern_out(pattern: KaizenPattern) -> KaizenPatternOut:
    return KaizenPatternOut(
        id=pattern.id,
        label=pattern.label,
        kaizen_count=pattern.kaizen_count,
        event_count=pattern.event_count,
        departments=list(pattern.departments or []),
        verified_cost_paise=pattern.verified_cost_paise,
        verified_cost_display=format_inr(pattern.verified_cost_paise),
        lost_minutes=pattern.lost_minutes,
        recommendation=pattern.recommendation,
        action_id=pattern.action_id,
        detected_on=pattern.detected_on,
    )


def _settings_out(config, plant: Plant) -> KaizenSettingsOut:
    return KaizenSettingsOut(
        template=config.template.value,
        steps=list(config.steps or []),
        auto_approve_below_paise=config.auto_approve_below_paise,
        auto_approve_min_score=config.auto_approve_min_score,
        safety_always_to_owner=config.safety_always_to_owner,
        finance_above_paise=config.finance_above_paise,
        history_window_days=config.history_window_days,
        score_weights=ScoreWeights.from_dict(config.score_weights).as_dict(),
        pattern_min_items=config.pattern_min_items,
        pattern_min_value_paise=config.pattern_min_value_paise,
        patterns_include_quick=config.patterns_include_quick,
        promote_above_paise=config.promote_above_paise,
        monthly_minimum=config.monthly_minimum,
        quick_win_investment_paise=config.quick_win_investment_paise,
        enabled_categories=list(config.enabled_categories or []),
        # Core's threshold, read not owned. Kaizen never introduces a second.
        action_threshold_paise=plant.action_cost_threshold,
        action_threshold_display=format_inr(plant.action_cost_threshold),
    )

