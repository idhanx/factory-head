"""Seed Layer 5 demo data into an existing plant — without wiping anything.

`seed.py` rebuilds the demo plant from scratch. This script only *adds* the
plugin data, and is safe to run repeatedly, so a database somebody has already
been using keeps its history.

Everything goes through the real plugin services. Kaizen ideas are scored by the
same code that scores a real operator's submission, against this plant's real
loss history — so the rupee figures in the demo are the ones the product would
actually produce, not numbers typed into a fixture.
"""

from __future__ import annotations

import asyncio
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

from app.core.db import AdminSessionFactory as SessionFactory
from app.models import (
    KaizenIdea,
    Operator,
    OperatorSkill,
    Plant,
    Section,
    Station,
)
from app.services import compliance as compliance_service
from app.services import kaizen as kaizen_service
from app.services import skills as skills_service

random.seed(20260913)

# Ideas an operator on a Hosur machining floor would genuinely write. Each is
# phrased the way people actually talk, because the scorer has to cope with that.
KAIZEN_IDEAS = [
    (
        "Kumar M.",
        "CNC-3",
        "Change insert at 240 parts, not when it breaks",
        "Tool insert on CNC-3 keeps breaking in the first hours of the shift. If we change "
        "the insert on a fixed count before it wears out, we stop the breakage and the "
        "scrap that comes with it.",
    ),
    (
        "Ravi S.",
        "Press-2",
        "Stage the material trolley before the shift starts",
        "Press-2 waits for material almost every morning. Stores can keep the trolley "
        "ready at the line the previous evening so the first job does not wait.",
    ),
    (
        "Selvam P.",
        "VMC-1",
        "Pre-kit changeover tools on a shadow board",
        "During setup we walk to stores for the tooling. A shadow board with the kit for "
        "each job next to VMC-1 would cut the changeover time.",
    ),
    (
        "Anbu R.",
        "CNC-1",
        "Check coolant concentration at shift start",
        "Coolant on CNC-1 gets diluted and the parts drift. A two-minute refractometer "
        "check at the start of every shift would catch it.",
    ),
    (
        "Dinesh K.",
        None,
        "Colour-code fixture locating pins by part",
        "Operators sometimes fit the wrong locating pin on the fixture and the first few "
        "parts go bad. Colour-coding the pins by part family would stop the mix-up.",
    ),
    (
        "Vignesh T.",
        "Drill-2",
        "Cross-train one more person on the radial drill",
        "When the regular drill operator is on leave, Drill-2 stands idle waiting for "
        "an operator. Training one more person would keep it running.",
    ),
    (
        "Prakash N.",
        None,
        "Put a fan near the inspection bench",
        "The QC bench gets very hot in the afternoon and it is hard to concentrate on "
        "inspection. A fan would help.",
    ),
    (
        "Manikandan S.",
        "Assy-L1",
        "Keep spare clamp pads at the assembly line",
        "Clamp pads on Assy-L1 wear out and we stop the line to fetch new ones from "
        "maintenance. Keeping a few spares at the line would avoid the stop.",
    ),
]


async def seed_plugins() -> None:
    async with SessionFactory() as session:
        plant = await session.scalar(select(Plant).where(Plant.code == "KUMARAN"))
        if plant is None:
            print("  No KUMARAN plant found — run scripts/seed.py first.")
            return

        # --- Compliance -----------------------------------------------------
        created = await compliance_service.seed_clauses(session, plant=plant)
        clauses = await compliance_service.score_readiness(session, plant=plant)
        print(
            f"  compliance ..... {created} clauses added, "
            f"readiness {compliance_service.readiness_score(clauses)}%"
        )

        # --- Skill matrix ---------------------------------------------------
        skills = await _skill_matrix(session, plant)
        print(f"  skill matrix ... {skills} certifications")

        # --- Kaizen ---------------------------------------------------------
        ideas = await _kaizen(session, plant)
        print(f"  kaizen ......... {ideas} ideas")

        # --- Training needs, derived from all of the above ------------------
        needs = await skills_service.detect_needs(session, plant=plant)
        print(f"  training needs . {len(needs)} ranked by rupee exposure")

        # Re-score now that skills and Kaizen exist, so those clauses reflect it.
        clauses = await compliance_service.score_readiness(session, plant=plant)
        print(f"  readiness now .. {compliance_service.readiness_score(clauses)}%")

        await session.commit()


