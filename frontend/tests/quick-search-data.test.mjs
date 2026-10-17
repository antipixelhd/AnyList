import assert from 'node:assert/strict';
import test from 'node:test';
import {quickSearchRequests,visibleQuickSearchSections,availableQuickSearchTabs,resolvedQuickSearchTab,steppedQuickSearchTab,mergeContributorResults,readQuickSearch} from '../src/lib/quick-search-data.ts';

test('pending contributor evidence renders immediately and refreshes only its own request',async()=>{
  const values=[],requests=[];
  const fetcher=async url=>{requests.push(url);return {ok:true,json:async()=>({verification_pending:requests.length<2,results:{studio:[requests.length]}})};};
  const pause=async()=>{assert.equal(values.length,1);};
  await readQuickSearch({kind:'contributors',url:'/studios'},new AbortController().signal,data=>values.push(data.results.studio),fetcher,pause);
  assert.deepEqual(values,[[1],[2]]);
  assert.deepEqual(requests,['/studios','/studios']);
});

test('verification polling is bounded and cancelled on query change',async()=>{
  const controller=new AbortController();let calls=0;
  const fetcher=async()=>{calls++;return {ok:true,json:async()=>({verification_pending:true})};};
  await readQuickSearch({kind:'contributors',url:'/studios'},controller.signal,()=>{},fetcher,async()=>{});
  assert.equal(calls,5);
  calls=0;
  await readQuickSearch({kind:'contributors',url:'/studios'},controller.signal,()=>controller.abort(),fetcher);
  assert.equal(calls,1);
});

test('desktop searches both media types and all contributors without duplicating people requests',()=>{
  const requests=quickSearchRequests('James & Co');
  assert.deepEqual(requests.map(row=>row.kind),['movie','series','contributors','contributors']);
  const params=new URL(requests[2].url,'https://anylist.test').searchParams;
  assert.equal(params.get('q'),'James & Co');assert.equal(params.get('kind'),'people');
  assert.equal(new URL(requests[3].url,'https://anylist.test').searchParams.get('kind'),'studio');
});

test('independent studio verification never erases people, regardless of response order',()=>{
  for(const order of [['studio','people'],['people','studio']]){
    let values={actor:[],staff:[],studio:[]};
    for(const scope of order)values=mergeContributorResults(values,scope==='studio'?{studio:['DC']}:{actor:['Actor'],staff:['Director']},scope);
    assert.deepEqual(values,{actor:['Actor'],staff:['Director'],studio:['DC']});
  }
});
test('mobile tabs share the same simultaneous media and contributor requests',()=>{
  const requests=quickSearchRequests('James');
  assert.deepEqual(requests.map(row=>row.kind),['movie','series','contributors','contributors']);
  assert.deepEqual(requests.slice(2).map(row=>new URL(row.url,'https://anylist.test').searchParams.get('kind')),['people','studio']);
});
test('desktop shows only matching sections and respects combined media; mobile displays one section',()=>{
  const counts={all:3,movie:1,series:2,actor:2,staff:0,studio:1};
  assert.deepEqual(visibleQuickSearchSections(false,'all',true,counts),['all','actor','studio']);
  assert.deepEqual(visibleQuickSearchSections(false,'all',false,counts),['movie','series','actor','studio']);
  assert.deepEqual(visibleQuickSearchSections(true,'all',false,counts),['all']);
  assert.deepEqual(visibleQuickSearchSections(true,'studio',true,counts),['studio']);
  assert.deepEqual(visibleQuickSearchSections(true,'staff',true,counts),[]);
});

test('mobile navigation removes empty tabs, recovers selection, and wraps through available results',()=>{
  const counts={all:0,movie:0,series:0,actor:2,staff:0,studio:1};
  assert.deepEqual(availableQuickSearchTabs(counts,false),['all','actor','staff','studio']);
  const available=availableQuickSearchTabs(counts,true);
  assert.deepEqual(available,['actor','studio']);
  assert.equal(resolvedQuickSearchTab('all',available),'actor');
  assert.equal(steppedQuickSearchTab('actor',available,-1),'studio');
  assert.equal(steppedQuickSearchTab('studio',available,1),'actor');
  assert.equal(steppedQuickSearchTab('actor',['actor'],1),null);
  assert.equal(resolvedQuickSearchTab('staff',[]),'all');
});
