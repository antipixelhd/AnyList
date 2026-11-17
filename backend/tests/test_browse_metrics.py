"""Indexed public ranking retains valid ordering and avoids catalogue scans."""
import os
import unittest
from unittest.mock import patch

from sqlalchemy import Float, func, insert, or_, select, text, update

import test_tracking_api as fixture
from core.browse import local_query
from core.browse_metrics import ImdbVotes, ImdbRank
from models import Media
from models.base import MediaType
from core.result_categories import default_excluded_categories


def query(sort='popular'):
    return local_query('movie', term='', genres=[], start=None, end=None, status=None,
        provider=None, region='US', sort=sort, show_anime=False, min_votes=250, min_popularity=25,
        min_runtime=30, excluded_genres=default_excluded_categories()).limit(25)


def previous_filters(query, data, media_type, *, min_runtime=0, excluded_genres=(), runtime=None):
    from core.browse import GENRES
    if media_type == 'movie' and min_runtime:
        duration = func.nullif(func.coalesce(runtime, data['runtime'].astext.cast(Float)), 0)
        query = query.where(or_(duration.is_(None), duration >= min_runtime))
    for native in excluded_genres:
        excluded = or_(data['genre_ids'].contains([native]), data['genres'].contains([{'id': native}]),
                       data['genres'].contains([GENRES[native]]), data['genres'].contains([{'name': GENRES[native]}]))
        query = query.where(~func.coalesce(excluded, False))
    return query


