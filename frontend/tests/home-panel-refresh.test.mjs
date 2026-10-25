import assert from 'node:assert/strict';
import test from 'node:test';
import {createHomePanelRefresh} from '../src/lib/home-panel-refresh.ts';

function fixture(t) {
  const controller = new AbortController();
  const replacements = [];
  const parsed = {sidebar: 'fresh'};
  let busy = false, calls = 0, finish, revision=0;
  const originalParser = globalThis.DOMParser;
  globalThis.DOMParser = class { parseFromString(html, type) {
    assert.equal(type, 'text/html'); assert.equal(html, '<aside>fresh</aside>'); return parsed;
  }};
  t.after(() => { globalThis.DOMParser = originalParser; });
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls++;
    assert.equal(url, '/home'); assert.equal(options.cache, 'no-store');
    assert.equal(options.signal, controller.signal);
    await new Promise(resolve => { finish = resolve; });
    return new Response('<aside>fresh</aside>', {headers: {'content-type':'text/html'}});
  });
  const refresh = createHomePanelRefresh({signal:controller.signal, isBusy:()=>busy, getRevision:()=>revision, replace:fresh=>replacements.push(fresh)});
  return {controller, replacements, parsed, refresh, setBusy:value=>{busy=value;}, advance:()=>{revision++;}, finish:()=>finish(), calls:()=>calls};
}

test('concurrent structural refreshes share one request and replace only the supplied panel', async t => {
  const f = fixture(t);
  const first = f.refresh(), second = f.refresh();
  assert.equal(f.calls(), 1);
  f.finish(); await Promise.all([first, second]);
  assert.deepEqual(f.replacements, [f.parsed]);
});

test('a departing page cannot be overwritten by a late response', async t => {
  const f = fixture(t); const pending = f.refresh();
  f.controller.abort(); f.finish(); await pending;
  assert.deepEqual(f.replacements, []);
  await f.refresh(); assert.equal(f.calls(), 1);
});

test('pending progress writes prevent both starting and applying refreshes', async t => {
  const f = fixture(t); f.setBusy(true); await f.refresh(); assert.equal(f.calls(), 0);
  f.setBusy(false); const pending = f.refresh(); f.setBusy(true); f.finish(); await pending;
  assert.deepEqual(f.replacements, []);
});

test('a write completed during a refresh invalidates its snapshot, while a later refresh still applies',async t=>{
  const f=fixture(t);const old=f.refresh();
  f.setBusy(true);f.advance();f.setBusy(false);
  f.finish();await old;
  assert.deepEqual(f.replacements,[]);
  const fresh=f.refresh();f.finish();await fresh;
  assert.deepEqual(f.replacements,[f.parsed]);
});
