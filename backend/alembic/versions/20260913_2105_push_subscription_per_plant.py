"""push subscriptions are unique per plant, not globally

Revision ID: 3a7c9e1f5b22
Revises: 8d2f4b6a1c90
Create Date: 2026-09-13 21:05:00

A tablet signed in to one plant and later to another subscribes the same
browser endpoint twice. Row-level security hides the first plant's row from the
second, so a global unique constraint turned that second subscription into an
error nobody could clear. One subscription per plant per endpoint instead.
"""
from __future__ import annotations

from alembic import op

revision: str = "3a7c9e1f5b22"
down_revision: str | None = "8d2f4b6a1c90"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_constraint("uq_push_subscriptions_endpoint", "push_subscriptions", type_="unique")
    op.create_unique_constraint(
        "uq_push_subscriptions_plant_endpoint", "push_subscriptions", ["plant_id", "endpoint"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_push_subscriptions_plant_endpoint", "push_subscriptions", type_="unique")
    op.create_unique_constraint("uq_push_subscriptions_endpoint", "push_subscriptions", ["endpoint"])
