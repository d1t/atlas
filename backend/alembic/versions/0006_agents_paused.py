"""strategy agent pause switch

Revision ID: 0006_agents_paused
Revises: 0005_task_capability
Create Date: 2026-07-29 16:20:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_agents_paused"
down_revision: str | None = "0005_task_capability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing strategies keep their agents running: the pause is a brake a person
    # applies, not a state the upgrade should silently put anyone into.
    op.add_column(
        "strategies",
        sa.Column(
            "agents_paused", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column("strategies", sa.Column("agents_paused_reason", sa.Text()))


def downgrade() -> None:
    op.drop_column("strategies", "agents_paused_reason")
    op.drop_column("strategies", "agents_paused")
