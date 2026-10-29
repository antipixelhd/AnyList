import test from 'node:test';
import assert from 'node:assert/strict';
import {bindContributorTabIntent} from '../src/lib/contributor-tab-intent.ts';

test('sibling warming requires dwell/keyboard intent and respects selection, network and navigation',t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const previous=globalThis.PointerEvent;globalThis.PointerEvent=class PointerEvent{};
  t.after(()=>{if(previous===undefined)delete globalThis.PointerEvent;else globalThis.PointerEvent=previous;});
  const handlers={},calls=[],controller=new AbortController();
  const form={addEventListener:(name,callback)=>handlers[name]=callback};
  let allowed=true;
  const input={disabled:false,checked:false,isConnected:true,value:'game'};
  const label={querySelector:()=>input,contains:()=>false};input.closest=()=>label;
  const event={target:{closest:()=>label}};
  bindContributorTabIntent(form,controller.signal,domain=>calls.push(domain),()=>allowed);
  t.mock.timers.tick(500);assert.deepEqual(calls,[]);
  handlers.pointerover(event);t.mock.timers.tick(179);assert.deepEqual(calls,[]);
  handlers.pointerout({relatedTarget:null});t.mock.timers.tick(1);assert.deepEqual(calls,[]);
  handlers.focusin(event);t.mock.timers.tick(180);assert.deepEqual(calls,['game']);
  handlers.focusout();allowed=false;handlers.pointerover(event);t.mock.timers.tick(180);assert.equal(calls.length,1);
  handlers.focusout();allowed=true;input.checked=true;handlers.focusin(event);t.mock.timers.tick(180);assert.equal(calls.length,1);
  input.checked=false;handlers.focusin(event);controller.abort();t.mock.timers.tick(180);assert.equal(calls.length,1);
});
