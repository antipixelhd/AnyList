import assert from 'node:assert/strict';
import test from 'node:test';
import {bindDetailViewerFrame,detailPosterViewerFrame,detailViewerFrame} from '../src/lib/detail-viewer-frame.ts';

test('portrait frames use image width rather than filling the desktop',()=>{
  const frame=detailViewerFrame(780,1170,1440,1000);
  assert.equal(frame.height,876);
  assert.ok(Math.abs(frame.width/frame.height-2/3)<1e-10);
});

test('wide artwork respects both available width and height',()=>{
  const frame=detailViewerFrame(2000,800,1000,800);
  assert.equal(frame.width,952);
  assert.ok(Math.abs(frame.height-380.8)<1e-10);
  const short=detailViewerFrame(1600,900,1440,500);
  assert.equal(short.height,376);
  assert.ok(Math.abs(short.width/short.height-16/9)<1e-10);
});

test('small images keep their natural size and zoomed viewports still fit',()=>{
  assert.deepEqual(detailViewerFrame(320,180,1440,1000),{width:320,height:180});
  const frame=detailViewerFrame(780,1170,700,450);
  assert.ok(frame.width<=652&&frame.height<=326);
  assert.ok(Math.abs(frame.width/frame.height-2/3)<1e-10);
});

test('poster stages reserve viewport margins and preserve portrait proportions',()=>{
  const frame=detailPosterViewerFrame(1440,1000);
  assert.equal(frame.height,952);
  assert.ok(Math.abs(frame.width/frame.height-2/3)<1e-10);
  const narrow=detailPosterViewerFrame(700,2000);
  assert.equal(narrow.width,652);
  assert.equal(narrow.height,978);
  assert.equal(detailPosterViewerFrame(2400,2400).height,1170);
});

test('poster framing is ready before opening, with resize work limited to visible viewers',t=>{
  const previousWindow=globalThis.window,previousMatchMedia=globalThis.matchMedia;
  const events=new Map(),properties=new Map();
  globalThis.window={innerWidth:1440,innerHeight:1000,addEventListener:(name,callback)=>events.set(name,callback)};
  globalThis.matchMedia=()=>({matches:true,addEventListener(){}});
  t.after(()=>{globalThis.window=previousWindow;globalThis.matchMedia=previousMatchMedia;});
  const dialog={open:false,dataset:{},style:{setProperty:(name,value)=>properties.set(name,value)},addEventListener:(name,callback)=>events.set(name,callback)};
  const fit=bindDetailViewerFrame(dialog,new AbortController().signal);
  fit(undefined,'posters');
  assert.equal(properties.get('--viewer-image-height'),'952px');
  window.innerHeight=800;
  events.get('resize')();
  assert.equal(properties.get('--viewer-image-height'),'952px');
  dialog.open=true;
  events.get('resize')();
  assert.equal(properties.get('--viewer-image-height'),'752px');
  const stage=new Map(properties);
  fit({naturalWidth:320,naturalHeight:480},'posters');
  assert.deepEqual(properties,stage);
  fit({naturalWidth:600,naturalHeight:900},'posters');
  assert.deepEqual(properties,stage);
  window.innerWidth=390;
  window.innerHeight=844;
  fit({naturalWidth:1920,naturalHeight:1080},'artwork');
  assert.equal(properties.get('--viewer-image-width'),'390px');
  assert.equal(properties.get('--viewer-image-height'),'219.375px');
  assert.equal(dialog.dataset.viewerPreviewsBelow,'true');
  fit(undefined,'posters');
  assert.equal(properties.get('--viewer-image-width'),'390px');
  assert.equal(properties.get('--viewer-image-height'),'585px');
  assert.equal(dialog.dataset.viewerPreviewsBelow,'true');
  window.innerHeight=450;
  fit(undefined,'posters');
  assert.equal(properties.get('--viewer-image-height'),'402px');
  assert.equal(dialog.dataset.viewerPreviewsBelow,'false');
  window.innerWidth=844;
  window.innerHeight=390;
  fit({naturalWidth:1920,naturalHeight:1080},'artwork');
  assert.equal(properties.get('--viewer-image-height'),'390px');
  assert.ok(Math.abs(parseFloat(properties.get('--viewer-image-width'))-390*16/9)<1e-10);
  assert.equal(dialog.dataset.viewerPreviewsBelow,'false');
  window.innerWidth=390;
  window.innerHeight=844;
  events.get('resize')();
  assert.equal(properties.get('--viewer-image-width'),'390px');
  assert.equal(properties.get('--viewer-image-height'),'219.375px');
  assert.equal(dialog.dataset.viewerPreviewsBelow,'true');
});
