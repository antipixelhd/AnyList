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

test('pending loads are bounded, shared through eviction, and intent promotes queued work',async()=>{
  const previous=globalThis.Image,started=[],finish=[];
  globalThis.Image=class {set src(value){started.push(value);}decode(){return new Promise(resolve=>finish.push(resolve));}};
  try{
    const cache=new DecodedArtwork(1),a=cache.warm('a'),b=cache.warm('b'),c=cache.warm('c'),d=cache.warm('d',true);
    assert.deepEqual(started,['a','b']);assert.strictEqual(cache.warm('a'),a);
    finish.shift()();await a;assert.deepEqual(started,['a','b','d']);
    finish.shift()();await b;assert.deepEqual(started,['a','b','d','c']);
    finish.shift()();await d;finish.shift()();await c;
    assert.equal(cache.get('a'),undefined);assert(cache.get('c'));
  }finally{globalThis.Image=previous;}
});

test('small backgrounds exceed twelve entries without expanding the decoded pixel budget',async()=>{
  const previous=globalThis.Image;
  globalThis.Image=class {naturalWidth=640;naturalHeight=360;decode(){return Promise.resolve();}};
  try{
    const cache=new DecodedArtwork();
    for(let i=0;i<32;i++)await cache.warm(String(i));
    for(let i=0;i<32;i++)assert(cache.get(String(i)));
    const bounded=new DecodedArtwork(32,640*360*2);
    await bounded.warm('a');await bounded.warm('b');await bounded.warm('c');
    assert.equal(bounded.get('a'),undefined);assert(bounded.get('b'));assert(bounded.get('c'));
  }finally{globalThis.Image=previous;}
});

test('clearing a viewer resolves queued loads and prevents late publication',async()=>{
  const previous=globalThis.Image,finish=[];
  globalThis.Image=class {decode(){return new Promise(resolve=>finish.push(resolve));}};
  try{
    const cache=new DecodedArtwork(),a=cache.warm('a'),b=cache.warm('b'),queued=cache.warm('c');
    cache.clear();await queued;finish.forEach(resolve=>resolve());await Promise.all([a,b]);
    assert.equal(cache.get('a'),undefined);assert.equal(cache.get('b'),undefined);
  }finally{globalThis.Image=previous;}
});
