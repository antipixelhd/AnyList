import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import vm from 'node:vm';
import {contributorPages} from '../src/lib/contributor-pages.ts';

test('a failed replacement filter restores pagination and ignores the older page', async () => {
  const source=readFileSync(new URL('../src/lib/contributor-page.ts',import.meta.url),'utf8');
  const pagination=source.slice(source.indexOf("  root.querySelector<HTMLElement>('[data-contributor-next]')"),source.indexOf('  let syncing='));
  const handlers={},pending=[],appended=[],errors=[];
  const more={disabled:false,textContent:'Load more',addEventListener:(name,handler)=>{handlers[name]=handler;}};
  const region={setAttribute(){},removeAttribute(){}};
  const data={has_more:true,page:1,source:'provider',list_count:null};
  const context=vm.createContext({
    Error,URL,URLSearchParams,AbortController,DOMException,structuredClone,FormData:class {forEach(){}},
    signal:new AbortController().signal,writeVersion:0,gameEdits:new Map(),syncing:false,syncAgain:false,loading:{hidden:true},
    root:{dataset:{endpoint:'people/1'},querySelector:()=>({addEventListener(){}})},
    contributorSourceRefresh:()=>({update(){},stop(){}}),
    contributorPages,updateEmpty(){},
    form:{action:'https://example.test/person/1',querySelectorAll:()=>[],addEventListener:(name,handler)=>{handlers[name]=handler;}},
    document:{addEventListener(){}},region,more,data,
    error:{hidden:true},params:new URLSearchParams(),
    createPrediction:()=>({cancel(){},consume:async()=>null}),
    fetch:()=>new Promise((resolve,reject)=>pending.push({resolve,reject})),
    contributorParams:query=>query,
    append:works=>appended.push(...works),showError:message=>errors.push(message),
    restoreFilterControls(){},
  });
  vm.runInContext(stripTypeScriptTypes(pagination),context);
  const flush=()=>new Promise(resolve=>setImmediate(resolve));
  handlers.click();await flush();
  assert.equal(more.disabled,true);
  handlers.submit({preventDefault(){}});
  pending[1].reject(new Error('Filter failed'));await flush();
  assert.deepEqual(errors,['Filter failed']);
  assert.equal(more.disabled,false);
  assert.equal(more.textContent,'Load more');
  pending[0].resolve({ok:true,json:async()=>({works:['stale'],page:2,has_more:false})});
  await flush();
  assert.deepEqual(appended,[]);
  assert.equal(data.page,1);
  handlers.click();await flush();
  assert.equal(pending.length,3);
  pending[2].resolve({ok:true,json:async()=>({works:['current'],page:2,has_more:false})});
  await flush();
  assert.deepEqual(appended,['current']);
  assert.equal(more.disabled,false);
});

test('a game filter read waits for pending writes and retries stale membership/counts',async()=>{
  const source=readFileSync(new URL('../src/lib/contributor-page.ts',import.meta.url),'utf8');
  const snippet=source.slice(source.indexOf('  const waitForGameWrites='),source.indexOf('  const prediction='));
  const pendingWrites=new Set(),network=[],invalidated=[],document=new EventTarget();
  const context=vm.createContext({document,DOMException,Error,URLSearchParams,Set,Map,Promise,structuredClone,
    root:{dataset:{endpoint:'contributors/actor/1'},querySelector:()=>null},signal:new AbortController().signal,data:{},params:new URLSearchParams(),
    contributorSourceRefresh:()=>({update(){},stop(){}}),
    contributorPages,
    writeVersion:0,gameEdits:new Map([[8,{}]]),items:new Map(),
    editorStore:{hasPendingSubject:subject=>pendingWrites.has(subject.catalogue_id),invalidateSubject:subject=>invalidated.push(subject.catalogue_id)},
    fetch:()=>new Promise(resolve=>network.push(resolve)),
  });
  vm.runInContext(stripTypeScriptTypes(snippet)+'\nthis.read=requestPage;',context);
  const flush=()=>new Promise(setImmediate),controller=new AbortController();
  const read=context.read('/contributors/actor/1?media_type=game&list_scope=in',controller.signal);
  await flush();assert.equal(network.length,1);
  pendingWrites.add(8);context.writeVersion++;
  network[0]({ok:true,json:async()=>({works:[],list_count:0})});
  await flush();assert.equal(network.length,1,'do not refetch while the write is still pending');
  pendingWrites.delete(8);context.writeVersion++;document.dispatchEvent(new Event('anylist:editor-state'));
  await flush();assert.equal(network.length,2);
  network[1]({ok:true,json:async()=>({works:[{id:8,type:'game',list_status:'playing'}],list_count:1})});
  const result=await read;
  assert.equal(result.list_count,1);assert.equal(result.works[0].id,8);
  assert.deepEqual(invalidated,[8]);
  pendingWrites.add(8);
  const abort=new AbortController(),waiting=context.read('/contributors/actor/1?media_type=game',abort.signal);
  await flush();assert.equal(network.length,2);
  abort.abort();await assert.rejects(waiting,{name:'AbortError'});
});

