import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {profileLoadingFrame} from '../src/lib/profile-loading.ts';
const read=path=>readFileSync(new URL(path,import.meta.url),'utf8');
test('the shared profile header includes the loading templates regardless of its active tab',()=>{
  const header=read('../src/components/ProfileHeader.astro');
  assert.match(header,/>Social<\/a><ProfileLoadingFrames gamesApproved=\{domains\.can_access_games\}\/>/);
  const templates=read('../src/components/ProfileLoadingFrames.astro');
  assert.match(templates,/import '\.\.\/styles\/stats-loading\.css'/);
  assert.match(templates,/data-profile-list-loading/);
  assert.match(templates,/data-profile-stats-loading/);
});

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
    const sibling={hidden:false};
    const frame={dataset:{},querySelector(selector){
      if(selector==='[data-loading-stats-heading]')return heading;
      return selector===`[data-statistics-domain="${domain}"]`?group:sibling;
    }};
    const nav={dataset:{mediaDomains:JSON.stringify({show_games:false,show_movies_series:false})},
      querySelector:()=>({content:{firstElementChild:{cloneNode:()=>frame}}})};
    assert.equal(profileLoadingFrame(kind,new URL('https://anylist.test/user/alex/'+kind+query),nav),frame);
    assert.deepEqual(frame.dataset,{domain,section,media});
    assert.equal(heading.textContent,section[0].toUpperCase()+section.slice(1));
    assert.equal(group.hidden,false);
    assert.equal(sibling.hidden,true);
    assert.equal(selected['aria-pressed'],'true');
  }
});
