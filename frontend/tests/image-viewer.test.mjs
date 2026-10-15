import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import test from 'node:test';

function viewer() {
  class Element {
    constructor(interactive=false) { this.region=typeof interactive==='string'?interactive:interactive?'button':undefined; this.listeners=new Map(); }
    closest(selector) { return selector.split(',').includes(this.region)?this:null; }
    addEventListener(name,handler) { this.listeners.set(name,handler); }
    fire(name,target=this) { this.listeners.get(name)?.({target}); }
  }
  const dialog=new Element(),button=new Element(true),image=new Element('img'),caption=new Element(),previewGap=new Element('[data-image-viewer-slots]');
  dialog.querySelector=()=>button;
  const closed=[];
  const context={Element,closeDialog:dialog=>closed.push(dialog)};
  const source=readFileSync(new URL('../src/lib/image-viewer.ts',import.meta.url),'utf8');
  runInNewContext(stripTypeScriptTypes(source.replace(/^import .*$/gm,'').replace(/export /g,''))+';globalThis.bind=bindImageViewerDismissal;',context);
  context.bind(dialog,new AbortController().signal);
  return {dialog,button,image,caption,previewGap,closed};
}

test('clicking the backdrop or empty viewer space dismisses through shared motion',()=>{
  const {dialog,caption,closed}=viewer();
  dialog.fire('pointerdown');dialog.fire('click');
  dialog.fire('pointerdown',caption);dialog.fire('click',caption);
  assert.deepEqual(closed,[dialog,dialog]);
});
test('images, controls, and drags starting inside an image never dismiss the viewer',()=>{
  const {dialog,image,button,closed}=viewer();
  for(const target of [image,button]){dialog.fire('pointerdown',target);dialog.fire('click',target);}
  dialog.fire('pointerdown',image);dialog.fire('click');
  assert.equal(closed.length,0);
  button.fire('click');assert.deepEqual(closed,[dialog]);
});

test('thumbnail-strip gaps and gestures starting in the strip never dismiss the viewer',()=>{
  const {dialog,previewGap,closed}=viewer();
  dialog.fire('pointerdown',previewGap);dialog.fire('click',previewGap);
  dialog.fire('pointerdown',previewGap);dialog.fire('click');
  assert.equal(closed.length,0);
});
