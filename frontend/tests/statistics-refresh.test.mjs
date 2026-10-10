import test from 'node:test';
import assert from 'node:assert/strict';
import { refreshStatistics } from '../src/lib/statistics-refresh-request.ts';

const ready = { status: 'ready', generation: 42, computed_at: '2026-10-10T12:00:00Z' };
test('refresh posts for the signed-in account and waits for publication', async () => {
  const calls = [];
  const results = [{ status: 'refreshing', generation: 1 }, ready];
  const value = await refreshStatistics(new AbortController().signal, async (url, options) => {
    calls.push([url, options.method, options.cache]);
    return Response.json(results.shift());
  }, async () => {});
  assert.deepEqual(value, ready);
  assert.deepEqual(calls, [
    ['/api/proxy/tracking/stats/refresh', 'POST', 'no-store'],
    ['/api/proxy/tracking/stats/refresh', 'GET', 'no-store'],
  ]);
});
test('calculation failure never reports the retained older snapshot as success', async () => {
  await assert.rejects(refreshStatistics(new AbortController().signal, async () => Response.json({ ...ready, status: 'error' })), /Could not refresh/);
  await assert.rejects(refreshStatistics(new AbortController().signal, async () => Response.json({ ...ready, status: 'pending' })), /data changed/);
});
test('expired authentication and navigation cancellation stop polling', async () => {
  await assert.rejects(refreshStatistics(new AbortController().signal, async () => new Response('', { status: 401 })), /Sign in again/);
  const controller = new AbortController();
  let calls = 0;
  await assert.rejects(refreshStatistics(controller.signal, async () => {
    calls++;
    return Response.json({ status: 'refreshing' });
  }, async () => { controller.abort(); }), { name: 'AbortError' });
  assert.equal(calls, 1);
});
