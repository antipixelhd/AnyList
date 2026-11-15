import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import test from 'node:test';

test('plain row clicks retain the hovered cover while links and controls dismiss it',()=>{
  const source=readFileSync(new URL('../src/lib/list-cover-preview.ts',import.meta.url),'utf8')
    .replace(/^import .*\r?\n/gm,'').replace('export function','function');
  const handlers=new Map(),preview={hidden:true,style:{},querySelector:()=>({})};
  const row={dataset:{},contains:()=>false,
    querySelector:()=>({dataset:{previewSrc:'/poster.jpg'},getBoundingClientRect:()=>({left:250,right:290,top:100,height:40})})};
  const target=(interactive=false)=>({closest:selector=>selector==='.entry-row'?row:
    selector==='a,button,input,select,textarea,[role="button"]'&&interactive?{}:null});
  const context={applyResponsiveArtwork(){},matchMedia:()=>({matches:true}),innerWidth:1280,innerHeight:900,
    document:{querySelector:selector=>selector==='#cover-preview'?preview:{open:false},
      addEventListener:(name,callback)=>handlers.set(name,callback)},window:{addEventListener(){}}};
  runInNewContext(stripTypeScriptTypes(source),context);
  context.mountListCoverPreview(new AbortController().signal);
  const plain=target();
  handlers.get('pointerover')({target:plain,relatedTarget:null});
  assert.equal(preview.hidden,false);
  for(let clicks=0;clicks<2;clicks++){
    handlers.get('click')({target:plain});
    assert.equal(preview.hidden,false);
  }
  handlers.get('click')({target:target(true)});
  assert.equal(preview.hidden,true);
  handlers.get('pointerout')({target:plain,relatedTarget:null});
  handlers.get('pointerover')({target:plain,relatedTarget:null});
  assert.equal(preview.hidden,false);
  handlers.get('pointerout')({target:plain,relatedTarget:null});
  assert.equal(preview.hidden,true);
});

test('holding or cancelling the edit button keeps the cover preview until a confirmed click',()=>{
  const source=readFileSync(new URL('../src/lib/list-cover-preview.ts',import.meta.url),'utf8')
    .replace(/^import .*\r?\n/gm,'').replace('export function','function');
  for(const attribute of ['[data-edit-entry]','[data-edit-game]']){
    const handlers=new Map(),preview={hidden:false,querySelector:()=>({})};
    const row={dataset:{}};
    const button={closest:selector=>selector==='.entry-row'?row:selector.includes(attribute)?button:null};
    const context={document:{querySelector:selector=>selector==='#cover-preview'?preview:{open:false},addEventListener:(name,callback)=>handlers.set(name,callback)},window:{addEventListener(){}}};
    runInNewContext(stripTypeScriptTypes(source),context);
    context.mountListCoverPreview(new AbortController().signal);
    const fire=name=>handlers.get(name)?.({target:button});
    fire('pointerdown');fire('focusin');assert.equal(preview.hidden,false);
    fire('pointercancel');assert.equal(preview.hidden,false);
    fire('pointerdown');fire('pointerup');assert.equal(preview.hidden,false);
    fire('click');assert.equal(preview.hidden,true);
  }
});

test('expanding seasons preserves the hovered poster through layout scrolling and row changes',()=>{
  const source=readFileSync(new URL('../src/lib/list-cover-preview.ts',import.meta.url),'utf8')
    .replace(/^import .*\r?\n/gm,'').replace('export function','function');
  const handlers=new Map(),windowHandlers=new Map(),posters=[];
  const preview={hidden:true,style:{},querySelector:()=>({})};
  const row=poster=>({dataset:{},contains:()=>false,matches:()=>true,
    querySelector:()=>({dataset:{previewSrc:poster},getBoundingClientRect:()=>({left:250,right:290,top:100,height:40})})});
  const series=row('/series.jpg'),season=row('/season.jpg');
  const toggle={closest:selector=>selector==='.entry-row'?series:selector==='[data-season-toggle]'?toggle:null};
  const seasonTarget={closest:selector=>selector==='.entry-row'?season:null};
  const context={
    applyResponsiveArtwork:(_image,poster)=>posters.push(poster),
    matchMedia:()=>({matches:true}),innerWidth:1280,innerHeight:900,
    document:{querySelector:selector=>selector==='#cover-preview'?preview:{open:false},
      addEventListener:(name,callback)=>handlers.set(name,callback)},
    window:{addEventListener:(name,callback)=>windowHandlers.set(name,callback)},
  };
  runInNewContext(stripTypeScriptTypes(source),context);
  const lifetime=new AbortController();
  context.mountListCoverPreview(lifetime.signal);
  handlers.get('pointerover')({target:toggle,relatedTarget:null});
  assert.equal(preview.hidden,false);
  for(let clicks=0;clicks<2;clicks++){
    handlers.get('click')({target:toggle});
    windowHandlers.get('scroll')();
    assert.equal(preview.hidden,false);
    assert.equal(posters.at(-1),'/series.jpg');
  }
  assert.equal(posters.length,1);
  handlers.get('pointerout')({target:toggle,relatedTarget:null});
  handlers.get('pointerover')({target:seasonTarget,relatedTarget:null});
  assert.equal(preview.hidden,false);
  assert.equal(posters.at(-1),'/season.jpg');
  handlers.get('pointerout')({target:seasonTarget,relatedTarget:null});
  assert.equal(preview.hidden,true);
  lifetime.abort();
});
