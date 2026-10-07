import assert from "node:assert/strict";
import test from "node:test";
import { createBrowseLoading } from "../src/lib/browse-loading.ts";

function view(visibleCards) {
  const cards = visibleCards.map((visible) => ({
    getClientRects: () => visible ? [{}] : [],
  }));
  const region = { dataset: {}, querySelectorAll: () => cards };
  const skeleton = { hidden: true };
  return { region, skeleton, loading: createBrowseLoading(region, skeleton) };
}

test("category and grid refreshes mask existing cards without replacing their layout", () => {
  for (const count of [24, 30]) {
    const v = view(Array(count).fill(true));
    const cards = v.region.querySelectorAll();
    v.loading.show();
    assert.equal(v.region.dataset.browseLoading, "replace");
    assert.equal(v.skeleton.hidden, true);
    assert.equal(v.region.querySelectorAll(), cards);
    v.loading.finish();
    assert.equal(v.region.dataset.browseLoading, undefined);
    assert.equal(v.skeleton.hidden, true);
  }
});

test("loading more keeps existing cards readable and shows the appended skeleton grid", () => {
  const v = view([true]);
  v.loading.show(true);
  assert.equal(v.region.dataset.browseLoading, "append");
  assert.equal(v.skeleton.hidden, false);
  v.loading.finish();
  assert.equal(v.skeleton.hidden, true);
});

test("empty results use fallback skeletons without counting hidden cards from the other view", () => {
  for (const visible of [[], [false, false]]) {
    const v = view(visible);
    v.loading.show();
    assert.equal(v.region.dataset.browseLoading, "empty");
    assert.equal(v.skeleton.hidden, false);
    v.loading.finish();
    assert.equal(v.region.dataset.browseLoading, undefined);
  }
});
