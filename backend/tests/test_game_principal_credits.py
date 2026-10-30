import unittest
from copy import deepcopy
from core.game_principal_credits import select_credits, CAST_LIMIT, STAFF_LIMIT

def credit(native, role='actor', **extra):
    return {'id':native,'kind':'person','role':role,'role_label':role.replace('_',' ').title(),'position':native,**extra}

class GamePrincipalCreditsTests(unittest.TestCase):
    def test_cast_is_bounded_by_existing_billing_not_portraits_and_preserves_sources(self):
        rows=[credit(i,image='portrait' if i>24 else None) for i in range(40)]
        before=deepcopy(rows);selected=select_credits(rows)
        self.assertEqual([r['id'] for r in selected],list(range(CAST_LIMIT)))
        self.assertEqual(rows,before)
    def test_exact_lead_jobs_and_group_quotas_exclude_support_categories(self):
        labels=['Game Director','Creative Director','Lead Designer','Narrative Director','Composer','Art Director','Lead Programmer','Producer',
                'Assistant Director','QA Lead','Localization Producer','Marketing Director','Additional Programming','Special Thanks']
        rows=[credit(i,'contributor',role_label=label) for i,label in enumerate(labels)]
        rows += [credit(100+i,'director') for i in range(12)]
        selected=select_credits(rows)
        self.assertTrue(set(range(8)) <= {r['id'] for r in selected})
        self.assertFalse(set(range(8,14)) & {r['id'] for r in selected})
        self.assertLessEqual(len(selected),STAFF_LIMIT)
        self.assertEqual(sum(r['role']=='director' or r['id']<2 for r in selected),4)
    def test_detailed_jobs_override_broad_category_and_archive_uncredited_are_omitted(self):
        rows=[credit(1,'producer',attributes={'game_role':{'jobs':['Localization producer']}}),
              credit(2,'miscellaneous',role_label='Other crew',attributes={'game_role':{'jobs':['Lead game designer']}}),
              credit(3,'actor',attributes={'uncredited':True}),credit(4,'archive_sound'),
              credit(5,'voice_actor',character_label='Additional voices (uncredited)'),credit(6,'narrator'),
              credit(7,'developer',kind='organization'),credit(8,'supporting',kind='organization')]
        self.assertEqual({r['id'] for r in select_credits(rows)},{2,6,7})
    def test_multirole_person_counts_once_per_cast_and_staff_group(self):
        rows=[credit(1),credit(1,'voice_actor'),credit(1,'writer')]
        self.assertEqual(select_credits(rows),rows)
