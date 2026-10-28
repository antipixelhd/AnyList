import assert from 'node:assert/strict';
import test from 'node:test';
import {gameSectionPending,paintGameSection,bindGameSection,gameSectionFailure} from '../src/lib/game-section-loading.ts';

function fixture(){
  const skeleton={hidden:false},content={hidden:true},retry=new EventTarget(),message={textContent:''};
  const error={hidden:true,querySelector:()=>message};
  const section={hidden:false,attrs:{},setAttribute(key,value){this.attrs[key]=value;},
    querySelectorAll:selector=>selector==='[data-game-section-loading]'?[skeleton]:[content],
    querySelector:selector=>selector==='[data-game-section-retry]'?retry:error};
  const root=new EventTarget();root.dataset={gamePrimaryPending:'false'};
  return {root,section,skeleton,content,error,message,retry};
}

test('missing content reserves a skeleton while cached values survive refreshes',()=>{
  const f=fixture();
  paintGameSection(f.section,false,{pending:true});assert.equal(f.section.hidden,false);assert.equal(f.skeleton.hidden,false);assert.equal(f.content.hidden,true);
  paintGameSection(f.section,true,{pending:true});assert.equal(f.skeleton.hidden,true);assert.equal(f.content.hidden,false);assert.equal(f.section.attrs['aria-busy'],'false');
  paintGameSection(f.section,false,{state:'ready'});assert.equal(f.section.hidden,true);assert.equal(f.skeleton.hidden,true);
  assert.equal(gameSectionPending({state:'unavailable'},true),true);
});

test('warm SSR skips duplicate reads and primary identity changes refresh after any in-flight request',()=>{
  for(const pending of [false,true]){
    const f=fixture(),controller=new AbortController(),calls=[];
    bindGameSection(f.root,f.section,controller.signal,{start:()=>calls.push('start'),retry:after=>{calls.push(after);return Promise.resolve();}},{state:pending?'pending':'ready',pending});
    assert.deepEqual(calls,pending?['start']:[]);
    f.root.dispatchEvent(new Event('anylist:game-primary'));assert.deepEqual(calls,pending?['start',true]:[]);
    controller.abort();const count=calls.length;f.root.dispatchEvent(new Event('anylist:game-primary'));assert.equal(calls.length,count);
  }
});

test('failed reads settle skeletons to a retry state without concealing usable content',()=>{
  const f=fixture();gameSectionFailure(f.section,'retry','Unavailable');
  assert.equal(f.skeleton.hidden,true);assert.equal(f.error.hidden,false);assert.equal(f.message.textContent,'Unavailable');
  paintGameSection(f.section,true,{state:'ready'});gameSectionFailure(f.section,'retry','Unavailable');
  assert.equal(f.content.hidden,false);assert.equal(f.error.hidden,true);
});
