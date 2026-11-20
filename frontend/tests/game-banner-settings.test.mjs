import test from 'node:test';
import assert from 'node:assert/strict';
import {mountGameBannerSettings} from '../src/lib/game-banner-settings.ts';

test('a ready primary renders without a redundant settings fetch, while changes still load choices',async t=>{
  const requests=[],handlers={};
  t.mock.method(globalThis,'fetch',async url=>{requests.push(url);return {ok:true,json:async()=>({backdrop:'selected',background_status:{state:'ready'}})};});
  const controller=new AbortController(),root={addEventListener:(name,fn)=>handlers[name]=fn,querySelectorAll:()=>[],querySelector:()=>null};
  mountGameBannerSettings(root,7,'viewer',controller.signal,()=>{},false,{backdrop:'primary',background_status:{state:'ready'}});
  assert.deepEqual(requests,[]);
  handlers['anylist:banner-pinned']();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(requests.length,1);controller.abort();
});

test('a pending primary continues acquisition even when a fallback is already visible',async t=>{
  let requests=0;
  t.mock.method(globalThis,'fetch',async()=>{requests++;return {ok:true,json:async()=>({backdrop:'preferred',background_status:{state:'ready'}})};});
  const controller=new AbortController(),root={addEventListener(){},querySelectorAll:()=>[],querySelector:()=>null};
  mountGameBannerSettings(root,7,'viewer',controller.signal,()=>{},false,{backdrop:'fallback',background_status:{state:'pending'}});
  await new Promise(resolve=>setImmediate(resolve));assert.equal(requests,1);controller.abort();
});

test('ready heroes load alternatives only on intent and share concurrent gallery reads',async t=>{
  let finish,requests=0;const output=[];
  t.mock.method(globalThis,'fetch',()=>{requests++;return new Promise(resolve=>finish=resolve);});
  const controller=new AbortController(),root={addEventListener(){},querySelectorAll:()=>[],querySelector:()=>null};
  const settings=mountGameBannerSettings(root,7,'viewer',controller.signal,data=>output.push(data),false,
    {backdrop:'primary',pinned_banner:'primary',background_status:{state:'ready'}});
  assert.equal(requests,0);
  const first=settings.load();assert.strictEqual(settings.load(),first);assert.equal(requests,1);
  finish({ok:true,json:async()=>({backdrop:'primary',pinned_banner:'primary',banners:['primary','other'],background_status:{state:'ready'}})});
  await first;assert.deepEqual(output[0].banners,['primary','other']);
  await settings.load();assert.equal(requests,1);controller.abort();
});

test('mobile requests IGDB even with a saved desktop animation preference',async t=>{
  const requests=[],output=[];
  t.mock.method(globalThis,'fetch',async url=>{requests.push(url);return {ok:true,json:async()=>({backdrop:'igdb',banners:['igdb'],background_status:{state:'ready'}})};});
  const previous=Object.getOwnPropertyDescriptor(globalThis,'localStorage');
  Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{getItem:()=>JSON.stringify({animated:true}),setItem(){}}});
  t.after(()=>previous?Object.defineProperty(globalThis,'localStorage',previous):delete globalThis.localStorage);
  const controller=new AbortController();
  mountGameBannerSettings({addEventListener(){},querySelectorAll:()=>[],querySelector:()=>null},1,'viewer',controller.signal,data=>output.push(data),true);
  await new Promise(resolve=>setImmediate(resolve));
  controller.abort();
  assert.deepEqual(requests,['/api/proxy/tracking/games/works/1/background?animated=false&provider=igdb']);
  assert.equal(output[0].backdrop,'igdb');
});

test('cycling advances once per visit and keeps viewer/game preferences separate',async t=>{
  const stored=new Map([['game-banner:viewer:1',JSON.stringify({cycle:true})]]);
  t.mock.method(globalThis,'fetch',async()=>({ok:true,json:async()=>({banners:['a','b','c'],cycle_banners:['a','b','c'],background_status:{state:'ready'}})}));
  const previous=Object.getOwnPropertyDescriptor(globalThis,'localStorage');
  Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{getItem:key=>stored.get(key),setItem:(key,value)=>stored.set(key,value)}});
  t.after(()=>previous?Object.defineProperty(globalThis,'localStorage',previous):delete globalThis.localStorage);
  const root={addEventListener(){},querySelectorAll:()=>[],querySelector:()=>null};
  const output=[];
  for(let visit=0;visit<2;visit++){
    const controller=new AbortController();
    mountGameBannerSettings(root,1,'viewer',controller.signal,data=>output.push(data.backdrop));
    await new Promise(resolve=>setImmediate(resolve));controller.abort();
  }
  assert.deepEqual(output,['a','b']);
  assert.equal(stored.get('game-banner:viewer:1:position:false'),'2');
  assert.equal(stored.has('game-banner:other:1'),false);
});

