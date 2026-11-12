import test from 'node:test';
import assert from 'node:assert/strict';
import {gameRatingRows} from '../src/lib/game-ratings-data.ts';

test('Steam review percentages use known counts, retain explicit zero and omit unknown windows',()=>{
  const data={steam_app_id:'42',all_time:{positive:9,negative:1,total:10,percentage:3},recent:{positive:0,negative:4,total:4},critic:{score:94,url:'https://www.metacritic.com/game/pc/fixture/'}};
  const rows=gameRatingRows(data);assert.equal(rows[0].value,'90% positive');assert.equal(rows[1].value,'0% positive');assert.equal(rows[2].value,'94/100');
  assert.match(rows[0].detail,/9 of 10 positive.*English.*Steam purchases/);
  assert.deepEqual(gameRatingRows({...data,recent:{positive:0,total:0}}).map(row=>row.field),['all_time','critic']);
  assert.deepEqual(gameRatingRows(null),[]);
});

test('critic sources are allowlisted and recent windows retain actual cached UTC bounds',()=>{
  const rows=gameRatingRows({steam_app_id:'42',critic:{score:0,url:'javascript:alert(1)'},recent:{positive:5,total:10,window_start:1790122352,window_end:1792714352}});
  assert.equal(rows[1].value,'0/100');assert.equal(rows[1].url,null);
  assert.match(rows[0].detail,/2026-09-23T00:12:32.000Z to 2026-10-23T00:12:32.000Z/);
  assert.deepEqual(gameRatingRows({steam_app_id:'42',critic:{score:101}}),[]);
});

test('existing verified RAWG critic metadata needs no Steam identity or invented PC label',()=>{
  const rows=gameRatingRows({steam_app_id:null,critic:{score:88,url:null,source:'rawg',platform:null}});
  assert.equal(rows.length,1);assert.equal(rows[0].value,'88/100');assert.equal(rows[0].detail,'Metacritic · via RAWG');
  assert.equal(gameRatingRows({steam_app_id:'42',critic:{score:99,source:'manual'}})[0].detail,'Metacritic');
  assert.doesNotThrow(()=>gameRatingRows({steam_app_id:'42',recent:{positive:5,total:10,window_start:1e100,window_end:1e101}}));
});
