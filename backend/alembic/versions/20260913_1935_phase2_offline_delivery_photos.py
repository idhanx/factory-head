"""phase 2: offline sync, delivery outbox, push, photos, action ownership

Revision ID: 8d2f4b6a1c90
Revises: 5c1e7a9d2b44
Create Date: 2026-09-13 19:35:00

* Capture rows get a ``client_ref`` so an offline tablet that retries a sync
  can never record the same stop or count twice.
* ``sync_holds`` keeps offline entries that arrived after their shift locked,
  for the owner to apply or discard.
* ``notifications`` is the delivery outbox for WhatsApp, SMS and push;
  ``push_subscriptions`` the browsers that asked to be woken.
* ``attachments`` stores photos of fixes.
* Actions carry the loss they are measured against (downtime or quality),
  the credit kept from before a reopen, and the day they were addressed in
  the morning meeting. Plants set how many days an auto-created Action gets.
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op

revision: str = "8d2f4b6a1c90"
down_revision: str | None = "5c1e7a9d2b44"
branch_labels: str | None = None
depends_on: str | None = None

NEW_TABLES = ("sync_holds", "notifications", "push_subscriptions", "attachments")
CAPTURE_TABLES = ("downtime_events", "production_counts", "quality_records")

STRICT_POLICY = """
    CREATE POLICY plant_isolation ON {table}
    USING (plant_id::text = current_setting('app.current_plant', true))
    WITH CHECK (plant_id::text = current_setting('app.current_plant', true))
"""


def _timestamps():
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def upgrade() -> None:
    # --- idempotent capture ----------------------------------------------------
    for table in CAPTURE_TABLES:
        op.add_column(table, sa.Column("client_ref", sa.String(length=64), nullable=True))
        op.execute(
            f"CREATE UNIQUE INDEX uq_{table}_client_ref ON {table} (plant_id, client_ref) "
            f"WHERE client_ref IS NOT NULL"
        )

    # --- plants and actions ------------------------------------------------------
    op.add_column(
        "plants",
        sa.Column("action_due_days", sa.Integer(), server_default="3", nullable=False),
    )
    op.add_column(
        "actions",
        sa.Column("loss_source", sa.String(length=12), server_default="downtime", nullable=False),
    )
    op.add_column("actions", sa.Column("part_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_actions_part", "actions", "parts", ["part_id"], ["id"], ondelete="SET NULL"
    )
    op.add_column(
        "actions",
        sa.Column("recovered_profit_prior", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column("actions", sa.Column("meeting_status_on", sa.Date(), nullable=True))

    # --- new tables ----------------------------------------------------------------
    op.create_table(
        "sync_holds",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("client_ref", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("shift_session_id", sa.UUID(), nullable=True),
        sa.Column("business_date", sa.Date(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("submitted_by_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolved_by_id", sa.UUID(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entity_id", sa.UUID(), nullable=True),
        sa.Column("plant_id", sa.UUID(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["plant_id"], ["plants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["shift_session_id"], ["shift_sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["submitted_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolved_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plant_id", "client_ref", name="uq_sync_holds_client_ref"),
    )
    op.create_index("ix_sync_holds_status", "sync_holds", ["plant_id", "status"])
    op.create_index("ix_sync_holds_plant_id", "sync_holds", ["plant_id"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("channel", sa.String(length=12), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("recipient_name", sa.String(length=200), nullable=True),
        sa.Column("recipient_number", sa.String(length=20), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("url", sa.String(length=300), nullable=True),
        sa.Column("template", sa.String(length=64), nullable=True),
        sa.Column("variables", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("subject_type", sa.String(length=32), nullable=True),
        sa.Column("subject_id", sa.UUID(), nullable=True),
        sa.Column("dedupe_key", sa.String(length=160), nullable=True),
        sa.Column("attempts", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("provider_message_id", sa.String(length=128), nullable=True),
        sa.Column("provider_response", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("plant_id", sa.UUID(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["plant_id"], ["plants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plant_id", "dedupe_key", name="uq_notifications_dedupe"),
    )
    op.create_index("ix_notifications_due", "notifications", ["plant_id", "status", "next_attempt_at"])
    op.create_index("ix_notifications_plant_id", "notifications", ["plant_id"])

    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.String(length=200), nullable=False),
        sa.Column("auth", sa.String(length=100), nullable=False),
        sa.Column("user_agent", sa.String(length=300), nullable=True),
        sa.Column("failures", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("plant_id", sa.UUID(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["plant_id"], ["plants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("endpoint", name="uq_push_subscriptions_endpoint"),
    )
    op.create_index("ix_push_subscriptions_plant_id", "push_subscriptions", ["plant_id"])

    op.create_table(
        "attachments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("entity_type", sa.String(length=24), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("filename", sa.String(length=200), nullable=True),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("width", sa.BigInteger(), nullable=True),
        sa.Column("height", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=300), nullable=False),
        sa.Column("uploaded_by_id", sa.UUID(), nullable=True),
        sa.Column("plant_id", sa.UUID(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["plant_id"], ["plants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_attachments_entity", "attachments", ["plant_id", "entity_type", "entity_id"])
    op.create_index("ix_attachments_plant_id", "attachments", ["plant_id"])

    for table in NEW_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(STRICT_POLICY.format(table=table))

    # Default privileges already cover tables created by the owning role, but a
    # database restored from a dump loses them; grant explicitly as well.
    role = os.environ.get("APP_DB_ROLE", "fm_app")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON sync_holds, notifications,
                    push_subscriptions, attachments TO {role};
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    for table in reversed(NEW_TABLES):
        op.drop_table(table)
    op.drop_column("actions", "meeting_status_on")
    op.drop_column("actions", "recovered_profit_prior")
    op.drop_constraint("fk_actions_part", "actions", type_="foreignkey")
    op.drop_column("actions", "part_id")
    op.drop_column("actions", "loss_source")
    op.drop_column("plants", "action_due_days")
    for table in CAPTURE_TABLES:
        op.execute(f"DROP INDEX IF EXISTS uq_{table}_client_ref")
        op.drop_column(table, "client_ref")
