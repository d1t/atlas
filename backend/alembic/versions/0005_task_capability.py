"""strategy task capability

Revision ID: 0005_task_capability
Revises: 0004_gmail_oauth
Create Date: 2026-07-29 14:10:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_task_capability"
down_revision: str | None = "0004_gmail_oauth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable: existing tasks predate agent execution and are human-owned, so a
    # null capability correctly means "nothing for the executor to run".
    op.add_column(
        "strategy_tasks", sa.Column("capability", sa.String(length=32), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("strategy_tasks", "capability")
