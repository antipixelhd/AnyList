import assert from 'node:assert/strict';
import test from 'node:test';
import {gameStatsQuery,gameHeadlines,gameChartRows,signedNumber} from '../src/lib/game-statistics-data.ts';

test('Games statistics keep unknown time and achievements distinct from explicit zero',()=>{
  const totals={listed_games:2,played_games:2,completed_games:1,recorded_seconds:null,known_time_games:0,mean_score:null,rated_games:0,achievement_percentage:null,achievement_progress_games:0,achievement_count:null,achievement_count_games:0};
  const data={totals,coverage:{unknown_time_games:2}};
  assert.deepEqual(gameHeadlines(data).slice(2).map(row=>row.value),['—','—','—','—']);
  Object.assign(totals,{recorded_seconds:0,achievement_percentage:0,achievement_count:0});
  assert.deepEqual(gameHeadlines(data).slice(2).map(row=>row.value),['≥ 0.0','—','0.0%','0']);
  const [row]=gameChartRows([{key:'1',label:'1h+',games:1,recorded_seconds:null,known_time_games:0,mean_score:null,rated_games:0,lower_seconds:3600,upper_seconds:18000}]);
  assert.equal(row.time_known,false);assert.match(row.detail,/1 to 5 hours, upper bound excluded/);
  assert.equal(signedNumber(null),'—');assert.equal(signedNumber(7200,3600),'+2.0');
});

test('URL selection is bounded, canonical and independent from Movies/Series scope',()=>{
  assert.deepEqual(gameStatsQuery(new URLSearchParams('section=actors&sort=time&page=2&media=all')),{section:'actors',sort:'time',page:2});
  assert.deepEqual(gameStatsQuery(new URLSearchParams('section=all&sort=untrusted&page=5001')),{section:'overview',sort:'count',page:1});
});
