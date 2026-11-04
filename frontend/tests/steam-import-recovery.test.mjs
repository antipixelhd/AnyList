import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';

test('Steam import can retry after both the import and recovery read fail', async () => {
  const source=readFileSync(new URL('../src/scripts/steam-settings.ts',import.meta.url),'utf8');
  const start=source.indexOf("  listen(get('import'), 'click'");
  const end=source.indexOf('  void list().catch(fail);',start);
  const nodes=new Map();
  const get=name=>{if(!nodes.has(name))nodes.set(name,{disabled:false,inert:false,textContent:''});return nodes.get(name);};
  let handler, requests=0, failures=0;
  const context={
    active:{id:1,generation:1},importing:false,reviewEpoch:0,games:[{app_id:'20'}],
    draft:{start_dates:{}},saveTimer:undefined,writes:Promise.resolve(),get,
    listen:(_node,_event,callback)=>{handler=callback;},payload:()=>({generation:1}),
    steamSelected:()=>true,finishDate:()=>null,clearTimeout,
    request:async()=>{requests++;throw new Error('offline');},
    load:async()=>{throw new Error('recovery offline');},fail:()=>{failures++;},
    list:async()=>{},message:()=>{},closeDialog:()=>{},dialog:{},sessionStorage:{removeItem(){}},
    paintSelections:()=>{get('import').disabled=context.importing;},
  };
  runInNewContext(stripTypeScriptTypes(source.slice(start,end)),context);
  handler();
  assert.equal(context.importing,true);
  assert.equal(get('controls').inert,true);
  assert.equal(get('cancel').disabled,true);
  await new Promise(setImmediate);
  assert.equal(requests,1);
  assert.equal(failures,2);
  assert.equal(context.importing,false);
  assert.equal(get('import').disabled,false);
  assert.equal(get('controls').inert,false);
  assert.equal(get('cancel').disabled,false);
  context.request=async()=>{requests++;return {imported:1};};
  handler();
  await new Promise(setImmediate);
  assert.equal(requests,2);
  assert.equal(context.importing,false);
});
