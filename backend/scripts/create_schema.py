"""Create the schema, RLS policies and TimescaleDB hypertables.

Used for local development and CI. Production changes go through Alembic; this
script is the fast path to a working database and the source the initial
migration is generated from.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.core.db import admin_engine
from app.models import Base

engine = admin_engine()

# Tables that are time-series by nature. TimescaleDB turns each into a
# hypertable so the owner's "last four weeks" queries stay fast as a plant
# accumulates a year of shifts.
HYPERTABLES = {
    "downtime_events": "started_at",
    "production_counts": "recorded_at",
    "quality_records": "recorded_at",
    "stage_completions": "completed_at",
}

# Multi-tenancy is enforced in the database, not by a WHERE convention a
# developer can forget (ARCHITECTURE.md §3.7). A query that omits the plant
# filter returns nothing rather than another plant's losses.
RLS_EXEMPT = {"plants", "users", "user_plant_roles", "readiness_assessments"}


async def create_schema(*, drop: bool = False) -> None:
    async with engine.begin() as connection:
        if drop:
            await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
        print(f"  tables ......... {len(Base.metadata.tables)}")

    await _apply_hypertables()
    await _apply_rls()
    await _apply_integrity_rules()


async def _apply_hypertables() -> None:
    async with engine.begin() as connection:
        available = await connection.scalar(
            text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
        )
        if not available:
            # Plain Postgres works correctly; it simply partitions less well at
            # scale. The btree indexes on (plant_id, business_date) carry a
            # pilot plant comfortably.
            print("  hypertables .... skipped (timescaledb extension not installed)")
            return

        for table, column in HYPERTABLES.items():
            await connection.execute(
                text(
                    f"SELECT create_hypertable('{table}', '{column}', "
                    "if_not_exists => TRUE, migrate_data => TRUE)"
                )
            )
        print(f"  hypertables .... {len(HYPERTABLES)}")


async def _apply_rls() -> None:
    async with engine.begin() as connection:
        applied = 0
        for table in Base.metadata.tables:
            if table in RLS_EXEMPT:
                continue
            await connection.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
            await connection.execute(
                text(f"DROP POLICY IF EXISTS plant_isolation ON {table}")
            )
            # An empty `app.current_plant` means "no plant chosen" — used by
            # migrations and the seed script, which run as the table owner and
            # therefore bypass RLS anyway.
            await connection.execute(
                text(
                    f"""
                    CREATE POLICY plant_isolation ON {table}
                    USING (
                        current_setting('app.current_plant', true) IS NULL
                        OR current_setting('app.current_plant', true) = ''
                        OR plant_id::text = current_setting('app.current_plant', true)
                    )
                    """
                )
            )
            applied += 1
        print(f"  rls policies ... {applied}")


async def _apply_integrity_rules() -> None:
    """Invariants the spec states as rules, enforced where they cannot be missed."""
    async with engine.begin() as connection:
        # §8.1 — "reworked parts are NEVER also counted as scrap".
        await connection.execute(
            text(
                """
                ALTER TABLE quality_records
                DROP CONSTRAINT IF EXISTS ck_quality_scrap_xor_rework
                """
            )
        )
        await connection.execute(
            text(
                """
                ALTER TABLE quality_records
                ADD CONSTRAINT ck_quality_scrap_xor_rework
                CHECK (NOT (disposition = 'scrap' AND rework_count > 0))
                """
            )
        )

        # One machine cannot be stopped twice at the same time. A duplicate tap
        # on a shared tablet must not double-count a single stop.
        await connection.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_one_open_downtime_per_station
                ON downtime_events (station_id)
                WHERE status = 'open'
                """
            )
        )

        # Factory Memory search (Layer 4). Postgres FTS carries retrieval far
        # past 100 plants; pgvector joins it only when a plant has the years of
        # history that make semantic recall worth a second index.
        await connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_memory_fts ON memory_entries
                USING gin (
                    to_tsvector('english',
                        coalesce(problem_summary,'') || ' ' ||
                        coalesce(what_fixed_it,'') || ' ' ||
                        coalesce(check_first_next_time,'')
                    )
                )
                """
            )
        )
        print("  constraints .... 3")


if __name__ == "__main__":
    drop = "--drop" in sys.argv
    print("Creating FactoryManager AI schema...")
    asyncio.run(create_schema(drop=drop))
    print("Done.")
