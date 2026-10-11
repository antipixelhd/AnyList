import assert from 'node:assert/strict';
import test from 'node:test';
import { genreSort, rankedGenres, genreBrowseHref, genreTime } from '../src/lib/stats-genres-data.ts';

const row = (label, titles, mean_score, minutes) => ({ key: label, label, titles, mean_score, minutes, runtime_missing_plays: 0, browse_filters: { movie: '28,12', series: '10759' } });
test('genre ranking caps at 18, uses the chosen metric and leaves snapshot rows intact', () => {
  const rows = Array.from({ length: 20 }, (_, i) => row(`Genre ${i}`, i, i === 19 ? null : i / 2, 20 - i));
  assert.equal(rankedGenres(rows, 'count').length, 18);
  assert.equal(rankedGenres(rows, 'count')[0].titles, 19);
  assert.equal(rankedGenres(rows, 'mean_score')[0].titles, 18);
  assert.equal(rankedGenres(rows, 'time')[0].titles, 0);
  assert.equal(rows[0].titles, 0);
  assert.deepEqual(rankedGenres([row('B', 2, null, 0), row('A', 2, null, 0)], 'mean_score').map(r => r.label), ['A', 'B']);
  assert.equal(genreSort('invalid'), 'count');
});
test('Browse genre links preserve Series and choose Movies for combined scope', () => {
  for (const scope of ['all', 'movie', 'series']) {
    const url = new URL(genreBrowseHref(row('Action', 1, 8, 10), scope), 'https://example.test');
    assert.equal(url.searchParams.get('type'), scope === 'series' ? 'series' : 'movie');
    assert.equal(url.searchParams.get('genres'), scope === 'series' ? '10759' : '28,12');
  }
  const custom = row('Suspense', 1, 8, 10); custom.browse_filters.movie = 'name:Suspense';
  assert.equal(new URL(genreBrowseHref(custom, 'all'), 'https://example.test').searchParams.get('genres'), 'name:Suspense');
});
test('watch time preserves incomplete runtime as a lower bound', () => {
  assert.equal(genreTime({ minutes: 1500, runtime_missing_plays: 0 }), '1 day 1 hour');
  assert.equal(genreTime({ minutes: 180, runtime_missing_plays: 2 }), '≥ 3 hours');
  assert.equal(genreTime({ minutes: 0, runtime_missing_plays: 1 }), '—');
  assert.equal(genreTime({ minutes: 45, runtime_missing_plays: 0 }), '45 minutes');
});
