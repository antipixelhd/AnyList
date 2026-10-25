import assert from 'node:assert/strict';
import test from 'node:test';
import {createSearchExpansion, shouldBroadenSearch, broadSearchParams} from '../src/lib/search-fallback.ts';
import {readQuickTitles, quickSearchRequests, createQuickSearchReader} from '../src/lib/quick-search-data.ts';

const empty = {results: [], has_more: false};
const values = () => new URLSearchParams({q: 'Specific title', media_type: 'movie', sort: 'popular', genres: '18', region: 'DE'});

test('only an exhausted healthy text search can broaden; empty discovery and explicit filters stay intact', () => {
  const input = values();
  assert.equal(shouldBroadenSearch(input, empty), true);
  assert.equal(shouldBroadenSearch(input, {...empty, partial: true}), true);
  for (const data of [{...empty, has_more: true}, {...empty, notice: 'Provider unavailable'},
    {...empty, verification_pending: true}, {results: [1]}]) assert.equal(shouldBroadenSearch(input, data), false);
  assert.equal(shouldBroadenSearch(new URLSearchParams(), empty), false);
  input.set('cursor', 'curated-cursor'); input.set('source', 'remote'); input.set('page', '6');
  const broad = broadSearchParams(input);
  assert.deepEqual([...broad], [...new URLSearchParams({q: 'Specific title', media_type: 'movie', sort: 'popular', genres: '18', region: 'DE', page: '1', unfiltered: 'true'})]);
  assert.equal(shouldBroadenSearch(broad, empty), false);
});

test('automatic browse expansion lasts through pagination but resets on every query/filter/type/layout change', () => {
  for (const [name, value] of [['q', 'New title'], ['media_type', 'series'], ['genres', '35'],
    ['region', 'US'], ['sort', 'title'], ['view', 'timeline'], ['provider', '8'], ['min_score', '8']]) {
    const state = createSearchExpansion(), input = values();
    state.activate(input);
    const next = broadSearchParams(input); next.set('page', '2'); next.set('cursor', 'next');
    assert.equal(state.forSearch(next), true);
    next.set(name, value);
    assert.equal(state.forSearch(next), false, name);
    assert.equal(state.forSearch(input), false, 'returning does not restore an old override');
  }
});

test('explicit return to curated results suppresses another automatic retry for that context', () => {
  const state = createSearchExpansion(), input = values();
  state.activate(input); state.suppress(input);
  assert.equal(state.forSearch(input), false);
  assert.equal(state.allowed(input), false);
  input.set('genres', '35');
  assert.equal(state.allowed(input), true);
});

function fixture(responder) {
  const calls = [];
  const controller = new AbortController();
  const reader = createQuickSearchReader(new AbortController().signal, async url => {
    const params = new URL(url, 'https://anylist.test').searchParams;
    const call = {type: params.get('media_type'), broad: params.get('unfiltered') === 'true', q: params.get('q')};
    calls.push(call);
    return {ok: true, json: () => responder(call)};
  });
  return {calls, controller, run: (query = 'Specific title', combined = true) =>
    readQuickTitles(quickSearchRequests(query), controller.signal, reader.read, combined)};
}

test('combined quick search expands both title scopes only after both curated scopes are empty', async () => {
  const f = fixture(({type, broad}) => ({...empty, results: broad ? [type] : []}));
  assert.deepEqual(await f.run(), {movie: ['movie'], series: ['series'], failed: 0});
  assert.deepEqual(f.calls.map(row => row.broad), [false, false, true, true]);
  assert.equal(f.calls.length, 4, 'people/studio requests are not retried');
});

test('movie/series opt-out skips the entire fallback batch while Games and shared contributors remain independent',async()=>{
  const requests=quickSearchRequests('Elden R',undefined,false,true);
  assert.deepEqual(requests.map(request=>request.kind),['game','contributors','contributors']);
  const result=await readQuickTitles(requests,new AbortController().signal,async()=>assert.fail('Disabled movie/series provider invoked'));
  assert.deepEqual(result,{movie:[],series:[],failed:0});
});

test('movie/series broadening never requests Games or changes its query or access boundary',async()=>{
  const requests=quickSearchRequests('Elden R','DE',true,true),calls=[];
  const game=requests.find(request=>request.kind==='game');
  await readQuickTitles(requests,new AbortController().signal,async(request,signal,publish)=>{
    assert.ok(['movie','series'].includes(request.kind));calls.push(request.url);publish(empty);
  });
  assert.equal(calls.length,4);
  assert.equal(game.url,'/api/proxy/tracking/games/search?q=Elden+R');
  assert.ok(calls.every(url=>new URL(url,'https://anylist.test').searchParams.get('region')==='DE'));
});

test('one curated hit prevents broadening in combined mode but separate categories expand independently', async () => {
  const f = fixture(({type, broad}) => ({...empty, results: type === 'series' || broad ? [type] : []}));
  assert.deepEqual(await f.run(), {movie: [], series: ['series'], failed: 0});
  assert.equal(f.calls.length, 2);
  assert.deepEqual(await f.run('Specific title', false), {movie: ['movie'], series: ['series'], failed: 0});
  assert.equal(f.calls.length, 3);
  // Recombining starts with the curated pair, never the expanded movie cache.
  assert.deepEqual(await f.run(), {movie: [], series: ['series'], failed: 0});
});

test('failed, pending and unscanned curated scopes are never treated as a verified negative', async () => {
  for (const reason of ['failure', 'notice', 'pending', 'has_more']) {
    const f = fixture(({type}) => {
      if (type === 'movie') return empty;
      if (reason === 'failure') throw new Error('Offline');
      return {...empty, ...(reason === 'notice' ? {notice: 'Unavailable'} :
        reason === 'pending' ? {verification_pending: true} : {has_more: true})};
    });
    await f.run();
    assert.equal(f.calls.length, 2, reason);
  }
});

test('aborting during the hidden retry cannot return stale expanded results', async () => {
  const releases = [];
  const f = fixture(({broad}) => broad ? new Promise(resolve => releases.push(resolve)) : empty);
  const pending = f.run();
  for (let attempt = 0; releases.length < 2 && attempt < 20; attempt++) await new Promise(resolve => setImmediate(resolve));
  assert.equal(releases.length, 2);
  f.controller.abort();
  const rejected = assert.rejects(pending, {name: 'AbortError'});
  releases.forEach(resolve => resolve({results: ['old query']}));
  await rejected;
});

test('new quick search terms always start curated and cannot inherit the old expansion', async () => {
  const f = fixture(({q, broad}) => ({...empty, results: q === 'Next title' || broad ? [q] : []}));
  await f.run();
  const result = await f.run('Next title');
  assert.deepEqual(result.movie, ['Next title']);
  assert.deepEqual(f.calls.slice(-2).map(row => row.broad), [false, false]);
});
