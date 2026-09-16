"""Alembic environment.

The URL comes from application settings, never from alembic.ini — one source of
truth for where the database is, and no credentials in a committed file.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.models import Base

config = context.config
# Migrations run as the owning role: the application role cannot run DDL. A
# caller that builds its own database (the test suite) passes the URL in
# explicitly, so it can never fall through to whatever .env points at.
config.set_main_option(
    "sqlalchemy.url", config.attributes.get("database_url") or settings.admin_database_url
)

# The Phase 1 migration creates the application role from APP_DB_PASSWORD. It
# usually lives in backend/.env, which the settings object has already read.
if settings.app_db_password and "APP_DB_PASSWORD" not in os.environ:
    os.environ["APP_DB_PASSWORD"] = settings.app_db_password

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


# Indexes created as raw SQL in a migration because SQLAlchemy metadata cannot
# express them: a partial unique index and an expression-based GIN index.
# Autogenerate sees no matching metadata and proposes dropping them, which
# would quietly remove the "one open downtime per station" invariant and the
# Factory Memory search index.
RAW_SQL_INDEXES = {"uq_one_open_downtime_per_station", "ix_memory_fts"}


def include_object(object, name, type_, reflected, compare_to):
    """Keep TimescaleDB's chunk tables and raw-SQL indexes out of diffs."""
    if type_ == "table" and name.startswith("_hyper_"):
        return False
    if type_ == "index" and name in RAW_SQL_INDEXES:
        return False
    return True


def render_item(type_, obj, autogen_context):
    """Render `EnumString` as the plain VARCHAR it actually is.

    A migration must never import application code. `EnumString` stores a
    VARCHAR and coerces in Python, so the DDL is identical — and rendering it
    as `sa.String` means deleting or renaming an enum class in two years
    cannot break replaying a migration written today.
    """
    from app.models.types import EnumString

    if type_ == "type" and isinstance(obj, EnumString):
        return f"sa.String(length={obj.impl.length})"
    return False


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
        render_item=render_item,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            render_item=render_item,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
