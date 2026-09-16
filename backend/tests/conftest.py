"""Integration-test fixtures.

These run against a real Postgres. The alternative — mocking the session — would
test the mocks: most of what matters here (effective-dated rate resolution, the
one-open-downtime-per-station constraint, RLS) only exists in the database.

The schema is built by running the Alembic migrations, not ``create_all``: the
constraints, partial indexes and row-level security that production relies on
exist only in the migrations, and a suite that never ran them never tested them.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date, time, timedelta
from decimal import Decimal
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.kernel import rupees
from app.models import (
    CostFamily,
    CostingMode,
    CostRate,
    DefectCode,
    EscalationRoute,
    OeeTreatment,
    Part,
    Plant,
    RateSource,
    ReasonCode,
    Recoverability,
    Role,
    RoutingOperation,
    Section,
    Shift,
    Station,
    User,
    UserPlantRole,
)

TEST_DB = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://factorymanager:factorymanager@localhost:5432/factorymanager_test",
)

# The restricted role the migration creates, under a test-only name so running
# the suite can never change the password of the role a real app connects as.
TEST_APP_ROLE = "fm_app_test"
TEST_APP_PASSWORD = "fm-app-test-only"
BACKEND = Path(__file__).resolve().parents[1]


def _migrate(url: str) -> None:
    from alembic.config import Config

    from alembic import command

    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.attributes["database_url"] = url
    os.environ["APP_DB_ROLE"] = TEST_APP_ROLE
    os.environ["APP_DB_PASSWORD"] = TEST_APP_PASSWORD
    command.upgrade(config, "head")


def restricted_url() -> str:
    """The test database, as the non-superuser role row-level security applies to."""
    head, name = TEST_DB.rsplit("/", 1)
    scheme, rest = head.split("://", 1)
    host = rest.split("@", 1)[1]
    return f"{scheme}://{TEST_APP_ROLE}:{TEST_APP_PASSWORD}@{host}/{name}"


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine():
    admin_url = TEST_DB.rsplit("/", 1)[0] + "/postgres"
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    name = TEST_DB.rsplit("/", 1)[1]
    async with admin.connect() as connection:
        from sqlalchemy import text

        await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await connection.execute(text(f'CREATE DATABASE "{name}"'))
    await admin.dispose()

    await asyncio.to_thread(_migrate, TEST_DB)

    engine = create_async_engine(TEST_DB)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def session(engine) -> AsyncSession:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def plant(session: AsyncSession) -> Plant:
    """A minimal plant carrying the spec's own CNC-3 / AK-447 economics."""
    plant = Plant(
        name="Test Plant",
        code=f"TEST{uuid.uuid4().hex[:6]}",
        costing_mode=CostingMode.STANDARD,
        action_cost_threshold=rupees(2500),
        action_duration_minutes=30,
        recurrence_count=3,
        recurrence_window_shifts=7,
        go_live_date=date.today() - timedelta(weeks=8),
        monthly_fee=rupees(25000),
    )
    session.add(plant)
    await session.flush()

    family = CostFamily(plant_id=plant.id, code="CNC-family", name="CNC Turning")
    session.add(family)
    await session.flush()

    effective = date.today() - timedelta(weeks=12)
    session.add_all(
        [
            CostRate(
                plant_id=plant.id,
                scope="cost_family",
                scope_ref=str(family.id),
                machine_rate=rupees(420),
                source=RateSource.DERIVED,
                effective_from=effective,
            ),
            CostRate(
                plant_id=plant.id,
                scope="section",
                scope_ref="machining",
                labour_rate=rupees(180),
                source=RateSource.MEASURED,
                effective_from=effective,
            ),
            CostRate(
                plant_id=plant.id,
                scope="plant",
                scope_ref="plant",
                overhead_rate=rupees(210),
                source=RateSource.DERIVED,
                effective_from=effective,
            ),
        ]
    )

    station = Station(
        plant_id=plant.id,
        code="CNC-3",
        name="CNC Turning 3",
        section=Section.MACHINING,
        line="Line 1",
        is_bottleneck=True,
        bottleneck_factor=Decimal("1.00"),
        cost_family_id=family.id,
    )
    session.add(station)

    part = Part(
        plant_id=plant.id,
        part_number="AK-447",
        description="Bracket Assembly LH",
        customer_name="Rane TRW",
        part_family="Brackets",
        contribution_margin=rupees(38),
        material_cost=rupees(72),
    )
    session.add(part)
    await session.flush()

    session.add(
        RoutingOperation(
            plant_id=plant.id,
            part_id=part.id,
            sequence=10,
            operation_code="Op10",
            section=Section.MACHINING,
            cost_family_id=family.id,
            cycle_time_min=Decimal("2.4"),
            cycle_time_source=RateSource.MEASURED,
        )
    )

    session.add_all(
        [
            ReasonCode(
                plant_id=plant.id,
                code="TOOL_BREAKAGE",
                label="Tool Breakage",
                recoverability=Recoverability.RECOVERABLE,
                oee_treatment=OeeTreatment.UNPLANNED,
                escalation_route=EscalationRoute.MAINTENANCE,
                display_order=1,
            ),
            ReasonCode(
                plant_id=plant.id,
                code="MATERIAL_WAIT",
                label="Material Wait",
                recoverability=Recoverability.RECOVERABLE,
                oee_treatment=OeeTreatment.UNPLANNED,
                escalation_route=EscalationRoute.STORES,
                display_order=2,
            ),
            ReasonCode(
                plant_id=plant.id,
                code="POWER_CUT",
                label="Power Cut",
                recoverability=Recoverability.STRUCTURAL,
                oee_treatment=OeeTreatment.UNPLANNED,
                escalation_route=EscalationRoute.NONE,
                display_order=3,
            ),
            ReasonCode(
                plant_id=plant.id,
                code="PLANNED_MAINTENANCE",
                label="Planned Maintenance",
                recoverability=Recoverability.STRUCTURAL,
                oee_treatment=OeeTreatment.PLANNED,
                escalation_route=EscalationRoute.MAINTENANCE,
                display_order=4,
            ),
        ]
    )
    session.add(
        Shift(
            plant_id=plant.id,
            name="Shift A",
            start_time=time(7, 0),
            end_time=time(15, 30),
            break_minutes=45,
            display_order=1,
        )
    )
    await session.flush()
    return plant


