import assert from "node:assert/strict";
import test from "node:test";
import { createBrowseLoading, prepareBrowsePoster } from "../src/lib/browse-loading.ts";

function view(visibleCards) {
  const cards = visibleCards.map((visible) => ({
    getClientRects: () => visible ? [{}] : [],
  }));
  const region = { dataset: {}, querySelectorAll: () => cards };
  const skeleton = { hidden: true };
  return { region, skeleton, loading: createBrowseLoading(region, skeleton) };
}

test("category and grid refreshes keep existing cards visible without skeletons", () => {
  for (const count of [24, 30]) {
    const v = view(Array(count).fill(true));
    const cards = v.region.querySelectorAll();
    v.loading.show();
    assert.equal(v.region.dataset.browseLoading, undefined);
    assert.equal(v.skeleton.hidden, true);
    assert.equal(v.region.querySelectorAll(), cards);
    v.loading.finish();
    assert.equal(v.region.dataset.browseLoading, undefined);
    assert.equal(v.skeleton.hidden, true);
  }
});

test("a displayed skeleton stays for 350ms, while ready content has no minimum wait", () => {
  let now = 100;
  const v = view([]);
  const loading = createBrowseLoading(v.region, v.skeleton, () => now);
  assert.equal(loading.remaining(), 0);
  loading.show();
  now = 200;
  assert.equal(loading.remaining(), 250);
  loading.show(); // Showing again cannot restart the minimum.
  now = 450;
  assert.equal(loading.remaining(), 0);
  loading.finish();
  assert.equal(loading.remaining(), 0);
});

test("aborting the active search cancels its minimum wait", async () => {
  const v = view([]);
  v.loading.show();
  const controller = new AbortController();
  const settled = v.loading.settle(controller.signal);
  controller.abort();
  await settled;
});

function poster(complete = false) {
  const cover = { attributes: new Set(), setAttribute(name) { this.attributes.add(name); }, removeAttribute(name) { this.attributes.delete(name); } };
  const image = new EventTarget();
  Object.assign(image, { complete, hidden: false, closest: () => cover });
  return { cover, image };
}

test("cached and quickly loaded posters never display a skeleton", t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  for (const cached of [true, false]) {
    const p = poster(cached);
    prepareBrowsePoster(p.image, new AbortController().signal);
    if (!cached) p.image.dispatchEvent(new Event('load'));
    t.mock.timers.tick(2000);
    assert.equal(p.cover.attributes.size, 0);
  }
});

test("slow posters have a minimum skeleton duration and stalled posters recover", t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let now = 0;
  t.mock.method(performance, 'now', () => now);
  const p = poster();
  prepareBrowsePoster(p.image, new AbortController().signal);
  now = 120;
  t.mock.timers.tick(120);
  assert.equal(p.cover.attributes.has('data-poster-loading'), true);
  now = 150;
  p.image.dispatchEvent(new Event('load'));
  t.mock.timers.tick(319);
  assert.equal(p.cover.attributes.has('data-poster-loading'), true);
  t.mock.timers.tick(1);
  assert.equal(p.cover.attributes.size, 0);
  const stalled = poster();
  prepareBrowsePoster(stalled.image, new AbortController().signal);
  now = 270;
  t.mock.timers.tick(120);
  now = 1650;
  t.mock.timers.tick(1380);
  assert.equal(stalled.cover.attributes.size, 0);
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
