import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import vm from 'node:vm';

function fixture() {
 let now=0,id=0,observer;const frames=new Map(),controller=new AbortController();
 const element={style:{},removed:false,setAttribute(){},remove(){this.removed=true;}};
 const media={matches:false,addEventListener(){}};
 const context={document:{createElement:()=>element},matchMedia:()=>media,
  performance:{now:()=>now},requestAnimationFrame:f=>(frames.set(++id,f),id),cancelAnimationFrame:i=>frames.delete(i),
  getComputedStyle:()=>({getPropertyValue:key=>key==='--preview-selected-scale'?'1.17':'4'}),
  ResizeObserver:class{disconnected=false;constructor(callback){observer=this;this.callback=callback;}observe(){}disconnect(){this.disconnected=true;}}};
 const spring=readFileSync(new URL('../src/lib/gallery-spring.ts',import.meta.url),'utf8');
 const selection=readFileSync(new URL('../src/lib/gallery-selection.ts',import.meta.url),'utf8').replace(/^import .*;\r?\n/,'');
 vm.runInNewContext(stripTypeScriptTypes((spring+'\n'+selection).replace(/export /g,''))+';globalThis.create=createGallerySelection;globalThis.bounds=gallerySelectionBounds;globalThis.barBounds=galleryBarBounds;',context);
 const handlers={};
 const strip={hidden:false,clientWidth:180,clientHeight:86,scrollLeft:0,querySelectorAll:()=>[button(16),button(124)],addEventListener:(event,callback)=>{handlers[event]=callback;},closest:()=>({addEventListener(){}})};
 const highlight=context.create(strip,controller.signal);
 const advance=ms=>{now+=ms;const work=[...frames.values()];frames.clear();work.forEach(f=>f(now));};
 return {highlight,element,media,frames,strip,handlers,advance,controller,bounds:context.bounds,barBounds:context.barBounds,resize:()=>observer.callback(),observer:()=>observer};
}
const button=x=>({offsetLeft:x,offsetTop:12,offsetWidth:44,offsetHeight:62});
const x=s=>Number([...s.element.style.clipPath.matchAll(/M ([-\d.]+)/g)][1][1]);

test('frame padding stays four pixels around scaled portrait and landscape thumbnails',()=>{
 const s=fixture();
 for(const [width,height] of [[44,62],[72,42]]){
  const b=s.bounds(20,12,width,height,1.17,4);
  assert.ok(Math.abs((b.width-width*1.17)/2-4)<1e-9);
  assert.ok(Math.abs((b.height-height*1.17)/2-4)<1e-9);
  assert.ok(Math.abs(b.x+b.width/2-(20+width/2))<1e-9);
 }
});

test('first selection snaps; later and rapidly retargeted selections glide from the current position',()=>{
 const s=fixture();s.highlight.update(button(12));const first=x(s);assert.equal(s.frames.size,0);
 s.highlight.update(button(68));assert.equal(x(s),first);s.advance(40);const middle=x(s);assert.ok(middle>first&&middle<60.26);
 s.highlight.update(button(124));assert.equal(x(s),middle);s.advance(1000);assert.ok(Math.abs(x(s)-116.26)<.001);
 assert.equal(s.frames.size,0);assert.equal(s.element.hidden,false);
});

test('resize and reduced motion place the frame immediately; teardown cancels pending work',()=>{
 const s=fixture(),selected=button(12);s.highlight.update(selected);selected.offsetLeft=40;s.resize();assert.ok(Math.abs(x(s)-32.26)<.001);
 s.media.matches=true;s.highlight.update(button(68));assert.equal(s.frames.size,0);assert.ok(Math.abs(x(s)-60.26)<.001);
 s.media.matches=false;s.highlight.update(button(124));assert.ok(s.frames.size>0);s.controller.abort();assert.equal(s.frames.size,0);assert.equal(s.observer().disconnected,true);assert.equal(s.element.removed,true);
});

test('scrolling keeps the bar under its viewport while its raised area stays attached to the selected image',()=>{
 const s=fixture();s.highlight.update(button(68));const before=x(s);
 s.strip.scrollLeft=20;s.handlers.scroll();
 const shapes=[...s.element.style.clipPath.matchAll(/M ([-\d.]+)/g)];
 assert.equal(shapes.length,2,'one clipping region combines the bar and raised area');
 assert.equal(Number(shapes[0][1]),20);assert.equal(x(s),before);assert.equal(s.frames.size,0);
});

test('side gutters leave clearance for the rounded shape around both portrait and wide previews',()=>{
 const s=fixture();
 for(const [width,height] of [[44,62],[72,42]]){
  const b=s.bounds(16,12,width,height,1.17,4);
  assert.ok(b.x>=4,'rounded enlarged preview keeps at least four pixels clear of the strip edge');
  const right=16+width+16;
  assert.ok(right-(b.x+b.width)>=4);
 }
});

test('visible background uses the same padding at its first, last, top and bottom edges',()=>{
 const s=fixture();
 for(const [width,height] of [[44,62],[72,42]]){
  const first=16,last=16+width+12;
  const b=s.barBounds(first,last+width,12,height,4,0,500);
  assert.equal(first-b.x,4);assert.equal(b.x+b.width-(last+width),4);
  assert.equal(12-b.y,4);assert.equal(b.y+b.height-(12+height),4);
 }
});

test('side gutters do not enlarge the painted background and scrolling clips only unseen content',()=>{
 const s=fixture();
 const a=s.barBounds(16,172,12,42,4,0,188);
 const b=s.barBounds(24,180,12,42,4,0,204);
 assert.equal(a.width,b.width);assert.equal(b.x-a.x,8);
 const clipped=s.barBounds(16,500,12,42,4,100,180);
 assert.equal(clipped.x,100);assert.equal(clipped.width,180);
});
