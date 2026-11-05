import assert from 'node:assert/strict';
import test from 'node:test';
import {replaceContributorSelection} from '../src/lib/contributor-selection.ts';

test('list replacement holds its height during the swap and preserves the current viewport, clamping only to the final page',t=>{
  const previous=globalThis.window;t.after(()=>globalThis.window=previous);
  const region={style:{minHeight:''},getBoundingClientRect:()=>({height:2400})};
  let maximum=3000;
  globalThis.window={scrollY:1200,scrollTo({top,behavior}){assert.equal(behavior,'instant');this.scrollY=Math.min(top,maximum);}};
  replaceContributorSelection(region,()=>{
    assert.equal(region.style.minHeight,'2400px');
    window.scrollY=0; // A layout read/anchor adjustment during replacement.
  });
  assert.equal(window.scrollY,1200);assert.equal(region.style.minHeight,'');
  replaceContributorSelection(region,()=>{maximum=500;});
  assert.equal(window.scrollY,500);assert.equal(region.style.minHeight,'');
});

test('replacement releases reserved space even if rendering fails',t=>{
  const previous=globalThis.window;t.after(()=>globalThis.window=previous);
  globalThis.window={scrollY:200,scrollTo(){}};
  const region={style:{minHeight:'10px'},getBoundingClientRect:()=>({height:500})};
  assert.throws(()=>replaceContributorSelection(region,()=>{throw new Error('render');}));
  assert.equal(region.style.minHeight,'10px');
});
