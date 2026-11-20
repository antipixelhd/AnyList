import assert from 'node:assert/strict';
import test from 'node:test';
import {actualSeconds,gameDatePatch,gameTimePatch,hoursInput,achievementCountInput,firstTimeCanFillTotal} from '../src/lib/game-editor-data.ts';

test('aggregate counts support unknown totals without accepting blank, fractional or out-of-range input',()=>{
  assert.equal(achievementCountInput('0',null),0);assert.equal(achievementCountInput('1234567',null),1234567);
  assert.equal(achievementCountInput('54',54),54);
  for(const value of ['', ' ', '-1','1.5','Infinity','2147483648'])assert.throws(()=>achievementCountInput(value,null));
  assert.throws(()=>achievementCountInput('55',54));
});

test('blank-total prefill is limited to first capture and never an existing first-time correction',()=>{
  assert.equal(firstTimeCanFillTotal({},false),true);
  assert.equal(firstTimeCanFillTotal({total_seconds:null,completion_seconds:36000},false),false);
  assert.equal(firstTimeCanFillTotal({total_seconds:null,completion_seconds:0},false),false);
  assert.equal(firstTimeCanFillTotal({total_seconds:0},false),false);
  assert.equal(firstTimeCanFillTotal({},true),false);
});

test('actual game hours retain unknown and explicit zero without rounding untouched source seconds',()=>{
  assert.equal(actualSeconds(''),null);assert.equal(actualSeconds('0'),0);
  assert.equal(actualSeconds('1.5'),5400);
  assert.throws(()=>actualSeconds('-1'));assert.throws(()=>actualSeconds('Infinity'));
  const current={total_seconds:12345,completion_seconds:8001,replay_count:null};
  assert.deepEqual(gameTimePatch(current,'completed',{total:hoursInput(12345),first:hoursInput(8001),replays:''}),{});
  assert.deepEqual(gameTimePatch(current,'playing',{total:'4',first:'99',replays:'5'}),{total_seconds:14400,replay_count:5});
});

test('first completion and cumulative time are independently corrected and no replay multiplier exists',()=>{
  const patch=gameTimePatch({total_seconds:7200,completion_seconds:3600,replay_count:1},'completed',{total:'0.5',first:'1',replays:'10'});
  assert.deepEqual(patch,{total_seconds:1800,replay_count:10});
  assert.deepEqual(gameTimePatch({},'completed',{total:'',first:'0',replays:''}),{completion_seconds:0});
});

test('new manual dates use the viewer calendar; explicit unknown dates suppress defaults',()=>{
  const today='2026-10-21';
  assert.deepEqual(gameDatePatch({status:'playing'},'completed',{start_date:'',finish_date:''},new Set(),today),{finish_date:today});
  assert.deepEqual(gameDatePatch({status:'playing'},'completed',{start_date:'',finish_date:''},new Set(['finish_date']),today),{finish_date:null});
  assert.deepEqual(gameDatePatch({status:'completed',finish_date:null},'completed',{start_date:'',finish_date:''},new Set(),today),{});
  assert.deepEqual(gameDatePatch({status:'completed',finish_date:'2020-01-01'},'playing',{start_date:'',finish_date:'2020-01-01'},new Set(),today),{start_date:today});
  assert.deepEqual(gameTimePatch({status:'playing',total_seconds:36000},'completed',{total:'10',first:'',replays:''}),{completion_seconds:null});
});
