"""Apply the application's timestamp policy to every mapped DateTime column."""
from datetime import datetime
from sqlalchemy import DateTime, ColumnDefault, DefaultClause, event, func
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.sql.elements import ClauseElement
from sqlalchemy.types import TypeDecorator
from core.timestamps import milliseconds


class MillisecondDateTime(TypeDecorator):
    impl = DateTime
    cache_ok = True

    def __init__(self, timezone=False):
        self.timezone = timezone
        super().__init__(timezone=timezone)

    def load_dialect_impl(self, dialect):
        if dialect.name == 'postgresql':
            return dialect.type_descriptor(TIMESTAMP(timezone=self.timezone, precision=3))
        return dialect.type_descriptor(DateTime(timezone=self.timezone))

    def process_bind_param(self, value, dialect):
        return milliseconds(value)

    def process_result_value(self, value, dialect):
        return milliseconds(value)


def _normalize_assignment(target, value, oldvalue, initiator):
    return milliseconds(value) if isinstance(value, datetime) else value


def configure_millisecond_timestamps(base):
    for mapper in base.registry.mappers:
        for column in mapper.columns:
            if not isinstance(column.type, DateTime):
                continue
            column.type = MillisecondDateTime(timezone=column.type.timezone)
            # Preserve SQL defaults/update behavior, with truncation occurring
            # before PostgreSQL's timestamp(3) conversion could round upward.
            if column.server_default is not None and hasattr(column.server_default, 'arg'):
                column.server_default = DefaultClause(func.date_trunc('milliseconds', column.server_default.arg))
            for name in ('default', 'onupdate'):
                default = getattr(column, name)
                if default is not None and isinstance(default.arg, ClauseElement):
                    setattr(column, name, ColumnDefault(func.date_trunc('milliseconds', default.arg)))
            event.listen(getattr(mapper.class_, column.key), 'set', _normalize_assignment, retval=True)