async def explain_plan(db, engine, statement):
    # Raw driver execution keeps EXPLAIN's one JSON column out of the SELECT's
    # cached ORM result metadata. Retain normal PostgreSQL JSON/enum processors.
    compiled = statement.compile(engine.sync_engine)
    parameters = tuple(compiled._bind_processors[key](compiled.params[key]) if key in compiled._bind_processors
                       else compiled.params[key] for key in compiled.positiontup)
    connection = await db.connection()
    return (await connection.exec_driver_sql('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+str(compiled), parameters)).scalar()


@unittest.skipUnless(os.getenv('TRACKING_TEST_DATABASE_URL'), 'Requires disposable PostgreSQL')
class BrowseMetricsTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.TrackingApiTests.asyncSetUp
    asyncTearDown = fixture.TrackingApiTests.asyncTearDown

    async def test_valid_numeric_ranking_and_ties_match_the_previous_query(self):
        for number, value in enumerate([None, 0, -1, 1, 25, ' 40 ', '2e3', 25]):
            self.db.add(Media(title=f'Rank {number}', media_type=MediaType.movie, runtime=90,
                tmdb_data={'vote_count':500, 'popularity':30, 'imdb':{'votes':value, 'popularity_rank':value}}))
        await self.db.flush()
        for sort in ('popular', 'trending'):
            current = list(await self.db.scalars(query(sort).with_only_columns(Media.id)))
            with patch('core.browse_metrics.ImdbVotes', lambda data:data['imdb']['votes'].astext.cast(Float)), \
                 patch('core.browse_metrics.ImdbRank', lambda data:data['imdb']['popularity_rank'].astext.cast(Float)):
                previous = list(await self.db.scalars(query(sort).with_only_columns(Media.id)))
            self.assertEqual(current, previous)

    async def test_malformed_and_out_of_range_legacy_values_cannot_break_indexed_writes(self):
        bad = [None, '', 'unknown', 'NaN', 'Infinity', '1e999', '1e-999', {}, [], True]
        ids = list(await self.db.scalars(insert(Media).returning(Media.id), [dict(title=f'Bad rank {i}',
            media_type=MediaType.movie, tmdb_data={'imdb':{'votes':value, 'popularity_rank':value}})
            for i,value in enumerate(bad)]))
        values = (await self.db.execute(select(ImdbVotes(Media.tmdb_data), ImdbRank(Media.tmdb_data))
            .where(Media.id.in_(ids)))).all()
        self.assertTrue(all(vote in (None, 0) and rank in (None, 0) for vote,rank in values))
        await self.db.execute(update(Media).where(Media.id == ids[0]).values(tmdb_data={'imdb':{'votes':250}}))
        self.assertEqual(await self.db.scalar(select(ImdbVotes(Media.tmdb_data)).where(Media.id == ids[0])), 250)

    async def test_bounded_browse_uses_the_ranking_index_with_and_without_positive_evidence(self):
        await self.db.execute(insert(Media), [dict(title=f'Indexed {i}', media_type=MediaType.movie, runtime=90,
            tmdb_data={'vote_count':500, 'popularity':30, 'overview':'x'*1500,
                **({'imdb':{'votes':6000-i, 'popularity_rank':i+1}} if i%2 else {})}) for i in range(6000)])
        await self.db.execute(text('ANALYZE media'))
        for sort, index in (('popular', 'idx_media_imdb_votes'), ('trending', 'idx_media_imdb_rank')):
            plan = await explain_plan(self.db, self.engine, query(sort))
            import json
            self.assertIn(index, json.dumps(plan))
            self.assertNotIn('Bitmap Heap Scan', json.dumps(plan))
            self.assertNotIn('"Node Type": "Sort"', json.dumps(plan))
            self.assertEqual(len(list(await self.db.scalars(query(sort)))), 25)

    async def test_combined_exclusions_preserve_missing_metadata_and_all_legacy_genre_shapes(self):
        from core.catalog_search import apply_result_filters
        values = [None, {}, {'genres':None}, {'genres':[]}, {'genre_ids':[]},
            {'genres':['Drama']}, {'genre_ids':[18]}, {'genres':[{'id':18}]},
            {'genre_ids':[99]}, {'genres':['Documentary']}, {'genres':[{'id':99}]},
            {'genres':[{'name':'Documentary'}]}, {'genres':[{'name':'Music'},'Drama']},
            {'genre_ids':[18,10770]}, {'genres':['Talk','Music']}, {'genres':[{'name':'Drama'}]},
            {'genres':None, 'genre_ids':[99]}, {'genres':['Drama'], 'runtime':12},
            {'genres':['Drama'], 'runtime':0}]
        ids = list(await self.db.scalars(insert(Media).returning(Media.id), [dict(title=f'Policy {i}',
            media_type=MediaType.movie, tmdb_data=value) for i,value in enumerate(values)]))
        for media_type in ('movie','series'):
            for exclusions in ([], [99], [10402,10770], default_excluded_categories()):
                for runtime in (0,30):
                    statement = select(Media.id).where(Media.id.in_(ids)).order_by(Media.id)
                    options = dict(min_runtime=runtime, excluded_genres=exclusions, runtime=Media.runtime)
                    actual = list(await self.db.scalars(apply_result_filters(statement, Media.tmdb_data, media_type, **options)))
                    expected = list(await self.db.scalars(previous_filters(statement, Media.tmdb_data, media_type, **options)))
                    self.assertEqual(actual, expected, (media_type,exclusions,runtime))

    async def test_fixture_ranking_upgrade_preserves_json_null_cold_stubs_and_existing_evidence(self):
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location('scale_fixture', Path(__file__).resolve().parents[2]/'scripts/stress/fixture.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        values = [None, {'title':'warm'}, {'imdb':{'votes':3}}, ['unexpected']]
        ids = list(await self.db.scalars(insert(Media).returning(Media.id), [dict(title=f'Fixture {i}',
            media_type=MediaType.movie, tmdb_id=8000000+i, tmdb_data=value) for i,value in enumerate(values)]))
        await module._populate_rankings(self.db)
        result = list(await self.db.scalars(select(Media.tmdb_data).where(Media.id.in_(ids)).order_by(Media.id)))
        self.assertIsNone(result[0])
        self.assertEqual(result[1]['title'], 'warm')
        self.assertGreater(result[1]['imdb']['votes'], 0)
        self.assertEqual(result[2:], values[2:])
