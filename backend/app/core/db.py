"""Async SQLAlchemy engine, session factory, and the tenancy guard.

Multi-tenancy is enforced by Postgres Row-Level Security, not by a ``WHERE``
convention a developer can forget (ARCHITECTURE.md §3.7). Every request sets
``app.current_plant`` on its connection; RLS policies do the rest. A query that
forgets the plant filter returns nothing instead of another plant's losses.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import settings

engine = create_async_engine(
    str(settings.database_url),
    echo=settings.db_echo,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
)

SessionFactory = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)

# Scripts (seeders, schema tools) write across plants and create tables, which
# the restricted application role cannot. They connect as the owning role.
# Created lazily, so the API never opens a pool it does not use.
_admin_engine = None


def admin_engine():
    global _admin_engine
    if _admin_engine is None:
        _admin_engine = create_async_engine(
            settings.admin_database_url, echo=settings.db_echo, pool_pre_ping=True
        )
    return _admin_engine


def AdminSessionFactory() -> AsyncSession:
    return AsyncSession(admin_engine(), expire_on_commit=False, autoflush=False)


async def set_plant_scope(session: AsyncSession, plant_id: UUID | None) -> None:
    """Bind this connection to one plant for the life of the transaction."""
    await session.execute(
        text("SELECT set_config('app.current_plant', :plant, true)"),
        {"plant": str(plant_id) if plant_id else ""},
    )


@asynccontextmanager
async def session_scope(plant_id: UUID | None = None) -> AsyncIterator[AsyncSession]:
    """Transactional scope for workers and scripts (the API uses a dependency)."""
    async with SessionFactory() as session:
        try:
            if plant_id is not None:
                await set_plant_scope(session, plant_id)
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    await engine.dispose()
