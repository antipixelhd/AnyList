import unittest
import os
from datetime import timedelta
from types import SimpleNamespace as Row

os.environ.setdefault('SECRET_KEY','game-work-test-only')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')
from core.game_work import choose, now


class FairAdmissionTests(unittest.TestCase):
    def ticket(self,owner,scope,priority=2,sequence=0):
        return Row(id=f'{owner}-{scope}',provider='igdb',priority=priority,owner=owner,
            scopes=[scope],created_at=now()+timedelta(milliseconds=sequence))

    def test_priority_beats_existing_lower_priority_tickets(self):
        tickets=[self.ticket('automatic','a',5),self.ticket('viewer','b',0,1)]
        self.assertEqual(choose(tickets,{}, {},now())[1].owner,'viewer')

    def test_more_entities_do_not_buy_a_user_more_turns(self):
        tickets=[self.ticket('a',str(index)) for index in range(20)]+[self.ticket('b','one',sequence=30)]
        turns={'igdb:2:a':now()}
        self.assertEqual(choose(tickets,{},turns,now())[1].owner,'b')

    def test_entities_share_their_users_turns(self):
        tickets=[self.ticket('a','first'),self.ticket('a','second',sequence=1)]
        selected=choose(tickets,{}, {'igdb:2:a:first':now()},now())
        self.assertEqual(selected[1].scopes,['second'])

    def test_live_promotion_changes_an_existing_ticket(self):
        low=self.ticket('automatic','shared',5)
        demand=Row(priority=0,origin=5,owner='viewer',active_until=now()+timedelta(seconds=5))
        rank,_,owner,_=choose([low],{'shared':demand},{},now())
        self.assertEqual(rank[0],0);self.assertEqual(owner,'igdb:0:viewer')
        demand.active_until=now()-timedelta(seconds=1)
        self.assertEqual(choose([low],{'shared':demand},{},now())[0][0],5)

    def test_expired_foreground_demand_does_not_promote_scheduled_retry(self):
        ticket=self.ticket('system','job:retry',4)
        demand=Row(priority=0,origin=2,owner='viewer',active_until=now()-timedelta(seconds=1))
        self.assertEqual(choose([ticket],{'job:retry':demand},{},now())[0][0],4)

    def test_verified_user_shares_turns_across_credentials(self):
        from dependencies import _work_user
        from core.game_work import current,work_context
        owners=[]
        for credential in ('token-a','api-key-b'):
            with work_context(0,credential,'page'):
                user=Row(id=7)
                self.assertIs(_work_user(user),user)
                owners.append(current.get().owner)
                self.assertEqual(current.get().priority,0)
                self.assertEqual(current.get().scopes,('page',))
        self.assertEqual(owners,['user:7','user:7'])