test('screen editor changes invalidate the warmed tab before switching away and returning',async()=>{
  const source=readFileSync(new URL('../src/lib/contributor-page.ts',import.meta.url),'utf8');
  const start=source.indexOf("  document.addEventListener('anylist:cache-invalidated'");
  const snippet=source.slice(start,source.indexOf('  const nextPath=',start));
  const document=new EventTarget(),controller=new AbortController();let reads=0;
  const pages=contributorPages(async()=>({list_count:++reads}),controller.signal);
  pages.seed('screen',{list_count:0});
  vm.runInNewContext(stripTypeScriptTypes(snippet),{document,signal:controller.signal,pages,prediction:{cancel(){}}});
  document.dispatchEvent(new Event('anylist:editor-state'));
  await pages.get('game');
  assert.equal((await pages.get('screen')).list_count,2);
  controller.abort();
});

test('cache invalidation during a tab switch retries the selected domain instead of restoring old results',async()=>{
  const source=readFileSync(new URL('../src/lib/contributor-page.ts',import.meta.url),'utf8');
  const start=source.indexOf("  form.addEventListener('submit',event=>{");
  const snippet=source.slice(start,source.indexOf('  let syncing=',start));
  const lifetime=new AbortController(),handlers={},network=[],errors=[],appended=[];
  const pages=contributorPages((path,signal)=>new Promise((resolve,reject)=>{
    network.push({path,resolve,signal});signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true});
  }),lifetime.signal);
  const node={hidden:false,textContent:'',removeAttribute(){},setAttribute(){}};
  const root={dataset:{endpoint:'contributors/actor/catalogue:1',mediaType:'all'},querySelector:selector=>selector==='.contributor-list-link'?null:node};
  const context=vm.createContext({Error,DOMException,AbortController,URL,URLSearchParams,
    domain:()=> 'movies-series',retained:new Map(),retainCurrent:false,syncing:false,signal:lifetime.signal,pages,root,revision:0,filterRequest:undefined,prediction:{cancel(){}},
    form:{action:'https://example.test/person/1',querySelectorAll:()=>[],addEventListener:(name,handler)=>handlers[name]=handler},
    FormData:class{forEach(fn){fn('game','media_type');}},contributorParams:query=>query,
    params:new URLSearchParams('media_type=all'),data:{kind:'actor',list_count:0},
    scrollY:0,region:{childNodes:[],hidden:false,setAttribute(){},removeAttribute(){},replaceChildren(){}},items:new Map(),
    error:{hidden:true},sourceRefresh:{update(){}},more:{},requestedView:'timeline',
    append:works=>appended.push(...works),updateEmpty(){},syncAgain:false,
    restoreFilterControls:()=>assert.fail('selection should be retained'),showError:message=>errors.push(message),
    location:{href:'https://example.test/person/1'},history:{replaceState(){}},
  });
  vm.runInContext(stripTypeScriptTypes(snippet),context);
  const flush=()=>new Promise(setImmediate);
  handlers.submit({preventDefault(){}});await flush();assert.equal(network.length,1);
  pages.clear();await flush();assert.equal(network[0].signal.aborted,true);assert.equal(network.length,2);
  assert.equal(root.dataset.mediaType,'all');
  network[1].resolve({kind:'actor',works:[{id:7}],roles:[],list_count:1,has_more:false});await flush();
  assert.equal(root.dataset.mediaType,'game');assert.equal(appended.length,1);assert.deepEqual(errors,[]);
  pages.clear();handlers.submit({preventDefault(){}});await flush();
  context.filterRequest.abort();await flush();assert.equal(network.length,3,'superseded consumers never retry');
  lifetime.abort();
});

