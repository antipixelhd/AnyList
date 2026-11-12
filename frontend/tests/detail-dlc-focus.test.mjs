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
  const root={querySelector(selector){return selector==='[data-game-dlc]'?host:selector.includes('"dlc"')?tab:overview;}};
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
