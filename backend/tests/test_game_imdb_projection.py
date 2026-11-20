"""Credit meaning and source identity survive projection and page boundaries."""
import unittest
from core.game_imdb_projection import credit_document


def cast(native='nm123',attributes=None):
    return {'name':{'id':native,'nameText':{'text':'Exact namesake'}},'category':{'id':'actor','text':'Cast'},
            'characters':[{'name':'Hero'}],'attributes':attributes}


class IMDbProjectionTests(unittest.TestCase):
    def project(self,rows,**options):
        return credit_document('10','tt123',rows,generation=options.pop('generation','generation'),**options)

    def test_unknown_language_and_voice_are_never_inferred_from_the_title(self):
        doc,_=self.project([cast(),cast('nm456',[{'text':'voice'}])])
        first,second=doc['credits']
        self.assertIsNone(first['attributes']['voice'])
        self.assertIsNone(first['attributes']['imdb_attributes'])
        self.assertEqual(second['role'],'voice_actor')
        self.assertEqual(second['attributes']['languages'],[])
        self.assertEqual(len({row['contributor']['identities'][0]['external_id'] for row in doc['credits']}),2)
        self.assertFalse(doc['characters'])
        self.assertEqual(doc['work']['identities'],[{'namespace':'igdb.game','external_id':'10'}])

    def test_explicit_version_credited_as_and_uncredited_are_separate_facts(self):
        attributes=[{'text':'voice'},{'text':'English version'},{'text':'as Other Name'},{'text':'uncredited'}]
        doc,_=self.project([cast(attributes=attributes)])
        row=doc['credits'][0]
        self.assertEqual(row['attributes']['languages'],['en'])
        self.assertEqual(row['attributes']['credited_as'],'Other Name')
        self.assertTrue(row['attributes']['uncredited'])
        self.assertEqual(row['attributes']['imdb_attributes'],attributes)
        self.assertEqual(row['attributes']['game_role']['characters'],['Hero'])

    def test_detailed_expansion_jobs_do_not_manufacture_relationships(self):
        raw={'name':{'id':'nm123','nameText':{'text':'Composer'}},'category':{'id':'composer','text':'Music'},
             'jobs':[{'text':'composer: Blood and Wine expansion'}]}
        doc,_=self.project([raw])
        row=doc['credits'][0]
        self.assertIn('Blood and Wine',row['role_label'])
        self.assertEqual(row['attributes']['imdb_jobs'],raw['jobs'])
        self.assertFalse(doc['relationships'])
        self.assertFalse(doc['coverage']['credits_complete'])

    def test_source_keys_are_idempotent_across_refresh_and_page_boundaries(self):
        first,occurrences=self.project([cast()],offset=0)
        second,_=self.project([cast()],offset=1,occurrences=occurrences,page_number=1)
        together,_=self.project([cast(),cast()],generation='new-generation')
        self.assertEqual([first['credits'][0]['source_key'],second['credits'][0]['source_key']],
                         [row['source_key'] for row in together['credits']])
        self.assertEqual(second['credits'][0]['position'],1)
        self.assertEqual(second['credits'][0]['attributes']['source_page'],1)