test('a write overlapping the final source revision queues another synchronization',async()=>{
  const source=readFileSync(new URL('../src/lib/contributor-page.ts',import.meta.url),'utf8');
  const start=source.indexOf('  const synchronize=async()=>');
  const snippet=source.slice(start,source.indexOf('      // Reuse mounted cards',start))+
    ' } finally { syncing=false;if(syncAgain&&!filterRequest&&!signal.aborted)void synchronize();} };this.run=synchronize;';
  let resolve,reads=0;const context=vm.createContext({
    signal:new AbortController().signal,pages:{forget(){}},syncing:false,syncAgain:false,
    filterRequest:undefined,more:{disabled:false},prediction:{cancel(){}},clearTimeout(){},syncRetry:undefined,
    revision:0,writeVersion:0,data:{page:1},params:new URLSearchParams('media_type=game'),URLSearchParams,
    pathFor:()=>'/games',requestPage:()=>{reads++;return new Promise(done=>resolve=done);},
  });
  vm.runInContext(stripTypeScriptTypes(snippet),context);
  const pending=context.run();context.writeVersion++;
  resolve({works:[],has_more:false});await pending;
  assert.equal(reads,2,'the completed source revision cannot disappear behind a concurrent edit');
  context.signal=new AbortController().signal;
});

test('returning to a contributor domain restores exact cards, filters and loaded window without a read',()=>{
  const source=readFileSync(new URL('../src/lib/contributor-page.ts',import.meta.url),'utf8');
  const start=source.indexOf("  form.addEventListener('submit',event=>{");
  const snippet=source.slice(start,source.indexOf('  let syncing=',start));
  let submit,reads=0,restores=0;const scrolls=[],screenNode={},gameNode={},gameItem={key:'game:1'};
  const snapshot={nodes:[gameNode],items:new Map([[gameNode,gameItem]]),params:new URLSearchParams('media_type=game&list_scope=in'),view:'grid',scroll:94,data:{works:[gameItem],roles:[],kind:'actor',list_count:2,page:3,has_more:true}};
  const retained=new Map([['game',snapshot]]),items=new Map([[screenNode,{key:'movie:1'}]]);
  const node={hidden:false,textContent:'',value:'',removeAttribute(){},setAttribute(){}};
  const region={childNodes:[screenNode],classList:{toggle(){}},setAttribute(){},removeAttribute(){},replaceChildren(...nodes){this.childNodes=nodes;}};
  const params=new URLSearchParams('media_type=movie'),data={kind:'actor',list_count:1};
  const context={Error,DOMException,AbortController,URL,URLSearchParams,Map,
    signal:new AbortController().signal,pages:{get(){reads++;throw new Error('Unexpected read');}},revision:0,filterRequest:undefined,prediction:{cancel(){}},
    form:{action:'https://example.test/person/1',querySelectorAll:()=>[],elements:{namedItem:()=>node},addEventListener:(name,handler)=>submit=handler},
    FormData:class{forEach(fn){fn('game','media_type');}},contributorParams:query=>query,
    domain:()=> 'movies-series',retained,retainCurrent:true,syncing:false,scrollY:37,window:{scrollTo:(x,y)=>scrolls.push(y)},
    params,data,region,items,error:node,root:{dataset:{endpoint:'people/1'},querySelector:selector=>selector==='.contributor-list-link'?null:node,querySelectorAll:()=>[]},
    sourceRefresh:{update(){}},more:{},requestedView:'timeline',append(){throw new Error('Cards must be retained');},updateEmpty(){},syncAgain:false,
    restoreFilterControls:()=>restores++,scrollToWorks(){},showError:message=>assert.fail(message),location:{href:'https://example.test/person/1'},history:{replaceState(){}},
  };
  vm.runInNewContext(stripTypeScriptTypes(snippet),context);submit({preventDefault(){}});
  assert.equal(reads,0);assert.equal(restores,1);assert.equal(region.childNodes[0],gameNode);assert.equal(items.get(gameNode),gameItem);
  assert.equal(data.page,3);assert.equal(params.get('list_scope'),'in');assert.equal(retained.get('movies-series').nodes[0],screenNode);assert.deepEqual(scrolls,[94]);
});
