import test from 'node:test';
import assert from 'node:assert/strict';
import { healthJobMessage, healthSummary, isHealthJobActive } from '../src/lib/data-health.ts';

test('health summary counts checks rather than overlapping missing records', () => {
  assert.deepEqual(healthSummary([
    { count: 20, severity: 'warning' }, { count: 10, severity: 'warning' },
    { count: 2, severity: 'info' }, { count: 0, severity: 'warning' },
  ]), { attention: 2, information: 1, clear: 1 });
});
test('only queued and running health jobs disable repair controls', () => {
  for (const status of ['pending', 'running']) assert.equal(isHealthJobActive({ status }), true);
  for (const status of ['completed', 'failed', 'interrupted']) assert.equal(isHealthJobActive({ status }), false);
  assert.equal(isHealthJobActive(null), false);
});
test('completed batches surface partial provider failures', () => {
  assert.match(healthJobMessage({ status: 'completed', results: { Metadata: { failed: 2 } } }), /2 failed requests/);
  assert.equal(healthJobMessage({ status: 'failed', error: 'Retry is safe.' }), 'Retry is safe.');
  assert.equal(healthJobMessage({ status: 'running', step: 'Country metadata' }), 'Country metadata');
});
