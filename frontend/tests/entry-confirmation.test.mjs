import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import test from 'node:test';

const source=stripTypeScriptTypes(readFileSync(new URL('../src/lib/entry-confirmation.ts',import.meta.url),'utf8'))
  .replace(/^import .*;$/m,'').replaceAll('export ','');
function fixture(){
  const dialog=new EventTarget(),body={textContent:''};
  dialog.open=false;dialog.querySelector=()=>body;
  dialog.close=value=>{dialog.returnValue=value;dialog.open=false;dialog.dispatchEvent(new Event('close'));};
  const context={document:{querySelector:()=>dialog},showDialog:element=>{element.open=true;},
    closeDialog:(element,value)=>element.close(value)};
  runInNewContext(source+';this.confirm=confirmEntryAction;',context);
  return {dialog,body,confirm:context.confirm};
}
test('shared confirmation keeps cancellation safe and can be reopened for explicit confirmation',async()=>{
  const {dialog,body,confirm}=fixture(),controller=new AbortController();
  const cancelled=confirm('Deletes watch evidence',controller.signal);
  assert.equal(dialog.open,true);assert.equal(body.textContent,'Deletes watch evidence');
  dialog.close('cancel');assert.equal(await cancelled,false);
  const accepted=confirm('Clear later episodes',controller.signal);
  dialog.close('confirm');assert.equal(await accepted,true);
});
test('navigation cancels an open confirmation and removes its lifetime listeners',async()=>{
  const {dialog,confirm}=fixture(),controller=new AbortController();
  const pending=confirm('Clear later episodes',controller.signal);
  controller.abort();assert.equal(await pending,false);assert.equal(dialog.open,false);
  assert.equal(await confirm('Must stay closed',controller.signal),false);
});
