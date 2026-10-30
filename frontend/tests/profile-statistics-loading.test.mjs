import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
const read=path=>readFileSync(new URL(path,import.meta.url),'utf8');
test('statistics and list entry routes do not ship speculative statistics loading frames',()=>{
  for(const path of ['../src/components/StatisticsPage.astro','../src/pages/user/[username]/games.astro','../src/pages/user/[username]/[section].astro']){
    assert.doesNotMatch(read(path),/StatsHeadlineLoading|ProfileStatisticsLoading|data-stats-headline-loading|data-profile-stats-preview/);
  }
  const shared=read('../src/components/StatisticsPage.astro');
  assert.match(shared,/overview-pending[^>]*hidden=\{ready\}/);
  assert.match(shared,/overview-results[^>]*hidden=\{!ready\}/);
});
