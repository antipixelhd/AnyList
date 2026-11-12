"""Reuse the credit scope column for sparse person-connection provenance."""
from alembic import op

revision='mt065'
down_revision='mt064'
branch_labels=depends_on=None


def upgrade():
    op.execute("""UPDATE catalogue_credits SET scope='person' WHERE provider='imdb'
        AND scope='title' AND attributes->>'imdb_person_evidence'='true'""")


def downgrade():
    op.execute("""UPDATE catalogue_credits SET scope='title' WHERE provider='imdb'
        AND scope='person' AND attributes->>'imdb_person_evidence'='true'""")
