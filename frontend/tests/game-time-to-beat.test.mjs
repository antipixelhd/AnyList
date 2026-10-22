import test from 'node:test';
import assert from 'node:assert/strict';
import {estimatedHours,completionCategories} from '../src/lib/game-time-to-beat.ts';

test('completion categories retain independent provider estimates and agreed labels',()=>{
  assert.deepEqual(completionCategories,[['main_story_seconds','Main Story'],['main_sides_seconds','Main + Sides'],['completionist_seconds','Completionist']]);
});

test('unknown or zero estimates stay absent and source seconds are displayed as time',()=>{
  for(const value of [null,undefined,0,-1,NaN,Infinity,'3600'])assert.equal(estimatedHours(value),null);
  assert.equal(estimatedHours(3600),'1h');assert.equal(estimatedHours(1800),'30m');
  assert.equal(estimatedHours(3599),'1h');assert.equal(estimatedHours(5),'1m');
  assert.ok(['1.5h','1,5h'].includes(estimatedHours(5400)));
});
