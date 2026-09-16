"""phase 1: escalation resolution, separate rework counts, enforced tenancy

Revision ID: 5c1e7a9d2b44
Revises: 0ba4b6bb4343
Create Date: 2026-09-13 17:45:00

Three things the audit found the database was only pretending to do:

* An escalation could be sent but never resolved, so one fired rung blocked
  shift closure forever. Escalations now carry who acknowledged them and when
  the machine restarted.
* ``ck_quality_scrap_xor_rework`` made a batch with both scrapped and reworked
  parts a 500. ``rework_count`` is how many of the good parts needed rework
  (which is how the existing data already reads), independent of what happened
  to the rejects, so the constraint becomes "never negative".
* Row-level security was enabled but inert: the policy let every row through
  when no plant was set, and the app connected as a superuser that bypasses
  RLS anyway. Policies are now strict, forced on table owners, and a
  non-superuser application role is created for the API and worker to use.
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op

revision: str = "5c1e7a9d2b44"
down_revision: str | None = "0ba4b6bb4343"
branch_labels: str | None = None
depends_on: str | None = None

# Tables that are deliberately not plant-scoped: identity is resolved from them
# before a plant is known, and the readiness questionnaire precedes the plant.
EXEMPT = {"plants", "users", "user_plant_roles", "readiness_assessments", "alembic_version"}

STRICT_POLICY = """
    CREATE POLICY plant_isolation ON {table}
    USING (plant_id::text = current_setting('app.current_plant', true))
    WITH CHECK (plant_id::text = current_setting('app.current_plant', true))
"""

PERMISSIVE_POLICY = """
    CREATE POLICY plant_isolation ON {table}
    USING (
        current_setting('app.current_plant', true) IS NULL
        OR current_setting('app.current_plant', true) = ''
        OR plant_id::text = current_setting('app.current_plant', true)
    )
"""


def _scoped_tables() -> list[str]:
    inspector = sa.inspect(op.get_bind())
    return sorted(
        table
        for table in set(inspector.get_table_names()) - EXEMPT
        if any(column["name"] == "plant_id" for column in inspector.get_columns(table))
    )


def upgrade() -> None:
    # --- escalations: acknowledgement and resolution ------------------------
    op.add_column(
        "escalations",
        sa.Column("acknowledged_by_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "escalations",
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_escalations_acknowledged_by",
        "escalations",
        "users",
        ["acknowledged_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # A machine that is already running again has nothing left to escalate.
    op.execute(
        """
        UPDATE escalations e
        SET resolved_at = d.ended_at
        FROM downtime_events d
        WHERE d.id = e.downtime_event_id
          AND d.status = 'closed'
          AND e.status IN ('sent', 'failed')
          AND e.resolved_at IS NULL
        """
    )

    # --- quality: scrap and rework are separate counts -------------------------
    op.execute("ALTER TABLE quality_records DROP CONSTRAINT IF EXISTS ck_quality_scrap_xor_rework")
    op.create_check_constraint(
        "ck_quality_rework_nonneg", "quality_records", "rework_count >= 0"
    )

    # --- tenancy: strict, forced policies --------------------------------------
    for table in _scoped_tables():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS plant_isolation ON {table}")
        op.execute(STRICT_POLICY.format(table=table))

    # --- the application role ---------------------------------------------------
    # Superusers bypass RLS whatever the policy says, so the API and worker must
    # connect as a role that cannot. Scripts and migrations keep the owner role.
    role = os.environ.get("APP_DB_ROLE", "fm_app")
    password = os.environ.get("APP_DB_PASSWORD")
    # Roles are cluster-wide. Without a password an existing role is left
    # exactly as it is — another database on this server may be logging in
    # with it — and a new one is created unable to log in until one is set.
    if password:
        create = f"CREATE ROLE {role} LOGIN PASSWORD {_quote(password)} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
        existing = f"ALTER ROLE {role} LOGIN PASSWORD {_quote(password)} NOSUPERUSER NOBYPASSRLS"
    else:
        create = f"CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
        existing = f"ALTER ROLE {role} NOSUPERUSER NOBYPASSRLS"
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                {create};
            ELSE
                {existing};
            END IF;
        END $$;
        """
    )
    op.execute(f"GRANT CONNECT ON DATABASE {_current_database()} TO {role}")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}")
    op.execute(f"REVOKE INSERT, UPDATE, DELETE ON alembic_version FROM {role}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}"
    )


def downgrade() -> None:
    role = os.environ.get("APP_DB_ROLE", "fm_app")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {role}"
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role};
                REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role};
                REVOKE USAGE ON SCHEMA public FROM {role};
            END IF;
        END $$;
        """
    )

    for table in _scoped_tables():
        op.execute(f"DROP POLICY IF EXISTS plant_isolation ON {table}")
        op.execute(PERMISSIVE_POLICY.format(table=table))
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")

    op.drop_constraint("ck_quality_rework_nonneg", "quality_records", type_="check")
    op.execute(
        """
        ALTER TABLE quality_records
        ADD CONSTRAINT ck_quality_scrap_xor_rework
        CHECK (NOT (disposition = 'scrap' AND rework_count > 0))
        NOT VALID
        """
    )

    op.drop_constraint("fk_escalations_acknowledged_by", "escalations", type_="foreignkey")
    op.drop_column("escalations", "resolved_at")
    op.drop_column("escalations", "acknowledged_by_id")


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _current_database() -> str:
    name = op.get_bind().execute(sa.text("SELECT current_database()")).scalar()
    return '"' + str(name).replace('"', '""') + '"'
