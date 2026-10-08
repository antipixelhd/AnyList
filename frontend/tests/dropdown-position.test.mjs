import assert from 'node:assert/strict';
import test from 'node:test';
import {dropdownPlacement} from '../src/lib/dropdown-position.ts';

const viewport = {left: 0, top: 0, width: 390, height: 844};
const anchor = (top, width = 200, left = 16) => ({left, top, bottom: top + 40, width});

test('short menus stay below their trigger when they fit', () => {
  const placed = dropdownPlacement(anchor(600), viewport, 80);
  assert.equal(placed.upward, false);
  assert.equal(placed.top, 645);
  assert.equal(placed.maxHeight, 80);
});

test('a full menu flips upward when its measured height does not fit below', () => {
  const placed = dropdownPlacement(anchor(600), viewport, 260);
  assert.equal(placed.upward, true);
  assert.equal(placed.top + placed.maxHeight, 595);
  assert.equal(placed.maxHeight, 260);
});

test('long menus choose the larger side and fit entirely within the viewport', () => {
  const smallViewport = {...viewport, height: 300};
  const placed = dropdownPlacement(anchor(160), smallViewport, 900);
  assert.equal(placed.upward, true);
  assert.equal(placed.top, 8);
  assert.equal(placed.maxHeight, 147);
});

test('menus near the top stay below and cap the scrollable height', () => {
  const placed = dropdownPlacement(anchor(10), viewport, 900);
  assert.equal(placed.upward, false);
  assert.equal(placed.maxHeight, 290);
});

test('wide and edge-aligned menus remain inside the screen', () => {
  const placed = dropdownPlacement(anchor(100, 500, -30), viewport, 100);
  assert.equal(placed.left, 8);
  assert.equal(placed.width, 374);
  const right = dropdownPlacement(anchor(100, 200, 300), viewport, 100);
  assert.equal(right.left + right.width, 382);
});

test('visual viewport offsets and keyboard height are respected', () => {
  const visible = {left: 20, top: 250, width: 320, height: 280};
  const placed = dropdownPlacement(anchor(450, 200, 280), visible, 250);
  assert.equal(placed.upward, true);
  assert.equal(placed.top, 258);
  assert.equal(placed.top + placed.maxHeight, 445);
  assert.equal(placed.left + placed.width, 332);
});
