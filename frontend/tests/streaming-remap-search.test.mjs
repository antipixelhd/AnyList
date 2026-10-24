import assert from 'node:assert/strict';
import test from 'node:test';
import {readRemapTitles} from '../src/lib/streaming-remap-search.ts';

function fixture(responder) {
  const calls = [], controller = new AbortController();
  const request = async (path, signal) => {
    assert.equal(signal, controller.signal);
    assert.ok(path.startsWith('/tracking/catalog?'));
    const params = new URL(path, 'https://anylist.test').searchParams;
    assert.equal(params.get('scan_empty'), 'true');
    calls.push(params);
    return responder(params);
  };
  return {calls, controller, run: (seriesOnly = false) =>
    readRemapTitles('Miss Undercover', seriesOnly, controller.signal, request)};
}

test('import remapping shares combined fallback and retains localized title queries', async () => {
  const movie = {tmdb_id: 1493, title: 'Miss Congeniality'};
  const f = fixture(params => ({results: params.get('unfiltered') && params.get('media_type') === 'movie' ? [movie] : []}));
  const result = await f.run();
  assert.deepEqual(result.movie, [movie]);
  assert.equal(f.calls.length, 4);
  assert.ok(f.calls.every(params => params.get('q') === 'Miss Undercover'));
});

test('a curated hit prevents combined remap expansion; series-only remapping expands its own scope', async () => {
  const f = fixture(params => ({results: params.get('media_type') === 'movie' || params.get('unfiltered') ? [{tmdb_id: 1}] : []}));
  await f.run();
  assert.equal(f.calls.length, 2);
  f.calls.length = 0;
  const result = await f.run(true);
  assert.equal(result.series.length, 1);
  assert.equal(f.calls.length, 2);
  assert.ok(f.calls.every(params => params.get('media_type') === 'series'));
});

test('remapping never treats failed or degraded searches as verified empty', async () => {
  const f = fixture(() => {throw new Error('Offline');});
  await assert.rejects(f.run(), /unavailable/);
  assert.equal(f.calls.length, 2);
  const degraded = fixture(() => ({results: [], notice: 'Provider unavailable'}));
  await degraded.run();
  assert.equal(degraded.calls.length, 2);
});

test('closing the remap dialog during fallback cancels publication', async () => {
  const f = fixture(params => {
    if (params.get('unfiltered')) f.controller.abort();
    return {results: []};
  });
  await assert.rejects(f.run(), {name: 'AbortError'});
});
