import assert from 'node:assert/strict';
import test from 'node:test';
import {advanceEpisode, completeSeason, initialEntry, initialWork, nextSeason, setPreviewProgress, setSeasonDate, totalProgress} from '../src/components/dev/season-editor-state.ts';

test('completion targets the earliest unfinished season, including older gaps', () => {
  const entry = initialEntry();
  assert.equal(nextSeason(entry).number, 3);
  completeSeason(nextSeason(entry), '2026-10-31');
  assert.equal(nextSeason(entry).number, 4);
  entry.seasons[0].completed = false;
  assert.equal(nextSeason(entry).number, 1);
});

test('the final episode dates only its own season and preserves older evidence', () => {
  const entry = initialEntry();
  const old = structuredClone(entry.seasons.slice(0,2));
  advanceEpisode(entry, '2026-10-31');
  assert.deepEqual(entry.seasons.slice(0,2), old);
  assert.equal(entry.seasons[2].completed, true);
  assert.equal(entry.seasons[2].date, '2026-10-31');
  assert.equal(entry.seasons[2].source, 'automatic');
  assert.equal(totalProgress(entry), 33);
  advanceEpisode(entry, '2026-10-31');
  assert.equal(entry.seasons[3].watched, 1);
  assert.equal(entry.seasons[3].date, '');
});

test('clearing a date preserves completion and progress without refilling it', () => {
  const season = initialEntry().seasons[0];
  setSeasonDate(season, '');
  completeSeason(season, '2026-10-31');
  assert.equal(season.completed, true);
  assert.equal(season.watched, season.total);
  assert.equal(season.date, '');
  assert.equal(season.source, null);
});

test('manual and inferred dates remain independent and invalid dates cannot mutate a season', () => {
  const entry = initialEntry();
  const season = entry.seasons[1];
  setSeasonDate(season, season.inferred, 'inferred');
  assert.equal(season.source, 'inferred');
  assert.equal(season.date, '2026-10-10');
  setSeasonDate(entry.seasons[4], '2026-10-25');
  assert.equal(entry.seasons[4].completed, true);
  assert.equal(entry.seasons[4].source, 'manual');
  assert.equal(nextSeason(entry).number, 3);
  const before = structuredClone(season);
  assert.throws(() => setSeasonDate(season, '2026-02-30'));
  assert.deepEqual(season, before);
});

test('progress stops when all seasons are completed', () => {
  const entry = initialEntry();
  entry.seasons.forEach(season => completeSeason(season, '2026-10-31'));
  assert.equal(totalProgress(entry), 62);
  assert.equal(nextSeason(entry), undefined);
  const before = structuredClone(entry);
  advanceEpisode(entry, '2026-11-01');
  assert.deepEqual(entry, before);
});

test('all media keep the normal start and finish dates and only series have seasons', () => {
  for (const kind of ['series','movie','game']) {
    const entry=initialWork(kind);
    assert.equal(typeof entry.start,'string');
    assert.equal(typeof entry.finish,'string');
    assert.equal(typeof entry.rewatches,'number');
    assert.equal(entry.seasons.length,kind==='series'?5:0);
  }
  assert.equal(initialWork('movie').finish,'2026-10-18');
  assert.equal(initialWork('game').status,'playing');
});

test('optional season dates never replace the normal entry dates', () => {
  const entry=initialEntry();
  entry.finish='2026-10-29';
  setSeasonDate(entry.seasons[1],entry.seasons[1].inferred,'inferred');
  setSeasonDate(entry.seasons[0],'');
  setPreviewProgress(entry,33,'2026-10-31');
  assert.equal(entry.start,'2026-09-25');
  assert.equal(entry.finish,'2026-10-29');
  assert.equal(entry.seasons[0].date,'');
  assert.equal(entry.seasons[1].date,'2026-10-10');
  assert.equal(entry.seasons[2].date,'2026-10-31');
});

test('typed progress saves dates only when crossing a season boundary', () => {
  const entry=initialEntry();
  setPreviewProgress(entry,34,'2026-10-31');
  assert.equal(entry.seasons[1].date,'');
  assert.equal(entry.seasons[2].date,'2026-10-31');
  assert.equal(entry.seasons[3].date,'');
  assert.equal(entry.seasons[3].watched,1);
  setPreviewProgress(entry,31,'2026-10-31');
  assert.equal(entry.seasons[2].completed,false);
  assert.equal(entry.seasons[2].date,'');
});
