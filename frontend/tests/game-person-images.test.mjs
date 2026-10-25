import assert from 'node:assert/strict';
import test from 'node:test';
import {mountGamePersonImages} from '../src/lib/game-person-images.ts';

function fixture(cards,fetcher){
  const previous=Object.fromEntries(['Image','IntersectionObserver','MutationObserver','fetch','setTimeout','clearTimeout'].map(key=>[key,globalThis[key]]));
  const images=[],timers=[],observers=[];let mutation;
  globalThis.Image=class {
    constructor(){this.attributes={};images.push(this);}
    setAttribute(key,value){this.attributes[key]=value;}
    removeAttribute(key){delete this.attributes[key];}
  };
  globalThis.IntersectionObserver=class{
    constructor(callback){this.callback=callback;this.nodes=[];observers.push(this);}
    observe(node){this.nodes.push(node);}
    unobserve(node){this.nodes=this.nodes.filter(value=>value!==node);}
    disconnect(){this.nodes=[];this.disconnected=true;}
  };
  globalThis.MutationObserver=class{constructor(callback){mutation=callback;}observe(){}disconnect(){}};
  globalThis.setTimeout=(callback,ms)=>{const timer={callback,ms};timers.push(timer);return timer;};
  globalThis.clearTimeout=timer=>{const index=timers.indexOf(timer);if(index>=0)timers.splice(index,1);};
  globalThis.fetch=fetcher;
  const controller=new AbortController();
  mountGamePersonImages({querySelectorAll:()=>cards},controller.signal);
  const tick=async()=>{const timer=timers.shift();if(timer)await timer.callback();for(let i=0;i<12;i++)await Promise.resolve();};
  return {images,timers,observers,controller,tick,mutate:()=>mutation(),
    expose(nodes=observers[0].nodes){observers[0].callback(nodes.map(target=>({target,isIntersecting:true})));},
    cleanup(){controller.abort();for(const [key,value] of Object.entries(previous))if(value===undefined)delete globalThis[key];else globalThis[key]=value;}};
}

function card(id,kind='actor',existing=false){
  const fallback={hidden:false};
  const node={dataset:{personId:String(id),personKind:kind},isConnected:true,images:existing?[{}]:[],fallback,
    matches:()=>false,hasAttribute:()=>false,
    prepend(image){this.images.push(image);},
    querySelector(selector){
      if(selector==='img[src],img[data-deferred-src]')return this.images[0]||null;
      if(selector==='.media-person-art')return kind==='actor'?this:null;
      return fallback;
    }};
  return node;
}

test('visible people share bounded batches; existing photos, characters and hidden crew cause no work',async()=>{
  const nodes=[...Array.from({length:20},(_,i)=>card(i+1)),card(90,'character'),card(91,'studio'),card(92,'actor',true),card(93,'staff')];
  const calls=[];
  const f=fixture(nodes,async(_,options)=>{
    const ids=JSON.parse(options.body).ids;calls.push(ids);
    return Response.json({people:ids.map(id=>({id,image:`https://m.media-amazon.com/images/M/${id}._V1_.jpg`,state:'ready'}))});
  });
  try{
    assert.equal(f.observers[0].nodes.length,21);
    f.expose(nodes.slice(0,23));await f.tick();await f.tick();
    assert.deepEqual(calls.map(ids=>ids.length),[16,4]);
    assert.equal(f.images.length,20);
    f.images.forEach(image=>image.onload());
    assert(nodes.slice(0,20).every(node=>node.images.length===1&&node.fallback.hidden));
    assert.equal(nodes[23].images.length,0);
    f.mutate();assert.equal(calls.length,2);
    f.controller.abort();assert(f.observers[0].disconnected);
  }finally{f.cleanup();}
});

test('late image and request responses cannot replace a newer portrait or update a departed page',async()=>{
  const node=card(1);let finish;
  const f=fixture([node],()=>new Promise(resolve=>{finish=resolve;}));
  try{
    f.expose();await f.tick();
    finish(Response.json({people:[{id:1,image:'https://m.media-amazon.com/images/M/one._V1_.jpg',state:'ready'}]}));
    for(let i=0;i<12;i++)await Promise.resolve();
    assert.equal(f.images.length,1);
    node.images.push({chosen:true});f.images[0].onload();
    assert.deepEqual(node.images,[{chosen:true}]);
    f.controller.abort();assert.equal(f.timers.length,0);
  }finally{f.cleanup();}
});

test('verified empty and failed portraits stop, pending leases retry with a bounded backoff and abort cleanup',async()=>{
  const calls=[];
  const f=fixture([card(1),card(2),card(3)],async(_,options)=>{
    const ids=JSON.parse(options.body).ids;calls.push(ids);
    return Response.json({people:ids.map(id=>({id,image:null,state:id===1?'ready':id===2?'retry':'pending'}))});
  });
  try{
    f.expose();await f.tick();
    assert.deepEqual(calls,[[1,2,3]]);assert.equal(f.timers[0].ms,3000);
    for(let i=0;i<12;i++)await f.tick();
    assert.equal(calls.length,7);assert(calls.slice(1).every(ids=>ids.length===1&&ids[0]===3));
    assert.equal(f.timers.length,0);assert.equal(f.images.length,0);
  }finally{f.cleanup();}
});

test('broken CDN images do not retry on every mutation and aborted requests never paint',async()=>{
  const node=card(1);
  const f=fixture([node],async()=>Response.json({people:[{id:1,image:'https://m.media-amazon.com/images/M/one._V1_.jpg',state:'ready'}]}));
  try{
    f.expose();await f.tick();f.images[0].onerror();
    f.mutate();f.mutate();assert.equal(f.images.length,1);assert.equal(node.images.length,0);
  }finally{f.cleanup();}
  let finish;
  const second=fixture([card(2)],()=>new Promise(resolve=>{finish=resolve;}));
  try{
    second.expose();await second.tick();second.controller.abort();
    finish(Response.json({people:[{id:2,image:'https://m.media-amazon.com/images/M/two._V1_.jpg',state:'ready'}]}));
    for(let i=0;i<12;i++)await Promise.resolve();
    assert.equal(second.images.length,0);assert.equal(second.timers.length,0);
  }finally{second.cleanup();}
});
