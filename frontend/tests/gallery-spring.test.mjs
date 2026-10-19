import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import vm from 'node:vm';
function spring(options={}){
 let now=0,id=0;const frames=new Map(),painted=[],controller=new AbortController();
 const context={performance:{now:()=>now},requestAnimationFrame:f=>(frames.set(++id,f),id),cancelAnimationFrame:i=>frames.delete(i)};
 const source=readFileSync(new URL('../src/lib/gallery-spring.ts',import.meta.url),'utf8');
 vm.runInNewContext(stripTypeScriptTypes(source.replace(/export /g,''))+';globalThis.create=createGallerySpring;',context);
 const motion=context.create({x:0},value=>painted.push(value.x),controller.signal,options);
 const advance=ms=>{now+=ms;const work=[...frames.values()];frames.clear();work.forEach(f=>f(now));};
 return {motion,advance,painted,controller,frames};
}
test('retargeting preserves both position and velocity rather than restarting an easing curve',()=>{
 const s=spring();s.motion.to({x:100});s.advance(40);const before=s.motion.read();
 s.motion.to({x:-100});const after=s.motion.read();assert.equal(after.value.x,before.value.x);assert.equal(after.velocity.x,before.velocity.x);assert.ok(after.velocity.x>0);
 s.advance(1);assert.ok(s.motion.read().value.x>before.value.x);s.advance(1000);assert.equal(s.motion.read().value.x,-100);assert.equal(s.frames.size,0);
});
test('gesture velocity is carried into settling and stale held gestures have no momentum',()=>{
 const s=spring();s.motion.drag({x:0});s.advance(20);s.motion.drag({x:20});assert.equal(s.motion.read().velocity.x,1000);
 s.motion.to({x:100},{gesture:true});assert.equal(s.motion.read().velocity.x,1000);
 s.motion.stop();s.motion.drag({x:20});s.advance(20);s.motion.drag({x:40});s.advance(150);s.motion.to({x:100},{gesture:true});assert.equal(s.motion.read().velocity.x,0);
});
test('critical damping from rest never bounces and bounds prohibit invalid geometric values',()=>{
 const s=spring({bounds:{x:{min:0,max:100}}});s.motion.to({x:100});let previous=0;
 for(let i=0;i<60;i++){s.advance(16);const current=s.motion.read().value.x;assert.ok(current>=previous&&current<=100);previous=current;}
 s.motion.to({x:0},{velocity:{x:-10000}});s.advance(30);assert.equal(s.motion.read().value.x,0);assert.equal(s.motion.read().velocity.x,0);
});
test('immediate transitions and aborted animations leave no frames or obsolete completion callbacks',()=>{
 const s=spring();let done=0;s.motion.to({x:100},{done:()=>done++});s.advance(40);s.motion.to({x:20},{immediate:true,done:()=>done++});assert.equal(s.motion.read().value.x,20);assert.equal(s.frames.size,0);assert.equal(done,1);
 s.motion.to({x:200},{done:()=>done++});s.controller.abort();s.advance(1000);assert.equal(done,1);assert.equal(s.frames.size,0);
});

test('finishing for a changed reduced-motion preference completes once without remaining frames',()=>{
 const s=spring();let done=0;s.motion.to({x:100},{done:()=>done++});s.advance(50);s.motion.finish();
 assert.equal(s.motion.read().value.x,100);assert.equal(done,1);assert.equal(s.frames.size,0);s.advance(1000);assert.equal(done,1);
});

test('an out-of-range target is bounded and still reaches rest without leaking frames',()=>{
 const s=spring({bounds:{x:{min:0,max:100}}});let done=0;s.motion.to({x:1000},{done:()=>done++});s.advance(1000);
 assert.equal(s.motion.read().value.x,100);assert.equal(done,1);assert.equal(s.frames.size,0);
});
