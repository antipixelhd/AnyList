import test from 'node:test';
import assert from 'node:assert/strict';
import {boundedProfileSelection,profileCrop,resizeProfileSelection,profileImageFit,profileMobileRegion,PROFILE_MOBILE_BACKGROUND_ASPECT} from '../src/lib/profile-crop-geometry.ts';

test('the complete source fits portrait and landscape editing areas without clipping',()=>{
  for(const source of [[1920,1080],[1080,1920],[3840,1240]]){
    for(const stage of [[342,700],[796,270],[952,600]]){
      const fit=profileImageFit(...source,...stage);
      assert.ok(fit.width<=stage[0]+1e-6&&fit.height<=stage[1]+1e-6);
      assert.ok(Math.abs(fit.width/fit.height-source[0]/source[1])<1e-6);
      assert.ok(Math.abs(fit.width-stage[0])<1e-6||Math.abs(fit.height-stage[1])<1e-6);
    }
  }
});

test('mobile guides follow actual focal framing instead of assuming a centered crop',()=>{
  const width=1920,height=1080,aspect=1700/330;
  for(const x of [0,50,100]){
    const rect={x:(width-1280)*x/100,y:200,width:1280,height:1280/aspect};
    const region=profileMobileRegion(rect,width,height,aspect,PROFILE_MOBILE_BACKGROUND_ASPECT);
    const framing=profileCrop(rect,width,height,aspect);
    const mobileWidth=Math.min(width,height*PROFILE_MOBILE_BACKGROUND_ASPECT)/framing.zoom;
    const mobileLeft=(width-mobileWidth)*framing.x/100;
    assert.ok(Math.abs(rect.x+rect.width*region.left/100-mobileLeft)<1e-6);
    assert.ok(Math.abs(rect.x+rect.width*region.right/100-mobileLeft-mobileWidth)<1e-6);
  }
  assert.equal(profileMobileRegion({x:0,y:0,width:1000,height:1000},1000,1000,1,PROFILE_MOBILE_BACKGROUND_ASPECT),null);
});

test('upload guides preview the mobile crop of the stored banner, not the uncropped source',()=>{
  const aspect=1700/330,ratio=PROFILE_MOBILE_BACKGROUND_ASPECT/aspect;
  for(const x of [0,500]){
    const region=profileMobileRegion({x,y:200,width:1000,height:1000/aspect},1920,1080,aspect,PROFILE_MOBILE_BACKGROUND_ASPECT,true);
    assert.ok(Math.abs(region.left-(1-ratio)*50)<1e-6);
    assert.ok(Math.abs(region.right-(1+ratio)*50)<1e-6);
  }
});

test('resize reverses immediately after a large overshoot',()=>{
  const bounds={x:0,y:0,width:800,height:400};
  let rect={x:100,y:100,width:400,height:100};
  rect=boundedProfileSelection(resizeProfileSelection(rect,600,0,'e-resize',4),rect,bounds,4);
  assert.equal(rect.width,700);
  rect=boundedProfileSelection(resizeProfileSelection(rect,-10,0,'e-resize',4),rect,bounds,4);
  assert.equal(rect.width,690);
  assert.equal(rect.x,100);
});

test('corner resizing responds to vertical as well as horizontal movement',()=>{
  const rect=resizeProfileSelection({x:0,y:0,width:400,height:100},0,10,'se-resize',4);
  assert.ok(rect.width>400);assert.equal(rect.width/rect.height,4);
});

test('corner resizing tracks the pointer independent of event sampling',()=>{
  const start={x:100,y:100,width:400,height:100};
  const combined=resizeProfileSelection(start,40,10,'se-resize',4);
  let sampled=start;
  for(let index=0;index<4;index++){
    sampled=resizeProfileSelection(sampled,10,0,'se-resize',4);
    sampled=resizeProfileSelection(sampled,0,2.5,'se-resize',4);
  }
  assert.ok(Math.abs(sampled.width-combined.width)<1e-6);
  assert.ok(Math.abs(sampled.height-combined.height)<1e-6);
});

test('dragging against an edge preserves movement along the free axis',()=>{
  const previous={x:0,y:100,width:400,height:100},bounds={x:0,y:0,width:800,height:400};
  assert.deepEqual(boundedProfileSelection({...previous,x:-30,y:125},previous,bounds,4),{...previous,y:125});
  assert.deepEqual(boundedProfileSelection({...previous,x:30,y:125},previous,bounds,4),{...previous,x:30,y:125});
});

test('resizing clamps smoothly without moving the opposite corner',()=>{
  const previous={x:100,y:100,width:400,height:100},bounds={x:0,y:0,width:800,height:400};
  const enlarged=boundedProfileSelection({x:-100,y:50,width:600,height:150},previous,bounds,4);
  assert.deepEqual(enlarged,{x:0,y:75,width:500,height:125});
  const smaller=boundedProfileSelection({x:450,y:187.5,width:50,height:12.5},previous,bounds,4);
  assert.ok(Math.abs(smaller.width-800/3)<1e-6);
  assert.equal(smaller.x+smaller.width,500);
  assert.equal(smaller.y+smaller.height,200);
});

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
