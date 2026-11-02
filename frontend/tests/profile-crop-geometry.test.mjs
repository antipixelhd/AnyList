import test from 'node:test';
import assert from 'node:assert/strict';
import {profileCrop} from '../src/lib/profile-crop-geometry.ts';

test('a crop maps back to the same rectangle under profile CSS framing',()=>{
  const width=1920,height=1080,aspect=4,rect={x:320,y:180,width:1280,height:320};
  const result=profileCrop(rect,width,height,aspect);
  const coverWidth=Math.min(width,height*aspect),shownWidth=coverWidth/result.zoom;
  const shownHeight=shownWidth/aspect;
  assert.ok(Math.abs(shownWidth-rect.width)<1e-6);
  assert.ok(Math.abs((width-shownWidth)*result.x/100-rect.x)<1e-6);
  assert.ok(Math.abs((height-shownHeight)*result.y/100-rect.y)<1e-6);
});

test('full and edge selections preserve valid focal percentages',()=>{
  assert.deepEqual(profileCrop({x:0,y:0,width:1920,height:620},1920,620,1920/620),{x:50,y:50,zoom:1});
  assert.deepEqual(profileCrop({x:1280,y:920,width:640,height:160},1920,1080,4),{x:100,y:100,zoom:3});
});
