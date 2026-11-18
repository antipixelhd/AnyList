import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import test from 'node:test';

const source=stripTypeScriptTypes(readFileSync(new URL('../src/lib/entry-confirmation.ts',import.meta.url),'utf8'))
  .replace(/^import .*;$/m,'').replaceAll('export ','');
function fixture(){
  const dialog=new EventTarget(),body={textContent:''},button={textContent:''},alternative={hidden:true,textContent:''};
  const closeButton={hidden:false};
  const elements={'[value="confirm"]':button,'[value="alternative"]':alternative,
    '#editor-confirmation-body':body,'.editor-confirmation-close':closeButton};
  dialog.open=false;dialog.querySelector=selector=>elements[selector];
  dialog.close=value=>{dialog.returnValue=value;dialog.open=false;dialog.dispatchEvent(new Event('close'));};
  const context={document:{querySelector:()=>dialog},showDialog:element=>{element.open=true;},
    closeDialog:(element,value)=>element.close(value)};
  runInNewContext(source+';this.confirm=confirmEntryAction;this.choose=chooseEntryAction;',context);
  return {dialog,body,button,alternative,closeButton,confirm:context.confirm,choose:context.choose};
}
test('shared confirmation keeps cancellation safe and can be reopened for explicit confirmation',async()=>{
  const {dialog,body,confirm}=fixture(),controller=new AbortController();
  const cancelled=confirm('Deletes watch evidence',controller.signal);
  assert.equal(dialog.open,true);assert.equal(body.textContent,'Deletes watch evidence');
  dialog.close('cancel');assert.equal(await cancelled,false);
  const accepted=confirm('Clear later episodes',controller.signal);
  dialog.close('confirm');assert.equal(await accepted,true);
});
test('Games base-game action uses Add and later confirmations restore the shared label',async()=>{
  const {dialog,button,confirm}=fixture(),controller=new AbortController();
  const add=confirm('Base game has to be added first',controller.signal,{confirmLabel:'Add'});
  assert.equal(button.textContent,'Add');dialog.close('confirm');assert.equal(await add,true);
  const remove=confirm('Delete entry?',controller.signal);
  assert.equal(button.textContent,'OK');dialog.close('cancel');assert.equal(await remove,false);
});
test('navigation cancels an open confirmation and removes its lifetime listeners',async()=>{
  const {dialog,confirm}=fixture(),controller=new AbortController();
  const pending=confirm('Clear later episodes',controller.signal);
  controller.abort();assert.equal(await pending,false);assert.equal(dialog.open,false);
  assert.equal(await confirm('Must stay closed',controller.signal),false);
});
test('season deletion offers both scopes and resets the alternate action after dismissal',async()=>{
  const {dialog,button,alternative,closeButton,confirm,choose}=fixture(),{signal}=new AbortController();
  const season=choose('Delete tracking history?',signal,{confirmLabel:'Delete Series',alternativeLabel:'Delete Season'});
  assert.deepEqual([button.textContent,alternative.textContent,alternative.hidden],['Delete Series','Delete Season',false]);
  assert.equal(closeButton.hidden,true);
  dialog.close('alternative');assert.equal(await season,'alternative');
  const ordinary=confirm('Delete entry?',signal);
  assert.equal(alternative.hidden,true);assert.equal(dialog.returnValue,'');
  assert.equal(closeButton.hidden,false);
  dialog.close();assert.equal(await ordinary,false);
});
