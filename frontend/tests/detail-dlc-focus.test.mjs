import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';

for(const moved of [false,true])test(`empty DLC retry restores focus unless the viewer moved it: ${moved}`,async()=>{
  const document={body:{},activeElement:null,addEventListener(){}};
  const outside={},rows={},status={},tab={hidden:false,tabIndex:0,getAttribute(){return 'true';}};
  let focused=false,selected=false,release;
  const more={addEventListener(){},set disabled(value){if(value&&document.activeElement===this)document.activeElement=document.body;}};
  const overview={click(){selected=true;},focus(){focused=true;document.activeElement=this;}};
  const host={contains(node){return node===more;},querySelector(selector){return selector.includes('-rows')?rows:selector.includes('-status')?status:more;}};
  const root={addEventListener(){},querySelector(selector){return selector==='[data-game-dlc]'?host:selector.includes('"dlc"')?tab:overview;}};
  const controller=new AbortController();
  const context={document,editorStore:{},setTimeout(){return 0;},clearTimeout(){},fetch:()=>new Promise(resolve=>release=resolve)};
  const source=readFileSync(new URL('../src/lib/media-detail-dlc.ts',import.meta.url),'utf8');
  runInNewContext(stripTypeScriptTypes(source.replace(/^import .*$/gm,'').replace(/export /g,''))+';globalThis.mount=mountDetailDlc;',context);
  document.activeElement=more;
  context.mount(root,42,controller.signal)();
  assert.equal(document.activeElement,document.body,'disabling Retry removes its native focus');
  if(moved)document.activeElement=outside;
  release(Response.json({results:[],total:0,coverage:'complete',has_more:false}));
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(tab.hidden,true);assert.equal(selected,true);
  assert.equal(focused,!moved);
  assert.equal(document.activeElement,moved?outside:overview);
});


test('DLC stays hidden until matching primary evidence confirms playable children, without fetching cards',()=>{
  const handlers={},controller=new AbortController();let requests=0;
  const tab={hidden:true,disabled:true,tabIndex:-1,dataset:{}};
  const host={querySelector:()=>({addEventListener(){}})};
  const root={querySelector:s=>s==='[data-game-dlc]'?host:tab,addEventListener:(name,fn)=>handlers[name]=fn};
  const context={document:{addEventListener(){}},fetch(){requests++;}};
  const source=readFileSync(new URL('../src/lib/media-detail-dlc.ts',import.meta.url),'utf8');
  runInNewContext(stripTypeScriptTypes(source.replace(/^import .*$/gm,'').replace(/export /g,''))+';globalThis.mount=mountDetailDlc;',context);
  context.mount(root,42,controller.signal);
  const publish=data=>handlers['anylist:game-primary']({detail:data});
  publish({id:42});publish({id:42,has_game_children:false});publish({id:99,has_game_children:true});
  assert.equal(tab.hidden,true);assert.equal(tab.disabled,true);assert.equal(requests,0);
  publish({id:42,has_game_children:true});
  assert.equal(tab.hidden,false);assert.equal(tab.disabled,false);assert.equal(tab.dataset.dlcRevealed,'true');
  assert.equal(tab.tabIndex,-1,'revealing does not steal tab selection or focus');assert.equal(requests,0);
  tab.hidden=true;controller.abort();publish({id:42,has_game_children:true});assert.equal(tab.hidden,true);
});
