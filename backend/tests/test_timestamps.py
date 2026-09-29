import unittest
from datetime import datetime, timedelta, timezone
from sqlalchemy.dialects import postgresql, sqlite
from core.timestamps import milliseconds
from core.status_provenance import naive_utc, provider_changed_at
from core.watch_dates import normalize_watch_datetime
from models import Base, TrackedEntry, User
from models.timestamps import MillisecondDateTime


class TimestampPrecisionTests(unittest.TestCase):
    def test_sql_default_preserves_postgres_truncation_and_works_on_sqlite(self):
        expression = User.__table__.c.created_at.server_default.arg
        options = {"literal_binds": True}
        self.assertEqual(str(expression.compile(dialect=postgresql.dialect(), compile_kwargs=options)), "date_trunc('milliseconds', now())")
        self.assertEqual(str(expression.compile(dialect=sqlite.dialect(), compile_kwargs=options)), "CURRENT_TIMESTAMP")

    def test_truncation_preserves_timezone_and_never_rounds_into_next_second(self):
        zone=timezone(timedelta(hours=2))
        for fraction in (931495,931995,999999,1,0):
            value=datetime(2026,9,29,12,0,59,fraction,tzinfo=zone)
            result=milliseconds(value)
            self.assertEqual(result.microsecond,(fraction//1000)*1000)
            self.assertEqual(result.second,59)
            self.assertEqual(result.tzinfo,zone)
        self.assertIsNone(milliseconds(None))

    def test_provider_formats_and_local_clock_compare_as_same_millisecond(self):
        local=datetime(2026,9,29,12,0,0,931995,tzinfo=timezone.utc)
        expected=datetime(2026,9,29,12,0,0,931000)
        self.assertEqual(naive_utc(local),expected)
        self.assertEqual(provider_changed_at({'modified_at':'2026-09-29T14:00:00.931995123+02:00'}),expected)
        self.assertEqual(provider_changed_at({'last_watched':int(local.timestamp()*1000)}),expected)
        self.assertEqual(normalize_watch_datetime(local),expected)

    def test_model_assignment_and_bound_updates_use_same_precision(self):
        value=datetime(2026,9,29,12,0,0,931995)
        entry=TrackedEntry(status_changed_at=value)
        self.assertEqual(entry.status_changed_at,datetime(2026,9,29,12,0,0,931000))
        entry.status_changed_at=None
        self.assertIsNone(entry.status_changed_at)
        column=TrackedEntry.__table__.c.status_changed_at
        self.assertEqual(column.type.process_bind_param(value,postgresql.dialect()),datetime(2026,9,29,12,0,0,931000))

    def test_all_mapped_timestamp_columns_declare_three_digit_postgres_precision(self):
        columns=[column for table in Base.metadata.tables.values() for column in table.columns
            if isinstance(column.type,MillisecondDateTime)]
        self.assertGreater(len(columns),50)
        for column in columns:
            with self.subTest(column=str(column)):
                self.assertIn('TIMESTAMP(3)',str(column.type.compile(dialect=postgresql.dialect())))
                if column.server_default is not None:
                    self.assertIn('date_trunc',str(column.server_default.arg))
