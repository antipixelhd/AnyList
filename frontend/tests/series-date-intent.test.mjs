import test from 'node:test';
import assert from 'node:assert/strict';
import {presetSeasonDates} from '../src/lib/series-date-intent.ts';

const today='2026-11-13';
const seasons=()=>[1,2,3].map(season_number=>({season_number,total:2,episodes:[{watched:false},{watched:false}]}));

test('Watching from season 3 presets its start without inventing prior dates',()=>{
  const result=presetSeasonDates(seasons(),'watching','planning',3,{season:3,count:0},today);
  assert.deepEqual([...result],[[3,{start_date:today}]]);
});
test('first and final episode presets are visible before saving',()=>{
  assert.deepEqual([...presetSeasonDates(seasons(),'watching','watching',2,{season:2,count:1},today)],[[2,{start_date:today}]]);
  assert.deepEqual([...presetSeasonDates(seasons(),'paused','paused',2,{season:2,count:2},today)],[[2,{start_date:today,completion_date:today}]]);
});
test('Completed presets only the latest finish and preserves existing dates',()=>{
  const list=seasons();
  assert.deepEqual([...presetSeasonDates(list,'completed','planning',1,undefined,today)],[[3,{completion_date:today}]]);
  list[2].start_date='2020-01-01';list[2].completion_date='2020-02-01';
  assert.equal(presetSeasonDates(list,'completed','planning',1,undefined,today).size,0);
});
test('explicit date edits and explicit clears outrank presets',()=>{
  const drafts=new Map([[2,{start_date:null,completion_date:'2020-02-01'}]]);
  assert.equal(presetSeasonDates(seasons(),'watching','planning',2,{season:2,count:2},today,drafts).size,0);
  const list=seasons();list[1].start_manual=true;list[1].date_manual=true;
  assert.equal(presetSeasonDates(list,'watching','planning',2,{season:2,count:2},today).size,0);
});
test('moving the season choice recomputes defaults instead of leaving an earlier start behind',()=>{
  assert.deepEqual([...presetSeasonDates(seasons(),'watching','planning',1,{season:1,count:0},today)],[[1,{start_date:today}]]);
  assert.deepEqual([...presetSeasonDates(seasons(),'watching','planning',3,{season:3,count:0},today)],[[3,{start_date:today}]]);
});
test('airing and untouched entries do not invent a finish date',()=>{
  const list=seasons();list[2].total=3;
  assert.equal(presetSeasonDates(list,'completed','planning',3,undefined,today).size,0);
  assert.equal(presetSeasonDates(list,'watching','watching',2,undefined,today).size,0);
});
