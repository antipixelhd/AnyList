import assert from "node:assert/strict";
import test from "node:test";
import { createBrowseLoading, handoffBrowseSkeletons, prepareBrowsePoster, trackBrowseSkeleton } from "../src/lib/browse-loading.ts";

function view(visibleCards) {
  const cards = visibleCards.map((visible) => ({
    getClientRects: () => visible ? [{}] : [],
  }));
  const region = { dataset: {}, querySelectorAll: () => cards };
  const skeletonGrid = {hidden: false, querySelectorAll: selector => skeleton.querySelectorAll(selector)};
  const skeletonSections = {hidden: true, querySelectorAll: selector => skeleton.querySelectorAll(selector)};
  const skeletonTimeline = {hidden: true, querySelectorAll: selector => skeleton.querySelectorAll(selector)};
  const skeleton = { hidden: true, querySelectorAll: () => [], querySelector: selector => selector === '[data-skeleton-grid]' ? skeletonGrid : selector === '[data-skeleton-timeline]' ? skeletonTimeline : skeletonSections };
  return { region, skeleton, skeletonGrid, skeletonSections, skeletonTimeline, loading: createBrowseLoading(region, skeleton) };
}

test('timeline loading and pagination use timeline slots, and switching views restores grid slots',()=>{
  const v=view([]);
  for(const append of [false,true]){
    v.loading.show(append,false,true);
    assert.equal(v.skeletonTimeline.hidden,false);
    assert.equal(v.skeletonGrid.hidden,true);
    assert.equal(v.skeletonSections.hidden,true);
    v.loading.finish();
  }
  v.loading.show();
  assert.equal(v.skeletonTimeline.hidden,true);
  assert.equal(v.skeletonGrid.hidden,false);
});

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

test("replacing a pending view releases its skeleton synchronously", () => {
  const v = view([]);
  v.loading.show();
  assert.equal(v.skeleton.hidden, false);
  v.loading.finish();
  assert.equal(v.skeleton.hidden, true);
  assert.equal(v.region.dataset.browseLoading, undefined);
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

function paintClock(t) {
  let now = 0, nextFrame = 0;
  const frames = new Map();
  const globals = {
    innerHeight: 800,
    requestAnimationFrame(callback) { frames.set(++nextFrame, callback); return nextFrame; },
    cancelAnimationFrame(frame) { frames.delete(frame); },
  };
  const saved = Object.fromEntries(Object.keys(globals).map(key => [key, Object.getOwnPropertyDescriptor(globalThis,key)]));
  Object.assign(globalThis, globals);
  t.after(() => Object.entries(saved).forEach(([key, descriptor]) => {
    if (descriptor) Object.defineProperty(globalThis, key, descriptor);
    else delete globalThis[key];
  }));
  t.mock.method(performance, 'now', () => now);
  t.mock.timers.enable({apis:['setTimeout']});
  return {
    element: {isConnected:true, getBoundingClientRect: () => ({width:100, height:150, top:100, bottom:250})},
    paint() { for (const [id,callback] of frames) { frames.delete(id); callback(); } },
    advance(ms) { now += ms; t.mock.timers.tick(ms); },
  };
}

test('only a painted placeholder holds a result, and abort releases the wait', async t => {
  const clock = paintClock(t);
  const controller = new AbortController();
  const unpainted = trackBrowseSkeleton(clock.element);
  assert.equal(unpainted.settle(controller.signal), undefined);
  const painted = trackBrowseSkeleton(clock.element);
  clock.paint();
  clock.advance(20);
  let resolved = false;
  const waiting = painted.settle(controller.signal).then(() => { resolved = true; });
  clock.advance(329);
  await Promise.resolve();
  assert.equal(resolved, false);
  controller.abort();
  await waiting;
  assert.equal(resolved, true);
});

test('a poster arriving during a painted skeleton holds it for 350 ms before fading in', async t => {
  const clock = paintClock(t);
  const {cover,image} = poster();
  Object.assign(cover,clock.element);
  prepareBrowsePoster(image,new AbortController().signal);
  clock.paint();
  clock.advance(50);
  image.dispatchEvent(new Event('load'));
  clock.advance(299);
  await Promise.resolve();
  assert(cover.attributes.has('data-poster-loading'));
  clock.advance(1);
  await Promise.resolve();
  assert.deepEqual([...cover.attributes],['data-poster-revealing','data-poster-ready']);
});

test('a poster continues its skeleton entrance from the same timeline position', () => {
  const frames = [{scale:'0.92', opacity:0}, {scale:'1', opacity:1}];
  const skeleton = {
    hasAttribute: () => true,
    getAnimations: () => [{playState:'running',startTime:120,effect:{getKeyframes:()=>frames,getTiming:()=>({duration:300})}}],
  };
  const continuation = {startTime:0};
  const attributes = [];
  const card = {
    setAttribute: name => attributes.push(name),
    classList:{remove(){}},
    querySelector:()=>null,
    animate(keyframes, timing) {
      assert.deepEqual(keyframes,frames);
      assert.equal(timing.duration,300);
      return continuation;
    },
  };
  handoffBrowseSkeletons({querySelectorAll:()=>[skeleton]},[card]);
  assert.deepEqual(attributes,['data-browse-appeared']);
  assert.equal(continuation.startTime,120);
});

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

test("loaded posters reveal immediately and stalled posters recover", t => {
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
