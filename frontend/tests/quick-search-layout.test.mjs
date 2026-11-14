import test from 'node:test';
import assert from 'node:assert/strict';
import {quickSearchPanelPositions} from '../src/lib/quick-search-layout.ts';

test('places a short fourth panel under the shortest first-row panel', () => {
  assert.deepEqual(quickSearchPanelPositions([510,390,100,100],3,32), [
    {column:1,top:0,height:510}, {column:2,top:0,height:390},
    {column:3,top:0,height:100}, {column:3,top:132,height:100},
  ]);
});

test('full-height crew and studio panels use neighboring free columns instead of stacking', () => {
  const positions=quickSearchPanelPositions([522,522,522,522,522],3,48);
  assert.deepEqual(positions.slice(3),[{column:1,top:570,height:522},{column:2,top:570,height:522}]);
});

test('respects two columns with a tall first panel', () => {
  const positions=quickSearchPanelPositions([600,100,120],2,32);
  assert.deepEqual(positions[2],{column:2,top:132,height:120});
});
