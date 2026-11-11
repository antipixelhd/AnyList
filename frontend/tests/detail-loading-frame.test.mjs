import test from 'node:test';
import assert from 'node:assert/strict';
import {DetailHints,detailDestination,fillDetailLoading} from '../src/lib/detail-loading.ts';

test('presentation hints separate provider/canonical namespaces and movie/game IDs',()=>{
  const cache=new DetailHints();cache.scope('alice');
  const urls=['/title/7','/games/7','/discover-title?type=movie&id=7','/discover-title?type=series&id=7','/discover-game?id=7'].map(path=>new URL(path,'https://local.test'));
  urls.forEach((url,i)=>cache.set(url,{kind:'movie',title:String(i),entry:{notes:'private'}}));
  assert.equal(new Set(urls.map(url=>detailDestination(url).key)).size,5);
  urls.forEach((url,i)=>assert.deepEqual(cache.get(url),{kind:'movie',title:String(i),poster:undefined,backdrop:undefined}));
  assert.equal(cache.get(new URL('/title/8','https://local.test')),undefined);
  cache.scope('bob');urls.forEach(url=>assert.equal(cache.get(url),undefined));
});

test('hint storage is bounded and invalid native identity never becomes a destination',()=>{
  const cache=new DetailHints(),url=n=>new URL('/games/'+n,'https://local.test');
  for(let n=1;n<=45;n++)cache.set(url(n),{kind:'game',title:String(n)});
  assert.equal(cache.get(url(1)),undefined);assert.equal(cache.get(url(45)).title,'45');
  for(const path of ['/games/99999999999999999999','/discover-game?id=-1','/discover-game?id=0','/discover-title?type=other&id=7','/browse'])assert.equal(detailDestination(new URL(path,'https://local.test')),undefined);
});

test('frame renders real title as text, ignores unsafe image URLs and adapts media-specific sections',()=>{
  const nodes=new Map(['title','kind-label','series','secondary-label','poster','backdrop'].map(key=>[key,{hidden:true}]));
  const root={dataset:{},querySelector:selector=>nodes.get(selector.match(/data-loading-(.+)\]/)[1])};
  fillDetailLoading(root,{kind:'series',title:'<b>A real title</b>',poster:'javascript:alert(1)',backdrop:'data:image/svg+xml,unsafe'});
  assert.equal(nodes.get('title').textContent,'<b>A real title</b>');assert.equal(nodes.get('series').hidden,false);
  assert.equal(nodes.get('poster').src,undefined);assert.equal(nodes.get('backdrop').src,undefined);
  fillDetailLoading(root,{kind:'game',title:'Game'});assert.equal(nodes.get('series').hidden,true);assert.equal(nodes.get('secondary-label').textContent,'Play time');
});
