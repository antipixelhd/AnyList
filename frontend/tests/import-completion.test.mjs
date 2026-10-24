import assert from 'node:assert/strict';
import test from 'node:test';
import {needsImportFinishDate, importFinishDateSuggestion} from '../src/lib/import-completion.ts';

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
