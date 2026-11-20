"""Release gates and negative evidence must never suppress transport recovery."""
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from core.enrichment_retry import content_recheck, schedule_failure, pause_unused, public_coverage


class EnrichmentRetryTests(unittest.TestCase):
    stamp = datetime(2026, 11, 4, 12)

    def work(self, release):
        return SimpleNamespace(attributes={'release_date':release})

    def row(self):
        return SimpleNamespace(attempts=0,error_code=None,coverage={},expires_at=None,next_attempt_at=None)

    def test_operational_demand_and_failure_evidence_stays_out_of_public_coverage(self):
        self.assertEqual(public_coverage({'requested_at':'private queue demand','same_failure_count':3,
            'retry_policy_version':1,'retry_mode':'on_demand','retry_steam_app_id':'42',
            'characters_complete':True}),{'characters_complete':True})

    def test_release_age_controls_content_rechecks_and_never_skips_release(self):
        for release, days in [('2020-01-01',90),('2025-01-01',30),('2026-01-01',7),
                              ('2026-11-01',1),('2027-04-01',7),(None,7),('bad date',7)]:
            self.assertEqual(content_recheck(self.work(release),self.stamp),self.stamp+timedelta(days=days))
        self.assertEqual(content_recheck(self.work('2026-11-05'),self.stamp),datetime(2026,11,5))
        self.assertEqual(content_recheck(self.work('2026'),self.stamp),self.stamp+timedelta(days=7))
        self.assertEqual(content_recheck(self.work('2026-11'),self.stamp),self.stamp+timedelta(days=1))
        quarter=SimpleNamespace(attributes={'release_date':'2026','release_date_precision':'quarter','release_quarter':3})
        self.assertEqual(content_recheck(quarter,self.stamp),self.stamp+timedelta(days=1))

    def test_repeated_content_gaps_leave_worker_queue_but_remain_unknown(self):
        row=self.row()
        for _ in range(3):
            schedule_failure(row,'content_unavailable',self.stamp,work=self.work('2020-01-01'))
        self.assertIsNone(row.next_attempt_at)
        self.assertEqual(row.expires_at,self.stamp+timedelta(days=90))
        self.assertEqual(row.error_code,'content_unavailable')
        self.assertEqual(row.coverage['retry_mode'],'on_demand')

    def test_old_ongoing_or_unknown_series_does_not_inherit_static_title_cooldowns(self):
        for status in ['Returning Series','In Production',None]:
            work=SimpleNamespace(kind='series',attributes={'release_date':'2000-01-01','status':status})
            self.assertEqual(content_recheck(work,self.stamp),self.stamp+timedelta(days=7))
        work=SimpleNamespace(kind='series',attributes={'release_date':'2000-01-01','status':'Ended'})
        self.assertEqual(content_recheck(work,self.stamp),self.stamp+timedelta(days=90))

    def test_provider_restrictions_malformed_replies_and_outages_keep_normal_backoff(self):
        for code in ['rate_limited','forbidden','unauthorized','not_entitled','unavailable','timeout','bad_response','ingestion_failed']:
            row=self.row()
            for _ in range(8):schedule_failure(row,code,self.stamp,work=self.work('2000-01-01'),retry_after=7200)
            self.assertEqual(row.next_attempt_at,self.stamp+timedelta(hours=2),code)
            self.assertIsNone(row.expires_at,code)
            self.assertEqual(row.coverage['retry_mode'],'background',code)

    def test_changed_failure_does_not_inherit_negative_evidence(self):
        row=self.row()
        for _ in range(3):schedule_failure(row,'identity_conflict',self.stamp,work=self.work('2020-01-01'))
        schedule_failure(row,'not_found',self.stamp,work=self.work('2020-01-01'))
        self.assertIsNotNone(row.next_attempt_at)
        self.assertEqual(row.coverage['same_failure_count'],1)

    def test_unused_failed_work_pauses_without_losing_data_or_delaying_new_demand(self):
        row=self.row();row.payload={'keep':'last good'}
        row.error_code='forbidden';row.next_attempt_at=self.stamp
        row.coverage={'requested_at':(self.stamp-timedelta(days=8)).isoformat()}
        self.assertTrue(pause_unused(row,self.stamp))
        self.assertEqual(row.payload,{'keep':'last good'})
        self.assertIsNone(row.next_attempt_at);self.assertEqual(row.expires_at,self.stamp)
        row.coverage={'requested_at':self.stamp.isoformat()}
        self.assertFalse(pause_unused(row,self.stamp))
