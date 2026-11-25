import assert from 'node:assert/strict';
import test from 'node:test';
import {browseSeasonCaption} from '../src/lib/browse-card-data.ts';

test('season captions prefer the name and display only the year', () => {
  assert.deepEqual(browseSeasonCaption({number:3, name:'The Return', date:'2026-11-25'}), {
    label:'The Return', date:'2026-11-25', year:'2026',
  });
  assert.equal(browseSeasonCaption({number:3, date:'2026-11'}).year, '2026');
  assert.equal(browseSeasonCaption({number:3, date:'2026'}).year, '2026');
  assert.deepEqual(browseSeasonCaption({number:3}), {
    label:'Season 3', date:undefined, year:undefined,
  });
  assert.equal(browseSeasonCaption(), undefined);
});
