import test from 'node:test';
import assert from 'node:assert/strict';
import {quickSearchPanelPositions} from '../src/lib/quick-search-layout.ts';

test('places a short fourth panel under the shortest first-row panel', () => {
  assert.deepEqual(quickSearchPanelPositions([510,390,100,100],3,32), [
    {column:1,top:0,height:510}, {column:2,top:0,height:390},
    {column:3,top:0,height:100}, {column:3,top:132,height:100},
  ]);
});

test('starts a new row when a panel cannot fit below the height limit', () => {
  const positions=quickSearchPanelPositions([510,490,480,100],3,32);
  assert.deepEqual(positions[3],{column:1,top:542,height:100});
});

test('respects two columns and a panel taller than the default limit', () => {
  const positions=quickSearchPanelPositions([600,100,120],2,32);
  assert.deepEqual(positions[2],{column:2,top:132,height:120});
});
