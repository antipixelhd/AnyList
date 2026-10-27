import test from 'node:test';
import assert from 'node:assert/strict';
import { refreshBrowseContextBatches } from '../src/lib/browse-context.ts';

test('refreshes all titles in bounded batches, even if a batch fails', async () => {
  const requested = [];
  const applied = [];
  await refreshBrowseContextBatches(Array.from({length: 165}, (_, i) => i), async batch => {
    requested.push(batch);
    if (batch[0] === 80) throw new Error('temporary failure');
    return batch;
  }, rows => applied.push(...rows), () => true);
  assert.deepEqual(requested.map(batch => batch.length), [80, 80, 5]);
  assert.deepEqual(applied, [...requested[0], ...requested[2]]);
});

test('does not apply stale responses or start further batches', async () => {
  let current = true;
  let requests = 0;
  const applied = [];
  await refreshBrowseContextBatches(Array.from({length: 100}, (_, i) => i), async batch => {
    requests++;
    current = false;
    return batch;
  }, rows => applied.push(...rows), () => current);
  assert.equal(requests, 1);
  assert.deepEqual(applied, []);
});
