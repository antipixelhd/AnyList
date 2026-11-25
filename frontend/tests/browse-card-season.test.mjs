import assert from 'node:assert/strict';
import test from 'node:test';
import {browseSeasonCaption} from '../src/lib/browse-card-data.ts';

test('season captions keep the provider name and format only the known date precision', () => {
  const german = {locale:'de-DE'};
  assert.deepEqual(browseSeasonCaption({number:3, name:'The Return', date:'2026-11-25'}, german), {
    label:'Season 3 · The Return', date:'2026-11-25', dateLabel:'25.11.2026',
  });
  assert.equal(browseSeasonCaption({number:3, date:'2026-11'}, german).dateLabel, 'November 2026');
  assert.equal(browseSeasonCaption({number:3, date:'2026'}, german).dateLabel, '2026');
  assert.equal(browseSeasonCaption({number:3}, german).dateLabel, '');
  assert.equal(browseSeasonCaption(undefined, german), undefined);
});
