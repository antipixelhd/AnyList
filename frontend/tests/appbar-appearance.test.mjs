import assert from 'node:assert/strict';
import test from 'node:test';
import {carryAppbarAppearance} from '../src/lib/appbar-appearance.ts';

test('navigation starts from the painted background, including a fade already in progress',()=>{
  for(const color of ['rgb(20, 33, 49)','rgba(20, 33, 49, 0.12)','rgba(20, 33, 49, 0.6)']){
    let measured=false;
    const previous={ownerDocument:{defaultView:{matchMedia:()=>({matches:false}),getComputedStyle:()=>({backgroundColor:color})}}};
    const incoming={style:{removeProperty(name){assert.equal(name,'background-color');assert(measured);delete this.backgroundColor;}},get offsetWidth(){measured=true;return 800;}};
    const release=carryAppbarAppearance(previous,incoming);
    assert.equal(incoming.style.backgroundColor,color);
    release();
    assert.equal(incoming.style.backgroundColor,undefined);
  }
});

test('mobile and reduced motion leave the destination appearance immediate',()=>{
  for(const preference of ['max-width','prefers-reduced-motion']){
    const previous={ownerDocument:{defaultView:{matchMedia:q=>({matches:q.includes(preference)}),getComputedStyle(){throw Error('Must not seed animation');}}}};
    const incoming={style:{}};
    assert.equal(carryAppbarAppearance(previous,incoming),undefined);
    assert.deepEqual(incoming.style,{});
  }
});

test('profile tab swaps carry the visible position even on mobile',()=>{
  let measured=false;
  const previous={ownerDocument:{defaultView:{matchMedia:q=>({matches:q.includes('max-width')}),
    getComputedStyle:()=>({transform:'matrix(1, 0, 0, 1, 0, -24)'})}}};
  const incoming={style:{removeProperty(name){assert.equal(name,'transform');assert(measured);delete this.transform;}},
    get offsetWidth(){measured=true;return 390;}};
  const release=carryAppbarAppearance(previous,incoming,true);
  assert.equal(incoming.style.transform,'matrix(1, 0, 0, 1, 0, -24)');
  release();
  assert.equal(incoming.style.transform,undefined);
});
