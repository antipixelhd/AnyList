import test from 'node:test';
import assert from 'node:assert/strict';
import {createPrediction,predictionAllowed,predictionManifest,predictivePage,paginationPosition,bindPredictivePagination} from '../src/lib/predictive-loading.ts';

test('prediction shares an in-flight batch with demand and is consumed once',async()=>{
  let resolve,calls=0;
  const lifetime=new AbortController();
  const prediction=createPrediction(()=>{calls++;return new Promise(yes=>resolve=yes);},lifetime.signal,()=>true);
  prediction.prefetch('next');prediction.prefetch('next');
  const demand=prediction.consume('next',lifetime.signal);
  resolve({works:[1,2]});
  assert.deepEqual(await demand,{works:[1,2]});assert.equal(calls,1);
  assert.equal(await prediction.consume('next',lifetime.signal),undefined);
});

test('prediction is bounded, expires and discards late cancelled results',async()=>{
  let now=0;const calls=[];const lifetime=new AbortController();
  const prediction=createPrediction((key,signal)=>{let resolve;const promise=new Promise(yes=>resolve=yes);calls.push({key,signal,resolve});return promise;},lifetime.signal,()=>true,()=>now,30);
  prediction.prefetch('first');prediction.prefetch('second');
  assert.equal(calls[0].signal.aborted,true);calls[0].resolve('obsolete');
  assert.equal(prediction.has('first'),false);
  now=31;assert.equal(prediction.has('second'),false);assert.equal(calls[1].signal.aborted,true);
  prediction.prefetch('third');lifetime.abort();assert.equal(calls[2].signal.aborted,true);
  assert.equal(prediction.has('third'),false);
});

test('offline, data saving and slow networks skip speculative requests',()=>{
  for(const network of [{onLine:false},{connection:{saveData:true}},{connection:{effectiveType:'3g'}},{connection:{effectiveType:'2g'}}])assert.equal(predictionAllowed(network),false);
  assert.equal(predictionAllowed({connection:{effectiveType:'4g'}}),true);
  let calls=0;createPrediction(async()=>{calls++;},new AbortController().signal,()=>false).prefetch('next');assert.equal(calls,0);
});

test('failed speculation falls back and cancelled demand rejects',async()=>{
  const lifetime=new AbortController();
  const failed=createPrediction(async()=>{throw new Error('offline');},lifetime.signal,()=>true);
  failed.prefetch('next');assert.equal(await failed.consume('next',lifetime.signal),undefined);
  const pending=createPrediction(()=>new Promise(()=>{}),lifetime.signal,()=>true);pending.prefetch('next');
  const request=new AbortController();const result=pending.consume('next',request.signal);request.abort();
  await assert.rejects(result,{name:'AbortError'});
});

test('manifest exposes only two primary images for the current identity',()=>{
  const html='<img src="/extra-poster"><script id="page-prediction" type="application/json">'+JSON.stringify({user:'7',artwork:[{src:'/api/proxy/media/image/one',srcset:'one 100w',sizes:'100vw'},'https://image.tmdb.org/two','https://image.tmdb.org/extra']})+'</script>';
  assert.equal(predictionManifest(html,'8'),undefined);
  assert.equal(predictionManifest(html,'7').length,2);
  assert.equal(predictionManifest(html,'7')[0].srcset,'one 100w');
  assert.equal(predictionManifest('<script id="page-prediction">broken</script>','7'),undefined);
});

test('only same-origin title and contributor pages can be predicted',()=>{
  const origin='https://anylist.test';
  for(const path of ['/title/1','/games/7','/actors/tmdb%3A123','/person/catalogue:7','/studio/tmdb:12'])assert.equal(predictivePage(new URL(path,origin),origin),true);
  for(const path of ['/logout','/discover-title?id=7','/title/0','/title/1#gallery','https://other.test/title/1'])assert.equal(predictivePage(new URL(path,origin),origin),false);
});

test('pagination predicts at 70% and appends within 500px of the end',()=>{
  assert.deepEqual(paginationPosition({top:-1399,height:3000,bottom:1601},1000),{predict:false,append:false});
  assert.deepEqual(paginationPosition({top:-1400,height:3000,bottom:1600},1000),{predict:true,append:false});
  assert.deepEqual(paginationPosition({top:-1500,height:3000,bottom:1500},1000),{predict:true,append:true});
});

test('people pagination requires downward intent and never loops on layout changes',()=>{
  const original={window:globalThis.window,requestAnimationFrame:globalThis.requestAnimationFrame,cancelAnimationFrame:globalThis.cancelAnimationFrame};
  const surface=new EventTarget();surface.scrollY=0;surface.innerHeight=1000;
  let frame,predictions=0,appends=0;
  globalThis.window=surface;globalThis.requestAnimationFrame=callback=>{frame=callback;return 1;};globalThis.cancelAnimationFrame=()=>{};
  try{
    const lifetime=new AbortController();
    bindPredictivePagination({getBoundingClientRect:()=>({top:-1600,height:3000,bottom:1400})},lifetime.signal,()=>true,()=>predictions++,()=>appends++);
    assert.equal(appends,0);
    surface.scrollY=100;surface.dispatchEvent(new Event('scroll'));frame();
    assert.equal(predictions,1);assert.equal(appends,1);frame=undefined;
    surface.dispatchEvent(new Event('scroll'));assert.equal(frame,undefined);
    surface.scrollY=90;surface.dispatchEvent(new Event('scroll'));assert.equal(frame,undefined);
    lifetime.abort();surface.scrollY=200;surface.dispatchEvent(new Event('scroll'));assert.equal(frame,undefined);
  }finally{Object.assign(globalThis,original);}
});
