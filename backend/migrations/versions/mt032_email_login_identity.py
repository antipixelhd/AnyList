"""Normalize account emails and enforce unique email login identities.

Revision ID: mt032
Revises: mt031
"""
from alembic import op
import sqlalchemy as sa

revision = "mt032"
down_revision = "mt031"
branch_labels = None
depends_on = None


def upgrade():
    # Prevent a concurrent signup between the collision audit and index creation.
    op.execute("LOCK TABLE users IN ACCESS EXCLUSIVE MODE")
    collisions = op.get_bind().execute(sa.text(
        "SELECT array_agg(id ORDER BY id) FROM users "
        "GROUP BY lower(trim(email)) HAVING count(*) > 1"
    )).scalars().all()
    if collisions:
        raise RuntimeError(
            "Email login migration requires resolving duplicate account emails first. "
            f"Conflicting user ID groups: {collisions}"
        )
    op.execute("UPDATE users SET email = lower(trim(email)) WHERE email <> lower(trim(email))")
    op.create_index("uq_users_email_normalized", "users", [sa.text("lower(trim(email))")], unique=True)


def downgrade():
    op.drop_index("uq_users_email_normalized", table_name="users")
