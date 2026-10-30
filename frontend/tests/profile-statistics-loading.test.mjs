import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
const read=path=>readFileSync(new URL(path,import.meta.url),'utf8');

test('statistics previews use the real metric grid with no speculative chart placeholders',()=>{
  const preview=read('../src/components/ProfileStatisticsLoading.astro');
  const headlines=read('../src/components/StatsHeadlineLoading.astro');
  assert.match(preview,/class="page-width stats-overview-page"/);
  assert.match(preview,/<StatsHeadlineLoading/);
  assert.doesNotMatch(preview,/<canvas|overview-plot|chart-skeleton/);
  assert.match(headlines,/class="overview-totals"/);
  assert.match(headlines,/class="stats-headline-placeholder"/);
});

test('pending aggregates expose headline positions before the status message and defer graphs',()=>{
  for(const route of ['stats.astro','stats/games.astro']){
    const source=read('../src/pages/user/[username]/'+route);
    assert(source.indexOf('<StatsHeadlineLoading')<source.indexOf('<div class="overview-pending"'));
    assert.match(source,/overview-results[^>]*hidden=/);
  }
});
