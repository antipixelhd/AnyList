import assert from 'node:assert/strict';
import test from 'node:test';
import {bindRovingOptions} from '../src/lib/roving-options.ts';

test('moving within a picker leaves one sequential Tab stop and respects its lifetime',()=>{
  const options=[{tabIndex:0},{tabIndex:-1},{tabIndex:-1}];let focus;
  const lifetime=new AbortController();
  const root={querySelectorAll:()=>options,contains:target=>options.includes(target),addEventListener:(_,handler,{signal})=>{focus=event=>{if(!signal.aborted)handler(event);};}};
  bindRovingOptions(root,'[role=option]',lifetime.signal);
  focus({target:{closest:()=>options[2]}});
  assert.deepEqual(options.map(option=>option.tabIndex),[-1,-1,0]);
  lifetime.abort();focus({target:{closest:()=>options[0]}});
  assert.deepEqual(options.map(option=>option.tabIndex),[-1,-1,0]);
});
