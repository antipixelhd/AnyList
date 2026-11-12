import assert from 'node:assert/strict';
import test from 'node:test';
import {quickSearchRequests,rankCombinedMedia,createQuickTitleBatch,createQuickSearchReader,visibleQuickSearchSections,availableQuickSearchTabs,resolvedQuickSearchTab,steppedQuickSearchTab,mergeContributorResults,readQuickSearch} from '../src/lib/quick-search-data.ts';

test('contributor discovery follows enabled domains instead of revealing hidden game studios',()=>{
  for(const [movies,games,domain] of [[true,true,'all'],[true,false,'movies_series'],[false,true,'game']]){
    const requests=quickSearchRequests('Supergiant',undefined,movies,games).filter(row=>row.kind==='contributors');
    assert.equal(requests.length,2);
    for(const request of requests)assert.equal(new URL(request.url,'https://example.test').searchParams.get('work_domain'),domain);
  }
});

test('movie/series opt-out rejects title fan-out and title tabs together, retaining shared people search',()=>{
  const requests=quickSearchRequests('Troy',undefined,false,false);
  assert.equal(requests.length,2);assert.ok(requests.every(request=>request.kind==='contributors'));
  const counts={all:1,movie:1,series:0,actor:2,staff:1,studio:0};
  assert.deepEqual(availableQuickSearchTabs(counts,false,false,true,false,false),['actor','staff','studio']);
  assert.deepEqual(availableQuickSearchTabs(counts,true,false,true,false),['actor','staff']);
});

test('warm titles reuse private dialog cache until expiry, invalidation or unmount',async()=>{
  let calls=0, now=0;
  const lifetime=new AbortController(), query=new AbortController();
  const fetcher=async()=>({ok:true,json:async()=>({results:[++calls]})});
  const reader=createQuickSearchReader(lifetime.signal,fetcher,()=>now);
  const request={kind:'movie',url:'/movie?q=below&region=DE'};
  const rows=[];
  await reader.read(request,query.signal,data=>rows.push(data.results));
  await reader.read(request,query.signal,data=>rows.push(data.results));
  assert.deepEqual(rows,[[1],[1]]);
  await reader.read({...request,url:'/movie?q=below&region=US'},query.signal,()=>{});
  assert.equal(calls,2);
  now=60001;
  await reader.read(request,query.signal,()=>{});
  assert.equal(calls,3);
  reader.clear();
  await reader.read(request,query.signal,()=>{});
  assert.equal(calls,4);
  lifetime.abort();
  await reader.read(request,query.signal,()=>assert.fail('unmounted reader must not publish'));
  const other=createQuickSearchReader(new AbortController().signal,fetcher,()=>now);
  await other.read(request,query.signal,()=>{});
  assert.equal(calls,5);
});

test('invalidated in-flight responses cannot refill the title cache',async()=>{
  let release, calls=0;
  const reader=createQuickSearchReader(new AbortController().signal,async()=>{
    calls++;
    if(calls===1)await new Promise(resolve=>release=resolve);
    return {ok:true,json:async()=>({results:[]})};
  });
  const request={kind:'series',url:'/series?q=war'}, signal=new AbortController().signal;
  const pending=reader.read(request,signal,()=>{});
  reader.clear();release();await pending;
  await reader.read(request,signal,()=>{});
  assert.equal(calls,2);
});

test('search cache stays bounded, skips degraded results and retains completed contributors',async()=>{
  let calls=0;
  const reader=createQuickSearchReader(new AbortController().signal,async url=>({ok:true,json:async()=>{
    calls++;
    return {results:[],...(url.includes('degraded')?{notice:'Local matches only'}:{})};
  }}));
  const signal=new AbortController().signal;
  for(let i=0;i<41;i++)await reader.read({kind:'movie',url:`/movie?q=${i}`},signal,()=>{});
  await reader.read({kind:'movie',url:'/movie?q=0'},signal,()=>{});
  assert.equal(calls,42);
  for(const request of [{kind:'movie',url:'/degraded'},{kind:'contributors',url:'/people'}]){
    await reader.read(request,signal,()=>{});
    await reader.read(request,signal,()=>{});
  }
  assert.equal(calls,45);
});

test('combined title ordering publishes once after both categories settle in either order', () => {
  for (const first of ['movie', 'series']) {
    const settle = createQuickTitleBatch();
    const second = first === 'movie' ? 'series' : 'movie';
    assert.equal(settle(first, [{title:first, search_rank:1}]), undefined);
    const batch = settle(second, [{title:second, search_rank:2}]);
    assert.deepEqual(rankCombinedMedia(batch.movie, batch.series).map(row => row.title), [second, first]);
    assert.equal(settle(first, [{title:'late replacement'}]), undefined);
    assert.equal(batch[first][0].title, first);
  }
});

test('a failed title source settles empty rather than suppressing the successful category', () => {
  const settle = createQuickTitleBatch();
  assert.equal(settle('series', [{title:'Below'}]), undefined);
  assert.deepEqual(settle('movie'), {movie:[], series:[{title:'Below'}]});
});

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
  assert.equal(calls,20);
  calls=0;
  await readQuickSearch({kind:'contributors',url:'/studios'},controller.signal,()=>controller.abort(),fetcher);
  assert.equal(calls,1);
});

test('cold people can settle after the old seven-second polling window',async()=>{
  let calls=0;const updates=[];
  await readQuickSearch({kind:'contributors',url:'/people'},new AbortController().signal,data=>updates.push(data),
    async()=>({ok:true,json:async()=>({results:{actor:++calls>=8?['verified person']:[]},verification_pending:calls<8})}),async()=>{});
  assert.equal(calls,8);
  assert.deepEqual(updates.at(-1).results.actor,['verified person']);
  assert.equal(updates.at(-1).verification_pending,false);
});

