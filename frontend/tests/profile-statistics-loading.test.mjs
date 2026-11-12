import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {profileLoadingFrame} from '../src/lib/profile-loading.ts';
const read=path=>readFileSync(new URL(path,import.meta.url),'utf8');
test('statistics and list entry routes do not ship speculative statistics loading frames',()=>{
  for(const path of ['../src/components/StatisticsPage.astro','../src/pages/user/[username]/games.astro','../src/pages/user/[username]/[section].astro']){
    assert.doesNotMatch(read(path),/StatsHeadlineLoading|ProfileStatisticsLoading|data-stats-headline-loading|data-profile-stats-preview/);
  }
  const shared=read('../src/components/StatisticsPage.astro');
  assert.match(shared,/overview-pending[^>]*hidden=\{ready\}/);
  assert.match(shared,/overview-results[^>]*hidden=\{!ready\}/);
});

test('Statistics frames match the destination section and scope',()=>{
  for(const [kind,query,domain,section,media] of [
    ['stats','?section=actors&media=series','screen','actors','series'],
    ['stats/games','?section=publishers','game','publishers','all'],
    ['stats','?section=developers&media=unknown','screen','overview','all'],
  ]){
    const heading={};
    const selected={classList:new Set(),setAttribute(name,value){this[name]=value;}};
    const group={hidden:true,querySelector(selector){assert.equal(selector,`[data-section="${section}"]`);return selected;}};
    const frame={dataset:{},querySelector(selector){
      if(selector==='[data-loading-stats-heading]')return heading;
      assert.equal(selector,`[data-statistics-domain="${domain}"]`);return group;
    }};
    const nav={querySelector:()=>({content:{firstElementChild:{cloneNode:()=>frame}}})};
    assert.equal(profileLoadingFrame(kind,new URL('https://anylist.test/user/alex/'+kind+query),nav),frame);
    assert.deepEqual(frame.dataset,{domain,section,media});
    assert.equal(heading.textContent,section[0].toUpperCase()+section.slice(1));
    assert.equal(group.hidden,false);
    assert.equal(selected['aria-pressed'],'true');
  }
});
