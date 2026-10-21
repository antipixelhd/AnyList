import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault('SECRET_KEY','credits-test-only')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')

from core import game_credit_sources as source
from core.catalogue_providers import ProviderError


def snak(value):
    if isinstance(value,str) and source.ITEM.fullmatch(value):value={'entity-type':'item','id':value}
    return {'snaktype':'value','datavalue':{'value':value}}


def claim(value,**extra):
    return {'mainsnak':snak(value),'rank':'normal',**extra}


def item(q,name=None,claims=None):
    return {'id':q,'lastrevid':123,'labels':{'en':{'value':name or q}},'claims':claims or {}}


def game():
    return item('Q100','Game',{'P5794':[claim('game',qualifiers={'P9043':[snak('10')]})]})


def actor(q='Q200'):
    return item(q,'Actor',{'P31':[claim('Q5')],'P4985':[claim('123')],'P345':[claim('nm123')]})


def voice(q='Q200',key='Q100$test-voice',**extra):
    return claim(q,id=key,**extra)


class ProjectionTests(unittest.TestCase):
    def test_verified_roles_characters_language_and_references_stay_associated(self):
        raw=game();raw['claims']['P725']=[voice(qualifiers={'P453':[snak('Q300')],
            'P407':[snak('Q1860')],'P1545':[snak('2')]},references=[{'hash':'reference','snaks':{'P854':[snak('https://example.test/source')]}}])]
        character=item('Q300','Character',{'P18':[claim('Character.png')]})
        doc=source.project(raw,{'Q200':actor(),'Q300':character,'Q1860':item('Q1860','English',{'P424':[claim('en')]})},10,'game')
        credit=doc['credits'][0]
        self.assertEqual((credit['role'],credit['position'],credit['character_label']),('voice_actor',1,'Character'))
        self.assertEqual(credit['attributes']['languages'],['en'])
        self.assertEqual(credit['attributes']['statement']['references'][0]['hash'],'reference')
        self.assertEqual(credit['contributor']['identities'],[
            {'namespace':'wikidata.item','external_id':'Q200'}, {'namespace':'tmdb.person','external_id':'123'},
            {'namespace':'imdb.person','external_id':'nm123'}])
        self.assertEqual(doc['performances'],[{'credit_key':'Q100$test-voice',
            'character_identity':{'namespace':'wikidata.item','external_id':'Q300'},'language':'en'}])
        self.assertEqual(doc['characters'][0]['character']['attributes']['image_candidates'],['Character.png'])
        self.assertNotIn('image_url',doc['characters'][0]['character'])
        self.assertFalse(doc['coverage']['credits_complete'])

    def test_missing_cast_is_unknown_and_characters_can_exist_without_a_performer(self):
        raw=game();raw['claims']['P674']=[claim('Q300')]
        doc=source.project(raw,{'Q300':item('Q300','Unvoiced protagonist')},10,'game')
        self.assertEqual(doc['coverage']['contributors'],'unknown')
        self.assertEqual(len(doc['characters']),1);self.assertEqual(doc['credits'],[])
        raw['claims']['P725']=[voice()]
        doc=source.project(raw,{'Q200':item('Q200','Unverified type')},10,'game')
        self.assertEqual(doc['credits'],[]);self.assertGreater(doc['coverage']['omitted_statements'],0)

    def test_deprecated_claims_conflicting_identifiers_and_unknown_language_are_not_guessed(self):
        raw=game();raw['claims']['P725']=[voice(qualifiers={'P4633':[snak({'text':'Narrator','language':'en'})]})]
        raw['claims']['P161']=[voice(rank='deprecated')]
        person=actor();person['claims']['P4985'].append(claim('999'))
        doc=source.project(raw,{'Q200':person},10,'game')
        self.assertEqual(len(doc['credits']),1)
        self.assertEqual(doc['credits'][0]['character_label'],'Narrator')
        self.assertEqual(doc['credits'][0]['attributes']['languages'],[])
        self.assertNotIn('tmdb.person',str(doc['credits'][0]['contributor']['identities']))
        raw['claims']['P5794'].append(claim('game',qualifiers={'P9043':[snak('999')]}))
        with self.assertRaisesRegex(ProviderError,'identity_conflict'):source.project(raw,{},10,'game')


class Cache:
    def __init__(self):self.rows={}
    async def get(self,provider,kind,key,loader,**kwargs):
        if (kind,key) not in self.rows:self.rows[kind,key]=await loader()
        return {'data':self.rows[kind,key],'stale':False}


class SourceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.public=SimpleNamespace(cache=Cache(),request=AsyncMock())
        self.api=source.GameCreditSources(self.public)

    async def test_batched_entities_preserve_all_credits_and_warm_reads_avoid_provider_calls(self):
        raw=game();raw['claims']['P725']=[voice(f'Q{i}',key=f'Q100$voice-{i}') for i in range(200,261)]
        async def request(provider,url,params):
            if 'query' in params:return {'results':{'bindings':[{'item':{'value':'http://www.wikidata.org/entity/Q100'},'id':{'value':'10'}}]}}
            ids=params['ids'].split('|')
            return {'entities':{q:raw if q=='Q100' else actor(q) for q in ids}}
        self.public.request.side_effect=request
        result=await self.api.game(10,'game')
        self.assertEqual(len(result['document']['credits']),61)
        self.assertEqual(self.public.request.await_count,4)
        self.assertEqual(await self.api.game(10,'game'),result)
        self.assertEqual(self.public.request.await_count,4)

    async def test_bridge_rejects_ambiguous_numeric_evidence_and_outage_is_retryable(self):
        self.public.request.return_value={'results':{'bindings':[{'item':{'value':'http://www.wikidata.org/entity/Q100'},'id':{'value':'11'}}]}}
        with self.assertRaisesRegex(ProviderError,'identity_conflict'):await self.api.game(10,'game')
        self.public.request.return_value={'error':{'code':'maxlag'}}
        for _ in range(2):
            with self.assertRaisesRegex(ProviderError,'bad_response'):await self.api.entities(['Q200'])
        self.assertEqual(self.public.request.await_count,3)

    async def test_unmatched_and_missing_items_are_distinct_from_an_empty_cast(self):
        self.public.request.return_value={'results':{'bindings':[]}}
        self.assertEqual((await self.api.game(10,'game'))['coverage'],'unmatched')
        self.public.request.return_value={'entities':{'Q201':actor('Q201')}}
        result=await self.api.entities(['Q200'])
        self.assertEqual(result['data'],{'entities':{},'missing':['Q200']})
        for bad in ([],['invalid'],['Q0'],[{}]):
            with self.assertRaisesRegex(ProviderError,'invalid_request'):await self.api.entities(bad)
