"""Principal people, provider evidence and historical studio boundaries."""
import unittest
from types import SimpleNamespace

from core.catalogue_normalize import normalize_tmdb, normalize_tvdb
from core.principal_credits import select_credits, remote_person_kind
from core.studio_names import studio_name


class PrincipalCreditsTests(unittest.TestCase):
    def selected(self, raw, kind='movie'):
        doc = normalize_tmdb(raw, kind)
        return doc, [c for c in doc['credits'] if c['attributes']['principal']]

    def test_department_heads_and_source_author_replace_supporting_jobs(self):
        jobs = ['Director', 'Novel', 'Screenplay', 'Editor', 'Original Music Composer',
                'Director of Photography', 'Production Design', 'Costume Design', 'Casting',
                'Assistant Director', 'Assistant Editor', 'Music', 'Production Assistant', 'Thanks']
        raw = {'id':1, 'credits':{'cast':[], 'crew':[
            {'id':i+1, 'name':job, 'job':job, 'credit_id':str(i)} for i, job in enumerate(jobs)]}}
        doc, chosen = self.selected(raw)
        self.assertEqual(len(doc['credits']), len(jobs))  # Source data stays complete.
        self.assertEqual({c['role_label'] for c in chosen}, set(jobs[:9]))

    def test_producer_flood_cannot_displace_leads_or_department_heads(self):
        raw = {'id':1, 'credits':{'cast':[], 'crew':[
            {'id':i+1, 'name':str(i), 'job':'Producer', 'popularity':i,
             'credit_id':str(i)} for i in range(80)] + [
            {'id':100, 'name':'Director without portrait', 'job':'Director'},
            {'id':101, 'name':'Editor', 'job':'Editor'},
            {'id':102, 'name':'Executive', 'job':'Executive Producer'}]}}
        _, chosen = self.selected(raw)
        producers = [c['contributor']['name'] for c in chosen if c['role_label']=='Producer']
        self.assertEqual(set(producers), {'79', '78'})
        self.assertIn('Director', {c['role_label'] for c in chosen})
        self.assertIn('Editor', {c['role_label'] for c in chosen})
        self.assertNotIn('Executive Producer', {c['role_label'] for c in chosen})

    def test_series_preserves_limited_producer_counts_without_summing_overlapping_jobs(self):
        raw = {'id':1, 'number_of_episodes':60, 'aggregate_credits':{'cast':[], 'crew':[
            {'id':2, 'name':'Producer', 'total_episode_count':60, 'jobs':[
                {'job':'Executive Producer', 'credit_id':'a', 'episode_count':12},
                {'job':'Producer', 'credit_id':'b', 'episode_count':8}]}]}}
        _, chosen = self.selected(raw, 'series')
        self.assertEqual([c['attributes']['episode_count'] for c in chosen], [12, 8])
        self.assertEqual(chosen[0]['attributes']['total_episode_count'], 60)

    def test_cast_is_bounded_and_series_preserves_early_billed_and_sustained_roles(self):
        cast = [{'id':i+1, 'name':str(i), 'order':i, 'total_episode_count':1,
                 'roles':[{'character':'Guest', 'credit_id':str(i), 'episode_count':1}]}
                for i in range(90)]
        cast[-1]['total_episode_count'] = 50
        raw = {'id':1, 'aggregate_credits':{'cast':cast, 'crew':[]}}
        doc, chosen = self.selected(raw, 'series')
        ids = {c['contributor']['identities'][0]['external_id'] for c in chosen}
        self.assertEqual(len(chosen), 24)
        self.assertEqual(len(doc['credits']), 90)
        self.assertTrue(set(map(str, range(1, 9))) <= ids)
        self.assertIn('90', ids)

    def test_archive_and_uncredited_actors_do_not_consume_cast_slots(self):
        raw = {'id':1, 'credits':{'cast':[
            {'id':1, 'name':'Archive', 'character':'Self (archive footage)', 'order':0},
            {'id':2, 'name':'Extra', 'character':'Guard (uncredited)', 'order':1},
            {'id':3, 'name':'Lead', 'character':'Lead', 'order':2}], 'crew':[]}}
        _, chosen = self.selected(raw)
        self.assertEqual([c['contributor']['name'] for c in chosen], ['Lead'])

    def test_tvdb_featured_status_survives_without_becoming_a_required_flag(self):
        raw = {'id':1, 'characters':[
            {'id':1, 'peopleId':10, 'personName':'Actor', 'type':3, 'sort':0, 'isFeatured':False},
            {'id':2, 'peopleId':11, 'personName':'Director', 'type':1, 'peopleType':'Director', 'isFeatured':True}]}
        doc = normalize_tvdb(raw)
        self.assertFalse(doc['credits'][0]['attributes']['featured'])
        self.assertTrue(all(c['attributes']['principal'] for c in doc['credits']))

    def test_same_person_is_counted_once_with_multiple_principal_jobs(self):
        person = SimpleNamespace(id=1, kind='person', image_url=None)
        def credit(job):
            return SimpleNamespace(role='contributor', role_label=job, character_label=None,
                                   position=None, attributes={})
        rows = [(credit('Director'), person), (credit('Writer'), person)]
        self.assertEqual(len(select_credits(rows, 'movie')), 2)

    def test_remote_discovery_excludes_unknown_and_minor_departments(self):
        works = [{'id':1, 'media_type':'movie'}]
        self.assertEqual(remote_person_kind({'known_for_department':'Directing','known_for':works}), 'staff')
        self.assertIsNone(remote_person_kind({'known_for_department':'Directing','known_for':[]}))
        self.assertIsNone(remote_person_kind({'known_for_department':'Crew','known_for':works,'popularity':100}))
        self.assertIsNone(remote_person_kind({'known_for_department':'Sound','known_for':works,'popularity':0.1}))
        self.assertEqual(remote_person_kind({'known_for_department':'Sound','known_for':works,'popularity':10}), 'staff')

    def test_historical_studios_stay_separate_and_legal_suffixes_are_display_only(self):
        self.assertEqual(studio_name('Marvel Studios, LLC'), 'Marvel Studios')
        self.assertEqual(studio_name('Marvel Films'), 'Marvel Films')
        raw = {'id':1, 'production_companies':[
            {'id':420, 'name':'Marvel Studios, LLC'}, {'id':2301, 'name':'Marvel Films'}]}
        doc = normalize_tmdb(raw, 'movie')
        self.assertEqual([c['contributor']['name'] for c in doc['credits']], ['Marvel Studios','Marvel Films'])
        self.assertNotEqual(doc['credits'][0]['contributor']['identities'], doc['credits'][1]['contributor']['identities'])

    def test_tvdb_company_bridge_uses_remote_ids_never_parent_company(self):
        raw = {'id':1, 'companies':{'studio':[
            {'id':3, 'name':'Studio alias', 'parentCompany':{'id':420},
             'remoteIds':[{'sourceName':'TheMovieDB.com','id':'99'}]}]}}
        company = normalize_tvdb(raw)['credits'][0]['contributor']
        self.assertIn({'namespace':'tmdb.company','external_id':'99'}, company['identities'])
        self.assertNotIn({'namespace':'tmdb.company','external_id':'420'}, company['identities'])
