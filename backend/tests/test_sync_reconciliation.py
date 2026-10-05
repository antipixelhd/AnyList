"""Shared category resolution and pull-only convergence regressions."""
import itertools
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from core.sync_reconciliation import Observation, Reconciliation, resolve, _ratings, _library, initialize, _current, add_watch_event
from core.pull_cycle import PullCycleState


class ResolutionTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 4, 16)

    def test_latest_reliable_edit_wins_in_every_fetch_order(self):
        observations = [Observation("trakt", 8, self.now-timedelta(hours=2)),
                        Observation("mdblist", 9, self.now-timedelta(hours=1))]
        for order in itertools.permutations(observations):
            decision = resolve(6, self.now-timedelta(hours=3), order)
            self.assertEqual(decision.value, 9)
            self.assertEqual(decision.sources, frozenset({"mdblist"}))

    def test_newer_local_edit_during_cycle_drops_only_this_proposal(self):
        decision = resolve(7, self.now, [Observation("trakt", 9, self.now+timedelta(hours=1))],
                           started_at=self.now-timedelta(minutes=1))
        self.assertEqual(decision.value, 7)
        self.assertTrue(decision.stale)
        self.assertFalse(decision.conflict)

    def test_unchanged_pull_only_value_does_not_restore_old_state(self):
        decision = resolve(False, self.now, [Observation("a", True, previous=True)])
        self.assertEqual(decision.value, False)
        self.assertFalse(decision.sources)
        self.assertFalse(decision.conflict)

    def test_known_older_evidence_is_not_a_conflict(self):
        decision = resolve(8, self.now, [Observation("a", 6, self.now-timedelta(hours=1))])
        self.assertEqual(decision.value, 8)
        self.assertFalse(decision.conflict)

    def test_unknown_or_tied_incompatible_evidence_preserves_local(self):
        for clocks in ((None, self.now), (self.now, self.now)):
            decision = resolve(6, self.now-timedelta(hours=1),
                [Observation("a", 8, clocks[0]), Observation("b", 9, clocks[1])])
            self.assertEqual(decision.value, 6)
            self.assertTrue(decision.conflict)

    def test_agreeing_evidence_combines_sources(self):
        decision = resolve(6, self.now-timedelta(hours=1),
            [Observation("a", 8, self.now), Observation("b", 8, self.now)])
        self.assertEqual(decision.sources, frozenset({"a", "b"}))

    def test_undated_removal_requires_baseline_order_against_local_edit(self):
        baseline = self.now-timedelta(hours=1)
        observation = Observation("a", False, previous=True, observed_after=baseline)
        self.assertEqual(resolve(True, baseline-timedelta(hours=1), [observation]).value, False)
        self.assertTrue(resolve(True, self.now, [observation]).conflict)

    def test_snapshot_delta_orders_later_cycles_but_not_same_cycle_disagreements(self):
        removal = Observation("a", False, previous=True, observed_after=self.now-timedelta(hours=2))
        decision = resolve(True, self.now-timedelta(hours=1), [removal], started_at=self.now)
        self.assertFalse(decision.value)
        self.assertEqual(decision.changed_at, self.now)
        competing = Observation("b", True, self.now-timedelta(minutes=1))
        for order in itertools.permutations([removal, competing]):
            self.assertTrue(resolve(True, self.now-timedelta(hours=1), order, started_at=self.now).conflict)

    def test_completion_resolves_covered_positions_but_preserves_later_episode(self):
        from core.sync_reconciliation import reconcile_completions
        positions = [Observation(source, ("watching", 1, 3, position, 100), self.now-timedelta(minutes=5))
                     for source, position in (("a", 20), ("b", 30))]
        complete = Observation("c", ("watching", 1, 3, None, None), self.now)
        for order in itertools.permutations(positions):
            self.assertEqual(reconcile_completions(order, [complete]), [complete])
        later = Observation("b", ("watching", 1, 4, 20, 100), self.now)
        self.assertEqual(reconcile_completions([later], [complete]), [later])
        unknown = Observation("a", ("watching", 1, 3, 20, 100))
        self.assertTrue(resolve(None, None, reconcile_completions([unknown], [complete])).conflict)


