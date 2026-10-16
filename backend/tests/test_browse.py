"""Discovery filter, paging and viewer-state regression tests."""

import os
import unittest
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("SECRET_KEY", "browse-tests-only")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from core.browse import (
    matches,
    metadata_fields,
    remote_page,
    remote_params,
    section_specs,
)
from db import get_db
from dependencies import get_current_user, get_optional_user
from models import Media, User, UserProfileData
from models.base import MediaType
from models.tracking import TrackedEntry
from routers.tracking import browse_ids, router

FILTERS = dict(
    genres=[],
    start=None,
    end=None,
    status="",
    provider=None,
    region="US",
    sort="popular",
)


class FilterTests(unittest.TestCase):
    def test_upcoming_titles_survive_vote_floor_in_any_and_upcoming(self):
        predicate={k:v for k,v in FILTERS.items() if k!='sort'}
        for kind,field in [('movie','release_date'),('series','first_air_date')]:
            item={field:'2099-12-01','vote_count':0,'status':'Returning Series'}
            self.assertTrue(matches(item,media_type=kind,min_votes=250,**predicate))
            self.assertTrue(matches(item,media_type=kind,min_votes=250,**{**predicate,'status':'upcoming'}))

    def test_airing_requires_started_season_and_future_episode_returning_excludes_it(self):
        predicate={k:v for k,v in FILTERS.items() if k!='sort'}
        active={'status':'Returning Series','first_air_date':'2020-01-01',
                'seasons':[{'season_number':1,'episode_count':8,'air_date':'2020-01-01'}],
                'next_episode_to_air':{'season_number':1,'episode_number':5,'air_date':'2099-01-01'}}
        for item,airing in [(active,True),({**active,'seasons':[{'season_number':1,'episode_count':8,'air_date':'2099-01-01'}]},False),
                            ({**active,'last_episode_to_air':active['next_episode_to_air']},False)]:
            for status,expected in [('airing',airing),('returning',not airing)]:
                self.assertEqual(matches(item,media_type='series',**{**predicate,'status':status}),expected)

    def test_tmdb_score_does_not_use_imdb_score(self):
        predicate = {k: v for k, v in FILTERS.items() if k != 'sort'}
        self.assertFalse(matches({'vote_average': 7.4, 'imdb_score': 9}, media_type='movie', min_score=7.5, **predicate))
        self.assertTrue(matches({'vote_average': 7.5, 'imdb_score': 1}, media_type='movie', min_score=7.5, **predicate))
        self.assertEqual(remote_params('movie', page=1, **{**FILTERS, 'sort': 'newest', 'min_score': 7.5})['vote_average.gte'], 7.5)

    def test_account_vote_floor_validation(self):
        from schemas import UserProfileUpdate
        from pydantic import ValidationError
        for value in (0, 250, 1000000):
            self.assertEqual(UserProfileUpdate(minimum_votes=value).model_dump(exclude_unset=True), {'minimum_votes': value})
        for value in (-1, 1000001, None, 2.5):
            with self.assertRaises(ValidationError):
                UserProfileUpdate(minimum_votes=value)

    def test_country_cinema_evidence_cannot_fall_back_to_other_countries(self):
        from core.cinema_releases import has_cinema_release
        # The Runner: digital in Germany/US, theatrical in other territories.
        raw={'release_dates':{'results':[
            {'iso_3166_1':'DE','release_dates':[{'type':4,'release_date':'2020-01-01'}]},
            {'iso_3166_1':'US','release_dates':[{'type':4,'release_date':'2020-01-01'}]},
            {'iso_3166_1':'RU','release_dates':[{'type':3,'release_date':'2020-01-01'}]},
            {'iso_3166_1':'GB','release_dates':[{'type':2,'release_date':'2099-01-01'}]}]}}
        stored=metadata_fields(raw)
        for evidence in (raw,stored):
            self.assertTrue(has_cinema_release(evidence))
            self.assertTrue(has_cinema_release(evidence,'RU'))
            for country in ('DE','US','GB','FR'):self.assertFalse(has_cinema_release(evidence,country))
        self.assertFalse(has_cinema_release({'theatrical_release_dates':['2020-01-01']},'DE'))
        filters={**FILTERS,'cinema':True,'cinema_country':'DE','sort':'newest','provider':8,'region':'US'}
        params=remote_params('movie',page=1,**filters)
        self.assertEqual(params['region'],'DE');self.assertEqual(params['watch_region'],'US')
        self.assertFalse(matches(raw,media_type='movie',**{k:v for k,v in filters.items() if k!='sort'}))

    def test_account_country_validation_preserves_partial_update(self):
        from schemas import UserProfileUpdate
        from pydantic import ValidationError
        self.assertEqual(UserProfileUpdate(country='de').model_dump(exclude_unset=True),{'country':'DE'})
        self.assertEqual(UserProfileUpdate(country=None).model_dump(exclude_unset=True),{'country':None})
        for value in ('XX','Germany','','123'):
            with self.assertRaises(ValidationError):UserProfileUpdate(country=value)

    def test_cinema_requires_past_theatrical_evidence_in_any_country(self):
        predicate={k:v for k,v in FILTERS.items() if k!='sort'}
        def item(types):
            return {'release_dates':{'results':[{'iso_3166_1':'GB','release_dates':types}]}}
        for kind in (2,3):
            self.assertTrue(matches(item([{'type':kind,'release_date':'2020-01-01T00:00:00Z'}]),media_type='movie',cinema=True,**predicate))
        for rows in ([{'type':4,'release_date':'2020-01-01'}],[{'type':1,'release_date':'2020-01-01'}],
                     [{'type':3,'release_date':'2099-01-01'}],[{'type':3,'release_date':'invalid'}],[]):
            self.assertFalse(matches(item(rows),media_type='movie',cinema=True,**predicate))
        self.assertTrue(matches(item([{'type':4,'release_date':'2020-01-01'},{'type':2,'release_date':'2021-01-01'}]),media_type='movie',cinema=True,**predicate))
        self.assertTrue(matches({},media_type='series',cinema=True,**predicate))
        params=remote_params('movie',page=2,cinema=True,**{**FILTERS,'sort':'newest'})
        self.assertEqual(params['with_release_type'],'2|3');self.assertNotIn('region',params)
        self.assertEqual(params['release_date.lte'],date.today().isoformat())

    def test_stored_cinema_dates_are_sorted_deduplicated_and_keep_future_evidence(self):
        self.assertNotIn('theatrical_release_dates',metadata_fields({'title':'Partial metadata'}))
        result=metadata_fields({'release_dates':{'results':[{'iso_3166_1':'GB','release_dates':[
            {'type':3,'release_date':'2099-01-01'},{'type':2,'release_date':'2020-01-01'},
            {'type':3,'release_date':'2020-01-01T00:00:00Z'},{'type':4,'release_date':'2019-01-01'}]}]}})
        self.assertEqual(result['theatrical_release_dates'],['2020-01-01','2099-01-01'])
    def test_browse_sections_include_only_the_four_discovery_categories(self):
        self.assertEqual(
            [title for title, _ in section_specs()],
            ["Trending now", "Newest", "All-time popular", "Highest rated"],
        )

    def test_vote_floor_has_an_inclusive_boundary_and_explicit_zero(self):
        predicate = {k: v for k, v in FILTERS.items() if k != "sort"}
        for votes, expected in [(249, False), (250, True), (251, True)]:
            self.assertEqual(matches({"vote_count": votes}, media_type="movie", min_votes=250, **predicate), expected)
        self.assertFalse(matches({}, media_type="movie", min_votes=250, **predicate))
        self.assertTrue(matches({}, media_type="movie", min_votes=0, **predicate))
        self.assertFalse(matches({"imdb_votes": 200, "vote_count": 10000}, media_type="movie", min_votes=250, **predicate))
        params = remote_params("movie", page=1, **{**FILTERS, "sort": "newest", "min_votes": 250})
        self.assertEqual(params["vote_count.gte"], 0)

    def test_newest_sorts_released_titles_without_a_season_boundary(self):
        for today in [date(2024, 2, 29), date(2026, 12, 31)]:
            values = section_specs(today)[1][1]
            self.assertEqual(values, {"sort": "newest", "end": today.isoformat()})
            for media_type, sort_field in [("movie", "primary_release_date"), ("series", "first_air_date")]:
                params = remote_params(media_type, page=1, **{**FILTERS, **values})
                self.assertEqual(params["sort_by"], f"{sort_field}.desc")
                self.assertEqual(params[f"{sort_field}.lte"], today.isoformat())

    def test_filters_use_and_for_multiple_genres(self):
        params = remote_params(
            "series",
            page=3,
            **{
                **FILTERS,
                "genres": [18, 35],
                "status": "airing",
                "provider": 8,
                "region": "DE",
                "start": "2020-01-01",
                "end": "2025-12-31",
                "sort": "newest",
            },
        )
        self.assertEqual(params["with_genres"], "18,35")
        self.assertEqual(params["first_air_date.gte"], "2020-01-01")
        self.assertEqual(params["with_status"], "0")
        self.assertEqual(params["with_watch_providers"], 8)
        self.assertEqual(params["watch_region"], "DE")
        self.assertEqual(params["sort_by"], "first_air_date.desc")

    def test_upcoming_movies_start_after_today(self):
        params = remote_params(
            "movie", page=1, **{**FILTERS, "status": "upcoming", "sort": "oldest"}
        )
        self.assertEqual(
            params["primary_release_date.gte"],
            (date.today() + timedelta(days=1)).isoformat(),
        )

    def test_detail_search_respects_every_filter_and_excludes_rentals(self):
        item = {
            "genres": [{"id": 18}, {"id": 35}],
            "first_air_date": "2021-02-03",
            "status": "Returning Series",
            "seasons": [{"season_number":1,"episode_count":8,"air_date":"2020-01-01"}],
            "next_episode_to_air": {"season_number":1,"episode_number":3,"air_date":"2099-01-01"},
            "watch/providers": {
                "results": {
                    "DE": {"flatrate": [{"provider_id": 8}]},
                    "US": {"rent": [{"provider_id": 8}]},
                }
            },
        }
        predicate = {k: v for k, v in FILTERS.items() if k != "sort"}
        predicate.update(
            genres=[18, 35],
            start="2020-01-01",
            end="2022-01-01",
            status="airing",
            provider=8,
            region="DE",
        )
        self.assertTrue(matches(item, media_type="series", **predicate))
        for changes in [
            {"genres": [18, 80]},
            {"region": "US"},
            {"start": "2022-01-01"},
            {"status": "finished"},
        ]:
            self.assertFalse(
                matches(item, media_type="series", **{**predicate, **changes})
            )

    def test_metadata_keeps_regions_for_local_browsing(self):
        result = metadata_fields(
            {
                "vote_count": 250,
                "watch/providers": {
                    "results": {"US": {"flatrate": [{"provider_id": 8}]}}
                },
            }
        )
        self.assertEqual(result["vote_count"], 250)
        self.assertEqual(
            result["watch_providers"]["US"]["flatrate"][0]["provider_id"], 8
        )

    def test_filter_ids_are_bounded_and_deduplicated(self):
        self.assertEqual(browse_ids("18,35,18"), [18, 35])
        from fastapi import HTTPException

        for value in [
            "0",
            "-1",
            "hello",
            "2147483647",
            ",".join(str(i) for i in range(1, 14)),
        ]:
            with self.assertRaises(HTTPException):
                browse_ids(value)


class RemotePagingTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_letter_search_bounds_large_catalogue_and_retains_a_continuation(self):
        feed=[{'id':i,'title':f'A movie {i}','imdb_votes':500} for i in range(1,10001)]
        with patch('core.tmdb.search_movies',AsyncMock(return_value={'total_pages':500,'results':[]})) as search, \
             patch('core.mdblist_discovery.ranked_rows',AsyncMock(return_value=(feed,False))):
            first,rows=await remote_page('a','movie','fixture',FILTERS,1,mdblist_key='fixture')
            self.assertLessEqual(len(rows),24)
            self.assertEqual(first['feed_next'],24)
            self.assertTrue(first['has_more'])
            next_data,next_rows=await remote_page('a','movie','fixture',FILTERS,501,mdblist_key='fixture',feed_offset=24)
        self.assertEqual(search.await_count,1)  # No typo expansion or provider fanout for one letter.
        self.assertEqual(len(next_rows),24)
        self.assertEqual(next_data['feed_next'],48)
        self.assertFalse({row['id'] for row in rows}&{row['id'] for row in next_rows})

    async def test_cached_catalogue_adds_predictive_matches_without_extra_searches(self):
        feed=[{'id':2,'title':'Digger','imdb_votes':500}, {'id':3,'title':'Unrelated','imdb_votes':500}]
        with patch('core.tmdb.search_movies',AsyncMock(return_value={'total_pages':1,'results':[{'id':1,'title':'The Dig','vote_count':500}]})) as search, \
             patch('core.mdblist_discovery.ranked_rows',AsyncMock(return_value=(feed,False))):
            _,rows=await remote_page('dig','movie','fixture',FILTERS,1,mdblist_key='fixture')
        self.assertEqual({row['title'] for row in rows},{'The Dig','Digger'})
        search.assert_awaited_once()

    async def test_hidden_translation_matches_do_not_flood_prefix_results(self):
        with patch('core.tmdb.search_movies',AsyncMock(return_value={'total_pages':1,'results':[
                {'id':1,'title':'Tenet','vote_count':500}, {'id':2,'title':'Nothing related','original_title':'Nothing related','vote_count':500}]})):
            _,rows=await remote_page('tene','movie','fixture',FILTERS,1)
        self.assertEqual([row['title'] for row in rows],['Tenet'])

    async def test_short_search_fetches_all_provider_pages_once_and_deduplicates(self):
        async def search(term, page=1, **kwargs):
            return {'total_pages':3,'results':[{'id':page,'title':f'Guardian {page}','vote_count':999},
                                              {'id':1,'title':'Guardian','vote_count':999}]}
        with patch('core.tmdb.search_movies',AsyncMock(side_effect=search)) as fetch:
            data,rows=await remote_page('guardian','movie','fixture',FILTERS,1)
        self.assertEqual([call.kwargs['page'] for call in fetch.await_args_list],[1,2,3])
        self.assertEqual({row['id'] for row in rows},{1,2,3})
        self.assertFalse(data['has_more']);self.assertEqual(data['page'],3)

    async def test_cinema_feed_keeps_rank_order_and_fills_pages_after_filtering(self):
        rows=[{'id':i,'type':'movie','imdb_id':f'tt{i}','imdb_score':8,'imdb_votes':1000} for i in range(1,61)]
        async def detail(identifier,api_key):
            return {'id':identifier,'release_dates':{'results':[{'iso_3166_1':'JP','release_dates':[
                {'type':3 if identifier%2==0 else 4,'release_date':'2020-01-01'}]}]}}
        with patch('core.mdblist_discovery.ranked_rows',AsyncMock(return_value=(rows,False))),patch(
            'core.tmdb.get_movie',AsyncMock(side_effect=detail)):
            first,items=await remote_page('','movie','fixture',{**FILTERS,'cinema':True},1,mdblist_key='fixture')
            second,more=await remote_page('','movie','fixture',{**FILTERS,'cinema':True},2,mdblist_key='fixture')
        self.assertEqual([r['id'] for r in items],list(range(2,49,2)))
        self.assertEqual([r['id'] for r in more],list(range(50,61,2)))
        self.assertTrue(first['has_more']);self.assertFalse(second['has_more'])

    async def test_cinema_search_excludes_future_digital_and_unknown_releases(self):
        async def detail(identifier,api_key):
            return {'id':identifier,'release_dates':{'results':[{'release_dates':[
                {'type':3 if identifier!=2 else 4,'release_date':'2099-01-01' if identifier==3 else '2020-01-01'}]}]}}
        with patch('core.tmdb.search_movies',AsyncMock(return_value={'results':[{'id':i,'title':'Title'} for i in range(1,4)],'total_pages':1})),patch(
            'core.tmdb.get_movie',AsyncMock(side_effect=detail)):
            _,items=await remote_page('title','movie','fixture',{**FILTERS,'cinema':True},1)
        self.assertEqual([r['id'] for r in items],[1])
    async def test_vote_floor_is_applied_before_feed_pagination_and_survives_detail_fetch(self):
        rows = [{"id": i, "imdb_id": f"tt{i}", "imdb_score": 8, "imdb_votes": 249 if i < 30 else 250} for i in range(60)]
        async def detail(identifier, api_key):
            return {"id": identifier, "vote_count": 1, "watch/providers": {"results": {"US": {"flatrate": [{"provider_id": 8}]}}}}
        with patch("core.mdblist_discovery.ranked_rows", AsyncMock(return_value=(rows, False))), patch("core.tmdb.get_movie", AsyncMock(side_effect=detail)):
            first, items = await remote_page("", "movie", "key", {**FILTERS, "provider": 8, "min_votes": 250}, 1, mdblist_key="mdb")
            second, more = await remote_page("", "movie", "key", {**FILTERS, "provider": 8, "min_votes": 250}, 2, mdblist_key="mdb")
        self.assertEqual([item["id"] for item in items], list(range(30, 54)))
        self.assertEqual([item["id"] for item in more], list(range(54, 60)))
        self.assertTrue(first["has_more"])
        self.assertFalse(second["has_more"])

    async def test_browse_search_excludes_low_vote_and_unknown_counts(self):
        with patch("core.tmdb.search_movies", AsyncMock(return_value={"results": [
            {"id": 1, "title":"Title", "vote_count": 249}, {"id": 2, "title":"Title", "vote_count": 250}, {"id": 3,"title":"Title"}], "total_pages": 2})):
            data, items = await remote_page("title", "movie", "key", {**FILTERS, "min_votes": 250}, 1)
        self.assertEqual([item["id"] for item in items], [2])
        self.assertEqual(data["total_pages"], 2)

    async def test_filtered_feed_is_paged_after_matching_and_excluding_anime(self):
        rows = [
            {"id": i, "type": "movie", "imdb_id": f"tt{i}", "imdb_score": 8,
             "imdb_votes": 100000, "genre_ids": [18] if i >= 30 else [35],
             "release_date": "2026-01-01", "original_language": "en"}
            for i in range(80)
        ]
        rows[30].update(genre_ids=[18, 16], original_language="ja")
        rows[31]["adult"] = True
        with patch("core.mdblist_discovery.ranked_rows", AsyncMock(return_value=(rows, False))) as feed:
            pages = [await remote_page("", "movie", None,
                     {**FILTERS, "genres": [18]}, page, mdblist_key="mdb", show_anime=False)
                     for page in (1, 2)]
        feed.assert_awaited_with("movie", "popular", "mdb")
        self.assertEqual([i["id"] for i in pages[0][1]], list(range(32, 56)))
        self.assertEqual([i["id"] for i in pages[1][1]], list(range(56, 80)))
        self.assertTrue(pages[0][0]["has_more"])
        self.assertFalse(pages[1][0]["has_more"])

    async def test_streaming_filter_scans_past_unmatched_first_page(self):
        rows = [{"id": i, "type": "movie", "imdb_id": f"tt{i}", "imdb_score": 8,
                 "imdb_votes": 100000, "genre_ids": []} for i in range(1, 60)]
        async def detail(identifier, api_key):
            return {"id": identifier, "watch/providers": {"results": {"US": {
                "flatrate": [{"provider_id": 8 if identifier > 24 else 9}]}}}}
        with patch("core.mdblist_discovery.ranked_rows", AsyncMock(return_value=(rows, False))), patch(
            "core.tmdb.get_movie", AsyncMock(side_effect=detail)
        ):
            first, items = await remote_page("", "movie", "tmdb", {**FILTERS, "provider": 8},
                                             1, mdblist_key="mdb")
            second, more = await remote_page("", "movie", "tmdb", {**FILTERS, "provider": 8},
                                             2, mdblist_key="mdb")
        self.assertEqual([i["id"] for i in items], list(range(25, 49)))
        self.assertEqual([i["id"] for i in more], list(range(49, 60)))
        self.assertTrue(first["has_more"])
        self.assertFalse(second["has_more"])

    async def test_filtered_search_drains_remaining_provider_pages_and_stops(
        self,
    ):
        with (
            patch(
                "core.tmdb.search_shows",
                AsyncMock(return_value={"results": [{"id": 1}], "total_pages": 3}),
            ) as search,
            patch(
                "core.tmdb.get_show",
                AsyncMock(return_value={"id": 1, "status": "Ended"}),
            ),
        ):
            data, items = await remote_page(
                "series", "series", "key", {**FILTERS, "status": "airing"}, 2
            )
        self.assertEqual([call.kwargs["page"] for call in search.await_args_list], [2,3])
        self.assertEqual(data["page"], 3)
        self.assertFalse(data["has_more"])
        self.assertEqual(data["total_pages"], 3)
        self.assertEqual(items, [])


class VoteThresholdApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        self.db.execute.return_value = result
        app = FastAPI()
        app.include_router(router, prefix="/tracking")
        async def session():
            yield self.db
        app.dependency_overrides[get_db] = session
        app.dependency_overrides[get_optional_user] = lambda: None
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        for target, value in [
            ("routers.tracking.catalog_access", None),
            ("routers.tracking.anime_is_visible", True),
            ("core.settings_store.get_user_tmdb_key", "key"),
            ("core.external_scores.effective_mdblist_key", None),
            ("core.tracking_editor.attach_editor_context", None),
        ]:
            replacement = patch(target, AsyncMock(return_value=value))
            replacement.start()
            self.addCleanup(replacement.stop)

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_browse_score_is_separate_from_account_vote_floor(self):
        for endpoint, target in [("browse", "core.browse.browse_page"), ("browse/sections", "core.browse.browse_sections")]:
            with patch(target, AsyncMock(return_value={"results": []})) as browse:
                for floor in [0, 250, 500]:
                    with patch("routers.tracking.result_filters", AsyncMock(return_value={"min_votes":floor,"min_runtime":30,"excluded_genres":[]})):
                        for score in [0, 7.5, 10]:
                            response = await self.client.get(f"/tracking/{endpoint}", params={"min_score": score, "min_votes": 0})
                            self.assertEqual(response.status_code, 200, response.text)
                            self.assertEqual(browse.await_args.kwargs["min_votes"], floor)
                            self.assertEqual(browse.await_args.kwargs["min_score"], score)
                for value in [-1, 10.1, "invalid"]:
                    response = await self.client.get(f"/tracking/{endpoint}", params={"min_score": value})
                    self.assertEqual(response.status_code, 422)

    async def test_quick_search_filters_remote_and_local_queries_on_backend_only(self):
        with patch("core.tmdb.search_movies", AsyncMock(return_value={"results": [
            {"id": 1, "title": "Low vote title", "vote_count": 249},
            {"id": 2, "title": "Qualified title", "vote_count": 250},
            {"id": 3, "title": "Unknown vote title"},
        ]})):
            response = await self.client.get("/tracking/catalog", params={"q": "title"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([row["tmdb_id"] for row in response.json()["results"]], [2])
        query = self.db.execute.await_args_list[0].args[0]
        self.assertIn(250, query.compile().params.values())
        self.assertNotIn("votes", response.text)


@unittest.skipUnless(
    os.getenv("TRACKING_TEST_DATABASE_URL"), "Requires disposable PostgreSQL database"
)
class BrowseApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_cursor_preserves_every_local_and_cached_match_in_bounded_pages(self):
        feed=[{'id':900000+i,'title':f'Browse Public {i}','imdb_votes':500} for i in range(70)]
        found=set();cursor=None
        with patch('core.settings_store.get_user_tmdb_key',AsyncMock(return_value='fixture')), \
             patch('core.external_scores.effective_mdblist_key',AsyncMock(return_value='fixture')), \
             patch('core.mdblist_discovery.ranked_rows',AsyncMock(return_value=(feed,False))), \
             patch('core.tmdb.search_movies',AsyncMock(return_value={'results':[],'total_pages':1})):
            for page in range(1,10):
                params={'q':'Browse','page':page,'source':'remote'}
                if cursor:params['cursor']=cursor
                response=await self.client.get('/tracking/browse',params=params)
                self.assertEqual(response.status_code,200,response.text)
                data=response.json();self.assertLessEqual(len(data['results']),48)
                found.update(row['tmdb_id'] for row in data['results'])
                if not data['has_more']:break
                cursor=data['cursor'];self.assertTrue(cursor)
            else:self.fail('Search pagination did not terminate')
        self.assertEqual(found,{movie.tmdb_id for movie in self.movies}|{row['id'] for row in feed})

    async def test_invalid_search_cursor_is_a_validation_error(self):
        response=await self.client.get('/tracking/browse',params={'q':'a','cursor':'not-a-cursor'})
        self.assertEqual(response.status_code,422)

    async def test_prefix_exact_typo_and_future_titles_use_indexed_local_search(self):
        titles=[Media(title=title,media_type=MediaType.movie,release_date='2099-01-01',tmdb_data={})
                for title in ('Dig','Digger','The Dig','Avengers: Doomsday')]
        self.db.add_all(titles);await self.db.commit()
        for term,expected in [('dig',['Dig','Digger','The Dig']),('doomsday',['Avengers: Doomsday'])]:
            response=await self.client.get('/tracking/browse',params={'q':term})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual([row['title'] for row in response.json()['results']],expected)
        response=await self.client.get('/tracking/browse',params={'q':'diggr'})
        self.assertIn('Digger',[row['title'] for row in response.json()['results']])

    async def test_airing_sql_and_detail_policy_agree(self):
        active=Media(title='Active release',media_type=MediaType.series,release_date='2020-01-01',status='Returning Series',
                     tmdb_data={'vote_count':1000,'status':'Returning Series',
                                'seasons':[{'season_number':1,'episode_count':8,'air_date':'2020-01-01'}],
                                'next_episode_to_air':{'season_number':1,'episode_number':5,'air_date':'2099-01-01'}})
        returning=Media(title='Between seasons',media_type=MediaType.series,release_date='2020-01-01',status='Returning Series',
                        tmdb_data={'vote_count':1000,'status':'Returning Series',
                                   'seasons':[{'season_number':2,'episode_count':8,'air_date':'2099-01-01'}]})
        self.db.add_all([active,returning]);await self.db.commit()
        for status,expected in [('airing',active.id),('returning',returning.id)]:
            response=await self.client.get('/tracking/browse',params={'status':status,'media_type':'series','sort':'title'})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual([row['id'] for row in response.json()['results']],[expected])

    async def test_runtime_and_categories_apply_before_paging_and_cached_shorts_cannot_reappear_remotely(self):
        from routers.profile import update_profile
        from schemas import UserProfileUpdate
        self.movies[0].runtime=2
        self.movies[1].runtime=30
        self.movies[2].tmdb_data={**self.movies[2].tmdb_data,'genres':[{'id':99,'name':'Documentary'}]}
        await self.db.flush()
        await update_profile(UserProfileUpdate(minimum_votes=0,excluded_categories=[99,10764]),current_user=self.owner,db=self.db)
        pages=[(await self.client.get('/tracking/browse',params={'page':page})).json()['results'] for page in (1,2,3)]
        ids=[row['id'] for page in pages for row in page]
        self.assertEqual(len(ids),53)
        self.assertNotIn(self.movies[0].id,ids);self.assertNotIn(self.movies[2].id,ids)
        self.assertIn(self.movies[1].id,ids)
        self.key_patch.stop()
        with patch('core.settings_store.get_user_tmdb_key',AsyncMock(return_value='fixture')), \
             patch('core.tmdb.search_movies',AsyncMock(return_value={'results':[{'id':self.movies[0].tmdb_id,'title':'Remote short','vote_count':500}],'total_pages':1})):
            for endpoint in ('catalog','browse'):
                response=await self.client.get(f'/tracking/{endpoint}',params={'q':'Unmatched query'})
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.json()['results'],[])
        await update_profile(UserProfileUpdate(minimum_movie_runtime=0,excluded_categories=[]),current_user=self.owner,db=self.db)
        self.assertEqual(len((await self.client.get('/tracking/catalog')).json()['results']),55)

    async def test_saved_vote_floor_applies_to_browse_and_catalog_with_tmdb_only_score(self):
        from routers.profile import update_profile
        from schemas import UserProfileUpdate
        await update_profile(UserProfileUpdate(minimum_votes=1000), current_user=self.owner, db=self.db)
        for endpoint in ('browse', 'catalog'):
            response = await self.client.get(f'/tracking/{endpoint}')
            self.assertEqual([row['id'] for row in response.json()['results']], [self.movies[0].id])
        await update_profile(UserProfileUpdate(minimum_votes=0), current_user=self.owner, db=self.db)
        response = await self.client.get('/tracking/browse', params={'min_score': 8.99})
        self.assertEqual({row['id'] for row in response.json()['results']}, {self.movies[0].id, self.movies[1].id})
        self.assertGreater(self.movies[0].tmdb_rating, self.movies[0].imdb_rating)

    async def asyncSetUp(self):
        self.engine = create_async_engine(os.environ["TRACKING_TEST_DATABASE_URL"])
        self.connection = await self.engine.connect()
        self.transaction = await self.connection.begin()
        self.db = AsyncSession(
            bind=self.connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        self.owner = User(
            username="browse-owner",
            email="browse-owner@example.test",
            api_key="browse-owner",
        )
        self.other = User(
            username="browse-other",
            email="browse-other@example.test",
            api_key="browse-other",
        )
        self.db.add_all([self.owner, self.other])
        await self.db.flush()
        self.movies = [
            Media(
                tmdb_id=400000 + i,
                title=f"Browse Film {i:02}",
                media_type=MediaType.movie,
                release_date="2021-02-03",
                imdb_id=f"tt{400000 + i}",
                imdb_rating=6 + i / 100,
                tmdb_rating=9 - i / 100,
                tmdb_data={
                    "genres": ["Drama", "Comedy"],
                    "popularity": i,
                    "imdb": {"votes": 1000 - i, "popularity_rank": i + 1},
                    "watch_providers": {
                        "US": {
                            "flatrate": [{"provider_id": 8, "provider_name": "Netflix"}]
                        }
                    },
                },
            )
            for i in range(55)
        ]
        self.db.add_all(self.movies)
        await self.db.flush()
        self.db.add_all(
            [
                TrackedEntry(
                    user_id=self.owner.id,
                    media_id=self.movies[0].id,
                    status="watching",
                    season_scores={},
                ),
                TrackedEntry(
                    user_id=self.other.id,
                    media_id=self.movies[1].id,
                    status="completed",
                    season_scores={},
                ),
            ]
        )
        await self.db.commit()
        self.viewer = self.owner
        app = FastAPI()
        app.include_router(router, prefix="/tracking")

        async def session():
            yield self.db

        app.dependency_overrides[get_db] = session
        app.dependency_overrides[get_optional_user] = lambda: self.viewer
        app.dependency_overrides[get_current_user] = lambda: self.viewer
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        self.key_patch = patch(
            "core.settings_store.get_user_tmdb_key", AsyncMock(return_value=None)
        )
        self.key_patch.start()
        self.chart_patch = patch("core.mdblist_discovery.cached_rows", return_value=[])
        self.chart_patch.start()
        self.mdblist_patch = patch("core.external_scores.effective_mdblist_key", AsyncMock(return_value=None))
        self.mdblist_patch.start()
        self.access_patch = patch("routers.tracking.catalog_access", AsyncMock())
        self.access_patch.start()
        self.anime_patch = patch(
            "routers.tracking.anime_is_visible", AsyncMock(return_value=True)
        )
        self.anime_patch.start()

    async def asyncTearDown(self):
        self.key_patch.stop()
        self.chart_patch.stop()
        self.mdblist_patch.stop()
        self.access_patch.stop()
        self.anime_patch.stop()
        await self.client.aclose()
        await self.db.close()
        await self.transaction.rollback()
        await self.connection.close()
        await self.engine.dispose()

    async def test_cinema_local_filter_intersects_genres_streaming_and_pagination(self):
        for index,movie in enumerate(self.movies):
            movie.tmdb_data={**movie.tmdb_data,'theatrical_release_dates':
                ['2099-01-01'] if index==0 else ['2020-01-01'] if index%2==0 else []}
        await self.db.flush()
        first=await self.client.get('/tracking/browse?cinema=1&genres=18&provider=8&sort=popular')
        self.assertEqual(first.status_code,200,first.text)
        self.assertEqual([r['id'] for r in first.json()['results']],[m.id for m in self.movies[2::2]][:24])
        self.assertTrue(first.json()['has_more'])
        second=await self.client.get('/tracking/browse?cinema=1&genres=18&provider=8&sort=popular&source=local&page=2')
        self.assertEqual([r['id'] for r in second.json()['results']],[m.id for m in self.movies[2::2]][24:])
        self.assertFalse(second.json()['has_more'])

    async def test_cinema_uses_account_country_in_grid_timeline_and_paging(self):
        profile=UserProfileData(user_id=self.viewer.id,country='DE')
        self.db.add(profile)
        for index,movie in enumerate(self.movies):
            movie.tmdb_data={**movie.tmdb_data,'theatrical_release_dates':['2020-01-01'],
                'theatrical_release_dates_by_country':{'US':['2020-01-01'],'DE':
                    ['2099-01-01'] if index==0 else ['2020-01-01'] if index%2==0 else []}}
        await self.db.flush()
        expected=[m.id for m in self.movies[2::2]]
        for sort in ('popular','newest'):
            found=[]
            for page in (1,2):
                response=await self.client.get('/tracking/browse',params={'cinema':1,'sort':sort,'page':page,'source':'local'})
                self.assertEqual(response.status_code,200,response.text)
                found.extend(row['id'] for row in response.json()['results'])
            self.assertEqual(set(found),set(expected))
        profile.country=None;await self.db.flush()
        response=await self.client.get('/tracking/browse?cinema=1&source=local&sort=popular')
        self.assertEqual(len(response.json()['results']),24)
        self.assertIn(self.movies[0].id,[row['id'] for row in response.json()['results']])

    async def test_account_country_save_preserves_other_profile_settings(self):
        from routers.profile import update_profile,account_countries
        from schemas import UserProfileUpdate
        profile=UserProfileData(user_id=self.viewer.id,country='US',bio='Preserved biography',profile_color='#ff5599',apply_site_wide=True)
        self.db.add(profile);await self.db.flush()
        updated=await update_profile(UserProfileUpdate(country='de'),current_user=self.viewer,db=self.db)
        self.assertEqual(updated.country,'DE');self.assertEqual(updated.bio,'Preserved biography')
        self.assertEqual(updated.profile_color,'#ff5599');self.assertTrue(updated.apply_site_wide)
        options=await account_countries(self.viewer)
        self.assertIn({'code':'DE','name':'Germany'},options)
        updated=await update_profile(UserProfileUpdate(country=None),current_user=self.viewer,db=self.db)
        self.assertIsNone(updated.country)

    async def test_category_previews_match_view_all_and_keep_viewer_state_private(self):
        self.movies[2].release_date = date.today().isoformat()
        self.movies[3].release_date = (date.today() + timedelta(days=365)).isoformat()
        self.movies[3].imdb_rating = 10
        await self.db.flush()
        response = await self.client.get("/tracking/browse/sections")
        self.assertEqual(response.status_code, 200, response.text)
        sections = response.json()["sections"]
        self.assertEqual(len(sections), 4)
        self.assertEqual(sections[0]["filters"], {"sort": "trending", "min_score": "0"})
        for section in sections:
            full = (
                await self.client.get("/tracking/browse", params=section["filters"])
            ).json()
            self.assertEqual(
                [row["id"] for row in section["results"]],
                [row["id"] for row in full["results"][:6]],
            )
            for row in section["results"]:
                if row["id"] == self.movies[0].id:
                    self.assertEqual(row["list_status"], "watching")
                if row["id"] == self.movies[1].id:
                    self.assertIsNone(row["list_status"])
        self.assertEqual(
            [row["id"] for row in sections[1]["results"]],
            [self.movies[i].id for i in (2, 0, 1, 4, 5, 6)],
        )
        self.assertNotIn(self.movies[3].id, [row["id"] for row in sections[1]["results"]])
        self.assertIn(self.movies[3].id, [row["id"] for row in sections[-1]["results"]])
        self.viewer = None
        anonymous = (await self.client.get("/tracking/browse/sections")).json()
        self.assertTrue(
            all(
                row["list_status"] is None
                for section in anonymous["sections"]
                for row in section["results"]
            )
        )

    async def test_highest_rated_uses_imdb_not_tmdb_and_excludes_missing_scores(self):
        self.movies[-1].imdb_rating = None
        await self.db.flush()
        response = await self.client.get("/tracking/browse", params={"sort": "score"})
        rows = response.json()["results"]
        self.assertEqual(rows[0]["id"], self.movies[-2].id)
        self.assertNotIn(self.movies[-1].id, [row["id"] for row in rows])
        self.assertNotEqual(rows[0]["id"], self.movies[0].id)

    async def test_popularity_and_trending_ignore_tmdb_order(self):
        for sort in ("popular", "trending"):
            rows = (
                await self.client.get("/tracking/browse", params={"sort": sort})
            ).json()["results"]
            self.assertEqual(rows[0]["id"], self.movies[0].id)
            self.assertEqual(rows[1]["id"], self.movies[1].id)

    async def test_cached_chart_supplies_missing_local_imdb_rating_and_rank(self):
        self.movies[0].imdb_rating = None
        self.movies[1].imdb_rating = None
        await self.db.flush()
        rows = [
            {
                "imdb_id": self.movies[1].imdb_id,
                "rating": 9.9,
                "votes": 10000,
                "rank": 1,
            },
            {
                "imdb_id": self.movies[0].imdb_id,
                "rating": None,
                "votes": 100,
                "rank": 2,
            },
        ]
        with patch("core.mdblist_discovery.cached_rows", return_value=rows):
            for sort in ("score", "trending", "popular"):
                result = (
                    await self.client.get("/tracking/browse", params={"sort": sort})
                ).json()
                self.assertEqual(result["results"][0]["id"], self.movies[1].id)
        with patch("core.mdblist_discovery.cached_rows", return_value=[rows[1]]):
            response = await self.client.get(
                "/tracking/browse", params={"sort": "score"}
            )
            self.assertEqual(response.status_code, 200, response.text)

    async def test_local_pagination_has_no_duplicates_and_ends(self):
        pages = [
            (
                await self.client.get(
                    "/tracking/browse", params={"source": "local", "page": page}
                )
            ).json()
            for page in (1, 2, 3)
        ]
        self.assertEqual([len(p["results"]) for p in pages], [24, 24, 7])
        self.assertEqual([p["has_more"] for p in pages], [True, True, False])
        self.assertEqual(len({r["id"] for p in pages for r in p["results"]}), 55)
        self.assertEqual(pages[0]["results"][0]["list_status"], "watching")
        self.assertIsNone(pages[0]["results"][1]["list_status"])

    async def test_broad_cached_feed_stays_below_database_parameter_limit(self):
        rows = [{"imdb_id": f"tt{i}", "rating": 8, "votes": i, "rank": i}
                for i in range(1, 10001)]
        rows.append({"imdb_id": self.movies[0].imdb_id, "rating": 10,
                     "votes": 1000000, "rank": 1})
        with patch("core.mdblist_discovery.cached_rows", return_value=rows):
            response = await self.client.get("/tracking/browse", params={"sort": "score"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["results"][0]["id"], self.movies[0].id)

    async def test_mdblist_browse_works_without_tmdb_and_retains_private_viewer_state(self):
        item = {"id": self.movies[0].tmdb_id, "type": "movie", "title": "MDBList title",
                "imdb_id": self.movies[0].imdb_id, "imdb_score": 9.3, "imdb_votes": 100000,
                "genre_ids": [18], "release_date": "2021-02-03"}
        with patch("core.external_scores.effective_mdblist_key", AsyncMock(return_value="mdb")), patch(
            "core.mdblist_discovery.ranked_rows", AsyncMock(return_value=([item], False))
        ), patch("core.tmdb.find_by_external_id", AsyncMock()) as resolve:
            response = await self.client.get("/tracking/browse", params={"sort": "score"})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["source"], "remote")
        self.assertFalse(result["has_more"])
        self.assertEqual(result["results"][0]["imdb_score"], 9.3)
        self.assertEqual(result["results"][0]["list_status"], "watching")
        resolve.assert_not_awaited()

    async def test_combined_local_filters_and_regional_streaming(self):
        filters = {
            "genres": "18,35",
            "start": "2020-01-01",
            "end": "2022-01-01",
            "status": "released",
            "provider": 8,
            "region": "US",
        }
        response = await self.client.get("/tracking/browse", params=filters)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["results"]), 24)
        response = await self.client.get(
            "/tracking/browse", params={**filters, "region": "DE"}
        )
        self.assertEqual(response.json()["results"], [])
        response = await self.client.get(
            "/tracking/browse", params={**filters, "genres": "18,80"}
        )
        self.assertEqual(response.json()["results"], [])

    async def test_fuzzy_search_and_literal_wildcards(self):
        response = await self.client.get(
            "/tracking/browse", params={"q": "Browze Flm 00"}
        )
        self.assertTrue(response.json()["results"])
        response = await self.client.get("/tracking/browse", params={"q": "%"})
        self.assertEqual(response.json()["results"], [])

    async def test_local_facets_retain_stored_services(self):
        response = await self.client.get("/tracking/browse/facets")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn({"id": 8, "name": "Netflix"}, response.json()["providers"])

    async def test_editor_snapshots_include_complete_viewer_fields_without_provider_calls(self):
        from sqlalchemy import select
        entry = (await self.db.execute(select(TrackedEntry).where(
            TrackedEntry.user_id == self.owner.id,
            TrackedEntry.media_id == self.movies[0].id,
        ))).scalar_one()
        entry.notes = "Viewer notes"
        entry.favorite = True
        entry.manual_score = 8.5
        entry.start_date = date(2021, 2, 3)
        entry.rewatch_count = 2
        self.movies[0].backdrop_path = "/background.jpg"
        await self.db.commit()
        with patch("core.external_scores.refresh_external_scores", AsyncMock()) as refresh:
            response = await self.client.get(f"/tracking/editor/{self.movies[0].id}")
        self.assertEqual(response.status_code, 200, response.text)
        refresh.assert_not_awaited()
        row = response.json()
        self.assertEqual(row["entry"]["notes"], "Viewer notes")
        self.assertEqual(row["entry"]["manual_score"], 8.5)
        self.assertTrue(row["entry"]["favorite"])
        self.assertEqual(row["entry"]["start_date"], "2021-02-03")
        self.assertEqual(row["entry"]["rewatch_count"], 2)
        self.assertEqual(row["backdrop"], "/background.jpg")
        self.assertFalse(row["editor_library"]["available"])
        self.viewer = self.other
        response = await self.client.get(f"/tracking/editor/{self.movies[0].id}")
        self.assertIsNone(response.json()["entry"])
        self.assertNotIn("Viewer notes", response.text)

    async def test_browse_and_search_seed_private_editor_snapshots_for_known_and_new_titles(self):
        response = await self.client.get("/tracking/browse", params={"sort": "oldest"})
        rows = response.json()["results"]
        own = next(row for row in rows if row["id"] == self.movies[0].id)
        other = next(row for row in rows if row["id"] == self.movies[1].id)
        self.assertEqual(own["entry"]["status"], "watching")
        self.assertIn("notes", own["entry"])
        self.assertIsNone(other["entry"])
        with (
            patch("core.settings_store.get_user_tmdb_key", AsyncMock(return_value="key")),
            patch("core.tmdb.search_movies", AsyncMock(return_value={"results":[
                {"id":400000,"title":"Known remote","vote_count":250},
                {"id":999999,"title":"New remote","vote_count":250},
            ]})),
        ):
            response = await self.client.get("/tracking/catalog", params={"q":"Remote"})
        rows = response.json()["results"]
        known = next(row for row in rows if row["tmdb_id"] == 400000)
        new = next(row for row in rows if row["tmdb_id"] == 999999)
        self.assertEqual(known["id"], self.movies[0].id)
        self.assertEqual(known["entry"]["status"], "watching")
        self.assertIsNone(new["id"])
        self.assertIsNone(new["entry"])
        self.viewer = None
        response = await self.client.get("/tracking/browse")
        self.assertTrue(all("entry" not in row for row in response.json()["results"]))

    async def test_editor_library_snapshot_respects_private_intents_and_avoids_credentials(self):
        from models import MediaServerConnection
        from models.streaming_library import StreamingLibraryIntent
        self.db.add_all([
            MediaServerConnection(user_id=self.owner.id, type="stremio", name="Stream",
                                  url="https://example.test", token="private-token"),
            StreamingLibraryIntent(user_id=self.owner.id, media_id=self.movies[0].id, desired=True),
            StreamingLibraryIntent(user_id=self.other.id, media_id=self.movies[1].id, desired=True),
        ])
        await self.db.commit()
        response = await self.client.get("/tracking/browse", params={"sort":"oldest"})
        rows = response.json()["results"]
        own = next(row for row in rows if row["id"] == self.movies[0].id)
        other = next(row for row in rows if row["id"] == self.movies[1].id)
        self.assertEqual(own["editor_library"], {"available":True, "desired":True})
        self.assertEqual(other["editor_library"], {"available":True, "desired":False})
        self.assertNotIn("private-token", response.text)
        self.viewer = self.other
        response = await self.client.get(f"/tracking/editor/{self.movies[1].id}")
        self.assertEqual(response.json()["editor_library"], {"available":False, "desired":False})

    async def test_remote_cards_resolve_local_ids_and_viewer_state(self):
        with (
            patch("core.external_scores.effective_mdblist_key", AsyncMock(return_value="mdb")),
            patch(
                "core.settings_store.get_user_tmdb_key", AsyncMock(return_value="key")
            ),
            patch(
                "core.browse.remote_page",
                AsyncMock(
                    return_value=(
                        {"total_pages": 2},
                        [
                            {
                                "id": 400000,
                                "title": "Remote name",
                                "genre_ids": [18],
                                "release_date": "2021-02-03",
                            }
                        ],
                    )
                ),
            ),
        ):
            response = await self.client.get("/tracking/browse")
        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()["results"][0]
        self.assertEqual(row["id"], self.movies[0].id)
        self.assertEqual(row["list_status"], "watching")
        self.assertEqual(row['release_date'], '2021-02-03')
        self.assertTrue(response.json()["has_more"])

    async def test_remote_failure_does_not_change_sources_mid_scroll(self):
        with (
            patch("core.external_scores.effective_mdblist_key", AsyncMock(return_value="mdb")),
            patch(
                "core.settings_store.get_user_tmdb_key", AsyncMock(return_value="key")
            ),
            patch("core.browse.remote_page", AsyncMock(side_effect=OSError("offline"))),
        ):
            first = await self.client.get("/tracking/browse")
            second = await self.client.get(
                "/tracking/browse", params={"page": 2, "source": "remote"}
            )
        self.assertEqual(first.json()["source"], "local")
        self.assertEqual(second.status_code, 502)

    async def test_invalid_filters_are_rejected(self):
        for filters in [
            {"genres": "123456"},
            {"start": "2022-01-01", "end": "2020-01-01"},
            {"status": "airing"},
            {"page": 0},
            {"region": "../US"},
        ]:
            response = await self.client.get("/tracking/browse", params=filters)
            self.assertEqual(response.status_code, 422, response.text)
