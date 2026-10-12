import assert from 'node:assert/strict';
import test from 'node:test';
import {currentDetailSeason,detailSeasonState} from '../src/lib/season-detail-data.ts';
const season=(number,watched,released)=>({season_number:number,episodes:watched.map((value,index)=>({watched:value,released:released?.[index]??true}))});
test('current season follows watch history, ignoring older gaps, specials and future episodes',()=>{
  assert.equal(currentDetailSeason([season(0,[false]),season(1,[true,false]),season(2,[true,false]),season(3,[false],[false])]),2);
  assert.equal(currentDetailSeason([season(1,[true,true]),season(2,[false,false])]),2);
});

test('only the watching season displays progress; other seasons use a plain watched state',()=>{
  const partial={season_number:2,watched_count:3,released_count:9};
  assert.equal(detailSeasonState(partial,2,true).label,'3 / 9 watched');
  assert.equal(detailSeasonState(partial,1,true).label,'Not watched');
  assert.equal(detailSeasonState(partial,2,false).label,'Not watched');
  assert.equal(detailSeasonState({...partial,watched_count:9},2,true).label,'Watched');
  for(const count of [0,null])assert.equal(detailSeasonState({...partial,released_count:count},2,true).label,'Not watched');
});
test('new viewers open the first released season and caught-up viewers leave seasons collapsed',()=>{
  assert.equal(currentDetailSeason([season(2,[false]),season(1,[false,false])]),1);
  assert.equal(currentDetailSeason([season(1,[true]),season(2,[false],[false])]),null);
  assert.equal(currentDetailSeason([]),null);
  assert.equal(currentDetailSeason([season(1,[true]),season(2,[true,false],[true,false])],true),2);
});