test('navigation suppresses a late banner response',async t=>{
  let resolveRead;
  t.mock.method(globalThis,'fetch',()=>new Promise(resolve=>{resolveRead=resolve;}));
  const controller=new AbortController(),output=[];
  mountGameBannerSettings({addEventListener(){},querySelectorAll:()=>[],querySelector:()=>null},2,'viewer',controller.signal,data=>output.push(data));
  controller.abort();
  resolveRead({ok:true,json:async()=>({backdrop:'late'})});
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(output,[]);
});

test('reloading settings and pin changes do not consume another cycle slot on the same page',async t=>{
  const key='game-banner:viewer:3',stored=new Map([[key,JSON.stringify({cycle:true})]]),handlers={},output=[];
  let banners=['a','b'];
  const controller=new AbortController(),input={dataset:{bannerSetting:'animated'},addEventListener:(name,fn)=>{handlers[name]=fn;}};
  t.mock.method(globalThis,'fetch',async()=>({ok:true,json:async()=>({cycle_banners:banners,background_status:{state:'ready'}})}));
  const previous=Object.getOwnPropertyDescriptor(globalThis,'localStorage');
  Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{getItem:key=>stored.get(key),setItem:(key,value)=>stored.set(key,value)}});
  t.after(()=>{controller.abort();if(previous)Object.defineProperty(globalThis,'localStorage',previous);else delete globalThis.localStorage;});
  const root={addEventListener:(name,fn)=>{handlers[name]=fn;},querySelectorAll:()=>[input],querySelector:()=>null};
  const turn=()=>new Promise(resolve=>setImmediate(resolve));
  mountGameBannerSettings(root,3,'viewer',controller.signal,data=>output.push(data.backdrop));
  await turn();banners=['b','a'];input.checked=true;handlers.change();await turn();handlers['anylist:banner-pinned']();await turn();
  assert.deepEqual(output,['a','a','a']);
  assert.equal(stored.get(key+':position:false'),'1');assert.equal(stored.has(key+':position:true'),false);
});

test('a delayed cycle response keeps the startup hold until its selected banner is ready',async t=>{
  let finish;
  t.mock.method(globalThis,'fetch',()=>new Promise(resolve=>finish=resolve));
  const previous=Object.getOwnPropertyDescriptor(globalThis,'localStorage');
  Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{getItem:()=>'{"cycle":true}',setItem(){}}});
  const controller=new AbortController(),output=[];
  t.after(()=>{controller.abort();if(previous)Object.defineProperty(globalThis,'localStorage',previous);else delete globalThis.localStorage;});
  const root={dataset:{gameBannerPending:'true'},addEventListener(){},querySelectorAll:()=>[],querySelector:()=>null};
  const settings=mountGameBannerSettings(root,7,'viewer',controller.signal,data=>output.push(data.backdrop),false,
    {backdrop:'primary',background_status:{state:'ready'}});
  assert.deepEqual(output,[]);
  assert.equal(root.dataset.gameBannerPending,'true');
  finish({ok:true,json:async()=>({backdrop:'primary',cycle_banners:['selected'],background_status:{state:'ready'}})});
  await settings.load();
  assert.deepEqual(output,['selected']);
  assert.equal(root.dataset.gameBannerPending,'true','the image commit owns the reveal after loading/decoding');
});

test('unfinished cycle acquisition stays hidden, and a failed retry restores the primary',async t=>{
  let fail=false;
  t.mock.method(globalThis,'fetch',async()=>{if(fail)throw Error('offline');return {ok:true,json:async()=>({backdrop:'primary',cycle_banners:[],background_status:{state:'pending'}})};});
  const previous=Object.getOwnPropertyDescriptor(globalThis,'localStorage');
  Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{getItem:()=>'{"cycle":true}',setItem(){}}});
  const controller=new AbortController(),output=[],handlers={},root={dataset:{gameBannerPending:'true'},addEventListener:(name,fn)=>handlers[name]=fn,querySelectorAll:()=>[],querySelector:()=>null};
  t.after(()=>{controller.abort();if(previous)Object.defineProperty(globalThis,'localStorage',previous);else delete globalThis.localStorage;});
  mountGameBannerSettings(root,7,'viewer',controller.signal,data=>output.push(data.backdrop),false,
    {backdrop:'primary',background_status:{state:'ready'}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(output,[]);
  fail=true;
  handlers['anylist:banner-pinned']();
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(output,['primary']);
});
