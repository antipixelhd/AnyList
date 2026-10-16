import os
import unittest
from types import SimpleNamespace

os.environ.setdefault('SECRET_KEY','role-plan-test')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')

from core.screen_characters import _character_plan
from models.catalogue import ScreenCharacter


class CharacterPlanTests(unittest.TestCase):
    def credit(self,id,label,provider='tmdb',actor=1):
        return SimpleNamespace(id=id,character_label=label,provider=provider,contributor_id=actor)

    def test_ambiguous_aliases_stay_separate_and_repeated_exact_roles_reuse_one_record(self):
        first=ScreenCharacter(actor_id=1,role_key='first',name='First',aliases=['shared name'])
        second=ScreenCharacter(actor_id=1,role_key='second',name='Second',aliases=['shared name'])
        selected,created,_=_character_plan([self.credit(1,'Shared Name'),self.credit(2,'Shared Name')],[first,second],99)
        self.assertEqual(len(created),1)
        self.assertIs(selected[0][1],created[0])
        self.assertIs(selected[1][1],created[0])

    def test_generic_exact_roles_remain_title_scoped_and_actor_scoped(self):
        current=ScreenCharacter(actor_id=1,role_key='work:99:guard',name='Guard',aliases=['guard'])
        other=ScreenCharacter(actor_id=1,role_key='work:98:guard',name='Guard',aliases=['guard'])
        selected,created,_=_character_plan([self.credit(1,'Guard'),self.credit(2,'Guard',actor=2)],[current,other],99)
        self.assertIs(selected[0][1],current)
        self.assertIs(selected[1][1],created[0])
        self.assertEqual(len(created),1)

    def test_same_title_secondary_slash_evidence_links_to_unique_primary_role(self):
        credits=[self.credit(1,'Tony Stark / Iron Man'),self.credit(2,'Iron Man','tvdb')]
        selected,created,aliases=_character_plan(credits,[],99)
        self.assertEqual(len(created),1)
        self.assertIs(selected[0][1],selected[1][1])
        self.assertEqual(set(aliases[created[0]]),{'tony stark iron man','iron man'})
