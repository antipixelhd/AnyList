import assert from 'node:assert/strict';
import test from 'node:test';
import {statisticsDomainUrl,createStatisticsSections} from '../src/lib/statistics-domains.ts';
const url=path=>new URL(path,'https://example.test');

test('statistics domains preserve shared sections and reset paging when changing the destination section',()=>{
  for(const section of ['overview','genres','actors','staff']){
    const games=statisticsDomainUrl(url('/user/alex/stats?section='+section),url('/user/alex/stats/games?sort=time&page=3'));
    assert.equal(games.searchParams.get('section'),section);assert.equal(games.searchParams.get('sort'),'time');
    assert.equal(games.searchParams.has('page'),section==='overview');
    const screen=statisticsDomainUrl(games,url('/user/alex/stats?media=series'));
    assert.equal(screen.searchParams.get('section'),section);assert.equal(screen.searchParams.get('media'),'series');
  }
});
test('unmatched sections fall back to Overview without mapping unrelated concepts',()=>{
  assert.equal(statisticsDomainUrl(url('/user/alex/stats?section=studios'),url('/user/alex/stats/games')).searchParams.get('section'),'overview');
  for(const section of ['developers','publishers'])assert.equal(statisticsDomainUrl(url('/user/alex/stats/games?section='+section),url('/user/alex/stats')).searchParams.get('section'),'overview');
  const other=url('/user/other/stats?section=actors');assert.equal(statisticsDomainUrl(url('/user/alex/stats/games?section=genres'),other).href,other.href);
});
test('ready section projections are bounded, generation-scoped and invalidated without caching partial data',()=>{
  const cache=createStatisticsSections(2),a={status:'ready',generation:1,data:'a'};
  cache.set('genres',a);cache.set('actors',{status:'pending',generation:1});assert.equal(cache.get('genres'),a);assert.equal(cache.get('actors'),undefined);
  cache.set('actors',{status:'ready',generation:1});cache.set('staff',{status:'ready',generation:1});assert.equal(cache.get('genres'),undefined);
  cache.set('overview',{status:'ready',generation:2});assert.equal(cache.get('actors'),undefined);cache.clear();assert.equal(cache.get('overview'),undefined);
});
