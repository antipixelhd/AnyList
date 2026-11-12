import assert from 'node:assert/strict';
import test from 'node:test';
import {initializeProfileSections} from '../src/lib/profile-section-selector.ts';
import {initializeEntryAnnotations} from '../src/lib/entry-annotations.ts';

test('dynamically introduced section selectors work and repeated initialization dispatches once',t=>{
  const descriptors=new Map(['document','Element','CustomEvent'].map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
  const handlers=[],events=[];
  class Element{}
  class CustomEvent{constructor(type,init){this.type=type;Object.assign(this,init);}}
  Object.defineProperties(globalThis,{document:{configurable:true,value:{addEventListener:(type,fn)=>handlers.push(fn)}},Element:{configurable:true,value:Element},CustomEvent:{configurable:true,value:CustomEvent}});
  t.after(()=>{for(const [key,value] of descriptors){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}});
  const selected=[];
  const option=Object.assign(new Element(),{dataset:{section:'genres'},classList:{toggle:(name,on)=>selected.push(on)},setAttribute(){}});
  const selector={querySelectorAll:()=>[option],dispatchEvent:event=>events.push(event)};
  option.closest=selectorText=>selectorText.startsWith('.profile-section-selector__option')?option:selector;
  initializeProfileSections();initializeProfileSections();
  assert.equal(handlers.length,1);handlers[0]({target:option});
  assert.equal(events.length,1);assert.equal(events[0].detail.value,'genres');assert.deepEqual(selected,[true]);
});

test('annotation behavior initializes for a dynamically loaded list without duplicate global handlers',t=>{
  const keys=['document','window'],descriptors=new Map(keys.map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
  const calls=[];
  for(const key of keys)Object.defineProperty(globalThis,key,{configurable:true,value:{addEventListener:type=>calls.push(key+':'+type)}});
  t.after(()=>{for(const [key,value] of descriptors){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}});
  initializeEntryAnnotations();initializeEntryAnnotations();
  assert.deepEqual(calls,['document:pointerover','document:focusin','document:keydown','window:resize']);
});