async def run_job(session: AsyncSession, fixtures: dict, shift_session):
    """Put AK-447 on CNC-3 for this shift.

    Events inherit their part, customer, lot and operator from whichever job is
    active on the station (§3's job-order spine) — so without a running job an
    event genuinely has no cycle time or margin, and costs only machine, labour
    and overhead. Tests that expect lost contribution must start a job first.
    """
    from sqlalchemy import select

    from app.models import (
        JobOrder,
        JobOrderStatus,
        JobSession,
        Lot,
        LotGranularity,
        RoutingOperation,
    )

    job = JobOrder(
        plant_id=fixtures["plant"].id,
        job_number="2231",
        part_id=fixtures["part"].id,
        customer_name=fixtures["part"].customer_name,
        planned_quantity=500,
        status=JobOrderStatus.RUNNING,
    )
    session.add(job)
    await session.flush()

    lot = Lot(
        plant_id=fixtures["plant"].id,
        lot_code="4471",
        job_order_id=job.id,
        granularity=LotGranularity.JOB_LOT,
        quantity=500,
    )
    session.add(lot)
    operation = await session.scalar(
        select(RoutingOperation).where(RoutingOperation.part_id == fixtures["part"].id)
    )
    await session.flush()

    job_session = JobSession(
        plant_id=fixtures["plant"].id,
        job_order_id=job.id,
        shift_session_id=shift_session.id,
        station_id=fixtures["station"].id,
        lot_id=lot.id,
        routing_operation_id=operation.id if operation else None,
        started_at=shift_session.started_at,
    )
    session.add(job_session)
    await session.flush()
    return job_session


@pytest_asyncio.fixture(loop_scope="session")
async def fixtures(session: AsyncSession, plant: Plant) -> dict:
    from sqlalchemy import select

    return {
        "plant": plant,
        "station": await session.scalar(
            select(Station).where(Station.plant_id == plant.id)
        ),
        "part": await session.scalar(select(Part).where(Part.plant_id == plant.id)),
        "shift": await session.scalar(select(Shift).where(Shift.plant_id == plant.id)),
        "reasons": {
            r.code: r
            for r in (
                await session.scalars(
                    select(ReasonCode).where(ReasonCode.plant_id == plant.id)
                )
            ).all()
        },
    }


@pytest_asyncio.fixture(loop_scope="session")
async def api(session, fixtures):
    """A client whose requests share the test's transaction, plus a token per role."""
    import httpx

    from app.api.deps import get_session
    from app.core.security import create_token, hash_password
    from app.main import app

    async def shared_session():
        yield session

    app.dependency_overrides[get_session] = shared_session
    plant = fixtures["plant"]

    tokens: dict[str, dict] = {}
    for role in (Role.OWNER, Role.SUPERVISOR, Role.QUALITY, Role.MAINTENANCE, Role.STORES):
        user = User(
            full_name=f"{role.value.title()} {uuid.uuid4().hex[:4]}",
            phone=f"+91{uuid.uuid4().int % 10**10:010d}",
            password_hash=hash_password("not-used-here"),
        )
        session.add(user)
        await session.flush()
        session.add(UserPlantRole(user_id=user.id, plant_id=plant.id, role=role))
        tokens[role.value] = {
            "Authorization": "Bearer "
            + create_token(user_id=user.id, plant_id=plant.id, roles=[role.value]),
            "user_id": user.id,
        }

    session.add(DefectCode(plant_id=plant.id, code="BURR", label="Burr", display_order=1))

    # A second plant, to prove its ids are refused.
    other = Plant(name="Other Plant", code=f"OTH{uuid.uuid4().hex[:6]}")
    session.add(other)
    await session.flush()
    foreign_station = Station(
        plant_id=other.id, code="PRESS-9", name="Press 9", bottleneck_factor=Decimal("1")
    )
    session.add(foreign_station)
    await session.flush()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:

        def as_(role: str) -> dict:
            return {"Authorization": tokens[role]["Authorization"]}

        yield {
            "client": client,
            "as": as_,
            "tokens": tokens,
            "foreign_station": foreign_station,
        }
    app.dependency_overrides.pop(get_session, None)
