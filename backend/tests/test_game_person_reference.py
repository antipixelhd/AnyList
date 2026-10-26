"""Reference citations are candidates, not unqualified person identifiers."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('SECRET_KEY', 'person-reference-unit-test-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from core.catalogue_providers import ProviderError
from core.game_person_reference import GamePersonReference, assess, candidate


def statement(value, **extra):
    return {'rank':'normal', 'mainsnak':{'snaktype':'value','datavalue':{'value':value}}, **extra}


def item():
    return {'id':'Q123', 'lastrevid':42, 'labels':{'en':{'value':'Shared actor'}}, 'claims':{
        'P31':[statement({'entity-type':'item','id':'Q5'})], 'P345':[statement('nm100')],
        'P569':[statement({'time':'+1990-08-18T00:00:00Z','precision':11,
            'calendarmodel':'http://www.wikidata.org/entity/Q1985727'}, id='Q123$birth', references=[{
                'hash':'proof', 'snaks':{'P248':[statement({'entity-type':'item','id':'Q20828898'})['mainsnak']],
                    'P4985':[statement('10')['mainsnak']]}}])]}}


PROFILE = {'id':10, 'name':'Shared actor', 'imdb_id':None, 'birthday':'1995-08-18'}
PERSON = {'id':'nm100','nameText':{'text':'Shared actor'}, 'birthDate':None}
SHARED = [{'imdb_id':f'tt{index}', 'tmdb_id':index, 'character':name} for index,name in enumerate(
    ('quinn','jill','llanofinn'), 1)]


class ReferenceEvidenceTests(unittest.TestCase):
    def test_birth_year_conflict_needs_multiple_exact_works_and_specific_characters(self):
        self.assertEqual(assess('nm100', item(), PROFILE, PERSON, [])[0], 'unresolved')
        state, proof = assess('nm100', item(), PROFILE, PERSON, SHARED)
        self.assertEqual(state, 'verified')
        self.assertTrue(proof['birthdate_discrepancy'])
        self.assertEqual(proof['corroboration'], 'exact_work_ids_and_specific_characters')
        self.assertEqual(proof['references'][0]['reference']['hash'], 'proof')
        for evidence in (SHARED[:2], [SHARED[0]]*4, [{**row,'character':'quinn'} for row in SHARED]):
            self.assertEqual(assess('nm100', item(), PROFILE, PERSON, evidence)[0], 'unresolved')

    def test_full_reference_birthday_can_corroborate_but_unknown_and_partial_cannot(self):
        self.assertEqual(assess('nm100', item(), {**PROFILE,'birthday':'1990-08-18'}, PERSON, [])[0], 'verified')
        for day in (None, '1990', '1990-08', '1990-99-99'):
            self.assertEqual(assess('nm100', item(), {**PROFILE,'birthday':day}, PERSON, [])[0], 'unresolved')
        for change in ({'precision':9}, {'calendarmodel':'http://www.wikidata.org/entity/Q1985786'}):
            raw=item();raw['claims']['P569'][0]['mainsnak']['datavalue']['value'].update(change)
            self.assertEqual(candidate(raw,'nm100')[0], 'unresolved')

    def test_unrelated_reference_source_or_context_and_deprecated_claims_are_not_promoted(self):
        for mutation in ('source','context','rank','human','imdb'):
            raw=item()
            if mutation=='source':raw['claims']['P569'][0]['references'][0]['snaks']['P248']=[]
            if mutation=='context':raw['claims']['P19']=raw['claims'].pop('P569')
            if mutation=='rank':raw['claims']['P569'][0]['rank']='deprecated'
            if mutation=='human':raw['claims']['P31']=[]
            if mutation=='imdb':raw['claims']['P345'][0]['rank']='deprecated'
            self.assertNotEqual(assess('nm100',raw,PROFILE,PERSON,SHARED)[0], 'verified')

    def test_namesakes_conflicting_ids_dates_and_multiple_references_remain_unmerged(self):
        for profile,person in (({**PROFILE,'name':'Other actor'},PERSON),
                ({**PROFILE,'imdb_id':'nm200'},PERSON), ({**PROFILE,'id':20},PERSON),
                (PROFILE,{**PERSON,'birthDate':{'dateComponents':{'year':1990,'month':8,'day':18}}})):
            self.assertEqual(assess('nm100',item(),profile,person,SHARED)[0], 'conflict')
        for prop,value in (('P345','nm200'),('P4985','20')):
            raw=item();raw['claims'].setdefault(prop,[]).append(statement(value))
            self.assertEqual(candidate(raw,'nm100')[0], 'conflict')
        raw=item();raw['claims']['P569'][0]['references'][0]['snaks']['P4985'].append(statement('20')['mainsnak'])
        self.assertEqual(candidate(raw,'nm100')[0], 'conflict')

    def test_direct_identifiers_require_consistent_subject_and_target(self):
        raw=item();raw['claims']['P4985']=[statement('10',rank='preferred')]
        self.assertEqual(assess('nm100',raw,PROFILE,PERSON,[])[0], 'verified')
        raw['claims']['P4985'].append(statement('20'))
        self.assertEqual(assess('nm100',raw,PROFILE,PERSON,[])[0], 'conflict')


class ReferenceSourceTests(unittest.IsolatedAsyncioTestCase):
    def service(self):
        async def cached(provider, kind, key, load, **kwargs):
            return {'data':await load(),'stale':False,'error':None}
        sources=SimpleNamespace(cache=SimpleNamespace(get=AsyncMock(side_effect=cached)))
        discovery=SimpleNamespace(sources=sources,items=AsyncMock(return_value={
            'data':{'items':{'nm100':['Q123']}},'stale':False}),
            wiki=SimpleNamespace(entities=AsyncMock(return_value={'data':{'entities':{'Q123':item()}},'stale':False})))
        return GamePersonReference(None,discovery=discovery,source=SimpleNamespace(query=AsyncMock()))

    async def test_exact_title_ids_and_specific_roles_count_not_titles_generic_self_or_names(self):
        service=self.service()
        rows=[{'title':{'id':f'tt{i}','titleText':{'text':name}},'category':{'id':'actress'},
            'characters':[{'name':character}]} for i,name,character in ((1,'One','Quinn'),(2,'Two','Self'),(3,'Three','Jill'))]
        profile={**PROFILE,'combined_credits':{'cast':[{'id':i,'media_type':'movie','title':name,'character':character}
            for i,name,character in ((1,'One','Quinn'),(2,'Two','Self'),(3,'Three','Jill'))]}}
        with patch('core.game_person_reference.tmdb.get_external_ids', AsyncMock(side_effect=lambda native,*a,**kw:
                {'id':native,'imdb_id':f'tt{native}' if native!=3 else 'tt999'})) as external:
            proof=await service.shared(profile,rows,'private-fixture-key')
        self.assertEqual(proof,[{'imdb_id':'tt1','tmdb_id':1,'media_type':'movie','character':'quinn'}])
        self.assertEqual(external.await_count,3)

    async def test_full_pagination_and_interruption_never_publish_partial_proof(self):
        service=self.service()
        def response(rows,more,cursor,total=2):
            return {'name':{**PERSON,'credits':{'total':total,'edges':[{'node':row} for row in rows],
                'pageInfo':{'hasNextPage':more,'endCursor':cursor}}}}
        first=response([{'title':{'id':'tt1'}}],True,'next')
        last=response([{'title':{'id':'tt2'}}],False,None)
        service.source.query.side_effect=[first,last]
        result=await service.performance('nm100')
        self.assertEqual(len(result['rows']),2)
        self.assertEqual(service.source.query.await_args_list[1].args[1]['after'],'next')
        for second in (TimeoutError(),response([],False,None),response([{}],True,'next')):
            service.source.query.side_effect=[first,second]
            with self.assertRaises((ProviderError,TimeoutError)):
                await service.performance('nm100')

    async def test_unique_redirect_entity_proof_and_conflicts_do_not_fan_out(self):
        service=self.service()
        raw=item();raw['id']='Q456';raw['claims']['P4985']=[statement('10')]
        service.discovery.wiki.entities.return_value={'data':{'entities':{'Q123':raw}},'stale':False}
        service.performance=AsyncMock(return_value={'person':PERSON,'rows':[]})
        with patch('core.game_person_reference.tmdb.get_person',AsyncMock(return_value=PROFILE)):
            state,profile,proof=await service.resolve('nm100','fixture-key')
        self.assertEqual((state,profile['id'],proof['wikidata_id']),('verified',10,'Q456'))
        for candidates in ([],['Q123','Q456']):
            service.discovery.items.return_value={'data':{'items':{'nm100':candidates}},'stale':False}
            with patch('core.game_person_reference.tmdb.get_person',AsyncMock()) as fetch:
                self.assertNotEqual((await service.resolve('nm100','fixture-key'))[0],'verified')
                fetch.assert_not_awaited()
        service.discovery.items.return_value={'data':{'items':{'nm100':['Q123']}},'stale':True}
        with self.assertRaises(ProviderError):await service.resolve('nm100','fixture-key')

    async def test_proof_work_is_bounded_to_twelve_target_titles(self):
        service=self.service()
        rows=[{'title':{'id':f'tt{i}','titleText':{'text':f'Work{i}'}},'category':{'id':'actor'},
            'characters':[{'name':'Specific character'}]} for i in range(30)]
        profile={**PROFILE,'combined_credits':{'cast':[{'id':i+1,'media_type':'movie','title':f'Work{i}',
            'character':'Specific character'} for i in range(30)]}}
        with patch('core.game_person_reference.tmdb.get_external_ids',AsyncMock(side_effect=lambda native,*a,**kw:
                {'id':native,'imdb_id':None})) as fetch:
            self.assertEqual(await service.shared(profile,rows,'fixture-key'),[])
            self.assertEqual(fetch.await_count,12)
