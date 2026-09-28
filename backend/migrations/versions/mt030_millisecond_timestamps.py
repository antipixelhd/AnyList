"""Store every timestamp at millisecond precision (existing dates are truncated).

Downgrading widens the types but cannot restore discarded sub-milliseconds.
"""
from alembic import op
import sqlalchemy as sa

revision = 'mt030'
down_revision = 'mt029'
branch_labels = None
depends_on = None


def _columns():
    return op.get_bind().execute(sa.text("""
        SELECT table_schema, table_name, column_name, data_type, column_default
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND data_type IN ('timestamp without time zone', 'timestamp with time zone')
        ORDER BY table_name, ordinal_position
    """)).mappings().all()


def _alter(precision):
    quote = op.get_bind().dialect.identifier_preparer.quote
    changes = {}
    for column in _columns():
        table = f"{quote(column['table_schema'])}.{quote(column['table_name'])}"
        name = quote(column['column_name'])
        zone = 'with time zone' if column['data_type'] == 'timestamp with time zone' else 'without time zone'
        clause = f"ALTER COLUMN {name} TYPE timestamp({precision}) {zone}"
        if precision == 3:
            clause += f" USING date_trunc('milliseconds', {name})"
        changes.setdefault(table, []).append(clause)
        if precision == 3 and column['column_default'] is not None:
            changes[table].append(f"ALTER COLUMN {name} SET DEFAULT date_trunc('milliseconds', {column['column_default']})")
    for table, clauses in changes.items():
        op.execute(sa.text(f"ALTER TABLE {table} " + ', '.join(clauses)))


def upgrade():
    _alter(3)


def downgrade():
    _alter(6)
