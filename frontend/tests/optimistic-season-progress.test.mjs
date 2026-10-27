import test from 'node:test';
import assert from 'node:assert/strict';
import {optimisticSeasonProgress} from '../src/lib/season-detail-data.ts';
const seasons = [
  {season_number:1,episode_count:2,released_count:2,watched_count:2,episodes:[{id:1,episode_number:1,released:true,watched:true},{id:2,episode_number:2,released:true,watched:true}]},
  {season_number:2,episode_count:2,released_count:1,watched_count:0,episodes:[{id:3,episode_number:1,released:true,watched:false},{id:4,episode_number:2,released:false,watched:false}]},
];
test('projects and rolls back progress across seasons without changing source evidence',()=>{
  const forward=optimisticSeasonProgress(seasons,3);
  assert.deepEqual(forward.map(s=>s.watched_count),[2,1]);
  assert.equal(forward[1].episodes[0].watched,true);
  assert.equal(seasons[1].episodes[0].watched,false);
  const back=optimisticSeasonProgress(seasons,1);
  assert.deepEqual(back.map(s=>s.watched_count),[1,0]);
  assert.equal(back[0].episodes[1].watched,false);
});
test('does not guess episode status when the released catalogue is incomplete',()=>{
  assert.equal(optimisticSeasonProgress([{...seasons[0],episodes:[]}],1),null);
  assert.equal(optimisticSeasonProgress(seasons,4),null);
});
