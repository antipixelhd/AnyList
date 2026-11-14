import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';

const component=readFileSync(new URL('../src/components/GameBannerStartup.astro',import.meta.url),'utf8');
const script=component.match(/<script[^>]*>([\s\S]*?)<\/script>/)[1];
function fixture(settings={cycle:true},pinned=false){
  const root={dataset:{gameBannerKey:'game-banner:viewer:7',gameBannerPinned:String(pinned)}};
  const listeners=[],reads=[];
  const context={window:{},document:{currentScript:{closest:()=>root},addEventListener:(name,fn)=>listeners.push([name,fn])},
    localStorage:{getItem:key=>{reads.push(key);return JSON.stringify(settings);}}};
  runInNewContext(script,context);
  return {root,listeners,reads,context};
}

test('cycling is held before the hero parses on the first reload',()=>{
  const {root,reads}=fixture();
  assert.equal(root.dataset.gameBannerPending,'true');
  assert.deepEqual(reads,['game-banner:viewer:7']);
});

test('disabled cycling and pinned banners keep immediate rendering',()=>{
  for(const settings of [{cycle:false},{animated:true},null]){
    assert.equal(fixture(settings).root.dataset.gameBannerPending,undefined);
  }
  const pinned=fixture({cycle:true},true);
  assert.equal(pinned.root.dataset.gameBannerPending,undefined);
  assert.deepEqual(pinned.reads,[]);
});

test('incoming revisits are held before the DOM swap, with one listener across visits',()=>{
  const {context,listeners}=fixture();
  runInNewContext(script,context);
  assert.equal(listeners.length,1);
  assert.equal(listeners[0][0],'astro:before-swap');
  const incoming={dataset:{gameBannerKey:'game-banner:other:8',gameBannerPinned:'false'}};
  listeners[0][1]({newDocument:{querySelector:()=>incoming}});
  assert.equal(incoming.dataset.gameBannerPending,'true');
  assert.doesNotThrow(()=>listeners[0][1]({newDocument:{querySelector:()=>null}}));
});

test('invalid or blocked storage leaves the primary usable',()=>{
  for(const getItem of [()=>'{',()=>{throw Error('blocked');}]){
    const {context,root}=fixture({cycle:false});
    context.localStorage.getItem=getItem;
    assert.doesNotThrow(()=>runInNewContext(script,context));
    assert.equal(root.dataset.gameBannerPending,undefined);
  }
});
