import assert from 'node:assert/strict';
import test from 'node:test';
import { rankedStudios } from '../src/lib/stats-studios-data.ts';
import { genreSort } from '../src/lib/stats-genres-data.ts';
test('studio cards select eighteen by each metric without mutating the snapshot', () => {
  const rows = Array.from({length: 30}, (_, i) => ({key: String(i), label: `Studio ${i}`, titles: i, minutes: 30-i, mean_score: i === 29 ? null : i/3}));
  assert.equal(rankedStudios(rows, 'count').length, 18);
  assert.equal(rankedStudios(rows, 'count')[0].titles, 29);
  assert.equal(rankedStudios(rows, 'time')[0].titles, 0);
  assert.equal(rankedStudios(rows, 'mean_score')[0].titles, 28);
  assert.equal(rows[0].titles, 0);
  assert.equal(genreSort('invalid'), 'count');
});
