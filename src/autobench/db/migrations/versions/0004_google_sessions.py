"""Google Workspace identities and revocable browser sessions."""

from alembic import op
import sqlalchemy as sa
from autobench.db.types import UTCDateTime

revision = "0004_google_sessions"
down_revision = "0003_transitions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_user",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("google_sub", sa.String(255), nullable=False, unique=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("last_login_at", UTCDateTime(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
    )
    op.create_table(
        "web_session",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("workspace_user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires_at", UTCDateTime(), nullable=False),
        sa.Column("revoked_at", UTCDateTime()),
    )
    op.create_index("ix_web_session_user_id", "web_session", ["user_id"])
    op.create_index("ix_web_session_expires_at", "web_session", ["expires_at"])
    op.create_table(
        "oauth_flow",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column("browser_hash", sa.String(64), nullable=False),
        sa.Column("nonce", sa.String(128), nullable=False),
        sa.Column("verifier", sa.String(128), nullable=False),
        sa.Column("expires_at", UTCDateTime(), nullable=False),
    )
    op.create_index("ix_oauth_flow_expires_at", "oauth_flow", ["expires_at"])


def downgrade() -> None:
    op.drop_table("oauth_flow")
    op.drop_table("web_session")
    op.drop_table("workspace_user")
