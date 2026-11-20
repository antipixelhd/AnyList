import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';

const source=stripTypeScriptTypes(readFileSync(new URL('../src/lib/series-progress-picker.ts',import.meta.url),'utf8')
  .replace(/^import .*\r?\n/gm,'').replaceAll('export ',''));
function fixture(t) {
  const lifetime=new AbortController(),microtasks=[],placements=[];
  const node=()=>Object.assign(new EventTarget(),{attributes:new Map(),
    setAttribute(key,value){this.attributes.set(key,value);},getAttribute(key){return this.attributes.get(key);},
    hasAttribute(key){return this.attributes.has(key);},removeAttribute(key){this.attributes.delete(key);},
    toggleAttribute(){},classList:{remove(){}},style:{removeProperty(){}}});
  const panel=node(),editor=node(),trigger=node(),picker=node(),scroll=node(),rows=node();
  const children=new Map([['[data-series-scroll]',scroll],['[data-series-rows]',rows],['[data-series-dismiss]',node()]]);
  picker.querySelector=selector=>children.get(selector)??node();
  let open=false;
  Object.assign(picker,{id:'picker',open:false,matches:()=>open,showPopover(){open=true;},hidePopover(){open=false;}});
  const elements=new Map([['[data-series-trigger]',trigger],['[data-series-picker]',picker]]);
  panel.querySelector=selector=>elements.get(selector)??node();
  const window=Object.assign(new EventTarget(),{visualViewport:new EventTarget()});
  const setup=runInNewContext(`${source}\nsetupSeriesProgressPicker`,{
    window,HTMLDialogElement:{prototype:{closedBy:'any'}},matchMedia:()=>Object.assign(new EventTarget(),{matches:false}),
    queueMicrotask:callback=>microtasks.push(callback),placeDropdown:()=>placements.push(true),
  });
  setup(panel,editor,lifetime.signal,()=>{},{});
  const beforeOpen=()=>{
    const event=new Event('beforetoggle');Object.defineProperty(event,'newState',{value:'open'});
    picker.dispatchEvent(event);
  };
  t.after(()=>lifetime.abort());
  return {lifetime,picker,trigger,microtasks,placements,beforeOpen};
}
test('positions a newly opened popover before the queued toggle event can paint',t=>{
  const f=fixture(t);
  f.beforeOpen();assert.equal(f.placements.length,0,'closed popovers cannot be measured yet');
  f.picker.showPopover();f.microtasks.shift()();
  assert.equal(f.placements.length,1,'placement finishes in the pre-paint microtask');
  assert.equal(f.trigger.getAttribute('aria-expanded'),'true');
});
test('queued placement ignores a closed popover or a disposed editor',t=>{
  const f=fixture(t);
  f.beforeOpen();f.microtasks.shift()();assert.equal(f.placements.length,0);
  f.beforeOpen();f.picker.showPopover();f.lifetime.abort();f.microtasks.shift()();
  assert.equal(f.placements.length,0);
});
