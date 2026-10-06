import assert from 'node:assert/strict';
import test from 'node:test';
import {initializeScrollMotion} from '../src/components/dev/detail-scroll-motion.ts';

function preview(t, {mobile = false, reduced = false, loading = false} = {}) {
  let now = 0, y = 380, hidden = false, nextFrame = 0;
  const frames = new Map(), classes = new Set(), animations = [];
  const controller = new AbortController();
  const motion = new EventTarget(), input = new EventTarget(), viewport = new EventTarget();
  motion.matches = reduced;
  input.matches = mobile;
  const element = {
    classList: {add: name => classes.add(name), remove: name => classes.delete(name)},
    getClientRects: () => hidden ? [] : [{}],
    getBoundingClientRect: () => ({top: 1200 - y + (classes.size ? mobile ? 2 : 6 : 0), bottom: 1360 - y}),
    matches: () => loading,
    animate: (keyframes, options) => {
      const animation = new EventTarget();
      Object.assign(animation, {keyframes, options, cancelled: false, cancel() {this.cancelled = true;}});
      animations.push(animation);
      return animation;
    },
  };
  const root = new EventTarget();
  root.querySelectorAll = () => [element];
  const observers = [];
  class Observer {
    constructor(callback) {this.callback = callback;observers.push(this);}
    observe() {}
    disconnect() {this.disconnected = true;}
  }
  const globals = {
    window: viewport, innerHeight: 800,
    matchMedia: query => query.includes('reduced') ? motion : input,
    requestAnimationFrame: callback => {frames.set(++nextFrame, callback);return nextFrame;},
    cancelAnimationFrame: id => frames.delete(id),
    getComputedStyle: () => ({translate: classes.size ? `0px ${mobile ? 2 : 6}px` : 'none'}),
    MutationObserver: Observer, ResizeObserver: Observer,
  };
  const original = new Map(Object.keys(globals).map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  original.set('scrollY', Object.getOwnPropertyDescriptor(globalThis, 'scrollY'));
  for (const [key, value] of Object.entries(globals)) Object.defineProperty(globalThis, key, {configurable: true, writable: true, value});
  Object.defineProperty(globalThis, 'scrollY', {configurable: true, get: () => y});
  t.mock.method(performance, 'now', () => now);
  t.after(() => {
    controller.abort();
    for (const [key, descriptor] of original) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  });
  initializeScrollMotion(root, controller.signal);
  const flush = () => {for (const [id, callback] of frames) {frames.delete(id);callback();}};
  return {
    classes, animations, observers, controller,
    scroll(to, elapsed = 100) {y = to;now += elapsed;viewport.dispatchEvent(new Event('scroll'));flush();},
    ready() {loading = false;observers[0].callback();flush();},
    hide() {hidden = true;observers[0].callback();flush();},
    focus() {const event = new Event('focusin');Object.defineProperty(event, 'target', {value: {closest: () => element}});root.dispatchEvent(event);},
    reduce() {motion.matches = true;motion.dispatchEvent(new Event('change'));},
    changeInput() {input.matches = !input.matches;input.dispatchEvent(new Event('change'));},
  };
}

test('desktop is prepared offscreen and fades once, even when scrolling stops', t => {
  const p = preview(t);
  assert(p.classes.has('lab-scroll-reveal'));
  assert.equal(p.animations.length, 0);
  p.scroll(388); // Module is still 12px below the viewport: begin early.
  assert.equal(p.animations.length, 1);
  assert.equal(p.animations[0].keyframes[0].opacity, 0);
  assert.equal(p.animations[0].options.duration, 340);
  p.animations[0].dispatchEvent(new Event('finish'));
  assert.equal(p.classes.size, 0);
  p.scroll(0);p.scroll(388);p.ready();
  assert.equal(p.animations.length, 1);
});

test('fast jumps expose content immediately without starting a fade', t => {
  const p = preview(t);
  p.scroll(800, 16);
  assert.equal(p.classes.size, 0);
  assert.equal(p.animations.length, 0);
});

test('a normal mouse wheel step still gets the desktop fade', t => {
  const p = preview(t);
  p.scroll(480, 200);
  assert.equal(p.animations.length, 1);
  assert(p.classes.has('lab-scroll-reveal'));
});

test('a fling completes an active fade while it is still near the edge', t => {
  const p = preview(t);
  p.scroll(388);
  p.scroll(430, 16);
  assert.equal(p.classes.size, 0);
  assert(p.animations[0].cancelled);
});

test('an active entrance finishes before entering the reading area', t => {
  const p = preview(t);
  p.scroll(388);
  p.scroll(535, 150);
  assert.equal(p.classes.size, 0);
  assert(p.animations[0].cancelled);
});

test('mobile keeps content opaque and uses a shorter, smaller entrance', t => {
  const p = preview(t, {mobile: true});
  p.scroll(408);
  assert.equal(p.animations.length, 1);
  assert.equal(p.animations[0].keyframes[0].opacity, 1);
  assert.equal(p.animations[0].keyframes[0].translate, '0 2px');
  assert.equal(p.animations[0].options.duration, 160);
});

test('loading completion starts a waiting entrance but cannot replay it', t => {
  const p = preview(t, {loading: true});
  p.scroll(408);
  assert.equal(p.animations.length, 0);
  p.ready();
  assert.equal(p.animations.length, 1);
  p.animations[0].dispatchEvent(new Event('finish'));
  p.ready();
  assert.equal(p.animations.length, 1);
});

for (const action of ['focus', 'reduce', 'changeInput']) {
  test(`${action} exposes pending content immediately`, t => {
    const p = preview(t);
    p[action]();
    assert.equal(p.classes.size, 0);
  });
}

test('reduced motion never prepares hidden content', t => {
  const p = preview(t, {reduced: true});
  assert.equal(p.classes.size, 0);
  assert.equal(p.animations.length, 0);
});

test('navigation cancels motion and disconnects observers', t => {
  const p = preview(t);
  p.scroll(388);
  p.controller.abort();
  assert.equal(p.classes.size, 0);
  assert(p.animations[0].cancelled);
  assert(p.observers.every(observer => observer.disconnected));
  p.scroll(400);
  assert.equal(p.animations.length, 1);
});
