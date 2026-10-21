import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import test from 'node:test';

test('holding or cancelling the edit button keeps the cover preview until a confirmed click',()=>{
  const source=readFileSync(new URL('../src/lib/list-cover-preview.ts',import.meta.url),'utf8')
    .replace(/^import .*\r?\n/gm,'').replace('export function','function');
  for(const attribute of ['[data-edit-entry]','[data-edit-game]']){
    const handlers=new Map(),preview={hidden:false,querySelector:()=>({})};
    const row={dataset:{},addEventListener:(name,callback)=>handlers.set(name,callback)};
    const button={closest:selector=>selector.includes(attribute)?button:null};
    const context={document:{querySelectorAll:()=>[row],querySelector:selector=>selector==='#cover-preview'?preview:{open:false},addEventListener(){}},window:{addEventListener(){}}};
    runInNewContext(stripTypeScriptTypes(source),context);
    context.mountListCoverPreview(new AbortController().signal);
    const fire=name=>handlers.get(name)?.({target:button});
    fire('pointerdown');fire('focusin');assert.equal(preview.hidden,false);
    fire('pointercancel');assert.equal(preview.hidden,false);
    fire('pointerdown');fire('pointerup');assert.equal(preview.hidden,false);
    fire('click');assert.equal(preview.hidden,true);
  }
});
