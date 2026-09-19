"""kaizen plugin edition — the operator journey, config, points and patterns

Revision ID: 9f3c2d6e8a41
Revises: 6b4d8f2c7a15
Create Date: 2026-09-17 11:00:00

Kaizen AI moves from a three-field suggestion box to the Plugin Edition: voice
and photo capture, the AI interview, the Core History Check, the hard/soft
classification layer, a configurable approval workflow, a points ledger and
cross-source pattern detection.

Three of these changes are to CORE tables, not Kaizen ones, and each is here
because Core is the owner of that fact and Kaizen only reads it:

* ``cost_rates.unit_rate`` — rates that are per *unit* rather than per hour:
  electricity per kWh, compressed air per m³, scrap sold back per kg. Plugin
  Spec 7.3 requires that a rate an approver supplies is written to *Core's* cost
  master, which means Core's cost master has to be able to hold it.
* ``parts.weight_kg`` — scrap is sold back. Ignoring that recovery overstates
  every scrap saving by 20-30%, and the recovery is ``weight × ₹/kg``.
* ``plants.working_days_per_year`` — every annualised figure in the product
  multiplies by this. 300 is the Indian auto-ancillary norm.

The existing ``kaizen_ideas`` rows are migrated, not dropped: ``credited_name``
backfills from the submitter, and the old single ``estimated_annual_saving``
becomes ``hard_saving_paise``. That is the honest reading of the old column —
the old scorer credited a share of observed recoverable loss, with no soft
column to put anything in.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "9f3c2d6e8a41"
down_revision: str | None = "6b4d8f2c7a15"
branch_labels: str | None = None
depends_on: str | None = None

STRICT_POLICY = """
    CREATE POLICY plant_isolation ON {table}
    USING (plant_id::text = current_setting('app.current_plant', true))
    WITH CHECK (plant_id::text = current_setting('app.current_plant', true))
