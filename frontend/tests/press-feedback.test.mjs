import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import { runInNewContext } from 'node:vm';
import test from 'node:test';

function fixture() {
  const document = new EventTarget(), window = new EventTarget();
  document.timeline = {currentTime:100};
  const reduced = Object.assign(new EventTarget(), {matches:false});
  window.matchMedia = () => reduced;
  window.getComputedStyle = () => ({scale:'1'});
  const calls = [];
  let mutation;
  class Element {
    isConnected = true;
    disabled = false;
    static = false;
    navigation = true;
    styles = new Map();
    style = {removeProperty: name => this.styles.delete(name)};
    contains(node) { return node === this; }
    getClientRects() { return this.isConnected ? [{}] : []; }
    closest(selector) {
      if (selector === '[inert]') return null;
      if (selector === '[data-motion-static]') return this.static ? this : null;
      return this;
    }
    matches(selector) {
      if (selector.startsWith('.status-filters')) return this.navigation;
      if (selector.startsWith(':disabled')) return this.disabled;
      return false;
    }
    hasAttribute() { return false; }
    animate(frames,timing) { return context.animate(this,frames,timing); }
  }
  class Anchor extends Element {}
  const source = readFileSync(new URL('../src/lib/press-feedback.ts', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '').replace(/\bexport /g, '');
  const context = {
    document, window, Element, HTMLElement:Element, HTMLAnchorElement:Anchor, Node:Element,
    MutationObserver:class {constructor(callback){mutation=callback;}observe(){}},
    animate(element, frames, timing) {
      let finish;
      const control = {finished:new Promise(resolve=>finish=resolve),finish:()=>finish(),cancel(){this.cancelled=true;}};
      calls.push({element,values:{scale:Number(frames.at(-1).scale)},timing,control});
      return control;
    },
  };
  runInNewContext(stripTypeScriptTypes(source)+';mountPressFeedback(document,window);', context);
  function send(type, element, properties={}, surface=document) {
    const event = new Event(type);
    for (const [key,value] of Object.entries({target:element,button:0,isPrimary:true,pointerId:1,pointerType:'mouse',clientX:0,clientY:0,...properties})) {
      Object.defineProperty(event,key,{value});
    }
    surface.dispatchEvent(event);
  }
  return {document,window,reduced,calls,Element,send,mutation};
}

test('navigation presses work inside static regions and release to their native styles',async()=>{
  const f=fixture(),button=new f.Element();button.static=true;
  f.send('pointerover',button);
  assert.equal(f.calls.length,0);
  f.send('pointerdown',button);
  assert.equal(f.calls[0].values.scale,.985);
  assert.equal(f.calls[0].control.startTime,100);
  f.send('pointerup',button,{},f.window);
  assert.equal(f.calls[1].values.scale,1);
  f.calls[1].control.finish();await Promise.resolve();await Promise.resolve();
  assert.equal(f.calls[1].control.cancelled,true);
  button.navigation=false;
  f.send('pointerdown',button);
  assert.equal(f.calls.length,2,'other static controls retain their own behavior');
});

test('touch panning, pointer cancellation and navigation cannot strand a compressed control',()=>{
  const f=fixture(),button=new f.Element();
  f.send('pointerdown',button,{pointerType:'touch'});
  f.send('pointermove',button,{pointerType:'touch',clientY:12});
  assert.equal(f.calls.at(-1).values.scale,1);
  f.send('pointerdown',button);
  f.send('pointercancel',button,{},f.window);
  assert.equal(f.calls.at(-1).values.scale,1);
  f.send('pointerdown',button);
  button.isConnected=false;f.mutation();
  assert.equal(f.calls.at(-1).control.cancelled,true);
  f.document.dispatchEvent(new Event('astro:before-swap'));
  assert(f.calls.every(call=>call.control.cancelled));
});

test('keyboard presses, disabled controls and reduced motion respect input state',()=>{
  const f=fixture(),button=new f.Element();
  f.send('pointerdown',button,{button:2});
  f.send('pointerdown',button,{isPrimary:false});
  button.disabled=true;f.send('pointerdown',button);
  assert.equal(f.calls.length,0);
  button.disabled=false;f.send('keydown',button,{key:'Enter',repeat:false});
  f.send('keydown',button,{key:'Enter',repeat:true});
  assert.equal(f.calls.length,1);
  f.send('keyup',button,{key:'Enter'},f.window);
  assert.equal(f.calls.at(-1).values.scale,1);
  f.reduced.matches=true;f.reduced.dispatchEvent(new Event('change'));
  assert.equal(f.calls.at(-1).control.cancelled,true);
  const count=f.calls.length;f.send('pointerdown',button);
  assert.equal(f.calls.length,count);
});
