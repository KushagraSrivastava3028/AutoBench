"""Persist console budget settings.

Revision ID: 0002_console
Revises: 0001_baseline
"""

from alembic import op
import sqlalchemy as sa
from autobench.db.types import JSONBType

revision = "0002_console"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "console_setting",
        sa.Column("key", sa.String(80), primary_key=True),
        sa.Column("value", JSONBType, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("console_setting")
