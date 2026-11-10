import test from 'node:test';
import assert from 'node:assert/strict';
import {gameListCollection,gameListSavedLimits,nextGameListLimit} from '../src/lib/game-list-progressive.ts';

const entry=(id,status='playing',extra={})=>({subject:{kind:'game',catalogue_id:id},listed:true,status,title:`Game ${String(id).padStart(4,'0')}`,genres:['Adventure'],year:'2024',...extra});
test('each section expands by thirty only within six hundred pixels of its bottom',()=>{
  assert.equal(nextGameListLimit(30,293,1501,900),30);
  assert.equal(nextGameListLimit(30,293,1500,900),60);
  assert.equal(nextGameListLimit(60,293,200,900),90);
  assert.equal(nextGameListLimit(270,293,0,900),293);
  assert.equal(nextGameListLimit(293,293,0,900),293);
});
test('filters search the complete collection independently of rendered limits',()=>{
  const entries=Array.from({length:2000},(_,i)=>entry(i+1));
  const all=gameListCollection(entries,new URLSearchParams({sort:'title'}));
  assert.equal(all.total,2000);assert.equal(all.groups.get('playing').length,2000);
  const last=gameListCollection(entries,new URLSearchParams({q:'2000',genre:'Adventure',year:'2024'}));
  assert.equal(last.total,1);assert.equal(last.groups.get('playing')[0].subject.catalogue_id,2000);
  assert.equal(last.counts.playing,2000);
});
test('group ordering, favorites and deterministic ties reuse complete canonical state',()=>{
  const entries=[entry(3,'completed',{score:8}),entry(2,'playing',{score:8,favorite:true}),entry(1,'playing',{score:8,favorite:true})];
  const result=gameListCollection(entries,new URLSearchParams({sort:'score',favorites:'true'}));
  assert.deepEqual(result.groups.get('playing').map(row=>row.subject.catalogue_id),[1,2]);
  assert.equal(result.groups.get('completed').length,0);
});
test('base removal hides retained children, while rollback restores their membership',()=>{
  const base=entry(1),child=entry(2,'paused',{base_work_id:1,game_class:'expansion'});
  assert.equal(gameListCollection([base,child],new URLSearchParams()).total,2);
  base.listed=false;
  assert.equal(gameListCollection([base,child],new URLSearchParams()).total,0);
  base.listed=true;
  assert.equal(gameListCollection([base,child],new URLSearchParams()).total,2);
});
test('reload render counts are viewer/profile/query scoped and reject invalid values',()=>{
  const raw=JSON.stringify({key:'7:player:sort=title',limits:{playing:120,completed:-1,planning:'900'}});
  assert.equal(gameListSavedLimits(encodeURIComponent(raw),'7:player:sort=title').playing,120);
  assert.equal(gameListSavedLimits(raw,'7:player:sort=title').completed,30);
  assert.equal(gameListSavedLimits(raw,'7:player:sort=title').planning,30);
  assert.deepEqual(gameListSavedLimits(raw,'8:player:sort=title'),{});
  assert.deepEqual(gameListSavedLimits('broken','7:player:sort=title'),{});
});
