"""Independent rating evidence survives season milestone edits and merging."""
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from sqlalchemy import delete, select, update
import test_season_completion as seasons
from models import Media, Rating, TrackedEntry, TrackingActivity, WatchEvent
from core.season_completion import run_batch, reconcile
from core.season_activity import record_season_activity
from core.tracking_projection import activity_data
from routers.ratings import router as ratings_router


@unittest.skipUnless(seasons.fixture.URL, 'Requires explicit disposable PostgreSQL')
class SeasonActivityRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await seasons.SeasonDatabaseTests.asyncSetUp(self)
        self.root=f'/tracking/entry/{self.series_id}'
        self.app.include_router(ratings_router,prefix='/ratings')

    async def asyncTearDown(self):
        await seasons.SeasonDatabaseTests.asyncTearDown(self)

    async def cards(self, db):
        return list(await db.scalars(select(TrackingActivity).where(TrackingActivity.media_id==self.series_id)))

    async def partial(self):
        async with self.Session() as db:
            await db.execute(delete(WatchEvent).where(WatchEvent.media_id.in_(self.episode_ids[1:])))
            entry=await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id==self.series_id))
            entry.status,entry.progress,entry.start_date,entry.finish_date='watching',1,None,None
            await db.commit()

    async def test_clearing_start_keeps_its_rating_event(self):
        await self.partial()
        response=await self.client.patch(self.root,json={'start_date':date.today().isoformat()})
        self.assertEqual(response.status_code,200,response.text)
        response=await self.client.patch(self.root,json={'rating_mode':'manual','season_scores':{'1':8}})
        self.assertEqual(response.status_code,200,response.text)
        async with self.Session() as db:
            original=next(c for c in await self.cards(db) if c.payload.get('rating_changed'))
            rated_at=original.payload['first_rated_at']
        response=await self.client.patch(self.root,json={'start_date':None})
        self.assertEqual(response.status_code,200,response.text)
        await run_batch(self.Session)
        async with self.Session() as db:
            rated=[c for c in await self.cards(db) if c.payload.get('rating_changed')]
            self.assertEqual(len(rated),1)
            self.assertTrue(rated[0].payload.get('season_rating_group'))
            self.assertEqual(rated[0].score,8)
            self.assertEqual(rated[0].payload['first_rated_at'],rated_at)
            self.assertFalse(rated[0].payload.get('started_watching'))
            self.assertEqual(await db.scalar(select(Rating.rating).where(Rating.media_id==self.series_id,Rating.season_number==1)),8)
        response=await self.client.patch(self.root,json={'start_date':date.today().isoformat()})
        self.assertEqual(response.status_code,200,response.text)
        await run_batch(self.Session)
        async with self.Session() as db:
            cards=await self.cards(db)
            self.assertEqual(len(cards),1)
            self.assertTrue(cards[0].payload.get('season_start_id'))
            self.assertEqual(cards[0].score,8)
            self.assertEqual(cards[0].payload['first_rated_at'],rated_at)
        response=await self.client.patch(self.root,json={'status':'dropped'})
        self.assertEqual(response.status_code,200,response.text)
        await run_batch(self.Session)
        async with self.Session() as db:
            cards=await self.cards(db)
            self.assertEqual(len(cards),1)
            self.assertEqual(cards[0].status,'dropped')
            self.assertEqual(cards[0].score,8)
            self.assertEqual(cards[0].payload['first_rated_at'],rated_at)

    async def test_clearing_finish_keeps_its_rating_event(self):
        summary=(await self.client.get(self.root+'/series-editor')).json()
        season=summary['seasons'][-1]
        async def save(day,score=None):
            edit={'season_number':2,'membership':season['membership'],'completion_date':day}
            if score is not None:edit['score']=score
            return await self.client.patch(self.root,json={'rating_mode':'manual',
                'series_editor':{'order':summary['order'],'seasons':[edit]}})
        response=await save(date.today().isoformat(),8)
        self.assertEqual(response.status_code,200,response.text)
        response=await save(None)
        self.assertEqual(response.status_code,200,response.text)
        await run_batch(self.Session)
        async with self.Session() as db:
            rated=[c for c in await self.cards(db) if c.payload.get('rating_changed')]
            self.assertEqual(len(rated),1)
            self.assertTrue(rated[0].payload.get('season_rating_group'))
            self.assertEqual(rated[0].payload['season_ratings'],{'2':8})
            self.assertFalse(rated[0].payload.get('season_completion_id'))

    async def test_clear_all_clears_recent_season_scores_but_preserves_actions_and_old_history(self):
        with patch('core.local_outbound.dispatch_local_tracking_delta',new=AsyncMock()):
            response=await self.client.post('/ratings',json={'media_id':self.series_id,'media_type':'series','season_number':1,'rating':8})
        self.assertEqual(response.status_code,200,response.text)
        async with self.Session() as db:
            now=datetime.now(timezone.utc).replace(tzinfo=None)
            old=TrackingActivity(user_id=self.user_id,media_id=self.series_id,status='watching',score=6,
                created_at=now-timedelta(days=10),payload={'episode_order':'tmdb:aired',
                    'window_key':'old-rating','season_rating_group':True,'rating_changed':True,'season_ratings':{'2':6}})
            action=TrackingActivity(user_id=self.user_id,media_id=self.series_id,status='watching',score=8,
                created_at=now,payload={'episode_order':'tmdb:aired','window_key':'recent-season',
                    'season_completion_id':999,'season_number':1,'finished_seasons':[1],
                    'rating_changed':True,'rating_snapshot':True,'season_ratings':{'1':8}})
            db.add_all([old,action]);await db.commit();old_id,action_id=old.id,action.id
        response=await self.client.delete('/ratings/all')
        self.assertEqual(response.status_code,200,response.text)
        async with self.Session() as db:
            self.assertEqual(list(await db.scalars(select(Rating).where(Rating.user_id==self.user_id))),[])
            cards=await self.cards(db)
            recent=[c for c in cards if c.id!=old_id]
            self.assertEqual([c.id for c in recent],[action_id])
            self.assertIsNone(recent[0].score)
            self.assertFalse(recent[0].payload.get('rating_changed'))
            self.assertFalse(recent[0].payload.get('rating_snapshot'))
            self.assertEqual(recent[0].payload['finished_seasons'],[1])
            self.assertEqual(next(c for c in cards if c.id==old_id).score,6)

    async def test_post_cooldown_corrections_preserve_baseline_and_net_reversions_disappear(self):
        now=datetime(2026,11,1,12)
        async with self.Session() as db:
            media=await db.get(Media,self.series_id)
            async def rate(before,after,at):
                await record_season_activity(db,user_id=self.user_id,media=media,status='completed',score=None,
                    season_changes={1:(before,after)},now=at)
            await rate(None,7,now)
            await rate(7,8,now+timedelta(days=4))
            await rate(8,9,now+timedelta(days=4,hours=1))
            cards=sorted(await self.cards(db),key=lambda c:c.created_at)
            self.assertEqual(len(cards),2)
            self.assertEqual(cards[-1].payload['previous_score'],7)
            self.assertFalse(cards[-1].payload['rating_first'])
            self.assertEqual(cards[-1].score,9)
            await rate(9,7,now+timedelta(days=4,hours=2))
            self.assertEqual(len(await self.cards(db)),1)
            self.assertEqual((await self.cards(db))[0].score,7)

    async def test_first_completion_has_one_introductory_card(self):
        for new_entry in (False,True):
            async with self.Session() as db:
                await db.execute(delete(TrackingActivity).where(TrackingActivity.media_id==self.series_id))
                await db.execute(delete(WatchEvent).where(WatchEvent.media_id.in_(self.episode_ids)))
                entry=await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id==self.series_id))
                if new_entry:await db.delete(entry)
                else:entry.status,entry.progress,entry.start_date,entry.finish_date='planning',0,None,None
                await db.commit()
            summary=(await self.client.get(self.root+'/series-editor')).json()
            response=await self.client.patch(self.root,json={'status':'completed','series_editor':{
                'order':summary['order'],'revision':summary['revision'],'seasons':[]}})
            self.assertEqual(response.status_code,200,response.text)
            await run_batch(self.Session)
            async with self.Session() as db:
                cards=activity_data([(c,await db.get(Media,self.series_id)) for c in await self.cards(db)])
                self.assertEqual(len(cards),1,(new_entry,cards))
                self.assertTrue(cards[0]['payload']['series_completed'])

    async def test_grouped_rating_baselines_survive_completion_absorption_and_reversion(self):
        now=datetime(2026,11,1,12)
        async with self.Session() as db:
            for sn,ids in ((1,self.episode_ids[:2]),(2,self.episode_ids[2:])):
                await db.execute(update(WatchEvent).where(WatchEvent.media_id.in_(ids)).values(
                    watched_at=now+timedelta(days=4,hours=sn)))
            media=await db.get(Media,self.series_id)
            async def rate(changes,at):
                await record_season_activity(db,user_id=self.user_id,media=media,status='completed',score=None,
                    season_changes=changes,now=at)
            await rate({1:(None,7),2:(None,6)},now)
            await rate({1:(7,8),2:(6,9)},now+timedelta(days=4))
            await reconcile(db,self.user_id,self.series_id)
            milestones=[c for c in await self.cards(db) if c.payload.get('season_completion_id')]
            self.assertEqual(len(milestones),2)
            self.assertEqual({c.payload['season_number']:c.payload.get('previous_score') for c in milestones},{1:7,2:6})
            await rate({1:(8,7),2:(9,6)},now+timedelta(days=4,hours=3))
            for card in milestones:
                self.assertFalse(card.payload.get('rating_changed'))
                self.assertNotIn('previous_score',card.payload)
                self.assertEqual(card.score,{1:7,2:6}[card.payload['season_number']])
            self.assertEqual(len(await self.cards(db)),3)

    async def test_net_revert_then_another_season_rating_keeps_the_new_event(self):
        now=datetime(2026,11,1,12)
        async with self.Session() as db:
            media=await db.get(Media,self.series_id)
            async def rate(changes,at):
                await record_season_activity(db,user_id=self.user_id,media=media,status='completed',score=None,
                    season_changes=changes,now=at)
            await rate({1:(None,7)},now)
            await rate({1:(7,8)},now+timedelta(days=4))
            await rate({1:(8,7),2:(None,9)},now+timedelta(days=4,hours=1))
            cards=sorted(await self.cards(db),key=lambda c:c.created_at)
            self.assertEqual([c.payload['season_ratings'] for c in cards],[{'1':7},{'2':9}])

    async def test_same_day_manual_start_merges_into_drop_and_reconciliation_stays_stable(self):
        await self.partial()
        response=await self.client.patch(self.root,json={'status':'dropped'})
        self.assertEqual(response.status_code,200,response.text)
        response=await self.client.patch(self.root,json={'start_date':date.today().isoformat()})
        self.assertEqual(response.status_code,200,response.text)
        async with self.Session() as db:
            await reconcile(db,self.user_id,self.series_id)
            cards=await self.cards(db)
            self.assertEqual(len(cards),1)
            self.assertEqual(cards[0].status,'dropped')
            self.assertEqual(cards[0].payload['season_number'],1)
            self.assertFalse(cards[0].payload.get('started_watching'))
