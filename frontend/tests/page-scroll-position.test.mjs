import assert from 'node:assert/strict';
import test from 'node:test';
import {pageScrollPosition} from '../src/lib/page-scroll-position.ts';

test('gallery locking and unlocking preserve the navigation scroll baseline',()=>{
  const before=pageScrollPosition(520,{position:'',top:''});
  const locked=pageScrollPosition(0,{position:'fixed',top:'-520px'});
  const restored=pageScrollPosition(520,{position:'',top:''});
  assert.equal(before,520);
  assert.equal(locked-before,0);
  assert.equal(restored-locked,0);
});

test('a gallery opened at the top remains at the top and invalid locks use native scroll',()=>{
  assert.equal(pageScrollPosition(0,{position:'fixed',top:'0px'}),0);
  assert.equal(pageScrollPosition(85,{position:'fixed',top:'auto'}),85);
  assert.equal(pageScrollPosition(85,{position:'relative',top:'-520px'}),85);
  assert.equal(pageScrollPosition(-10,{position:'',top:''}),0);
});
