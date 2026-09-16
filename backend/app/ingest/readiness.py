"""The Digital Readiness Questionnaire (§4.1 Step 0).

    "A short questionnaire the owner answers on their phone before the first
     visit ... The system returns a Digital Readiness Score — e.g. '41/100. You
     can start immediately with manual capture. Estimated onboarding: half a
     day. Priority gaps: downtime logging, traceability.'"

Cheap to build, disproportionate sales value: it qualifies the plant before we
spend a day there, tailors the onboarding, and is itself a sales artefact.

The score never gates the sale. A plant scoring 20 is a *better* customer than
one scoring 80 — it has more to recover. The score sets expectations about
onboarding effort, nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass

Answers = dict[str, object]


@dataclass(frozen=True, slots=True)
class Question:
    key: str
    prompt: str
    weight: int
    gap_if_missing: str | None = None
    section: str = "General"


QUESTIONS: tuple[Question, ...] = (
    # --- what already exists digitally -------------------------------------
    Question(
        "uses_erp", "Do you use an ERP (SAP, Tally ERP, other)?", 6, section="Systems"
    ),
    Question("uses_tally", "Do you use Tally for accounts?", 4, section="Systems"),
    Question("uses_excel", "Do you keep production data in Excel?", 5, section="Systems"),
    Question(
        "has_machine_list",
        "Do you have a list of all machines with their types?",
        8,
        "Machine list — we will build it on the floor walk (30 min)",
        "Masters",
    ),
    Question(
        "has_part_master",
        "Do you have a list of part numbers with customers?",
        8,
        "Part master — the single biggest onboarding item",
        "Masters",
    ),
    Question(
        "has_route_cards",
        "Do you have route cards or process sheets with cycle times?",
        7,
        "Cycle times — we will estimate from past output and refine in week 1",
        "Masters",
    ),
    # --- daily discipline ---------------------------------------------------
    Question(
        "logs_production",
        "Does each shift record production counts on paper or in a register?",
        8,
        "Production recording — the habit has to be built, not just the screen",
        "Discipline",
    ),
    Question(
        "logs_downtime",
        "Do you record machine downtime with reasons?",
        10,
        "Downtime logging — this is where the recoverable money is hiding",
        "Discipline",
    ),
    Question(
        "tracks_oee",
        "Do you track OEE or machine utilisation today?",
        5,
        section="Discipline",
    ),
    Question(
        "has_quality_records",
        "Do you record rejections with defect reasons?",
        8,
        "Quality records — needed for the cross-verification that keeps data honest",
        "Discipline",
    ),
    Question(
        "has_maintenance_register",
        "Do you keep a maintenance/breakdown register?",
        5,
        "Maintenance register — feeds MTBF/MTTR and Factory Memory",
        "Discipline",
    ),
    Question(
        "has_morning_meeting",
        "Do you hold a daily production meeting?",
        6,
        "Daily meeting — the product's rhythm depends on one existing",
        "Discipline",
    ),
    # --- traceability -------------------------------------------------------
    Question(
        "has_lot_numbers",
        "Do you track lot or batch numbers through production?",
        8,
        "Lot traceability — we can start with day-batches on day one",
        "Traceability",
    ),
    Question(
        "has_barcodes",
        "Do you use barcodes or QR codes anywhere?",
        4,
        section="Traceability",
    ),
    Question(
        "runs_kanban", "Do you run kanban cards between stages?", 4, section="Traceability"
    ),
    # --- cost data ----------------------------------------------------------
    Question(
        "knows_machine_rates",
        "Do you know your hourly cost per machine?",
        6,
        "Machine rates — 15 minutes with you and your accountant covers this",
        "Costing",
    ),
    Question(
        "knows_margins",
        "Do you know the contribution margin per part?",
        6,
        "Contribution margins — L/M/H bands per family are enough to start",
        "Costing",
    ),
    # --- readiness to operate ----------------------------------------------
    Question(
        "has_smartphones", "Do your supervisors have smartphones?", 5, section="Operations"
    ),
    Question(
        "has_shopfloor_wifi",
        "Is there reliable Wi-Fi or mobile data on the shop floor?",
        4,
        "Connectivity — the app queues entries and syncs when the network returns",
        "Operations",
    ),
    Question(
        "supervisors_literate_digital",
        "Are your supervisors comfortable using a phone app?",
        5,
        "Digital comfort — week 1 is habit training, not configuration",
        "Operations",
    ),
)

MAX_SCORE = sum(question.weight for question in QUESTIONS)


@dataclass(slots=True)
class ReadinessOutcome:
    score: int
    band: str
    priority_gaps: list[str]
    estimated_onboarding_hours: int
    message: str


def score(answers: Answers) -> ReadinessOutcome:
    """Score out of 100, with the gaps that actually cost onboarding time."""
    earned = sum(
        question.weight for question in QUESTIONS if _truthy(answers.get(question.key))
    )
    normalised = round(earned * 100 / MAX_SCORE)

    # Heaviest-weighted first: the gaps that cost the most onboarding time.
    gaps = [
        gap
        for _, gap in sorted(
            (
                (question.weight, question.gap_if_missing)
                for question in QUESTIONS
                if question.gap_if_missing and not _truthy(answers.get(question.key))
            ),
            reverse=True,
        )
    ][:5]

    if normalised >= 70:
        band = "Ready"
        hours = 4
        message = (
            "You can start immediately. Most of what we need already exists in "
            "your systems — onboarding is about half a day."
        )
    elif normalised >= 40:
        band = "Mostly ready"
        hours = 6
        message = (
            "You can start immediately with manual capture. Estimated "
            "onboarding: half a day, plus a floor walk to fill the gaps below."
        )
    else:
        band = "Start from scratch"
        hours = 8
        message = (
            "You can still start on day one — we begin with day-batch "
            "traceability and Basic costing, and tighten as the plant builds the "
            "habit. Expect a full day of onboarding."
        )

    return ReadinessOutcome(
        score=normalised,
        band=band,
        priority_gaps=gaps,
        estimated_onboarding_hours=hours,
        message=message,
    )


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"yes", "y", "true", "1"}
    return bool(value)
