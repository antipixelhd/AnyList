import assert from 'node:assert/strict';
import test from 'node:test';
import {mutationFetch} from '../src/lib/mutation-fetch.ts';

test('attempts writes even when browser connectivity hint is offline', async () => {
  let calls = 0, successes = 0, failures = 0;
  const wrapped = mutationFetch(async () => { calls++; return new Response(); },
    'https://beta.example/home', () => successes++, () => failures++);
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  Object.defineProperty(globalThis, 'navigator', {configurable:true, value:{onLine:false}});
  try {
    assert.equal((await wrapped('/api/proxy/tracking/1', {method:'PATCH'})).status, 200);
    assert.deepEqual([calls, successes, failures], [1, 1, 0]);
  } finally {
    if (descriptor) Object.defineProperty(globalThis, 'navigator', descriptor);
    else delete globalThis.navigator;
  }
});

test('reports genuine mutation network failures without affecting read or abort errors', async () => {
  let failures = 0;
  const fail = new TypeError('network failed');
  const wrapped = mutationFetch(async () => { throw fail; }, 'https://beta.example', () => {}, () => failures++);
  await assert.rejects(wrapped('/api/proxy/tracking/1', {method:'PATCH'}), fail);
  await assert.rejects(wrapped('/api/proxy/tracking/1'), fail);
  await assert.rejects(wrapped('https://other.example', {method:'POST'}), fail);
  assert.equal(failures, 1);
  const aborted = mutationFetch(async () => { throw new DOMException('cancelled', 'AbortError'); },
    'https://beta.example', () => {}, () => failures++);
  await assert.rejects(aborted('/api/proxy/tracking/1', {method:'PATCH'}));
  assert.equal(failures, 1);
});

test('HTTP errors are left for the calling control and are not reported as offline', async () => {
  let calls = 0;
  const wrapped = mutationFetch(async () => new Response('', {status:403}),
    'https://beta.example', () => calls++, () => calls++);
  assert.equal((await wrapped(new Request('https://beta.example/api/proxy/tracking/1', {method:'POST'}))).status, 403);
  assert.equal(calls, 0);
});
