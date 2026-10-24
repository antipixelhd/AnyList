import assert from 'node:assert/strict';
import test from 'node:test';
import {importDecisionStep} from '../src/lib/import-steps.ts';

test('an already-covered Netflix or Amazon upload goes directly to final review', () => {
  const empty = {matches: false, progress: false, ratings: false};
  assert.equal(importDecisionStep(2, 1, empty), 5);
  assert.equal(importDecisionStep(4, -1, empty), null);
});

test('auto-matched complete titles skip matching and progress but retain optional ratings', () => {
  const decisions = {matches: false, progress: false, ratings: true};
  assert.equal(importDecisionStep(2, 1, decisions), 4);
  assert.equal(importDecisionStep(5, 1, decisions), 5);
  assert.equal(importDecisionStep(4, -1, decisions), 4);
  assert.equal(importDecisionStep(3, -1, decisions), null);
});

test('Continue and Back skip empty stages while retaining real progress decisions', () => {
  const decisions = {matches: true, progress: true, ratings: false};
  assert.equal(importDecisionStep(2, 1, decisions), 2);
  assert.equal(importDecisionStep(3, 1, decisions), 3);
  assert.equal(importDecisionStep(4, 1, decisions), 5);
  assert.equal(importDecisionStep(4, -1, decisions), 3);
  assert.equal(importDecisionStep(2, -1, decisions), 2);
});
