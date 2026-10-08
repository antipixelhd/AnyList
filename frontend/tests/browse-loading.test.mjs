import assert from "node:assert/strict";
import test from "node:test";
import { createBrowseLoading, prepareBrowsePoster } from "../src/lib/browse-loading.ts";

function view(visibleCards) {
  const cards = visibleCards.map((visible) => ({
    getClientRects: () => visible ? [{}] : [],
  }));
  const region = { dataset: {}, querySelectorAll: () => cards };
  const skeletonGrid = {hidden: false, querySelectorAll: selector => skeleton.querySelectorAll(selector)};
  const skeletonSections = {hidden: true, querySelectorAll: selector => skeleton.querySelectorAll(selector)};
  const skeleton = { hidden: true, querySelectorAll: () => [], querySelector: selector => selector === '[data-skeleton-grid]' ? skeletonGrid : skeletonSections };
  return { region, skeleton, skeletonGrid, skeletonSections, loading: createBrowseLoading(region, skeleton) };
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

test("only skeleton slots that appeared hand their entrance to replacement covers", () => {
  const v = view([]);
  const appeared = {hasAttribute: () => true};
  const pending = {hasAttribute: () => false};
  v.skeleton.querySelectorAll = selector => selector === '.browse-skeleton-body' ? [appeared, pending] : [];
  const covers = Array.from({length: 3}, () => ({
    attributes: new Set(),
    setAttribute(name) { this.attributes.add(name); },
  }));
  v.loading.handoff(covers);
  assert.equal(covers[0].attributes.size, 0);
  v.loading.show();
  v.loading.handoff(covers);
  assert(covers[0].attributes.has('data-browse-appeared'));
  assert.equal(covers[1].attributes.size, 0);
  assert.equal(covers[2].attributes.size, 0);
});

test("category discovery uses section slots; filtered results and pagination use grid slots", () => {
  const v = view([]);
  v.loading.show(false, true);
  assert.equal(v.skeletonGrid.hidden, true);
  assert.equal(v.skeletonSections.hidden, false);
  v.loading.show(false, false);
  assert.equal(v.skeletonGrid.hidden, false);
  assert.equal(v.skeletonSections.hidden, true);
  v.loading.finish();
  v.loading.show(true, true);
  assert.equal(v.skeletonGrid.hidden, false);
  assert.equal(v.skeletonSections.hidden, true);
});

function poster(complete = false) {
  const cover = { attributes: new Set(), setAttribute(name) { this.attributes.add(name); }, removeAttribute(name) { this.attributes.delete(name); } };
  const image = new EventTarget();
  Object.assign(image, { complete, hidden: false, closest: () => cover });
  return { cover, image };
}

test("cached posters fade immediately; uncached posters start with an entrance skeleton", t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  for (const cached of [true, false]) {
    const p = poster(cached);
    prepareBrowsePoster(p.image, new AbortController().signal);
    assert.equal(p.cover.attributes.has('data-poster-loading'), !cached);
    if (!cached) p.image.dispatchEvent(new Event('load'));
    t.mock.timers.tick(2000);
    assert.deepEqual([...p.cover.attributes], ['data-poster-ready']);
  }
});

test("artwork readiness is cached per poster without making other cards wait", t => {
  t.mock.timers.enable({apis: ['setTimeout']});
  const ready = new Set();
  const first = poster(), slow = poster();
  first.image.src = 'https://example.test/first.jpg';
  slow.image.src = 'https://example.test/slow.jpg';
  prepareBrowsePoster(first.image, new AbortController().signal, ready);
  prepareBrowsePoster(slow.image, new AbortController().signal, ready);
  first.image.dispatchEvent(new Event('load'));
  t.mock.timers.tick(350);
  assert(first.cover.attributes.has('data-poster-ready'));
  assert(slow.cover.attributes.has('data-poster-loading'));
  const repeated = poster(); // Browser cache may complete asynchronously on a fresh img.
  repeated.image.src = first.image.src;
  prepareBrowsePoster(repeated.image, new AbortController().signal, ready);
  assert.deepEqual([...repeated.cover.attributes], ['data-poster-ready']);
});

test("slow posters have a minimum skeleton duration and stalled posters recover", t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let now = 0;
  t.mock.method(performance, 'now', () => now);
  const p = poster();
  prepareBrowsePoster(p.image, new AbortController().signal);
  assert(p.cover.attributes.has('data-poster-pending'));
  now = 120;
  t.mock.timers.tick(120);
  assert.equal(p.cover.attributes.has('data-poster-loading'), true);
  now = 150;
  p.image.dispatchEvent(new Event('load'));
  now = 349;
  t.mock.timers.tick(199);
  assert.equal(p.cover.attributes.has('data-poster-loading'), true);
  now = 350;
  t.mock.timers.tick(1);
  assert.deepEqual([...p.cover.attributes], ['data-poster-ready']);
  const stalled = poster();
  prepareBrowsePoster(stalled.image, new AbortController().signal);
  now = 270;
  t.mock.timers.tick(120);
  now = 1650;
  t.mock.timers.tick(1380);
  assert.equal(stalled.cover.attributes.size, 0);
  stalled.image.dispatchEvent(new Event('load'));
  assert.deepEqual([...stalled.cover.attributes], ['data-poster-ready']);
});

test("loading more keeps existing cards readable and shows the appended skeleton grid", () => {
  const v = view([true]);
  v.loading.show(true);
  assert.equal(v.region.dataset.browseLoading, "append");
  assert.equal(v.skeleton.hidden, false);
  v.loading.finish();
  assert.equal(v.skeleton.hidden, true);
});

test("navigation removes pending artwork state and prevents a late poster fade", t => {
  t.mock.timers.enable({apis: ['setTimeout']});
  const p = poster();
  const controller = new AbortController();
  prepareBrowsePoster(p.image, controller.signal);
  assert(p.cover.attributes.has('data-poster-loading'));
  controller.abort();
  p.image.dispatchEvent(new Event('load'));
  t.mock.timers.tick(2000);
  assert.equal(p.cover.attributes.size, 0);
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
