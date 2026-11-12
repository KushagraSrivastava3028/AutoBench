"""Durable SSE resume, committed with trial results.
Revision ID: 0003_transitions
Revises: 0002_console
"""

from alembic import op
import sqlalchemy as sa
from autobench.db.types import AutoBigInt, JSONBType, UTCDateTime

revision = "0003_transitions"
down_revision = "0002_console"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_transition",
        sa.Column("id", AutoBigInt, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("payload", JSONBType, nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
    )
    op.create_index("ix_run_transition_run_id_id", "run_transition", ["run_id", "id"])


def downgrade() -> None:
    op.drop_table("run_transition")
