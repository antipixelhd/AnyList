"""Persistent SSO identities, verified email changes and revocable sessions."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "mt033"
down_revision = "mt032"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("session_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("users", sa.Column("oidc_login_email", sa.String(255), nullable=True))
    # Keep the original invitation/login identity until its provider subject is linked.
    # Editing an email must not silently rebind an existing account to another Google user.
    op.execute("UPDATE users SET oidc_login_email = email")
    op.create_table(
        "oidc_identities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(500), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.UniqueConstraint("provider", "subject"),
        sa.UniqueConstraint("user_id", "provider"),
    )
    op.create_index("ix_oidc_identities_user_id", "oidc_identities", ["user_id"])
    op.create_table(
        "email_change_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("previous_email", sa.String(255), nullable=False),
        sa.Column("session_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(precision=3),
            nullable=False,
            server_default=sa.text("date_trunc('milliseconds', now())"),
        ),
    )


def downgrade():
    op.drop_table("email_change_tokens")
    op.drop_table("oidc_identities")
    op.drop_column("users", "oidc_login_email")
    op.drop_column("users", "session_version")