test('exhausted or long-cooldown verification stays partial and preserves usable results',async()=>{
  for(const retry_after of [2,60]){
    let calls=0;const updates=[];
    await readQuickSearch({kind:'contributors',url:'/people'},new AbortController().signal,data=>updates.push(data),
      async()=>{calls++;return {ok:true,json:async()=>({results:{actor:['existing person']},verification_pending:true,retry_after})};},async()=>{});
    assert.equal(calls,retry_after===60?1:20);
    assert.equal(updates.at(-1).partial,true);
    assert.deepEqual(updates.at(-1).results.actor,['existing person']);
  }
});

test('desktop searches both media types and all contributors without duplicating people requests',()=>{
  const requests=quickSearchRequests('James & Co');
  assert.deepEqual(requests.map(row=>row.kind),['movie','series','game','contributors','contributors']);
  const params=new URL(requests[3].url,'https://anylist.test').searchParams;
  assert.equal(params.get('q'),'James & Co');assert.equal(params.get('kind'),'people');
  assert.equal(new URL(requests[4].url,'https://anylist.test').searchParams.get('kind'),'studio');
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
  assert.deepEqual(requests.map(row=>row.kind),['movie','series','game','contributors','contributors']);
  assert.deepEqual(requests.slice(3).map(row=>new URL(row.url,'https://anylist.test').searchParams.get('kind')),['people','studio']);
});
test('combined movie and series results follow shared relevance instead of alternating types',()=>{
  const movies=[{title:'War movie',search_rank:305},{title:'War stories',search_rank:210}];
  const series=[{title:'War series',search_rank:314},{title:'War report',search_rank:208}];
  assert.deepEqual(rankCombinedMedia(movies,series).map(row=>row.title),
    ['War series','War movie','War stories','War report']);
});
test('desktop shows only matching sections and respects combined media; mobile displays one section',()=>{
  const counts={all:3,movie:1,series:2,actor:2,staff:0,studio:1};
  assert.deepEqual(visibleQuickSearchSections(false,'all',true,counts),['all','actor','studio']);
  assert.deepEqual(visibleQuickSearchSections(false,'all',false,counts),['movie','series','actor','studio']);
  assert.deepEqual(visibleQuickSearchSections(true,'all',false,counts),['movie']);
  assert.deepEqual(visibleQuickSearchSections(true,'series',false,counts),['series']);
  assert.deepEqual(visibleQuickSearchSections(true,'studio',true,counts),['studio']);
  assert.deepEqual(visibleQuickSearchSections(true,'staff',true,counts),[]);
});

test('separate mobile lists navigate movies and series independently through loading and empty results',()=>{
  const empty={all:0,movie:0,series:0,actor:0,staff:0,studio:0};
  assert.deepEqual(availableQuickSearchTabs(empty,false,false,false),['movie','series','game','actor','staff','studio']);
  assert.deepEqual(availableQuickSearchTabs(empty,true,true,false),['movie','series']);
  const counts={...empty,all:2,series:2,actor:1};
  const available=availableQuickSearchTabs(counts,true,false,false);
  assert.deepEqual(available,['series','actor']);
  assert.equal(resolvedQuickSearchTab('movie',available),'series');
  assert.equal(steppedQuickSearchTab('series',available,1),'actor');
  assert.equal(steppedQuickSearchTab('actor',available,1),'series');
  assert.equal(resolvedQuickSearchTab('series',availableQuickSearchTabs(counts,true,false,true)),'all');
});

test('mobile navigation removes empty tabs, recovers selection, and wraps through available results',()=>{
  const counts={all:0,movie:0,series:0,actor:2,staff:0,studio:1};
  assert.deepEqual(availableQuickSearchTabs(counts,false),['all','game','actor','staff','studio']);
  const available=availableQuickSearchTabs(counts,true);
  assert.deepEqual(available,['actor','studio']);
  assert.deepEqual(availableQuickSearchTabs(counts,true,true),['all','actor','studio']);
  assert.equal(resolvedQuickSearchTab('all',available),'actor');
  assert.equal(steppedQuickSearchTab('actor',available,-1),'studio');
  assert.equal(steppedQuickSearchTab('studio',available,1),'actor');
  assert.equal(steppedQuickSearchTab('actor',['actor'],1),null);
  assert.equal(resolvedQuickSearchTab('staff',[]),'all');
});


test('completed empty contributor results are cached and invalidation immediately restores discovery', async () => {
  let calls=0, now=0;
  const reader=createQuickSearchReader(new AbortController().signal,async()=>{
    calls++;return {ok:true,json:async()=>({results:{studio:[]},verification_pending:false})};
  },()=>now);
  const request={kind:'contributors',url:'/studio?q=obscure'}, signal=new AbortController().signal;
  await reader.read(request,signal,()=>{});
  await reader.read(request,signal,()=>{});
  assert.equal(calls,1);
  now=60001;
  await reader.read(request,signal,()=>{});
  assert.equal(calls,2);
  reader.clear();
  await reader.read(request,signal,()=>{});
  assert.equal(calls,3);
});

test('unchanged contributor rows publish completion so pending responses never become a false terminal cache', async () => {
  let calls=0;
  const updates=[];
  await readQuickSearch({kind:'contributors',url:'/studio'},new AbortController().signal,
    data=>updates.push(data.verification_pending),async()=>({ok:true,json:async()=>({results:{studio:[]},verification_pending:++calls===1})}),async()=>{});
  assert.deepEqual(updates,[true,false]);
});
