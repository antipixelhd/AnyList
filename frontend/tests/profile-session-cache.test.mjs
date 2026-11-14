import assert from 'node:assert/strict';
import test from 'node:test';
import {createProfileSessionCache} from '../src/lib/profile-session-cache.ts';
import {returnViewLifetime} from '../src/lib/return-view-cache.ts';

function storage(){
  const data=new Map();
  return {getItem:key=>data.get(key),setItem:(key,value)=>data.set(key,value),removeItem:key=>data.delete(key)};
}

test('profile snapshots survive reload only for the same viewer and permissions',()=>{
  const disk=storage(),now=()=>10;
  const cache=createProfileSessionCache(disk,'42:true',now);
  cache.set('/user/alex/stats',{title:'Stats',content:'ready',styles:'',checkedAt:now()});
  cache.flush();
  assert.equal(createProfileSessionCache(disk,'42:true',now).get('/user/alex/stats').content,'ready');
  assert.equal(createProfileSessionCache(disk,'42:false',now).get('/user/alex/stats'),undefined);
  assert.equal(createProfileSessionCache(disk,'43:true',now).get('/user/alex/stats'),undefined);
  cache.clear();
  assert.equal(createProfileSessionCache(disk,'42:true',now).get('/user/alex/stats'),undefined);
});

test('restoring a snapshot preserves its original age and the storage budget',()=>{
  let time=10;
  const disk=storage(),now=()=>time;
  const cache=createProfileSessionCache(disk,'42:true',now);
  for(const key of ['overview','movies','games']){
    cache.set(key,{title:key,content:'x'.repeat(750_000),styles:'',checkedAt:time});
  }
  cache.flush();
  assert.equal(cache.get('overview'),undefined);
  time+=returnViewLifetime-1;
  const restored=createProfileSessionCache(disk,'42:true',now);
  assert.equal(restored.get('games').checkedAt,10);
  time++;
  assert.equal(restored.get('games'),undefined);
});

test('old snapshots with incomplete production styles are discarded before restoring tabs',()=>{
  const disk=storage();
  disk.setItem('anylist:profile-tabs:v1',JSON.stringify({identity:'42:true',entries:[{
    key:'/user/alex/favorites',title:'Favorites',content:'populated cards',styles:'',checkedAt:10,bytes:100,
  }]}));
  const cache=createProfileSessionCache(disk,'42:true',()=>10);
  assert.equal(cache.get('/user/alex/favorites'),undefined);
  assert.equal(disk.getItem('anylist:profile-tabs:v1'),undefined);
  cache.set('/user/alex/favorites',{title:'Favorites',content:'populated cards',styles:'<style>.favorite-card{position:relative}</style>',checkedAt:10});
  cache.flush();
  assert.match(createProfileSessionCache(disk,'42:true',()=>10).get('/user/alex/favorites').styles,/favorite-card/);
});
