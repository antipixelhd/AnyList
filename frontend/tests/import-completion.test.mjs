import assert from 'node:assert/strict';
import test from 'node:test';
import {needsImportFinishDate, importFinishDateSuggestion, needsImportProgressReview, hasConfirmedMovieEvidence} from '../src/lib/import-completion.ts';
import {createImportSequence} from '../src/lib/import-sequence.ts';

test('a weak movie choice stays on its card until Continue; a strong remap leaves the queue', () => {
  const items = [{id:'movie', kind:'movie', requires_watch_confirmation:true, progress_reviewed:false},
    {id:'show', kind:'show'}];
  const sequence = createImportSequence();
  sequence.sync(items.map(item => item.id));
  const pending = () => sequence.pending().filter(id => !hasConfirmedMovieEvidence(items.find(item => item.id === id)));
  items[0].progress_reviewed = true;
  assert.equal(sequence.current(pending()), 'movie', 'editing a decision must not advance the card');
  sequence.complete('movie');
  assert.equal(sequence.current(pending()), 'show', 'Continue advances to the next series');
  sequence.invalidate('movie');
  items[0].requires_watch_confirmation = false;
  assert.deepEqual(pending(), ['show'], 'a remap with verified completion needs no new progress review');
});

test('evidence-confirmed movies bypass progress review; weak and unknown watches need a decision', () => {
  assert.equal(needsImportProgressReview({kind:'movie', progress_reviewed:true}), false);
  assert.equal(needsImportProgressReview({kind:'movie', progress_reviewed:false, requires_watch_confirmation:true}), true);
  assert.equal(needsImportProgressReview({kind:'movie'}), true);
});

test('series progress review still preserves partial, unknown and completed season decisions', () => {
  const item = {kind:'show', seasons:[{season_number:1, represented:8, total_released:8}]};
  assert.equal(needsImportProgressReview(item), false);
  item.seasons.push({season_number:2, represented:3, total_released:8});
  assert.equal(needsImportProgressReview(item), true);
  item.seasons[1].total_released = null;
  assert.equal(needsImportProgressReview(item), true);
});

const series = () => ({kind: 'show', outcome: {}, completion_evidence: {finish_date: null, suggested_date: '2022-05-03'}});

test('returning to Completed prompts again after confirming or skipping a date; Continue does not', () => {
  for (const answer of ['2022-05-04', null]) {
    const item = series();
    assert.equal(needsImportFinishDate(item), true);
    item.outcome.finish_date = answer;
    assert.equal(needsImportFinishDate(item), false, 'submitting the current step keeps the answer');
    for (const status of ['partial', 'skip']) {
      item.outcome.status = status;
      assert.equal(needsImportFinishDate(item, true), true, `a new Completed choice after ${status}`);
    }
    assert.equal(importFinishDateSuggestion(item), answer ?? '2022-05-03');
  }
});

test('verified completion evidence and movies never ask for a series finish date', () => {
  const item = series();
  item.completion_evidence.finish_date = '2022-06-01';
  assert.equal(needsImportFinishDate(item), false);
  assert.equal(needsImportFinishDate(item, true), false);
  item.kind = 'movie';
  item.completion_evidence.finish_date = null;
  assert.equal(needsImportFinishDate(item, true), false);
});

test('a series without any date evidence can still leave the completion date blank', () => {
  const item = {kind: 'show', outcome: {}};
  assert.equal(needsImportFinishDate(item), true);
  assert.equal(importFinishDateSuggestion(item), '');
  item.outcome.finish_date = null;
  assert.equal(needsImportFinishDate(item), false);
  assert.equal(needsImportFinishDate(item, true), true);
});
