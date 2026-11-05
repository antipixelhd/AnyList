import test from 'node:test';
import assert from 'node:assert/strict';
import {pinnedRailTarget} from '../src/components/dev/pinned-rail-data.ts';

test('short rails follow the top cap without leaving their layout or crossing the footer',()=>{
  assert.equal(pinnedRailTarget(400,500,2500,900,0),0);
  assert.equal(pinnedRailTarget(-400,500,2500,900,0),496);
  assert.equal(pinnedRailTarget(-3000,500,2500,900,496),2000);
  assert.equal(pinnedRailTarget(100,500,2500,900,496),0);
});

test('tall rails scroll through their content between the top and bottom caps in either direction',()=>{
  // Tall content has room to scroll naturally from y=96 through y=-324.
  assert.equal(pinnedRailTarget(-100,1200,3000,900,0),0);
  const bottom=pinnedRailTarget(-700,1200,3000,900,0);
  assert.equal(-700+bottom+1200,876);
  assert.equal(pinnedRailTarget(-400,1200,3000,900,bottom),bottom);
  const top=pinnedRailTarget(0,1200,3000,900,bottom);
  assert.equal(top,96);
  assert.equal(pinnedRailTarget(-200,1200,1000,900,0),0);
});
