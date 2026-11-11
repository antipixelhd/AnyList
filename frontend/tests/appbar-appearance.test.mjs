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
