import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';

const source=stripTypeScriptTypes(readFileSync(new URL('../src/lib/series-progress-picker.ts',import.meta.url),'utf8')
  .replace(/^import .*\r?\n/gm,'').replaceAll('export ',''));
function fixture(t) {
  const lifetime=new AbortController(),placements=[];
  const node=()=>Object.assign(new EventTarget(),{attributes:new Map(),
    setAttribute(key,value){this.attributes.set(key,value);},getAttribute(key){return this.attributes.get(key);},
    hasAttribute(key){return this.attributes.has(key);},removeAttribute(key){this.attributes.delete(key);},
    toggleAttribute(){},classList:{remove(){}},style:{removeProperty(){}}});
  const panel=node(),editor=node(),trigger=node(),picker=node(),scroll=node(),rows=node();
  const children=new Map([['[data-series-scroll]',scroll],['[data-series-rows]',rows],['[data-series-dismiss]',node()]]);
  picker.querySelector=selector=>children.get(selector)??node();
  let open=false;
  Object.assign(picker,{id:'picker',open:false,matches:()=>open,
    showPopover(options){
      // Native beforetoggle listener microtasks may run before the open state is applied.
      const event=new Event('beforetoggle');Object.defineProperty(event,'newState',{value:'open'});
      picker.dispatchEvent(event);
      picker.invoker=options?.source;open=true;
    },hidePopover(){open=false;}});
  const elements=new Map([['[data-series-trigger]',trigger],['[data-series-picker]',picker]]);
  panel.querySelector=selector=>elements.get(selector)??node();
  const window=Object.assign(new EventTarget(),{visualViewport:new EventTarget()});
  const setup=runInNewContext(`${source}\nsetupSeriesProgressPicker`,{
    window,HTMLDialogElement:{prototype:{closedBy:'any'}},matchMedia:()=>Object.assign(new EventTarget(),{matches:false}),
    queueMicrotask:callback=>callback(),placeDropdown:()=>{assert.equal(open,true);placements.push(true);},
  });
  setup(panel,editor,lifetime.signal,()=>{},{});
  const click=()=>{
    const event=new Event('click',{cancelable:true});trigger.dispatchEvent(event);return event;
  };
  t.after(()=>lifetime.abort());
  return {lifetime,picker,trigger,placements,click};
}
test('trusted click timing positions synchronously after opening even when beforetoggle microtasks run early',t=>{
  const f=fixture(t);
  const event=f.click();
  assert.equal(event.defaultPrevented,true,'cancel the declarative action to avoid double toggling');
  assert.equal(f.placements.length,1,'placement finishes before the click handler returns');
  assert.equal(f.picker.invoker,f.trigger,'retain native keyboard focus order');
  assert.equal(f.trigger.getAttribute('aria-expanded'),'true');
});
test('repeat activation closes the popover and disposal removes activation handlers',t=>{
  const f=fixture(t);
  f.click();f.click();
  assert.equal(f.picker.matches(),false);assert.equal(f.trigger.getAttribute('aria-expanded'),'false');
  assert.equal(f.placements.length,1);
  f.lifetime.abort();f.click();assert.equal(f.placements.length,1);
});
