"""Keep PostgreSQL ordering constraints and portable SQLite fixture uniqueness."""
import sqlite3
import unittest

from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateTable

from models import GameFavorite


class FavoriteSchemaTests(unittest.TestCase):
    def test_position_constraint_retains_postgres_deferral_and_sqlite_uniqueness(self):
        pg = str(CreateTable(GameFavorite.__table__).compile(dialect=postgresql.dialect()))
        local = str(CreateTable(GameFavorite.__table__).compile(dialect=sqlite.dialect()))
        self.assertEqual(pg.count('CONSTRAINT uq_game_favorite_position'), 1)
        self.assertIn('UNIQUE (user_id, position) DEFERRABLE INITIALLY DEFERRED', pg)
        self.assertEqual(local.count('CONSTRAINT uq_game_favorite_position'), 1)
        self.assertNotIn('DEFERRABLE', local)
        with sqlite3.connect(':memory:') as db:
            db.execute(local)
            db.execute('INSERT INTO game_favorites VALUES (1, 10, 0)')
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('INSERT INTO game_favorites VALUES (1, 20, 0)')
