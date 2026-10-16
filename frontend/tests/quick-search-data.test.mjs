import assert from 'node:assert/strict';
import test from 'node:test';
import {quickSearchRequests,visibleQuickSearchSections,mergeContributorResults,readQuickSearch} from '../src/lib/quick-search-data.ts';

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
  const requests=quickSearchRequests('James & Co',false,'actor');
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
test('mobile searches only the selected section, defaulting to series and movies',()=>{
  assert.deepEqual(quickSearchRequests('James',true,'all').map(row=>row.kind),['movie','series']);
  for(const section of ['actor','staff','studio']){
    const requests=quickSearchRequests('James',true,section);
    assert.equal(requests.length,1);assert.equal(requests[0].kind,'contributors');
    assert.equal(new URL(requests[0].url,'https://anylist.test').searchParams.get('kind'),section);
  }
});
test('desktop shows only matching sections and respects combined media; mobile displays one section',()=>{
  const counts={all:3,movie:1,series:2,actor:2,staff:0,studio:1};
  assert.deepEqual(visibleQuickSearchSections(false,'all',true,counts),['all','actor','studio']);
  assert.deepEqual(visibleQuickSearchSections(false,'all',false,counts),['movie','series','actor','studio']);
  assert.deepEqual(visibleQuickSearchSections(true,'all',false,counts),['all']);
  assert.deepEqual(visibleQuickSearchSections(true,'studio',true,counts),['studio']);
  assert.deepEqual(visibleQuickSearchSections(true,'staff',true,counts),[]);
});
