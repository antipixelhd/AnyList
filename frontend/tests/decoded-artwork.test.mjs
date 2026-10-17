import test from 'node:test';
import assert from 'node:assert/strict';
import {DecodedArtwork} from '../src/lib/decoded-artwork.ts';

test('decoded elements survive A/B/A switches and concurrent warms share one request',async()=>{
  const previous=globalThis.Image;let created=0;
  globalThis.Image=class {constructor(){created++;}decode(){return Promise.resolve();}};
  try{
    const cache=new DecodedArtwork(2);
    const first=cache.warm('a');assert.strictEqual(cache.warm('a'),first);
    await first;const a=cache.get('a');assert(a);
    await cache.warm('b');assert(cache.get('b'));assert.strictEqual(cache.get('a'),a);
    assert.equal(created,2);
    await cache.warm('c');assert.equal(cache.get('b'),undefined);assert.strictEqual(cache.get('a'),a);
    cache.clear();assert.equal(cache.get('a'),undefined);
  }finally{globalThis.Image=previous;}
});

test('failed decode is retryable and never advertises a ready background',async()=>{
  const previous=globalThis.Image;let attempt=0;
  globalThis.Image=class {decode(){return ++attempt===1?Promise.reject(Error('offline')):Promise.resolve();}};
  try{const cache=new DecodedArtwork();await cache.warm('a');assert.equal(cache.get('a'),undefined);
    await cache.warm('a');assert(cache.get('a'));
  }finally{globalThis.Image=previous;}
});
