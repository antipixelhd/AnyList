import assert from 'node:assert/strict';
import test from 'node:test';
import {createImportSequence} from '../src/lib/import-sequence.ts';

test('mobile progress advances once per title and retains reviewed decisions for direct editing', () => {
  const sequence = createImportSequence();
  sequence.sync([1, 2, 3]);
  assert.equal(sequence.current(), '1');
  sequence.complete(1);
  assert.equal(sequence.current(), '2');
  sequence.visit(1);
  assert.equal(sequence.current(), '1');
  sequence.complete(1);
  assert.equal(sequence.current(), '2', 'editing a prior decision returns to the pending title');
  sequence.complete(2); sequence.complete(3);
  assert.equal(sequence.current(), null);
  assert.deepEqual(sequence.ids, ['1', '2', '3']);
});

test('title review follows authoritative pending decisions rather than its own navigation history', () => {
  const sequence = createImportSequence();
  sequence.sync(['movie', 'series', 'unknown']);
  assert.equal(sequence.current(['movie', 'series', 'unknown']), 'movie');
  sequence.complete('movie');
  assert.equal(sequence.current(['series', 'unknown']), 'series');
  sequence.visit('movie');
  assert.equal(sequence.current(['series', 'unknown']), 'movie');
  sequence.resume();
  assert.equal(sequence.current(['series', 'unknown']), 'series');
});

test('returning to a whole step shows an overview; editing one entry returns to that overview', () => {
  const sequence = createImportSequence();
  sequence.sync([1, 2]); sequence.complete(1);
  sequence.showOverview();
  assert.equal(sequence.current(), null);
  sequence.visit(1);
  assert.equal(sequence.current(), '1');
  sequence.complete(1);
  assert.equal(sequence.current(), null);
  assert.equal(sequence.overview, true);
  sequence.start();
  assert.equal(sequence.current(), '2');
});

test('rerenders append new titles without reopening completed ones; a new import resets navigation', () => {
  const sequence = createImportSequence();
  sequence.sync([1, 2]); sequence.complete(1); sequence.sync([1, 2, 3]);
  assert.deepEqual(sequence.pending(), ['2', '3']);
  sequence.reset(); sequence.sync([9]);
  assert.equal(sequence.current(), '9');
  assert.deepEqual(sequence.ids, ['9']);
});

test('changing an earlier title match makes its progress review pending again', () => {
  const sequence = createImportSequence();
  sequence.sync([1, 2]); sequence.complete(1); sequence.complete(2);
  sequence.invalidate(1);
  assert.deepEqual(sequence.pending(), ['1']);
  assert.equal(sequence.current(), '1');
});
