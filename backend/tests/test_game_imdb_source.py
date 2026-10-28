"""IMDb query contract and response semantics, without importing third-party code."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault('SECRET_KEY','imdb-source-test-only')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')

from core.game_imdb_source import IMDbGameSource, TITLE_CREDITS, PERSON_CREDITS, page
from core.catalogue_providers import ProviderError


def title(native='tt123',kind='videoGame'):
    return {'id':native,'titleText':{'text':'Game'},'titleType':{'id':kind},'releaseYear':{'year':2020}}


def connection(rows,total=None,more=False,cursor=None):
    return {'total':len(rows) if total is None else total,'pageInfo':{'hasNextPage':more,'endCursor':cursor},
            'edges':[{'node':row} for row in rows]}


class IMDbSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_optional_denied_release_date_never_becomes_identity_evidence(self):
        raw={**title(),'releaseDate':{'year':2020,'month':1,'day':1}}
        self.http.request.return_value={'data':{'title':raw},'extensions':{'entitlements':{'fields':{'Title.releaseDate':'DENY'}}}}
        result=await self.source.title('tt123')
        self.assertIsNone(result['releaseDate'])

    async def test_person_suggestions_keep_namesake_ids_and_ignore_unrelated_fuzzy_hits(self):
        self.http.request.return_value = {'d': [
            {'id': 'nm1', 'l': 'Cissy Jones', 's': 'Actress, Firewatch'},
            {'id': 'nm2', 'l': 'Cissy Jones'},
            {'id': 'nm3', 'l': 'Sissy Grover'},
            {'id': 'nm1', 'l': 'Cissy Jones'},
        ]}
        result = await self.source.people_candidates('Cissy Jones')
        self.assertEqual([row['id'] for row in result], ['nm1', 'nm2'])
        self.assertTrue(all('s' not in row for row in result))
        self.assertEqual(self.http.request.await_args.args[2],
            'https://v3.sg.media-imdb.com/suggestion/names/x/Cissy%20Jones.json')

    async def test_person_suggestion_images_are_optional_and_restricted_to_imdb_cdn(self):
        self.http.request.return_value = {'d': [
            {'id': 'nm1', 'l': 'Actor One', 'i': {'imageUrl': 'https://evil.example/photo.jpg'}},
            {'id': 'nm2', 'l': 'Actor Two', 'i': {'imageUrl': 'https://m.media-amazon.com/images/M/photo._V1_.jpg'}},
        ]}
        result = await self.source.people_candidates('Actor')
        self.assertIsNone(result[0]['image'])
        self.assertEqual(result[1]['image'], 'https://m.media-amazon.com/images/M/photo._V1_.jpg')

    async def test_malformed_person_suggestions_are_not_verified_empty_or_title_bindings(self):
        for body in ({}, {'d': None}, {'d': [{'id': 'tt1', 'l': 'Name'}]}, {'d': [{'id': 'nm1'}]}):
            self.http.request.return_value = body
            with self.assertRaises(ProviderError):
                await self.source.people_candidates('Name')
    def setUp(self):
        self.http=SimpleNamespace(request=AsyncMock())
        self.source=IMDbGameSource(self.http)

    async def test_expanded_connection_uses_id_cursor_and_preserves_null_attributes(self):
        cast={'name':{'id':'nm123','nameText':{'text':'Actor'}},'category':{'id':'actor','text':'Cast'},
              'characters':[{'name':'Hero'}],'attributes':None}
        self.http.request.return_value={'data':{'title':{**title(),'credits':connection([cast],200,True,'next')}}}
        result=await self.source.credits('tt123','previous')
        self.assertIsNone(result['rows'][0]['attributes'])
        self.assertEqual((result['total'],result['has_next'],result['cursor']),(200,True,'next'))
        args,kwargs=self.http.request.call_args
        self.assertEqual(args,('imdb','POST','https://caching.graphql.imdb.com/'))
        self.assertEqual(kwargs['json']['variables'],{'id':'tt123','after':'previous'})
        self.assertIn('$after: ID',kwargs['json']['query'])
        self.assertIn('first: 100',kwargs['json']['query'])
        self.assertIn('... on Crew',kwargs['json']['query'])
        self.assertNotIn('principalCredits',TITLE_CREDITS)
        self.assertEqual(set(kwargs['headers']),{'Content-Type','User-Agent','Origin','Referer'})
        self.assertFalse(kwargs['cache'])

    async def test_person_connection_is_not_known_for_and_does_not_request_images(self):
        raw={'id':'nm123','nameText':{'text':'Actor'},'bio':None,
             'credits':connection([{'title':title(),'category':{'id':'actor'}}])}
        self.http.request.return_value={'data':{'name':raw}}
        result=await self.source.credits('nm123',person=True)
        self.assertIsNone(result['header']['bio'])
        self.assertEqual(result['rows'][0]['title']['titleType']['id'],'videoGame')
        self.assertIn('first: 50',PERSON_CREDITS)
        self.assertNotIn('knownFor',PERSON_CREDITS)
        self.assertNotIn('primaryImage',PERSON_CREDITS)
        self.assertTrue(self.http.request.call_args.kwargs['json']['variables']['profile'])
        await self.source.credits('nm123','next',person=True)
        self.assertFalse(self.http.request.call_args.kwargs['json']['variables']['profile'])

    async def test_wrong_title_kind_and_identity_are_rejected_before_binding(self):
        for raw in (title(kind='movie'),title(native='tt456'),None):
            self.http.request.return_value={'data':{'title':raw}}
            with self.assertRaisesRegex(ProviderError,'identity_conflict'):await self.source.title('tt123')

    async def test_portraits_are_one_small_batch_with_explicit_null_images(self):
        image={'url':'https://m.media-amazon.com/images/M/person._V1_.jpg','width':500,'height':700}
        self.http.request.return_value={'data':{'p0':{'id':'nm1','primaryImage':image},'p1':{'id':'nm2','primaryImage':None}}}
        result=await self.source.portraits(['nm1','nm2'])
        self.assertEqual(result['nm1']['image'],image['url']);self.assertIsNone(result['nm2']['image'])
        self.http.request.assert_awaited_once()
        query=self.http.request.call_args.kwargs['json']
        self.assertEqual(query['variables'],{'id0':'nm1','id1':'nm2'})
        self.assertNotIn('credits',query['query'])
        self.assertNotIn('knownFor',query['query'])

    async def test_portrait_denial_missing_field_identity_and_unsafe_urls_are_not_verified_empty(self):
        for body in [
            {'data':{'p0':{'id':'nm1','primaryImage':None}},'extensions':{'entitlements':{'fields':{'Name.primaryImage':'DENY'}}}},
            {'data':{'p0':{'id':'nm2','primaryImage':None}}},
            {'data':{'p0':{'id':'nm1'}}},
            {'data':{'p0':{'id':'nm1','primaryImage':{'url':'https://evil.example/image.jpg','width':200,'height':300}}}},
            {'data':{'p0':{'id':'nm1','primaryImage':{'url':'https://m.media-amazon.com/images/M/a.jpg','width':True,'height':300}}}},
        ]:
            self.http.request.return_value=body
            with self.assertRaises(ProviderError):await self.source.portraits(['nm1'])
        self.http.request.reset_mock()
        for ids in [[],['nm1']*2,['tt1'],[{}],['nm1']*17]:
            with self.assertRaises(ProviderError):await self.source.portraits(ids)
        self.http.request.assert_not_awaited()

    async def test_entitlement_denial_cannot_become_verified_empty_credits(self):
        self.http.request.return_value={'data':{'title':{**title(),'credits':connection([])}},
            'extensions':{'entitlements':{'fields':{'Title.credits':'DENY'}}}}
        with self.assertRaisesRegex(ProviderError,'not_entitled'):await self.source.credits('tt123')
        self.http.request.return_value={'data':{'name':{'id':'nm123','nameText':{'text':'Actor'},
            'bio':None,'credits':connection([])}},'extensions':{'entitlements':{'fields':{'Name.bio':'DENY'}}}}
        result=await self.source.credits('nm123',person=True)
        self.assertEqual(result['header']['unavailable_fields'],['Name.bio'])

    async def test_errors_never_trigger_an_alternate_transport(self):
        for body in ({'data':{'title':title()},'errors':[{'message':'denied'}]}, {}, {'data':None}):
            self.http.request.return_value=body
            with self.assertRaisesRegex(ProviderError,'graphql_error'):await self.source.title('tt123')
        self.http.request.side_effect=ProviderError('rate_limited')
        with self.assertRaisesRegex(ProviderError,'rate_limited'):await self.source.credits('tt123')
        self.assertEqual(self.http.request.await_count,4)
        self.assertTrue(all(call.args[2]=='https://caching.graphql.imdb.com/' for call in self.http.request.await_args_list))

    async def test_invalid_identifiers_and_cursors_do_not_make_requests(self):
        for native in ['nm123','tt123/','https://imdb.com/title/tt123',True,None]:
            with self.assertRaises(ProviderError):await self.source.credits(native)
        for cursor in [True,{},'', 'x'*4097]:
            with self.assertRaises(ProviderError):await self.source.credits('tt123',cursor)
        self.http.request.assert_not_awaited()

    async def test_search_candidates_are_filtered_and_still_need_identity_validation(self):
        self.http.request.return_value={'d':[{'id':'tt1','l':'Game','y':2020,'qid':'videoGame'},
            {'id':'tt2','l':'Film','qid':'movie'},{'id':'nm1','l':'Person','qid':'videoGame'}]}
        self.assertEqual(await self.source.candidates('Game / Test'),[{'id':'tt1','name':'Game','year':2020}])
        self.assertEqual(self.http.request.call_args.args[2],
            'https://v3.sg.media-imdb.com/suggestion/titles/x/Game%20%2F%20Test.json')

    def test_incomplete_or_malformed_pages_are_not_valid_empty_connections(self):
        self.assertEqual(page(connection([]),100)['total'],0)
        for raw in [None,connection([],True),connection([],0,True,'next'),
                    connection([{}],1,True,None),{'total':1,'pageInfo':{'hasNextPage':False},'edges':[{'node':None}]}]:
            with self.assertRaises(ProviderError):page(raw,100)
