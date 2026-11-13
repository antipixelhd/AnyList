import assert from 'node:assert/strict';
import test from 'node:test';
import {browseReturnTarget,createReturnViewCache,returnViewKey,returnViewLifetime,isReturnViewTrackingWrite} from '../src/lib/return-view-cache.ts';

test('reading a return view does not extend the five minutes away',()=>{
  let now=10;
  const views=createReturnViewCache(()=>now);
  views.set('/browse','snapshot');
  now+=returnViewLifetime-1;
  assert.equal(views.get('/browse').html,'snapshot');
  now++;
  assert.equal(views.get('/browse'),undefined);
});

test('retention is bounded by bytes and keeps recently used views',()=>{
  const views=createReturnViewCache(()=>0,8,3);
  views.set('/old','one');
  views.set('/recent','two');
  views.get('/old');
  views.set('/new','three');
  assert.equal(views.get('/recent'),undefined);
  assert.equal(views.get('/old').html,'one');
  assert.equal(views.get('/new').html,'three');
  views.set('/large','123456789');
  assert.equal(views.get('/large'),undefined);
});

test('tracking writes keep the view available for reconciliation, and a new identity clears it',()=>{
  const views=createReturnViewCache(()=>0);
  views.scope('viewer:true');
  views.set('/browse','snapshot');
  assert(isReturnViewTrackingWrite(new URL('https://example.test/api/proxy/tracking/games/entries/42')));
  assert(!isReturnViewTrackingWrite(new URL('https://example.test/api/proxy/tracking/preferences')));
  views.markDirty();
  assert.equal(views.get('/browse').dirty,true);
  views.scope('viewer:false');
  assert.equal(views.get('/browse'),undefined);
});

test('return keys preserve query identity and ignore parameter order, fragments, and trailing slashes',()=>{
  const key=path=>returnViewKey(new URL(path,'https://example.test'));
  assert.equal(key('/browse/?sort=score&type=series#results'),key('/browse?type=series&sort=score'));
  assert.notEqual(key('/browse?sort=score'),key('/browse?sort=newest'));
});

test('the generic Browse link resumes the last view while explicit Browse links keep their destination',()=>{
  const url=path=>new URL(path,'https://example.test'),last=url('/browse/games?sort=score').href;
  assert.equal(browseReturnTarget(url('/browse'),last).href,last);
  assert.equal(browseReturnTarget(url('/browse?type=series'),last),undefined);
  assert.equal(browseReturnTarget(url('/browse/games'),last),undefined);
  assert.equal(browseReturnTarget(url('/browse'),undefined),undefined);
});
