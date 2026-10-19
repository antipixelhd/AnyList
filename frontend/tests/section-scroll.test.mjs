import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import vm from 'node:vm';

function setup() {
  let now=0,id=0;
  const frames=new Map(),window=new EventTarget(),reduced=new EventTarget(),controller=new AbortController();
  reduced.matches=false;
  const context={window,scrollY:0,innerHeight:800,document:{documentElement:{scrollHeight:3000}},
    getComputedStyle:()=>({scrollMarginTop:'96px'}),matchMedia:()=>reduced,
    performance:{now:()=>now},requestAnimationFrame:f=>(frames.set(++id,f),id),cancelAnimationFrame:i=>frames.delete(i)};
  window.scrollTo=({top})=>{context.scrollY=top;};
  const source=['gallery-spring','section-scroll'].map(name=>readFileSync(new URL(`../src/lib/${name}.ts`,import.meta.url),'utf8').replace(/^import .*;\r?\n/gm,'').replace(/export /g,'')).join('\n');
  vm.runInNewContext(stripTypeScriptTypes(source)+';globalThis.create=createSectionScroll;',context);
  const scroll=context.create(controller.signal);
  const target=top=>({getBoundingClientRect:()=>({top:top-context.scrollY})});
  const advance=ms=>{now+=ms;const work=[...frames.values()];frames.clear();work.forEach(f=>f(now));};
  return {scroll,target,advance,context,frames,window,reduced,controller};
}

test('section scroll arrives quickly without overshoot and respects sticky header clearance',()=>{
  const s=setup();s.scroll(s.target(1200));let previous=0;
  for(let i=0;i<20;i++){s.advance(16);assert.ok(s.context.scrollY>=previous&&s.context.scrollY<=1104);previous=s.context.scrollY;}
  assert.ok(1104-s.context.scrollY<1,'within one pixel after 320ms');
  s.advance(100);assert.equal(s.context.scrollY,1104);assert.equal(s.frames.size,0);
});
test('user input interrupts immediately and a later click starts at the actual scroll position',()=>{
  const s=setup();s.scroll(s.target(1200));s.advance(50);
  s.window.dispatchEvent(new Event('touchstart'));const interrupted=s.context.scrollY;s.advance(500);
  assert.equal(s.context.scrollY,interrupted);assert.equal(s.frames.size,0);
  s.context.scrollY=500;s.scroll(s.target(1600));assert.equal(s.context.scrollY,500);s.advance(500);assert.equal(s.context.scrollY,1504);
  s.scroll(s.target(500));s.controller.abort();assert.equal(s.frames.size,0);
});
test('reduced motion jumps immediately, preference changes finish, and destinations are bounded',()=>{
  const s=setup();s.reduced.matches=true;s.scroll(s.target(4000));assert.equal(s.context.scrollY,2200);assert.equal(s.frames.size,0);
  s.reduced.matches=false;s.scroll(s.target(0));s.advance(40);s.reduced.matches=true;s.reduced.dispatchEvent(new Event('change'));
  assert.equal(s.context.scrollY,0);assert.equal(s.frames.size,0);
});
