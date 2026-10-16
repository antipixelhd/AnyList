import assert from 'node:assert/strict';
import test from 'node:test';
import {ToastStore} from '../src/lib/toast-store.ts';

function fixture() {
  let now=0, sequence=0;
  const timers=new Map();
  const store=new ToastStore(()=>now,(callback,delay)=>{const id=++sequence;timers.set(id,{callback,at:now+delay});return id;},id=>timers.delete(id));
  const advance=milliseconds=>{now+=milliseconds;for(const [id,timer] of [...timers])if(timer.at<=now){timers.delete(id);timer.callback();}};
  return {store,advance,timers};
}
test('deduplication renews an existing message and caps the visible stack',()=>{
  const {store,advance}=fixture();
  const id=store.show({message:'Saved',kind:'success',duration:100});advance(60);
  assert.equal(store.show({message:'Saved',kind:'success',duration:100}),id);
  advance(50);assert.equal(store.toasts.length,1);advance(50);assert.equal(store.toasts.length,0);
  for(let i=0;i<8;i++)store.show({message:String(i),duration:0});
  assert.deepEqual(store.toasts.map(toast=>toast.message),['4','5','6','7']);
});
test('hover and focus pause independently and preserve the remaining expiry',()=>{
  const {store,advance}=fixture();
  const id=store.show({message:'Error',kind:'error',duration:100});advance(40);
  store.pause(id);store.pause(id,'focus');advance(500);store.resume(id);advance(500);
  assert.equal(store.toasts.length,1);
  store.resume(id,'focus');advance(59);assert.equal(store.toasts.length,1);
  advance(1);assert.equal(store.toasts.length,0);
});
test('keyed feedback updates in place and actions run once',async()=>{
  const {store,timers}=fixture();let actions=0;
  const id=store.show({message:'Saving',key:'entry',duration:100});
  assert.equal(store.show({message:'Saved',key:'entry',duration:0,action:{label:'Undo',run:()=>{actions++;}}}),id);
  assert.equal(timers.size,0);assert.equal(store.toasts[0].message,'Saved');
  await Promise.all([store.act(id),store.act(id)]);assert.equal(actions,1);assert.equal(store.toasts.length,0);
});
test('clearing on navigation cancels pending timers and releases actions',()=>{
  const {store,timers,advance}=fixture();store.show({message:'Old page'});store.clear();
  assert.equal(timers.size,0);advance(20000);assert.deepEqual(store.toasts,[]);
  assert.equal(store.show({message:'   '}),null);
});