"""

NEW_TABLES = (
    "kaizen_settings",
    "kaizen_category_codes",
    "kaizen_points",
    "kaizen_patterns",
)


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
    # --- Core master data Kaizen reads --------------------------------------
    op.add_column(
        "cost_rates",
        sa.Column("unit_rate", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column("parts", sa.Column("weight_kg", sa.Numeric(10, 3), nullable=True))
    op.add_column(
        "plants",
        sa.Column(
            "working_days_per_year", sa.Integer(), server_default="300", nullable=False
        ),
    )

    # --- kaizen_ideas: the whole journey ------------------------------------
    op.alter_column("kaizen_ideas", "submitted_by_operator_id", new_column_name="credited_operator_id")

    columns = [
        sa.Column("track", sa.String(length=8), server_default="full", nullable=False),
        sa.Column("voice_transcript", sa.Text(), nullable=True),
        sa.Column("voice_language", sa.String(length=8), nullable=True),
        sa.Column("submitted_by_user_id", sa.UUID(), nullable=True),
        sa.Column("credited_name", sa.String(length=200), nullable=True),
        sa.Column("board", sa.String(length=16), server_default="shopfloor", nullable=False),
        sa.Column("department", sa.String(length=64), nullable=True),
        sa.Column("submitted_on", sa.Date(), nullable=True),
        sa.Column("job_order_id", sa.UUID(), nullable=True),
        sa.Column("part_id", sa.UUID(), nullable=True),
        sa.Column("defect_code_id", sa.UUID(), nullable=True),
        sa.Column("context_confirmed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("unknown_station_note", sa.String(length=300), nullable=True),
        sa.Column("category", sa.String(length=20), nullable=True),
        sa.Column("saving_type", sa.String(length=24), nullable=True),
        sa.Column("vision_findings", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("vision_ran", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("interview", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("history", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("similar", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("challenge", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("forecast", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("fingerprint", postgresql.ARRAY(sa.String(length=48)), nullable=True),
        sa.Column("embedding", postgresql.ARRAY(sa.Numeric(12, 8)), nullable=True),
        sa.Column("core_value_paise", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("hard_saving_paise", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("soft_saving_paise", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("saving_basis", sa.Text(), nullable=True),
        sa.Column("intangible_stars", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("investment_paise", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "investment_is_estimate", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
        sa.Column("needs_shutdown", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("priority_score", sa.Integer(), server_default="0", nullable=False),
        sa.Column("priority_verdict", sa.String(length=8), nullable=True),
        sa.Column("score_components", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("provisional", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("provisional_note", sa.Text(), nullable=True),
        sa.Column("safety_flagged", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("approval_step", sa.Integer(), server_default="0", nullable=False),
        sa.Column("approval_route", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("approval_trail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("duplicate_action_id", sa.UUID(), nullable=True),
        sa.Column("duplicate_of_id", sa.UUID(), nullable=True),
        sa.Column("is_recurrence", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("promoted_from_id", sa.UUID(), nullable=True),
        sa.Column("implemented_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_saving_paise", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("points_awarded", sa.Integer(), server_default="0", nullable=False),
        sa.Column("signals", postgresql.ARRAY(sa.String(length=32)), nullable=True),
    ]
    for column in columns:
        op.add_column("kaizen_ideas", column)

    # Existing rows keep their credit and their number. The old scorer credited
    # a share of observed recoverable loss with nowhere to put a soft figure,
    # so that value is a hard claim under the new split.
    op.execute(
        """
        UPDATE kaizen_ideas
        SET credited_name = submitted_by_name,
            submitted_on = created_at::date,
            hard_saving_paise = COALESCE(estimated_annual_saving, 0),
            track = 'full'
        WHERE credited_name IS NULL
        """
    )
    op.alter_column("kaizen_ideas", "credited_name", nullable=False)
    op.drop_column("kaizen_ideas", "estimated_annual_saving")

    for name, target, column, ondelete in (
        ("fk_kaizen_user", "users", "submitted_by_user_id", "SET NULL"),
        ("fk_kaizen_job_order", "job_orders", "job_order_id", "SET NULL"),
        ("fk_kaizen_part", "parts", "part_id", "SET NULL"),
        ("fk_kaizen_defect_code", "defect_codes", "defect_code_id", "SET NULL"),
        ("fk_kaizen_duplicate_action", "actions", "duplicate_action_id", "SET NULL"),
        ("fk_kaizen_duplicate_of", "kaizen_ideas", "duplicate_of_id", "SET NULL"),
        ("fk_kaizen_promoted_from", "kaizen_ideas", "promoted_from_id", "SET NULL"),
    ):
        op.create_foreign_key(
            name, "kaizen_ideas", target, [column], ["id"], ondelete=ondelete
        )

    op.create_index(op.f("ix_kaizen_ideas_part_id"), "kaizen_ideas", ["part_id"])
    op.create_index(
        op.f("ix_kaizen_ideas_credited_operator_id"), "kaizen_ideas", ["credited_operator_id"]
    )
    op.create_index(
        op.f("ix_kaizen_ideas_reason_code_id"), "kaizen_ideas", ["reason_code_id"]
    )
    op.create_index(op.f("ix_kaizen_ideas_submitted_on"), "kaizen_ideas", ["submitted_on"])
    op.create_index(
        "ix_kaizen_priority", "kaizen_ideas", ["plant_id", "status", "priority_score"]
    )
    op.create_index("ix_kaizen_month", "kaizen_ideas", ["plant_id", "submitted_on"])
    op.create_unique_constraint(
        "uq_kaizen_ideas_reference", "kaizen_ideas", ["plant_id", "reference"]
    )

    # --- K4 and K5: the whole configuration surface -------------------------
    op.create_table(
        "kaizen_settings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("template", sa.String(length=12), server_default="simple", nullable=False),
        sa.Column("steps", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "auto_approve_below_paise", sa.BigInteger(), server_default="500000", nullable=False
        ),
        sa.Column("auto_approve_min_score", sa.Integer(), server_default="80", nullable=False),
        sa.Column(
            "safety_always_to_owner", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
        sa.Column(
            "finance_above_paise", sa.BigInteger(), server_default="2500000", nullable=False
        ),
        sa.Column("history_window_days", sa.Integer(), server_default="180", nullable=False),
        sa.Column("score_weights", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("pattern_min_items", sa.Integer(), server_default="5", nullable=False),
        sa.Column(
            "pattern_min_value_paise", sa.BigInteger(), server_default="5000000", nullable=False
        ),
        sa.Column(
            "patterns_include_quick", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
        sa.Column(
            "promote_above_paise", sa.BigInteger(), server_default="2000000", nullable=False
        ),
        sa.Column("monthly_minimum", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "quick_win_investment_paise",
            sa.BigInteger(),
            server_default="1000000",
            nullable=False,
        ),
        sa.Column("enabled_categories", postgresql.ARRAY(sa.String(length=20)), nullable=True),
        sa.Column("plant_id", sa.UUID(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["plant_id"], ["plants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plant_id", name="uq_kaizen_settings_plant"),
    )
    op.create_index(op.f("ix_kaizen_settings_plant_id"), "kaizen_settings", ["plant_id"])

    op.create_table(
        "kaizen_category_codes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("reason_code_id", sa.UUID(), nullable=True),
        sa.Column("defect_code_id", sa.UUID(), nullable=True),
        sa.Column("plant_id", sa.UUID(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["defect_code_id"], ["defect_codes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plant_id"], ["plants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reason_code_id"], ["reason_codes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plant_id",
            "category",
            "reason_code_id",
            "defect_code_id",
            name="uq_kaizen_category_codes",
        ),
    )
    op.create_index(
        op.f("ix_kaizen_category_codes_plant_id"), "kaizen_category_codes", ["plant_id"]
    )
    op.create_index(
        "ix_kaizen_category_codes_cat", "kaizen_category_codes", ["plant_id", "category"]
    )

    # --- The points ledger ---------------------------------------------------
    # A ledger rather than a counter because the biggest award — verification —
    # lands weeks after implementation and must be reversible if a fix stops
    # holding. A single integer column could not be audited or undone.
    op.create_table(
        "kaizen_points",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("idea_id", sa.UUID(), nullable=False),
        sa.Column("operator_id", sa.UUID(), nullable=True),
        sa.Column("person_name", sa.String(length=200), nullable=False),
        sa.Column("board", sa.String(length=16), server_default="shopfloor", nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("awarded_on", sa.Date(), nullable=False),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("plant_id", sa.UUID(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["idea_id"], ["kaizen_ideas.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["operator_id"], ["operators.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["plant_id"], ["plants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # One award per Kaizen per outcome — makes every award idempotent.
        sa.UniqueConstraint("plant_id", "idea_id", "kind", name="uq_kaizen_points_once"),
    )
    op.create_index(op.f("ix_kaizen_points_plant_id"), "kaizen_points", ["plant_id"])
    op.create_index(op.f("ix_kaizen_points_idea_id"), "kaizen_points", ["idea_id"])
    op.create_index(
        "ix_kaizen_points_person", "kaizen_points", ["plant_id", "board", "person_name"]
    )

    # --- Detected patterns ---------------------------------------------------
    op.create_table(
        "kaizen_patterns",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("label", sa.String(length=300), nullable=False),
        sa.Column("signature", postgresql.ARRAY(sa.String(length=48)), nullable=True),
        sa.Column("idea_ids", postgresql.ARRAY(sa.UUID()), nullable=True),
        sa.Column("downtime_event_ids", postgresql.ARRAY(sa.UUID()), nullable=True),
        sa.Column("departments", postgresql.ARRAY(sa.String(length=64)), nullable=True),
        sa.Column("kaizen_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("event_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("verified_cost_paise", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("lost_minutes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("action_id", sa.UUID(), nullable=True),
        sa.Column("dismissed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("detected_on", sa.Date(), nullable=False),
        sa.Column("plant_id", sa.UUID(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["action_id"], ["actions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["plant_id"], ["plants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_kaizen_patterns_plant_id"), "kaizen_patterns", ["plant_id"])
    op.create_index("ix_kaizen_patterns_open", "kaizen_patterns", ["plant_id", "dismissed"])

    # --- Tenancy: the same strict, forced policy every other table carries ---
    #
    # Phase 1 already set ALTER DEFAULT PRIVILEGES for the application role, so
    # these grants are belt-and-braces. They are guarded because a GRANT to a
    # role that does not exist aborts the whole migration, and the role name is
    # environment-dependent: a database restored somewhere else, or migrated
    # before Phase 1 created the role, would otherwise fail here for a
    # permission it does not need yet.
    role = _app_role()
    for table in NEW_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS plant_isolation ON {table}")
        op.execute(STRICT_POLICY.format(table=table))
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                    GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {role};
                END IF;
            END $$;
            """
        )


