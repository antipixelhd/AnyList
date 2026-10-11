import assert from 'node:assert/strict';
import test from 'node:test';
import { chartMinimumWidth } from '../src/lib/stats-chart-density.ts';

test('crowded charts overflow phone widths while sparse charts fit', () => {
  assert.ok(chartMinimumWidth(20) > 390);
  assert.ok(chartMinimumWidth(5) < 320);
  assert.ok((chartMinimumWidth(100) - 32) / 100 >= 44);
});
