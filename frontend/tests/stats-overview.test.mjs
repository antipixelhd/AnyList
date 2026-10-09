import assert from 'node:assert/strict';
import test from 'node:test';
import { headlineValues, distributionRows, metricValue, watchYearRows } from '../src/lib/stats-overview-data.ts';
import { createOverviewLoader } from '../src/lib/stats-overview-request.ts';

test('incomplete runtime is a lower bound and unavailable means remain absent', () => {
  const data = { media_type: 'all', totals: { listed_titles: 2, watched_titles: 2, episode_plays: 3, distinct_episodes: 1, watch_minutes: 1440, watch_days: 1, planned_minutes: 0, planned_days: 0, mean_score: null, standard_deviation: null, rated_titles: 0 }, coverage: { runtime_known_plays: 1, runtime_estimated_plays: 0, runtime_missing_plays: 2, planned_unknown_titles: 1 } };
  const values = headlineValues(data);
  assert.equal(values[2].value, '≥ 1.00');
  assert.equal(values[3].value, '—');
  assert.equal(values[4].value, '—');
  assert.equal(metricValue({ minutes: 120, mean_score: null }, 'hours'), 2);
  assert.equal(metricValue({ mean_score: null }, 'mean_score'), null);
});

test('country percentages use fractional title shares while exposing overlapping counts', () => {
  const rows = distributionRows({ countries: [{ key: 'US', titles: 2, share: 1.5 }, { key: 'GB', titles: 1, share: .5 }] }, 'countries');
  assert.deepEqual(rows.map(row => [row.titles, row.percent]), [[2, 75], [1, 25]]);
});

test('watch-year range leaves lifetime rows untouched', () => {
  const rows = [2024, 2025, 2026].map(year => ({ key: String(year), titles: year === 2025 ? 0 : 1, mean_score: null }));
  assert.equal(watchYearRows(rows, '2025', '2026').length, 2);
  assert.equal(rows.length, 3);
});

test('latest media request wins, privacy denial clears data, and navigation cancels callbacks', async t => {
  const requests = [], rendered = [], errors = [];
  t.mock.method(globalThis, 'fetch', (url, options) => new Promise(resolve => requests.push({ url, options, resolve })));
  const loader = createOverviewLoader('viewer name', { render: value => rendered.push(value), onError: (...args) => errors.push(args), onBusy() {} });
  const first = loader.load('movie'), second = loader.load('series');
  assert.equal(requests[0].options.signal.aborted, true);
  assert.equal(requests[1].options.cache, 'no-store');
  assert.match(requests[1].url, /viewer%20name\/stats\/overview\?media_type=series$/);
  requests[1].resolve(Response.json({ generation: 2 })); await second;
  requests[0].resolve(Response.json({ generation: 1 })); await first;
  assert.deepEqual(rendered, [{ generation: 2 }]);
  const denied = loader.load('all'); requests[2].resolve(new Response(null, { status: 403 })); await denied;
  assert.deepEqual(errors, [['This profile is private.', true]]);
  const redirected = loader.load('all'); requests[3].resolve({ redirected: true }); await redirected;
  assert.deepEqual(errors[1], ['Sign in to view these statistics.', true]);
  const pending = loader.load('all'); loader.stop(); requests[4].resolve(Response.json({ generation: 3 })); await pending;
  assert.equal(rendered.length, 1);
});
