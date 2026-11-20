import assert from 'node:assert/strict';
import test from 'node:test';
import {setFeedbackText,setFeedbackPressed,cancelStateFeedback} from '../src/lib/state-feedback.ts';

function fixture({reduced=false,top=20,connected=true,inert=false}={}) {
  const calls=[];
  const view={innerHeight:800,matchMedia:()=>({matches:reduced}),getComputedStyle:()=>({scale:'1.07',translate:'0 1px',opacity:'.8'})};
  const doc={defaultView:view,timeline:{currentTime:125}};
  const element={textContent:'8',isConnected:connected,ownerDocument:doc,
    closest:()=>inert?{}:null,getBoundingClientRect:()=>({width:20,height:20,top,bottom:top+20}),
    animate(frames,timing){
      let resolve,reject;
      const call={frames,timing,finished:new Promise((yes,no)=>{resolve=yes;reject=no;}),finish:()=>resolve(),cancel(){this.cancelled=true;reject();}};
      calls.push(call);return call;
    }};
  const attrs=new Map([['aria-pressed','false']]);
  const button={getAttribute:name=>attrs.get(name)??null,setAttribute:(name,value)=>attrs.set(name,value),querySelector:()=>element};
  return {element,button,calls};
}

test('state hydration, identical values and a server echo stay quiet',()=>{
  cancelStateFeedback();const f=fixture();
  setFeedbackText(f.element,'9');
  setFeedbackText(f.element,'9',true);
  setFeedbackPressed(f.button,true);
  setFeedbackPressed(f.button,true,true);
  assert.equal(f.calls.length,0);
  setFeedbackText(f.element,'7',true);
  setFeedbackText(f.element,'7',true);
  assert.equal(f.calls.length,1);
  assert.deepEqual(f.calls[0].frames,{opacity:['.45','1'],translate:['0 3px','0 0']});
  assert.equal(f.calls[0].timing.duration,280);
  assert.equal(f.calls[0].startTime,125);
  cancelStateFeedback();
});

test('icon acknowledgement changes only the icon and survives rapid reversals',async()=>{
  const f=fixture();
  setFeedbackPressed(f.button,true,true);
  assert.equal(f.button.getAttribute('aria-pressed'),'true');
  assert.deepEqual(f.calls[0].frames.scale,['.86','1.16','1']);
  setFeedbackPressed(f.button,false,true);
  assert.equal(f.calls[0].cancelled,true);
  assert.deepEqual(f.calls[1].frames.scale,['1.07','.86','1']);
  await Promise.resolve();
  assert.equal(f.calls[1].cancelled,undefined,'the old completion cannot cancel the replacement');
  f.calls[1].finish();await Promise.resolve();
  assert.equal(f.calls[1].cancelled,true);
});

test('an interrupted value continues from its visible state without scaling text',()=>{
  const f=fixture();
  setFeedbackText(f.element,'9',true);
  setFeedbackText(f.element,'7',true);
  assert.equal(f.calls[0].cancelled,true);
  assert.deepEqual(f.calls[1].frames,{opacity:['.8','1'],translate:['0 1px','0 0']});
  cancelStateFeedback();
});

test('reduced motion, hidden/offscreen targets and aborted views still publish their state',()=>{
  for(const options of [{reduced:true},{top:900},{connected:false},{inert:true}]){
    const f=fixture(options);setFeedbackText(f.element,'9',true);setFeedbackPressed(f.button,true,true);
    assert.equal(f.element.textContent,'9');assert.equal(f.button.getAttribute('aria-pressed'),'true');assert.equal(f.calls.length,0);
  }
  const f=fixture(),controller=new AbortController();controller.abort();
  setFeedbackText(f.element,'9',true,controller.signal);
  assert.equal(f.calls.length,0);
});

test('view disposal and global cancellation release animations without changing final state',()=>{
  const f=fixture(),controller=new AbortController();
  setFeedbackPressed(f.button,true,true,controller.signal);controller.abort();
  assert.equal(f.calls[0].cancelled,true);assert.equal(f.button.getAttribute('aria-pressed'),'true');
  setFeedbackText(f.element,'9',true);cancelStateFeedback();
  assert.equal(f.calls[1].cancelled,true);assert.equal(f.element.textContent,'9');
});

test('a rollback cancels the acknowledgement and restores state without another animation',()=>{
  const text=fixture(),icon=fixture();
  setFeedbackText(text.element,'9',true);setFeedbackPressed(icon.button,true,true);
  setFeedbackText(text.element,'8');setFeedbackPressed(icon.button,false);
  assert.equal(text.calls.length,1);assert.equal(icon.calls.length,1);
  assert.equal(text.calls[0].cancelled,true);assert.equal(icon.calls[0].cancelled,true);
  assert.equal(text.element.textContent,'8');assert.equal(icon.button.getAttribute('aria-pressed'),'false');
});
