"""the maintenance and stores desks — escalation routing, repair log, staging

Revision ID: 6b4d8f2c7a15
Revises: 3a7c9e1f5b22
Create Date: 2026-09-16 18:30:00

§1's "Who uses it" table gives the Maintenance In-charge two duties ("receives
escalation alerts", "logs what was done to fix breakdowns") and the Stores
In-charge two more ("receives material-shortage alerts", "confirms material
staging"). None of the four had anywhere to land. This migration adds what they
need:

* ``escalations.route`` — §3 Layer 2 says "the system picks the recipient from
  the reason code", and the ladder does exactly that, but then stored only the
  person's name. Without the route, an inbox cannot separate a breakdown
  addressed to Maintenance from a material wait addressed to Stores, and the
  routing decision is lost the day the recipient list changes. Backfilled from
  the reason code each existing escalation was raised under, using the same
  rules the ladder applies live.
* ``downtime_events.repair_*`` — what was actually done at the machine, by whom.
* ``material_stagings`` — Stores' standing answer, one row per job order per day.
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op

revision: str = "6b4d8f2c7a15"
down_revision: str | None = "3a7c9e1f5b22"
branch_labels: str | None = None
depends_on: str | None = None

STRICT_POLICY = """
    CREATE POLICY plant_isolation ON {table}
    USING (plant_id::text = current_setting('app.current_plant', true))
    WITH CHECK (plant_id::text = current_setting('app.current_plant', true))
"""


def _timestamps():
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def upgrade() -> None:
    # --- who each rung was aimed at -------------------------------------------
    op.add_column("escalations", sa.Column("route", sa.String(length=20), nullable=True))
    op.create_index("ix_escalations_route", "escalations", ["plant_id", "route"])

    # Backfill with the ladder's own rules (app/services/downtime.py):
    # L1 is the push to the supervisor already on the screen; L3 is the plant
    # head; L2 follows the reason code. An event whose reason was still blank
    # when it was raised routes to production, as the live path does.
    op.execute(
        """
        UPDATE escalations SET route = 'supervisor' WHERE level = 1;

        UPDATE escalations SET route = 'plant_head' WHERE level >= 3;

        UPDATE escalations e
           SET route = COALESCE(r.escalation_route, 'production')
          FROM downtime_events d
          LEFT JOIN reason_codes r ON r.id = d.reason_code_id
         WHERE d.id = e.downtime_event_id AND e.level = 2;

        UPDATE escalations SET route = 'production' WHERE route IS NULL;
        """
    )

    # --- what was done about it -----------------------------------------------
    op.add_column("downtime_events", sa.Column("repair_note", sa.Text(), nullable=True))
    op.add_column(
        "downtime_events", sa.Column("repair_part_changed", sa.String(length=200), nullable=True)
    )
    op.add_column("downtime_events", sa.Column("repaired_by_id", sa.UUID(), nullable=True))
    op.add_column(
        "downtime_events",
        sa.Column("repaired_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_downtime_repaired_by",
        "downtime_events",
        "users",
        ["repaired_by_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # --- Stores' standing answer ----------------------------------------------
    op.create_table(
        "material_stagings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_order_id", sa.UUID(), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("quantity_required", sa.Integer(), nullable=True),
        sa.Column("quantity_staged", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("confirmed_by_id", sa.UUID(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("plant_id", sa.UUID(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["plant_id"], ["plants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_order_id"], ["job_orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["confirmed_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    # The model declares its own __table_args__, so the plant-scoped mixin's
    # ix_<table>_plant is not created for it — only the column's own index.
    op.create_index("ix_material_stagings_plant_id", "material_stagings", ["plant_id"])
    op.create_index("ix_material_stagings_job_order_id", "material_stagings", ["job_order_id"])
    op.create_index(
        "uq_material_staging_job_date",
        "material_stagings",
        ["plant_id", "job_order_id", "business_date"],
        unique=True,
    )
    op.create_index(
        "ix_material_staging_date", "material_stagings", ["plant_id", "business_date", "status"]
    )

    op.execute("ALTER TABLE material_stagings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE material_stagings FORCE ROW LEVEL SECURITY")
    op.execute(STRICT_POLICY.format(table="material_stagings"))

    # Default privileges cover tables created by the owning role, but a database
    # restored from a dump loses them; grant explicitly as well.
    role = os.environ.get("APP_DB_ROLE", "fm_app")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON material_stagings TO {role};
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.drop_table("material_stagings")

    op.drop_constraint("fk_downtime_repaired_by", "downtime_events", type_="foreignkey")
    op.drop_column("downtime_events", "repaired_at")
    op.drop_column("downtime_events", "repaired_by_id")
    op.drop_column("downtime_events", "repair_part_changed")
    op.drop_column("downtime_events", "repair_note")

    op.drop_index("ix_escalations_route", table_name="escalations")
    op.drop_column("escalations", "route")
