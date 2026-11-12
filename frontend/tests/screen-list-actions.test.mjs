import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import test from 'node:test';

function fixture(scope){
  let adapter;
  const writes=[];
  const context={
    statuses:[],paintScreenListRow(){},screenListEditorTitle(){},showActionFailure(){},
    editorStore:{async write(title,patch){writes.push([title.id,patch.progress]);}},
    initializeVirtualList(_panel,_signal,options){adapter=options;},
  };
  const source=readFileSync(new URL('../src/lib/profile-list.ts',import.meta.url),'utf8')
    .replace(/^import .*\r?\n/gm,'').replace('export function','function');
  runInNewContext(stripTypeScriptTypes(source),context);
  context.initializeProfileList({querySelector:()=>({dataset:{screenScope:scope}})},new AbortController().signal);
  return {adapter,writes};
}

test('the combined panel takes screen writes and keeps overlapping game IDs isolated',()=>{
  const {adapter}=fixture('all');
  assert.equal(adapter.accepts({id:1,type:'movie'}),true);
  assert.equal(adapter.accepts({id:1,type:'series'}),true);
  assert.equal(adapter.accepts({id:1,type:'game'}),false);
  assert.equal(fixture('movie').adapter.accepts({id:1,type:'series'}),false);
  assert.equal(fixture('series').adapter.accepts({id:1,type:'movie'}),false);
});

test('a late-mounted progress control writes the next episode using the current editor state',()=>{
  const {adapter,writes}=fixture('series');
  const title={id:1,type:'series',entry:{progress:12}};
  adapter.onClick({closest:()=>({disabled:false})},title);
  adapter.onClick({closest:()=>({disabled:true})},title);
  assert.deepEqual(writes,[[1,13]]);
});
