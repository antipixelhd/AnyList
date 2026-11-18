import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';

const source=stripTypeScriptTypes(readFileSync(new URL('../src/lib/screen-list-seasons.ts',import.meta.url),'utf8')
  .replace(/^import [^\r\n]+\r?\n/gm,'').replace('export function','function'));

function fixture(t){
  const lifetime=new AbortController(),profile=new EventTarget(),window=new EventTarget();
  const document=Object.assign(new EventTarget(),{createElement:()=>({dataset:{},setAttribute(){},
    replaceChildren(){},getBoundingClientRect:()=>({height:0}),remove(){this.isConnected=false;}})});
  const buttons=new Map(),rendered=new Set();
  const root=Object.assign(new EventTarget(),{dataset:{owner:'true',listEndpoint:'profile/test/all',screenScope:'all'},
    querySelectorAll:selector=>selector==='.list-season-panel'?[...rendered].filter(panel=>panel.isConnected):
      [...buttons.keys()].filter(toggle=>toggle.attributes.get('aria-expanded')==='true')});
  function row(id){
    const entry={isConnected:true,dataset:{listEntry:String(id)},hasAttribute:()=>false,
      append(panel){panel.isConnected=true;rendered.add(panel);},
      querySelector:selector=>selector.startsWith('[data-season-toggle]')?toggle:null};
    const attributes=new Map([['aria-expanded','false']]);
    const toggle={attributes,closest:()=>entry,setAttribute:(key,value)=>attributes.set(key,value),
      removeAttribute:key=>attributes.delete(key)};
    buttons.set(toggle,entry);
    return {entry,toggle,get open(){return attributes.get('aria-expanded')==='true';}};
  }
  let reads=0;
  const mount=runInNewContext(`${source}\nmountScreenListSeasons`,{window,document,AbortController,AbortSignal,
    setTimeout,clearTimeout,queueMicrotask,panelActive:()=>true,matchMedia:()=>({matches:true}),
    fetch:async()=>{reads++;return {ok:true,json:async()=>({order:'tmdb',seasons:[],pending:false})};}});
  const dispatchClick=({toggle})=>{
    const event=new Event('click');Object.defineProperty(event,'target',{value:{closest:()=>toggle}});
    root.dispatchEvent(event);
  };
  t.after(()=>lifetime.abort());
  return {row,mount:()=>mount(root,profile,{},lifetime.signal),profile,window,document,click:dispatchClick,
    get reads(){return reads;}};
}

test('season visibility is visit-local while cached summaries and edited rows remain usable',async t=>{
  const f=fixture(t),first=f.row(12);
  first.toggle.setAttribute('aria-expanded','true'); // Markup from an older saved tab.
  first.toggle.setAttribute('aria-controls','old-panel');
  const controller=f.mount();
  assert.equal(first.open,false);
  assert.equal(first.toggle.attributes.has('aria-controls'),false);
  controller.observe(first.entry);
  assert.equal(f.reads,0);

  f.click(first);
  await new Promise(setImmediate);
  assert.equal(first.open,true);
  assert.equal(f.reads,1);
  const savedRow=f.row(12),savedPanel={remove(){this.removed=true;}};
  savedRow.toggle.setAttribute('aria-expanded','true');
  savedRow.toggle.setAttribute('aria-controls','saved-panel');
  controller.resetSnapshot({querySelectorAll:selector=>selector==='.list-season-panel'?[savedPanel]:[savedRow.toggle]});
  assert.equal(savedPanel.removed,true);
  assert.equal(savedRow.open,false);
  assert.equal(first.open,true,'snapshot capture does not close the visible row');
  f.document.dispatchEvent(new CustomEvent('anylist:editor-committed',{detail:{title:{id:12,type:'series',entry:{}},patch:{status:'completed'}}}));
  assert.equal(first.open,true);
  controller.remove(first.entry);
  first.entry.isConnected=false;
  await new Promise(setImmediate);
  const remounted=f.row(12);
  controller.observe(remounted.entry);
  assert.equal(remounted.open,true);
  assert.equal(f.reads,2,'the edit revalidated the summary once, virtualization reused it');

  f.profile.dispatchEvent(new Event('anylist:panel-inactive'));
  controller.observe(remounted.entry);
  assert.equal(remounted.open,false);
  f.click(remounted);
  assert.equal(f.reads,2,'opening manually on return reuses the summary');
  f.window.dispatchEvent(Object.assign(new Event('pageshow'),{persisted:true}));
  controller.observe(remounted.entry);
  assert.equal(remounted.open,false);
});
