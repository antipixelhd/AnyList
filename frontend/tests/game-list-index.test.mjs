import test from 'node:test';
import assert from 'node:assert/strict';
import {GameListIndex} from '../src/lib/game-list-index.ts';

const row=(id,title,status='playing',extra={})=>{
  const v={score:null,seconds:null,updated:null,year:'2024',genres:['Adventure'],favorite:false,base:null,rank:id,...extra};
  return [id,title,status,v.score,v.seconds,v.updated,v.year,v.genres,v.favorite,v.base,v.rank];
};
const query=raw=>new URLSearchParams(raw);
const snapshot=()=>[row(1,'zulu','playing',{rank:3,score:8}),row(2,'alpha','playing',{rank:1}),
  row(3,'alpha expansion','paused',{base:2,rank:2,score:9}),row(4,'100% game','planning',{favorite:true,rank:4})];

test('incomplete and invalidated indexes always use server queries',()=>{
  const index=new GameListIndex();
  assert.equal(index.select(query('')),null);
  assert.equal(index.publish({complete:false,results:snapshot()}),false);
  assert.equal(index.select(query('q=zulu')),null);
  index.publish({complete:true,results:snapshot()});index.invalidate();
  assert.equal(index.select(query('')),null);
});

test('complete index filters the entire library, grouping with canonical database title ranks',()=>{
  const index=new GameListIndex();index.publish({complete:true,results:snapshot()});
  assert.deepEqual(index.select(query('')).ids,[2,1,3,4]);
  assert.deepEqual(index.select(query('sort=score')).ids,[1,2,3,4]);
  assert.deepEqual(index.select(query('q=%')).ids,[4]);
  assert.deepEqual(index.select(query('favorites=true')).ids,[4]);
  assert.equal(index.select(query('year=2020')).total,0);
  assert.equal(index.select(query('genre=Adventure')).total,4);
  assert.deepEqual(index.select(query('status=paused')).group_counts,{paused:1});
  assert.deepEqual(index.select(query('q=ALPHA')).status_counts,{playing:2,paused:1,planning:1});
  assert.equal(index.select(query('q=Ä')),null);
});

test('pending edits, base removal and rollback update membership without partial results',()=>{
  const index=new GameListIndex();index.publish({complete:true,results:snapshot()});
  const edit=(id,patch)=>index.update({subject:{catalogue_id:id},listed:true,status:'playing',score:null,...patch});
  edit(2,{status:'completed',favorite:true,score:10});
  assert.deepEqual(index.select(query('favorites=true')).ids,[2,4]);
  edit(2,{listed:false});
  assert.deepEqual(index.select(query('')).ids,[1,4]);
  edit(2,{});
  assert.deepEqual(index.select(query('q=alpha')).ids,[2,3]);
  edit(99,{}); // A new member lacks a canonical collation rank.
  assert.equal(index.select(query('')),null);
});

test('score/time nulls and equal values match server id tie breakers',()=>{
  const index=new GameListIndex();index.publish({complete:true,results:[row(9,'a','playing',{seconds:0,score:0}),row(2,'b'),row(3,'c','playing',{seconds:0,score:0})]});
  assert.deepEqual(index.select(query('sort=progress')).ids,[3,9,2]);
  assert.deepEqual(index.select(query('sort=score')).ids,[3,9,2]);
});

test('updated ordering preserves PostgreSQL microseconds and equivalent UTC encodings',()=>{
  const index=new GameListIndex();index.publish({complete:true,results:[
    row(1,'a','playing',{updated:'2026-11-10T10:00:00.123001+00:00'}),
    row(9,'b','playing',{updated:'2026-11-10T10:00:00.123002+00:00'}),
    row(3,'c','playing',{updated:'2026-11-10T10:00:00Z'}),
    row(2,'d','playing',{updated:'2026-11-10T10:00:00.000000+00:00'}),
    row(4,'e','playing',{updated:'2026-11-10T10:00:00'})]});
  assert.deepEqual(index.select(query('sort=updated')).ids,[9,1,2,3,4]);
});
