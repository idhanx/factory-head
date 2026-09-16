"""Row-level security, as the application actually connects.

The audit found RLS enabled but inert: a permissive policy, and an app that
connected as a superuser. These tests connect as the restricted role the
migration creates and prove the database itself keeps plants apart — so a
handler that forgets a plant filter returns nothing rather than a competitor's
losses.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import restricted_url


@pytest_asyncio.fixture(loop_scope="session")
async def two_plants(engine):
    """Two committed plants with one operator each; removed afterwards."""
    codes = [f"RLS{uuid.uuid4().hex[:6]}", f"RLS{uuid.uuid4().hex[:6]}"]
    async with engine.begin() as connection:
        ids = []
        for code in codes:
            plant_id = uuid.uuid4()
            await connection.execute(
                text(
                    "INSERT INTO plants (id, name, code, timezone, currency, costing_mode, "
                    "business_date_from_shift_start, action_cost_threshold, action_duration_minutes, "
                    "recurrence_count, recurrence_window_shifts, reconciliation_tolerance_parts, "
                    "reconciliation_tolerance_bp, monthly_fee, is_active) VALUES "
                    "(:id, :code, :code, 'Asia/Kolkata', 'INR', 'basic', true, 250000, 30, 3, 7, 5, 200, 0, true)"
                ),
                {"id": plant_id, "code": code},
            )
            await connection.execute(
                text(
                    "INSERT INTO operators (id, name, is_active, plant_id) "
                    "VALUES (:id, :name, true, :plant)"
                ),
                {"id": uuid.uuid4(), "name": f"Operator of {code}", "plant": plant_id},
            )
            ids.append(plant_id)
    yield ids
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM plants WHERE id = ANY(:ids)"), {"ids": ids})


async def test_the_app_role_sees_nothing_until_a_plant_is_bound(two_plants):
    restricted = create_async_engine(restricted_url())
    try:
        async with restricted.connect() as connection:
            unscoped = await connection.scalar(text("SELECT count(*) FROM operators"))
            assert unscoped == 0

            await connection.execute(
                text("SELECT set_config('app.current_plant', :plant, true)"),
                {"plant": str(two_plants[0])},
            )
            rows = (await connection.execute(text("SELECT plant_id FROM operators"))).all()
            assert {row[0] for row in rows} == {two_plants[0]}
    finally:
        await restricted.dispose()


async def test_the_app_role_cannot_write_into_another_plant(two_plants):
    restricted = create_async_engine(restricted_url())
    try:
        async with restricted.connect() as connection:
            await connection.execute(
                text("SELECT set_config('app.current_plant', :plant, true)"),
                {"plant": str(two_plants[0])},
            )
            with pytest.raises(DBAPIError, match="row-level security"):
                await connection.execute(
                    text(
                        "INSERT INTO operators (id, name, is_active, plant_id) "
                        "VALUES (:id, 'Planted', true, :other)"
                    ),
                    {"id": uuid.uuid4(), "other": two_plants[1]},
                )
    finally:
        await restricted.dispose()


async def test_the_app_role_is_not_a_superuser(engine):
    async with engine.connect() as connection:
        flags = (
            await connection.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'fm_app_test'")
            )
        ).one()
    assert flags == (False, False)
