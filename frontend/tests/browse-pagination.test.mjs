import test from 'node:test';
import assert from 'node:assert/strict';
import { createBrowsePaginationDemand } from '../src/lib/browse-pagination.ts';

test('a short Tenet result and empty continuation pages cannot request another page while idle', () => {
  const demand = createBrowsePaginationDemand(0);
  assert.equal(demand.consume(), false);
  demand.advance(100, true);
  assert.equal(demand.consume(), true);
  demand.reset(100); // The next response is empty but still has_more.
  for (let notifications = 0; notifications < 10; notifications++)
    assert.equal(demand.consume(), false);
  demand.advance(150, true);
  assert.equal(demand.consume(), true);
});

test('scroll intent is consumed once even when skeleton and result layout changes notify repeatedly', () => {
  const demand = createBrowsePaginationDemand(0);
  demand.advance(100, true);
  demand.advance(200, true);
  assert.equal(demand.consume(), true);
  assert.equal(demand.consume(), false);
  demand.advance(200, true);
  assert.equal(demand.consume(), false);
  demand.advance(100, true);
  assert.equal(demand.consume(), false);
  demand.advance(150, true);
  assert.equal(demand.consume(), true);
});

test('scrolling during an active request or after failure cannot queue a background retry', () => {
  const demand = createBrowsePaginationDemand(0);
  demand.advance(200, false);
  assert.equal(demand.consume(), false);
  demand.reset(200);
  assert.equal(demand.consume(), false);
  demand.advance(300, false);
  assert.equal(demand.consume(), false);
});

test('changing filters discards scroll intent from the previous search', () => {
  const demand = createBrowsePaginationDemand(500);
  demand.advance(600, true);
  demand.reset(600);
  assert.equal(demand.consume(), false);
  demand.advance(650, true);
  assert.equal(demand.consume(), true);
});