async def _skill_matrix(session, plant: Plant) -> int:
    """A realistic matrix — including the gaps a real plant has.

    Most stations get two or three competent operators. A few are deliberately
    left thin, including CNC-3, the bottleneck: the single-person dependency a
    plant never notices until the one person who can run it is off sick. That is
    the finding the Skill Matrix plugin exists to surface.
    """
    already = await session.scalar(
        select(func.count(OperatorSkill.id)).where(OperatorSkill.plant_id == plant.id)
    )
    if already:
        return int(already)

    stations = (
        await session.scalars(
            select(Station)
            .where(
                Station.plant_id == plant.id,
                Station.section.in_([Section.MACHINING, Section.ASSEMBLY]),
            )
            .order_by(Station.code)
        )
    ).all()
    operators = (
        await session.scalars(
            select(Operator).where(Operator.plant_id == plant.id).order_by(Operator.name)
        )
    ).all()
    if not stations or not operators:
        return 0

    # Stations deliberately left with a single competent operator.
    thin = {"CNC-3", "Drill-2", "Weld-1"}
    certified_on = date.today() - timedelta(days=120)
    count = 0

    for station in stations:
        pool = random.sample(operators, k=min(len(operators), 5))
        competent = 1 if station.code in thin else random.choice([2, 2, 3])

        for index, operator in enumerate(pool):
            if index < competent:
                level = 3 if index == 0 and random.random() < 0.4 else 2
            elif index < competent + 1:
                level = 1  # in training — not yet able to run it alone
            else:
                continue

            await skills_service.set_skill(
                session,
                plant=plant,
                operator_id=operator.id,
                station_id=station.id,
                level=level,
                certified_on=certified_on + timedelta(days=random.randint(0, 90)),
            )
            count += 1

    return count


async def _kaizen(session, plant: Plant) -> int:
    already = await session.scalar(
        select(func.count(KaizenIdea.id)).where(KaizenIdea.plant_id == plant.id)
    )
    if already:
        return int(already)

    # The plugin needs its category-to-reason-code map before the History Check
    # can find anything — the same fifteen minutes a consultant spends at
    # onboarding, done here from Core's own T6 codes.
    await kaizen_service.settings_for(session, plant=plant)

    stations = {
        s.code: s
        for s in (
            await session.scalars(select(Station).where(Station.plant_id == plant.id))
        ).all()
    }
    # Core issues no operator logins at launch, so month one runs on
    # submit-on-behalf: a supervisor files it, the named operator gets the
    # points, the credit and the participation count.
    operators = {
        o.name: o
        for o in (
            await session.scalars(select(Operator).where(Operator.plant_id == plant.id))
        ).all()
    }

    ideas: list[KaizenIdea] = []
    for name, station_code, title, description in KAIZEN_IDEAS:
        station = stations.get(station_code) if station_code else None
        operator = operators.get(name)
        idea = await kaizen_service.submit(
            session,
            plant=plant,
            submission=kaizen_service.Submission(
                title=title,
                description=description,
                station_id=station.id if station else None,
                credited_operator_id=operator.id if operator else None,
                # A plausible shop-floor interview: two tapped answers, which is
                # what the operator journey actually produces.
                interview=[
                    {"key": "minutes_per_wait", "answer": "5-15 min"},
                    {"key": "waits_per_shift", "answer": 2},
                    {"key": "seconds_per_trip", "answer": "30-60 sec"},
                    {"key": "trips_per_shift", "answer": 8},
                    {"key": "parts_per_month", "answer": 40},
                ],
            ),
            submitted_by_name=name,
        )
        ideas.append(idea)

    # Decide some of them, the way a supervisor would in a weekly review.
    #
    # The facilities request is declined first — with a reason, because an idea
    # that vanishes without an answer is how a suggestion scheme dies. Then the
    # ideas with the biggest *measured* saving are approved. Only ideas the
    # engine could price are eligible: approving one the system values at zero
    # would put an unmeasurable promise on the board.
    fan = next((i for i in ideas if "fan" in i.title.lower()), None)
    if fan is not None:
        await kaizen_service.decide(
            session,
            plant=plant,
            idea=fan,
            accept=False,
            user=None,
            note=(
                "Good idea, but it is a facilities request rather than a production "
                "improvement — passed to the admin office, who have ordered two fans."
            ),
        )

    undecided = [
        i for i in ideas if i.decided_at is None and i.hard_saving_paise > 0
    ]
    for idea in sorted(undecided, key=lambda i: i.hard_saving_paise, reverse=True)[:3]:
        await kaizen_service.decide(
            session, plant=plant, idea=idea, accept=True, user=None, note="Worth doing."
        )

    return len(ideas)


if __name__ == "__main__":
    print("Seeding Layer 5 plugin data...")
    asyncio.run(seed_plugins())
    print("Done.")
