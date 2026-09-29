"""Add stable account identity and identity fencing to streaming connections.

Revision ID: mt031
Revises: mt030
"""
from urllib.parse import urlsplit, urlunsplit

from alembic import op
import sqlalchemy as sa


revision = "mt031"
down_revision = "mt030"
branch_labels = None
depends_on = None


def _canonical_nuvio_url(value: str) -> str:
    """Match the application's URL normalization without importing app code."""
    parsed = urlsplit(value.strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return value.strip().rstrip("/")
    host = parsed.hostname.encode("idna").decode("ascii").lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        return value.strip().rstrip("/")
    scheme = parsed.scheme.lower()
    if port is not None and not (scheme == "https" and port == 443) and not (scheme == "http" and port == 80):
        host = f"{host}:{port}"
    # Preserve any userinfo exactly; URLs should not usually contain it, but
    # canonicalization must not silently change connection credentials.
    userinfo = parsed.netloc.rsplit("@", 1)[0] + "@" if "@" in parsed.netloc else ""
    path = parsed.path.rstrip("/")
    return urlunsplit((scheme, userinfo + host, path, parsed.query, parsed.fragment))


def upgrade() -> None:
    op.add_column(
        "media_server_connections",
        sa.Column("provider_account_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "media_server_connections",
        sa.Column("identity_version", sa.Integer(), server_default="0", nullable=False),
    )

    bind = op.get_bind()
    bind.execute(sa.text(
        "UPDATE media_server_connections "
        "SET provider_account_id = server_user_id "
        "WHERE type = 'stremio' AND server_user_id IS NOT NULL"
    ))
    rows = bind.execute(sa.text(
        "SELECT id, url FROM media_server_connections WHERE type = 'nuvio'"
    )).all()
    for row in rows:
        normalized = _canonical_nuvio_url(row.url)
        if normalized != row.url:
            bind.execute(
                sa.text("UPDATE media_server_connections SET url = :url WHERE id = :id"),
                {"url": normalized, "id": row.id},
            )

    op.create_index(
        "uq_msc_user_stremio_account",
        "media_server_connections",
        ["user_id", "provider_account_id"],
        unique=True,
        postgresql_where=sa.text("type = 'stremio' AND provider_account_id IS NOT NULL"),
        sqlite_where=sa.text("type = 'stremio' AND provider_account_id IS NOT NULL"),
    )
    op.create_index(
        "uq_msc_user_nuvio_identity",
        "media_server_connections",
        ["user_id", "url", "provider_account_id", "server_user_id"],
        unique=True,
        postgresql_where=sa.text(
            "type = 'nuvio' AND provider_account_id IS NOT NULL AND server_user_id IS NOT NULL"
        ),
        sqlite_where=sa.text(
            "type = 'nuvio' AND provider_account_id IS NOT NULL AND server_user_id IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_msc_user_nuvio_identity", table_name="media_server_connections")
    op.drop_index("uq_msc_user_stremio_account", table_name="media_server_connections")
    op.drop_column("media_server_connections", "identity_version")
    op.drop_column("media_server_connections", "provider_account_id")