@unittest.skipUnless(os.getenv("TRACKING_TEST_DATABASE_URL"), "Requires disposable PostgreSQL")
class CombinedDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirmed_watched_episode_updates_series_progress_status_and_dates(self):
        from models import Media, Show, WatchEvent
        from models.base import MediaType
        from models.tracking import TrackedEntry, SyncReview
        from core.sync_reconciliation import resolve_category_review
        from sqlalchemy import select
        for previous, have_history, catalogue_complete, expected in (
            ("planning", False, False, "watching"),
            ("watching", True, True, "completed"),
            ("completed", True, False, "completed"),
        ):
            with self.subTest(previous=previous):
                async with self.factory() as db:
                    show = Show(tmdb_id=72001, title="Confirmed series")
                    db.add(show)
                    await db.flush()
                    root = Media(tmdb_id=show.tmdb_id, media_type=MediaType.series, title=show.title,
                        tmdb_data={"tracking_catalogue_refreshed_at": self.now.isoformat()} if catalogue_complete else {})
                    episodes = [Media(show_id=show.id, media_type=MediaType.episode, title="Episode",
                        season_number=1, episode_number=number, release_date="2020-01-01") for number in (1, 2)]
                    special = Media(show_id=show.id, media_type=MediaType.episode, title="Special",
                                    season_number=0, episode_number=1)
                    db.add_all([root, *episodes, special])
                    await db.flush()
                    old_finish = self.now.date() if previous == "completed" else None
                    entry = TrackedEntry(user_id=self.user.id, media_id=root.id, status=previous,
                                         progress=0, finish_date=old_finish)
                    db.add(entry)
                    if have_history:
                        db.add_all([WatchEvent(user_id=self.user.id, media_id=episodes[0].id, completed=True)
                                    for _ in range(2)])
                    db.add(WatchEvent(user_id=self.user.id, media_id=special.id, completed=True))
                    await db.flush()
                    review = SyncReview(user_id=self.user.id, provider="combined", media_id=episodes[1].id,
                        kind="watch_conflict", message="fixture", payload={"category": "watched state",
                            "observations": [{"source": "trakt", "value": True}]})
                    self.assertTrue(await resolve_category_review(db, review, "confirm"))
                    self.assertEqual(entry.progress, 2 if have_history else 1)
                    self.assertEqual(entry.status, expected)
                    self.assertEqual(entry.status_source, "local")
                    if previous == "completed":
                        self.assertEqual(entry.finish_date, old_finish)
                    elif expected == "completed":
                        self.assertEqual(entry.finish_date, datetime.utcnow().date())
                    else:
                        self.assertIsNotNone(entry.start_date)
                    count = len((await db.execute(select(WatchEvent).where(
                        WatchEvent.media_id == episodes[1].id, WatchEvent.user_id == self.user.id))).scalars().all())
                    self.assertEqual(count, 1)
                    await db.rollback()

    async def test_keeping_episode_review_preserves_history_and_confirming_unwatched_reopens_series(self):
        from models import Media, Show, WatchEvent
        from models.base import MediaType
        from models.tracking import TrackedEntry, SyncReview
        from core.sync_reconciliation import resolve_category_review
        async with self.factory() as db:
            show = Show(tmdb_id=72002, title="Reviewed series")
            db.add(show)
            await db.flush()
            root = Media(tmdb_id=show.tmdb_id, media_type=MediaType.series, title=show.title)
            episode = Media(show_id=show.id, media_type=MediaType.episode, title="Episode",
                            season_number=1, episode_number=1)
            db.add_all([root, episode])
            await db.flush()
            entry = TrackedEntry(user_id=self.user.id, media_id=root.id, status="completed",
                                 progress=1, finish_date=self.now.date())
            db.add_all([entry, WatchEvent(user_id=self.user.id, media_id=episode.id, completed=True)])
            await db.flush()
            review = SyncReview(user_id=self.user.id, provider="combined", media_id=episode.id,
                kind="watch_conflict", message="fixture", payload={"category": "watched state",
                    "observations": [{"source": "trakt", "value": False}]})
            await resolve_category_review(db, review, "keep")
            self.assertEqual((entry.progress, entry.status, entry.finish_date), (1, "completed", self.now.date()))
            await resolve_category_review(db, review, "confirm")
            self.assertEqual((entry.progress, entry.status, entry.finish_date), (0, "planning", None))
            await db.rollback()

    async def asyncSetUp(self):
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from models import User, UserSettings, Media, MediaServerConnection
        from models.base import MediaType
        from models.tracking import CloudBaseline, StreamBaseline
        self.engine = create_async_engine(os.environ["TRACKING_TEST_DATABASE_URL"])
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.now = datetime.utcnow()-timedelta(hours=1)
        async with self.factory() as db:
            self.user = User(email="combined-owner@test.invalid", username="combined-owner", api_key="combined-owner-test")
            db.add(self.user)
            await db.flush()
            db.add(UserSettings(user_id=self.user.id))
            self.media = [Media(tmdb_id=70001+i, media_type=MediaType.movie, title=f"Fixture {i}") for i in range(2)]
            self.connections = [MediaServerConnection(user_id=self.user.id, type="stremio", name=f"Fixture {i}",
                                                      url="http://invalid", token=f"fixture-{i}", push_collection=False) for i in range(2)]
            db.add_all(self.media+self.connections)
            await db.flush()
            for conn in self.connections:
                db.add(StreamBaseline(user_id=self.user.id, connection_id=conn.id, approved=True,
                                      snapshot={}, observed_at=self.now))
            for provider in ("trakt", "mdblist"):
                db.add(CloudBaseline(user_id=self.user.id, provider=provider, approved=True, snapshot={}, observed_at=self.now))
            await db.commit()

    async def asyncTearDown(self):
        from sqlalchemy import delete
        from models import User, Media
        async with self.factory() as db:
            await db.execute(delete(User).where(User.id == self.user.id))
            await db.execute(delete(Media).where(Media.id.in_([m.id for m in self.media])))
            await db.commit()
        await self.engine.dispose()

    async def test_independent_ratings_have_independent_source_exclusions(self):
        state, cycle = Reconciliation(self.user.id), PullCycleState(self.user.id)
        state.ratings = {(self.media[0].id, None): [Observation("trakt", 8, self.now)],
                         (self.media[1].id, None): [Observation("mdblist", 9, self.now)]}
        async with self.factory() as db:
            await _ratings(db, state, cycle)
            self.assertEqual(cycle.rating_sources[(self.media[0].id, None)], {"trakt"})
            self.assertEqual(cycle.rating_sources[(self.media[1].id, None)], {"mdblist"})

    async def test_removal_survives_other_pull_only_account_and_next_unchanged_cycle(self):
        from models import Collection, CollectionFile
        from models.base import CollectionSource
        from models.streaming_library import StreamingLibraryIntent
        from sqlalchemy import select
        state, cycle = Reconciliation(self.user.id), PullCycleState(self.user.id)
        async with self.factory() as db:
            collection = Collection(user_id=self.user.id, media_id=self.media[0].id)
            db.add(collection)
            await db.flush()
            files = [CollectionFile(collection_id=collection.id, connection_id=conn.id,
                                    source=CollectionSource.stremio, source_id=f"{conn.id}:fixture") for conn in self.connections]
            db.add_all(files)
            await db.commit()
            await initialize(db, state)
            await db.delete(files[0])
            await db.commit()
            await _library(db, state, cycle)
            intent = (await db.execute(select(StreamingLibraryIntent).where(
                StreamingLibraryIntent.user_id == self.user.id))).scalar_one()
            self.assertFalse(intent.desired)
            self.assertEqual(cycle.library_source_ids_by_media[self.media[0].id], {self.connections[0].id})
            second = Reconciliation(self.user.id)
            await initialize(db, second)
            second_cycle = PullCycleState(self.user.id)
            await _library(db, second, second_cycle)
            self.assertFalse(intent.desired)
            self.assertFalse(second_cycle.library_new_ids)

    async def test_conflicts_are_created_after_collection_and_keep_local_rating(self):
        from models import Rating
        from models.tracking import SyncReview
        from sqlalchemy import select
        from core.sync_reconciliation import collect_rating
        state, cycle = Reconciliation(self.user.id), PullCycleState(self.user.id)
        async with self.factory() as db:
            row = Rating(user_id=self.user.id, media_id=self.media[0].id, rating=6, rated_at=self.now)
            db.add(row)
            await db.commit()
            token = _current.set(state)
            try:
                await collect_rating(db, self.user.id, "trakt", self.media[0].id, None, 8, None)
                await collect_rating(db, self.user.id, "mdblist", self.media[0].id, None, 9, None)
            finally:
                _current.reset(token)
            self.assertFalse((await db.execute(select(SyncReview).where(SyncReview.user_id == self.user.id))).scalars().all())
            await _ratings(db, state, cycle)
            await db.refresh(row)
            self.assertEqual(row.rating, 6)
            reviews = (await db.execute(select(SyncReview).where(SyncReview.user_id == self.user.id))).scalars().all()
            self.assertEqual(len(reviews), 1)
            self.assertEqual(len(reviews[0].payload["observations"]), 2)
            self.assertFalse(cycle.new_ratings)


    async def test_playback_uses_newest_source_and_updates_both_raw_baselines(self):
        from core.sync_reconciliation import _playback
        from models import PlaybackProgress
        from models.tracking import TrackedEntry, StreamBaseline, StreamAction
        from sqlalchemy import select
        state, cycle = Reconciliation(self.user.id, collecting=False), PullCycleState(self.user.id)
        movie = self.media[0]
        async with self.factory() as db:
            entry = TrackedEntry(user_id=self.user.id, media_id=movie.id, status="watching", progress=0,
                                 status_changed_at=self.now, status_source="local")
            db.add(entry)
            for index, conn in enumerate(self.connections):
                old = dict(content_id="fixture", content_type="movie", position=5000, duration=100000,
                           modified_at=(self.now-timedelta(minutes=1)).isoformat()+"Z")
                baseline = await db.get(StreamBaseline, conn.id)
                baseline.snapshot = {"progress": {"fixture": old}, "mappings": {"fixture": movie.tmdb_id},
                                     "records": {"progress": [old], "watched": [], "library": []}}
                row = {**old, "position": 10000*(index+1),
                       "modified_at": (self.now+timedelta(minutes=index+1)).isoformat()+"Z"}
                state.snapshots.append(dict(connection_id=conn.id, identity_version=conn.identity_version,
                    library=[], watched=[], progress=[row], tmdb_ids={"fixture": movie.tmdb_id},
                    complete=True, sync_playback=True, sync_watched=False))
            await db.commit()
            token = _current.set(state)
            try:
                await _playback(db, state, cycle, set())
            finally:
                _current.reset(token)
            await db.refresh(entry)
            self.assertEqual(entry.status_source, f"stremio:{self.connections[1].id}")
            progress = (await db.execute(select(PlaybackProgress).where(
                PlaybackProgress.user_id == self.user.id, PlaybackProgress.media_id == movie.id))).scalar_one()
            self.assertEqual(progress.progress_seconds, 20)
            self.assertEqual((await db.get(StreamBaseline, self.connections[0].id)).snapshot["progress"]["fixture"]["position"], 10000)
            self.assertFalse((await db.execute(select(StreamAction).where(StreamAction.user_id == self.user.id))).scalars().all())


    async def test_watch_removal_conflicts_with_new_cloud_play_without_erasing_local_history(self):
        from core.sync_reconciliation import _watches, _source, collect_watch
        from models import WatchEvent
        from models.tracking import StreamBaseline, SyncReview
        from sqlalchemy import select
        state, cycle = Reconciliation(self.user.id), PullCycleState(self.user.id)
        movie = self.media[0]
        async with self.factory() as db:
            original = WatchEvent(user_id=self.user.id, media_id=movie.id, completed=True, watched_at=self.now)
            db.add(original)
            await db.commit()
            await initialize(db, state)
            baseline = await db.get(StreamBaseline, self.connections[0].id)
            baseline.snapshot = {"mappings": {"fixture": movie.tmdb_id}, "records": {"watched": [
                dict(content_id="fixture", content_type="movie", watched_at=self.now.isoformat()+"Z")]}}
            state.snapshots = [dict(connection_id=self.connections[0].id, sync_watched=True, complete=True,
                                    watched=[], tmdb_ids={})]
            token, source = _current.set(state), _source.set("trakt")
            try:
                collect_watch(movie.id, self.now+timedelta(minutes=5))
                new = WatchEvent(user_id=self.user.id, media_id=movie.id, completed=True,
                                 watched_at=self.now+timedelta(minutes=5))
                add_watch_event(db, new)
                await db.commit()
                state.collecting = False
                await _watches(db, state, cycle)
            finally:
                _source.reset(source)
                _current.reset(token)
            rows = (await db.execute(select(WatchEvent).where(WatchEvent.user_id == self.user.id))).scalars().all()
            self.assertEqual([row.id for row in rows], [original.id])
            review = (await db.execute(select(SyncReview).where(SyncReview.user_id == self.user.id))).scalar_one()
            self.assertEqual(review.kind, "watch_conflict")
            self.assertFalse(cycle.removed_watch_exclusions)

    async def test_unchanged_cloud_history_cannot_restore_removed_watch(self):
        from core.sync_reconciliation import _watches, _source, collect_watch
        from models import WatchEvent
        from models.tracking import CloudBaseline
        from sqlalchemy import select
        state, cycle = Reconciliation(self.user.id), PullCycleState(self.user.id)
        movie = self.media[0]
        async with self.factory() as db:
            baseline = (await db.execute(select(CloudBaseline).where(CloudBaseline.user_id == self.user.id,
                CloudBaseline.provider == "trakt"))).scalar_one()
            baseline.snapshot = {"watch_observations": {str(movie.id): self.now.isoformat()}}
            await db.commit()
            await initialize(db, state)
            token, source = _current.set(state), _source.set("trakt")
            try:
                collect_watch(movie.id, self.now)
                add_watch_event(db, WatchEvent(user_id=self.user.id, media_id=movie.id, completed=True, watched_at=self.now))
                await db.commit()
                state.collecting = False
                await _watches(db, state, cycle)
            finally:
                _source.reset(source)
                _current.reset(token)
            self.assertFalse((await db.execute(select(WatchEvent.id).where(WatchEvent.user_id == self.user.id))).all())

    async def test_rejected_import_does_not_delete_concurrent_manual_watch(self):
        from core.sync_reconciliation import _watches, _source, collect_watch
        from models import WatchEvent
        from sqlalchemy import select
        state, cycle = Reconciliation(self.user.id), PullCycleState(self.user.id)
        movie = self.media[0]
        async with self.factory() as db:
            await initialize(db, state)
            token, source = _current.set(state), _source.set("trakt")
            try:
                collect_watch(movie.id, self.now)
                add_watch_event(db, WatchEvent(user_id=self.user.id, media_id=movie.id, completed=True, watched_at=self.now))
                await db.commit()
                _source.reset(source)
                source = _source.set("")
                manual = WatchEvent(user_id=self.user.id, media_id=movie.id, completed=True)
                db.add(manual)
                await db.commit()
                state.protected_fields[movie.id] = {"status"}
                state.collecting = False
                await _watches(db, state, cycle)
            finally:
                _source.reset(source)
                _current.reset(token)
            self.assertEqual((await db.execute(select(WatchEvent.id).where(WatchEvent.user_id == self.user.id))).scalars().all(), [manual.id])

    async def test_arvio_and_stream_progress_resolve_in_one_comparison(self):
        from core.sync_reconciliation import _playback
        from models import PlaybackProgress
        from models.tracking import TrackedEntry
        from sqlalchemy import select
        state, cycle = Reconciliation(self.user.id, collecting=False), PullCycleState(self.user.id)
        movie, conn = self.media[0], self.connections[0]
        async with self.factory() as db:
            arvio = self.connections[1]
            arvio.type = "arvio"
            db.add(TrackedEntry(user_id=self.user.id, media_id=movie.id, status="watching",
                               status_changed_at=self.now, status_source="local"))
            state.raw_progress[movie.id] = [Observation(f"connection:{arvio.id}", (30, 30), self.now+timedelta(minutes=2))]
            row = dict(content_id="fixture", content_type="movie", position=20000, duration=100000,
                       modified_at=(self.now+timedelta(minutes=1)).isoformat()+"Z")
            state.snapshots = [dict(connection_id=conn.id, library=[], watched=[], progress=[row],
                tmdb_ids={"fixture": movie.tmdb_id}, complete=True, sync_playback=True, sync_watched=False)]
            await db.commit()
            token = _current.set(state)
            try:
                await _playback(db, state, cycle, set())
            finally:
                _current.reset(token)
            progress = (await db.execute(select(PlaybackProgress).where(PlaybackProgress.user_id == self.user.id))).scalar_one()
            self.assertEqual(progress.progress_seconds, 30)
            self.assertEqual(state.playback[movie.id].sources, frozenset({f"connection:{arvio.id}"}))


    async def test_full_finalize_discards_raw_fanout_for_a_watch_conflict(self):
        from core.sync_reconciliation import finalize, _source, collect_watch
        from models import WatchEvent
        from models.tracking import StreamBaseline, TrackedEntry
        from sqlalchemy import select
        state, cycle = Reconciliation(self.user.id), PullCycleState(self.user.id)
        movie, conn = self.media[0], self.connections[0]
        async with self.factory() as db:
            original = WatchEvent(user_id=self.user.id, media_id=movie.id, completed=True, watched_at=self.now)
            db.add(original)
            db.add(TrackedEntry(user_id=self.user.id, media_id=movie.id, status="completed", progress=1,
                               status_source="import", status_changed_at=self.now))
            baseline = await db.get(StreamBaseline, conn.id)
            baseline.snapshot = {"mappings": {"fixture": movie.tmdb_id}, "records": {"watched": [
                dict(content_id="fixture", content_type="movie", watched_at=self.now.isoformat()+"Z")]}}
            await db.commit()
            await initialize(db, state)
            state.snapshots = [dict(connection_id=conn.id, library=[], progress=[], sync_playback=False,
                sync_watched=True, complete=True, watched=[], tmdb_ids={})]
            token, source = _current.set(state), _source.set("trakt")
            try:
                collect_watch(movie.id, self.now+timedelta(minutes=5))
                add_watch_event(db, WatchEvent(user_id=self.user.id, media_id=movie.id, completed=True,
                                 watched_at=self.now+timedelta(minutes=5)))
                await db.commit()
                cycle.new_watched_ids.add(movie.id)
                await finalize(db, state, cycle)
            finally:
                _source.reset(source)
                _current.reset(token)
            self.assertFalse(cycle.new_watched_ids)
            self.assertFalse(cycle.removed_watch_exclusions)
            self.assertEqual((await db.execute(select(WatchEvent.id).where(WatchEvent.user_id == self.user.id))).scalars().all(), [original.id])

    async def test_failed_finalize_rolls_back_all_category_decisions(self):
        from core.sync_reconciliation import finalize, _source
        from models import Rating, WatchEvent
        from sqlalchemy import select
        state, cycle = Reconciliation(self.user.id), PullCycleState(self.user.id)
        async with self.factory() as db:
            await initialize(db, state)
            state.ratings[(self.media[0].id, None)] = [Observation("trakt", 8, self.now)]
            with patch("core.sync_reconciliation._library", side_effect=RuntimeError("test failure")):
                token = _current.set(state)
                source = _source.set("trakt")
                try:
                    add_watch_event(db, WatchEvent(user_id=self.user.id, media_id=self.media[0].id,
                        completed=True, watched_at=self.now))
                    with self.assertRaises(RuntimeError):
                        await finalize(db, state, cycle)
                finally:
                    _source.reset(source)
                    _current.reset(token)
            await db.rollback()
            self.assertFalse((await db.execute(select(Rating.id).where(Rating.user_id == self.user.id))).all())
            self.assertFalse((await db.execute(select(WatchEvent.id).where(WatchEvent.user_id == self.user.id))).all())


    async def test_inferred_watch_dates_merge_independently_of_provider_order(self):
        from core.sync_reconciliation import _watches, _watch_dates, _source
        from core.watch_dates import reconcile_inferred_watch_date
        from models import WatchEvent
        from sqlalchemy import select, delete
        movie = self.media[0]
        dates = [self.now-timedelta(days=2), self.now-timedelta(days=1)]
        async with self.factory() as db:
            for order in itertools.permutations(zip(("trakt", "mdblist"), dates)):
                await db.execute(delete(WatchEvent).where(WatchEvent.user_id == self.user.id))
                event = WatchEvent(user_id=self.user.id, media_id=movie.id, watched_at=self.now,
                                   date_inferred=True, completed=True)
                db.add(event)
                await db.commit()
                state = Reconciliation(self.user.id)
                await initialize(db, state)
                token = _current.set(state)
                try:
                    for provider, at in order:
                        source = _source.set(provider)
                        try:
                            self.assertTrue(await reconcile_inferred_watch_date(db, self.user.id, movie.id, at))
                        finally:
                            _source.reset(source)
                    self.assertEqual(event.watched_at, self.now.replace(microsecond=self.now.microsecond//1000*1000))
                    state.collecting = False
                    await _watches(db, state, PullCycleState(self.user.id))
                    await _watch_dates(db, state)
                    await db.commit()
                finally:
                    _current.reset(token)
                rows = (await db.execute(select(WatchEvent).where(WatchEvent.user_id == self.user.id))).scalars().all()
                self.assertEqual({row.watched_at for row in rows}, {at.replace(microsecond=at.microsecond//1000*1000) for at in dates})
                self.assertTrue(all(not row.date_inferred for row in rows))


    async def test_connection_added_during_cycle_runs_one_serial_followup(self):
        from core import account_sync
        from models.sync import SyncJob, SyncStatus
        from sqlalchemy import select
        calls = []
        async def provider(user_id, job_id):
            async with self.factory() as db:
                job = await db.get(SyncJob, job_id)
                calls.append(job_id)
                if len(calls) == 1:
                    await account_sync.request_automatic_pull(db, user_id)
                job.status = SyncStatus.completed
                await db.commit()
        with patch.object(account_sync, "engine", self.engine), \
             patch.object(account_sync.settings_store, "get_effective_tmdb_key", return_value="fixture"), \
             patch.object(account_sync, "_run_provider", side_effect=provider), \
             patch("core.scheduler._flush_pull_cycle"):
            async with self.factory() as db:
                job = await account_sync.queue_account_pull(db, self.user.id)
            await account_sync.run_account_pull(self.user.id, job.id)
        async with self.factory() as db:
            parents = (await db.execute(select(SyncJob).where(SyncJob.user_id == self.user.id,
                SyncJob.job_type == "pull_cycle"))).scalars().all()
            self.assertEqual(len(parents), 2)
            self.assertTrue(all(parent.status == SyncStatus.completed for parent in parents))
            self.assertEqual(len(calls), 4)


    async def test_combined_rating_resolution_queues_the_selected_local_value(self):
        from fastapi import BackgroundTasks
        from routers.tracking import resolve_event, ReviewResolution
        from models import Rating
        from models.tracking import SyncReview
        async with self.factory() as db:
            for index, action in enumerate(("confirm", "keep")):
                media_id = self.media[index].id
                event = SyncReview(user_id=self.user.id, media_id=media_id, provider="combined",
                    kind="rating_conflict", previous_score=6 if action == "keep" else None,
                    proposed_score=8, message="Fixture conflict", payload={"category": "rating", "observations": []})
                db.add(event)
                if action == "keep":
                    db.add(Rating(user_id=self.user.id, media_id=media_id, rating=6, rated_at=self.now))
                await db.commit()
                tasks = BackgroundTasks()
                await resolve_event(event.id, ReviewResolution(action=action), tasks, db, self.user)
                self.assertEqual(tasks.tasks[0].args[2], {(media_id, None): 8 if action == "confirm" else 6})
                self.assertEqual(event.state, "confirmed" if action == "confirm" else "corrected")


    async def test_rating_baselines_preserve_raw_values_across_local_precision_changes(self):
        from core.sync_reconciliation import collect_rating
        from models import Rating
        from models.tracking import CloudBaseline
        from sqlalchemy import select
        media_id = self.media[0].id
        async with self.factory() as db:
            rating = Rating(user_id=self.user.id, media_id=media_id, rating=7.2, rated_at=self.now)
            db.add(rating)
            await db.commit()
            first = Reconciliation(self.user.id)
            first.ratings[(media_id, None)] = [Observation("trakt", 7, None)]
            await _ratings(db, first, PullCycleState(self.user.id))
            baseline = (await db.execute(select(CloudBaseline).where(CloudBaseline.user_id == self.user.id,
                CloudBaseline.provider == "trakt"))).scalar_one()
            self.assertEqual(baseline.snapshot["rating_observations"][f"{media_id}:None"]["value"], 7)
            rating.rating, rating.rated_at = 8.2, datetime.utcnow()
            await db.commit()
            second, cycle = Reconciliation(self.user.id), PullCycleState(self.user.id)
            token = _current.set(second)
            try:
                await collect_rating(db, self.user.id, "trakt", media_id, None, 7, None)
                await _ratings(db, second, cycle)
            finally:
                _current.reset(token)
            self.assertEqual(rating.rating, 8.2)
            self.assertFalse(cycle.new_ratings)


    async def test_staged_history_is_invisible_then_deduplicated_at_atomic_commit(self):
        from core.sync_reconciliation import _source, finalize
        from models import WatchEvent
        from sqlalchemy import select
        state, cycle = Reconciliation(self.user.id), PullCycleState(self.user.id)
        async with self.factory() as db:
            await initialize(db, state)
            token = _current.set(state)
            try:
                for provider in ("trakt", "mdblist"):
                    source = _source.set(provider)
                    try:
                        add_watch_event(db, WatchEvent(user_id=self.user.id, media_id=self.media[0].id,
                            completed=True, watched_at=self.now))
                        await db.commit()
                    finally:
                        _source.reset(source)
                async with self.factory() as observer:
                    self.assertFalse((await observer.execute(select(WatchEvent.id).where(
                        WatchEvent.user_id == self.user.id))).all())
                await finalize(db, state, cycle)
                rows = (await db.execute(select(WatchEvent.id).where(WatchEvent.user_id == self.user.id))).all()
                self.assertEqual(len(rows), 1)
            finally:
                _current.reset(token)

    async def test_cancelled_cycle_does_not_persist_staged_history(self):
        from core import account_sync
        from core.sync_reconciliation import _source
        from models import WatchEvent
        from models.sync import SyncJob, SyncStatus
        from sqlalchemy import select
        async def provider(user_id, job_id):
            async with self.factory() as db:
                source = _source.set("trakt")
                try:
                    add_watch_event(db, WatchEvent(user_id=user_id, media_id=self.media[0].id,
                        completed=True, watched_at=self.now))
                    await db.commit()
                finally:
                    _source.reset(source)
                parents = (await db.execute(select(SyncJob).where(SyncJob.user_id == user_id,
                    SyncJob.job_type == "pull_cycle"))).scalars().all()
                await account_sync.request_cycle_cancel(db, parents[0])
        with (patch.object(account_sync, "engine", self.engine),
              patch.object(account_sync.settings_store, "get_effective_tmdb_key", return_value="fixture"),
              patch.object(account_sync, "_run_provider", side_effect=provider),
              patch("core.scheduler._flush_pull_cycle") as flush):
            async with self.factory() as db:
                job = await account_sync.queue_account_pull(db, self.user.id)
            await account_sync.run_account_pull(self.user.id, job.id)
            flush.assert_not_called()
        async with self.factory() as db:
            self.assertFalse((await db.execute(select(WatchEvent.id).where(WatchEvent.user_id == self.user.id))).all())
            self.assertEqual((await db.get(SyncJob, job.id)).status, SyncStatus.cancelled)

    async def test_movie_completion_does_not_generate_its_own_removal_conflict(self):
        from core.sync_reconciliation import finalize
        from models import WatchEvent, PlaybackProgress
        from models.tracking import StreamBaseline, TrackedEntry, SyncReview
        from sqlalchemy import select
        state, cycle = Reconciliation(self.user.id), PullCycleState(self.user.id)
        movie, conn = self.media[0], self.connections[0]
        old = dict(content_id="fixture", content_type="movie", position=50000, duration=100000,
                   modified_at=self.now.isoformat()+"Z")
        watched = dict(content_id="fixture", content_type="movie", watched_at=(self.now+timedelta(minutes=5)).isoformat()+"Z")
        async with self.factory() as db:
            db.add(TrackedEntry(user_id=self.user.id, media_id=movie.id, status="watching",
                               status_changed_at=self.now, status_source="local"))
            db.add(PlaybackProgress(user_id=self.user.id, media_id=movie.id,
                progress_seconds=50, progress_percent=0.5, updated_at=self.now))
            baseline = await db.get(StreamBaseline, conn.id)
            baseline.snapshot = {"progress": {"fixture": old}, "mappings": {"fixture": movie.tmdb_id},
                "records": {"progress": [old], "watched": [], "library": []}}
            await db.commit()
            await initialize(db, state)
            state.snapshots = [dict(connection_id=conn.id, identity_version=conn.identity_version,
                library=[], watched=[watched], progress=[], tmdb_ids={"fixture": movie.tmdb_id},
                complete=True, sync_playback=True, sync_watched=True)]
            from core.sync_reconciliation import _source
            token, source = _current.set(state), _source.set(f"connection:{conn.id}")
            try:
                add_watch_event(db, WatchEvent(user_id=self.user.id, media_id=movie.id,
                    completed=True, watched_at=self.now+timedelta(minutes=5)))
                await finalize(db, state, cycle)
            finally:
                _source.reset(source)
                _current.reset(token)
            entry = (await db.execute(select(TrackedEntry).where(TrackedEntry.user_id == self.user.id))).scalar_one()
            self.assertEqual(entry.status, "completed")
            self.assertFalse((await db.execute(select(PlaybackProgress.id).where(PlaybackProgress.user_id == self.user.id))).all())
            self.assertFalse(state.playback[movie.id].conflict)
            self.assertFalse((await db.execute(select(SyncReview.id).where(SyncReview.user_id == self.user.id,
                SyncReview.state == "pending", SyncReview.kind.in_(["conflict", "playback_removed"])))).all())

    async def test_series_conflict_filters_episode_history_by_tracking_root(self):
        from core.sync_reconciliation import _history, Decision
        from models import Media, Show
        from models.base import MediaType
        async with self.factory() as db:
            show = Show(tmdb_id=71001, title="Series fixture")
            db.add(show)
            await db.flush()
            episode = Media(show_id=show.id, media_type=MediaType.episode, title="Episode", season_number=1, episode_number=1)
            root = Media(tmdb_id=show.tmdb_id, media_type=MediaType.series, title=show.title)
            db.add_all([root, episode])
            await db.flush()
            state = Reconciliation(self.user.id, collecting=False)
            state.histories = [dict(provider="trakt", new_media_ids={episode.id}, initial=False)]
            state.playback[root.id] = Decision(("watching", None, None, None, None), conflict=True)
            with patch("core.cloud_history_reconciliation.reconcile_cloud_watch_events") as reconcile:
                await _history(db, state, PullCycleState(self.user.id), set())
                reconcile.assert_not_called()
            await db.rollback()


    async def test_completed_episode_clears_covered_resume_but_keeps_later_episode(self):
        from core.sync_reconciliation import _clear_completed_playback, Decision
        from models import Media, Show, PlaybackProgress
        from models.base import MediaType
        from sqlalchemy import select
        async with self.factory() as db:
            show = Show(tmdb_id=71002, title="Resume fixture")
            db.add(show)
            await db.flush()
            root = Media(tmdb_id=show.tmdb_id, media_type=MediaType.series, title=show.title)
            episodes = [Media(show_id=show.id, media_type=MediaType.episode, title="Episode",
                season_number=1, episode_number=number) for number in (3, 4)]
            db.add_all([root, *episodes])
            await db.flush()
            for episode in episodes:
                db.add(PlaybackProgress(user_id=self.user.id, media_id=episode.id,
                    progress_seconds=50, progress_percent=0.5, updated_at=self.now))
            await db.flush()
            state = Reconciliation(self.user.id)
            decision = Decision(("watching", 1, 3, None, None), frozenset({"trakt"}), self.now)
            await _clear_completed_playback(db, state, root.id, decision)
            remaining = (await db.execute(select(PlaybackProgress.media_id).where(
                PlaybackProgress.user_id == self.user.id))).scalars().all()
            self.assertEqual(remaining, [episodes[1].id])
            await db.rollback()

    async def test_progress_only_completion_is_not_a_dismissal(self):
        from core.sync_reconciliation import _playback
        from models.tracking import StreamBaseline, TrackedEntry
        from sqlalchemy import select
        movie, conn = self.media[0], self.connections[0]
        old = dict(content_id="fixture", content_type="movie", position=50000, duration=100000,
                   modified_at=self.now.isoformat()+"Z")
        completed = {**old, "position": 95000, "modified_at": (self.now+timedelta(minutes=5)).isoformat()+"Z"}
        state, cycle = Reconciliation(self.user.id, collecting=False), PullCycleState(self.user.id)
        state.snapshots = [dict(connection_id=conn.id, identity_version=conn.identity_version,
            library=[], watched=[], progress=[completed], tmdb_ids={"fixture": movie.tmdb_id},
            complete=True, sync_playback=True, sync_watched=False)]
        async with self.factory() as db:
            db.add(TrackedEntry(user_id=self.user.id, media_id=movie.id, status="watching",
                status_changed_at=self.now, status_source="local"))
            baseline = await db.get(StreamBaseline, conn.id)
            baseline.snapshot = {"progress": {"fixture": old}, "mappings": {"fixture": movie.tmdb_id},
                "records": {"progress": [old], "watched": [], "library": []}}
            await db.commit()
            token = _current.set(state)
            try:
                await _playback(db, state, cycle, set())
            finally:
                _current.reset(token)
            self.assertEqual(state.playback[movie.id].value[0], "completed")
            entry = (await db.execute(select(TrackedEntry).where(TrackedEntry.user_id == self.user.id))).scalar_one()
            self.assertEqual(entry.status, "completed")
