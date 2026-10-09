import assert from 'node:assert/strict';
import test from 'node:test';
import { activityTimeline, scoreBuckets, watchTime } from '../src/lib/profile-stats-presentation.ts';

test('activity includes silent months and preserves play counts across year boundaries', () => {
  assert.deepEqual(activityTimeline([
    { month: '2026-02', movies: 2, episodes: 12 },
    { month: '2025-12', movies: 4, episodes: 0 },
  ], null), [
    { month: '2025-12', movies: 4, episodes: 0 },
    { month: '2026-01', movies: 0, episodes: 0 },
    { month: '2026-02', movies: 2, episodes: 12 },
  ]);
  assert.deepEqual(activityTimeline([], null), []);
});

test('a selected year shows twelve months, including an empty year', () => {
  const empty = activityTimeline([], 2026);
  assert.equal(empty.length, 12);
  assert.equal(empty[0].month, '2026-01');
  assert.equal(empty[11].month, '2026-12');
  assert.ok(empty.every(item => item.movies === 0 && item.episodes === 0));
  const timeline = activityTimeline([{ month: '2026-07', movies: 5, episodes: 10 }], 2026);
  assert.deepEqual(timeline[6], { month: '2026-07', movies: 5, episodes: 10 });
});

test('score ranges include every half point without changing total ratings', () => {
  const distribution = Array.from({ length: 20 }, (_, index) => ({ score: (index + 1) / 2, count: index + 1 }));
  const buckets = scoreBuckets(distribution);
  assert.equal(buckets.length, 10);
  assert.deepEqual(buckets[0], { score: 1, range: 'Above 0, up to 1', count: 3 });
  assert.deepEqual(buckets[9], { score: 10, range: 'Above 9, up to 10', count: 39 });
  assert.equal(buckets.reduce((total, item) => total + item.count, 0), distribution.reduce((total, item) => total + item.count, 0));
  assert.ok(scoreBuckets([]).every(item => item.count === 0));
});

test('season average ratings belong to the range shown in chart tooltips and tables', () => {
  const buckets = scoreBuckets([{ score: 7, count: 2 }, { score: 7.333333333, count: 3 }]);
  assert.equal(buckets[6].count, 2);
  assert.equal(buckets[7].count, 3);
  assert.equal(buckets[7].range, 'Above 7, up to 8');
});

test('watch time preserves minutes as it moves from minutes to hours to days', () => {
  assert.deepEqual(watchTime(0), { value: 0, unit: 'minutes', detail: '' });
  assert.deepEqual(watchTime(1), { value: 1, unit: 'minute', detail: '' });
  assert.deepEqual(watchTime(59), { value: 59, unit: 'minutes', detail: '' });
  assert.deepEqual(watchTime(60), { value: 1, unit: 'hour', detail: '0m' });
  assert.deepEqual(watchTime(1439), { value: 23, unit: 'hours', detail: '59m' });
  assert.deepEqual(watchTime(1440), { value: 1, unit: 'day', detail: '0h 0m' });
  assert.deepEqual(watchTime(3001), { value: 2, unit: 'days', detail: '2h 1m' });
});
