import assert from 'node:assert/strict';
import test from 'node:test';
import {importRecovery} from '../src/lib/import-recovery.ts';

const checkpoint = () => ({revision: 4, expiresAt: new Date(Date.now() + 60000).toISOString(), step: 3,
  patches: [{itemId: 'series', changes: {latest_episode: 8}}],
  matches: {ids: [], finished: [], selected: null, overview: false},
  progress: {ids: ['series'], finished: [], selected: 'series', overview: false}});
function storage() {const rows = new Map(); return {getItem: k => rows.get(k), setItem: (k,v) => rows.set(k,v), removeItem: k => rows.delete(k)};}
test('reload restores the exact decision and pending edits only for the same viewer and import', () => {
  const store = storage(); const mine = importRecovery(store, 'owner'); const saved = checkpoint();
  mine.save('draft', saved);
  assert.deepEqual(mine.read('draft'), saved);
  assert.equal(importRecovery(store, 'someone-else').read('draft'), null);
  assert.equal(mine.read('other-draft'), null);
  mine.remove('draft'); assert.equal(mine.read('draft'), null);
});
test('expired drafts and unavailable browser storage do not block review', () => {
  const mine = importRecovery(storage(), 'owner');
  mine.save('expired', {...checkpoint(), expiresAt: '2020-01-01T00:00:00Z'});
  assert.equal(mine.read('expired'), null);
  const unavailable = importRecovery({getItem() {throw Error();}, setItem() {throw Error();}, removeItem() {throw Error();}}, 'owner');
  unavailable.save('draft', checkpoint()); assert.equal(unavailable.read('draft'), null); unavailable.remove('draft');
});
