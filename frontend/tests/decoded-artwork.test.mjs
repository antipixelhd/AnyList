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
    assert.deepEqual(started,['a','b','d']);assert.strictEqual(cache.warm('a'),a);
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

test('opening a queued entry starts a reserved high-priority request without restarting active downloads',async()=>{
  const previous=globalThis.Image,images=[],finish=new Map();
  globalThis.Image=class {constructor(){images.push(this);}set src(value){this.url=value;}decode(){return new Promise(resolve=>finish.set(this.url,resolve));}};
  try{
    const cache=new DecodedArtwork(),a=cache.warm('a'),b=cache.warm('b'),c=cache.warm('c');
    assert.equal(images.length,2);
    assert.strictEqual(cache.warm('c',true),c);assert.equal(images.length,3);
    assert.equal(images[2].fetchPriority,'high');
    assert.strictEqual(cache.warm('a',true),a);assert.equal(images[0].fetchPriority,'high');assert.equal(images.length,3);
    finish.get('c')();await c;assert(cache.get('c'));
    finish.get('a')();finish.get('b')();await Promise.all([a,b]);
  }finally{globalThis.Image=previous;}
});

test('the same pixel budget retains thirteen 720p backgrounds but only two 4K-wide heroes',async()=>{
  const previous=globalThis.Image;let dimensions=[1280,720];
  globalThis.Image=class {naturalWidth=dimensions[0];naturalHeight=dimensions[1];decode(){return Promise.resolve();}};
  try{
    for(const [size,expected] of [[[1280,720],13],[[3840,1240],2]]){
      dimensions=size;const cache=new DecodedArtwork();
      for(let i=0;i<20;i++)await cache.warm(String(i));
      assert.equal(Array.from({length:20},(_,i)=>cache.get(String(i))).filter(Boolean).length,expected);
    }
  }finally{globalThis.Image=previous;}
});

test('opening an active preload extends its download budget without restarting or extending it repeatedly',async t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const previous=globalThis.Image;let image,removed=0,finish;
  globalThis.Image=class {constructor(){image=this;}removeAttribute(){removed++;}decode(){return new Promise(resolve=>finish=resolve);}};
  try{
    const cache=new DecodedArtwork(),load=cache.warm('slow');
    t.mock.timers.tick(7000);assert.strictEqual(cache.warm('slow',true),load);
    t.mock.timers.tick(2000);assert.equal(removed,0);assert.equal(image.fetchPriority,'high');
    cache.warm('slow',true);t.mock.timers.tick(27999);assert.equal(removed,0);
    t.mock.timers.tick(1);await load;assert.equal(removed,1);assert.equal(cache.get('slow'),undefined);
    finish();
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