def downgrade() -> None:
    for table in reversed(NEW_TABLES):
        op.drop_table(table)

    op.drop_constraint("uq_kaizen_ideas_reference", "kaizen_ideas", type_="unique")
    op.drop_index("ix_kaizen_month", table_name="kaizen_ideas")
    op.drop_index("ix_kaizen_priority", table_name="kaizen_ideas")
    op.drop_index(op.f("ix_kaizen_ideas_submitted_on"), table_name="kaizen_ideas")
    op.drop_index(op.f("ix_kaizen_ideas_reason_code_id"), table_name="kaizen_ideas")
    op.drop_index(op.f("ix_kaizen_ideas_credited_operator_id"), table_name="kaizen_ideas")
    op.drop_index(op.f("ix_kaizen_ideas_part_id"), table_name="kaizen_ideas")

    op.add_column(
        "kaizen_ideas",
        sa.Column("estimated_annual_saving", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.execute("UPDATE kaizen_ideas SET estimated_annual_saving = hard_saving_paise")

    for name in (
        "fk_kaizen_promoted_from",
        "fk_kaizen_duplicate_of",
        "fk_kaizen_duplicate_action",
        "fk_kaizen_defect_code",
        "fk_kaizen_part",
        "fk_kaizen_job_order",
        "fk_kaizen_user",
    ):
        op.drop_constraint(name, "kaizen_ideas", type_="foreignkey")

    for column in (
        "signals",
        "points_awarded",
        "verified_at",
        "verified_saving_paise",
        "implemented_at",
        "promoted_from_id",
        "is_recurrence",
        "duplicate_of_id",
        "duplicate_action_id",
        "approval_trail",
        "approval_route",
        "approval_step",
        "safety_flagged",
        "provisional_note",
        "provisional",
        "score_components",
        "priority_verdict",
        "priority_score",
        "needs_shutdown",
        "investment_is_estimate",
        "investment_paise",
        "intangible_stars",
        "saving_basis",
        "soft_saving_paise",
        "hard_saving_paise",
        "core_value_paise",
        "embedding",
        "fingerprint",
        "forecast",
        "challenge",
        "similar",
        "history",
        "interview",
        "vision_ran",
        "vision_findings",
        "saving_type",
        "category",
        "unknown_station_note",
        "context_confirmed",
        "defect_code_id",
        "part_id",
        "job_order_id",
        "submitted_on",
        "department",
        "board",
        "credited_name",
        "submitted_by_user_id",
        "voice_language",
        "voice_transcript",
        "track",
    ):
        op.drop_column("kaizen_ideas", column)

    op.alter_column(
        "kaizen_ideas", "credited_operator_id", new_column_name="submitted_by_operator_id"
    )

    op.drop_column("plants", "working_days_per_year")
    op.drop_column("parts", "weight_kg")
    op.drop_column("cost_rates", "unit_rate")


def _app_role() -> str:
    import os

    return os.environ.get("APP_DB_ROLE", "fm_app")
