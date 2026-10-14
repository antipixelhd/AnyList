import assert from 'node:assert/strict';
import test from 'node:test';
import {detailViewerFrame} from '../src/lib/detail-viewer-frame.ts';

test('portrait frames use image width rather than filling the desktop',()=>{
  const frame=detailViewerFrame(780,1170,1440,1000);
  assert.equal(frame.height,828);
  assert.equal(frame.width/frame.height,2/3);
});

test('wide artwork respects both available width and height',()=>{
  const frame=detailViewerFrame(2000,800,1000,800);
  assert.equal(frame.width,840);
  assert.equal(frame.height,336);
  const short=detailViewerFrame(1600,900,1440,500);
  assert.equal(short.height,328);
  assert.ok(Math.abs(short.width/short.height-16/9)<1e-10);
});

test('small images keep their natural size and zoomed viewports still fit',()=>{
  assert.deepEqual(detailViewerFrame(320,180,1440,1000),{width:320,height:180});
  const frame=detailViewerFrame(780,1170,700,450);
  assert.ok(frame.width<=540&&frame.height<=278);
  assert.equal(frame.width/frame.height,2/3);
});
