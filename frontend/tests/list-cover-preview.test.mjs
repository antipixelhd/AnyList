import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import test from 'node:test';

test('holding or cancelling the edit button keeps the cover preview until a confirmed click',()=>{
  const source=readFileSync(new URL('../src/pages/user/[username]/[section].astro',import.meta.url),'utf8');
  const start=source.indexOf("  document.querySelectorAll<HTMLElement>('.entry-row').forEach");
  const bindings=source.slice(start,source.indexOf("  document.addEventListener('keydown'",start));
  const handlers=new Map();let hidden=false;
  const row={dataset:{},addEventListener:(name,callback)=>handlers.set(name,callback)};
  const button={closest:selector=>selector==='[data-edit-entry]'?button:null};
  const context={document:{querySelectorAll:()=>[row],querySelector:()=>({open:false})},pageListeners:new AbortController(),hoveredRow:row,previewRow:row,
    hidePreview:()=>{hidden=true;},showPreview:()=>{hidden=false;}};
  runInNewContext(stripTypeScriptTypes(bindings),context);
  const fire=name=>handlers.get(name)?.({target:button});
  fire('pointerdown');fire('focusin');assert.equal(hidden,false);
  fire('pointercancel');assert.equal(hidden,false);
  fire('pointerdown');fire('pointerup');assert.equal(hidden,false);
  fire('click');assert.equal(hidden,true);
});
