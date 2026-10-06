import assert from 'node:assert/strict';
import test from 'node:test';
import {initializeScrollMotion} from '../src/components/dev/detail-scroll-motion.ts';

function preview(t, {mobile = false, reduced = false, loading = false, initialY = 380} = {}) {
  let now = 0, y = initialY, hidden = false, focused = false, nextFrame = 0;
  const frames = new Map(), classes = new Set(), animations = [];
  const controller = new AbortController();
  const motion = new EventTarget(), input = new EventTarget(), viewport = new EventTarget();
  motion.matches = reduced;
  input.matches = mobile;
  const element = {
    classList: {add: name => classes.add(name), remove: name => classes.delete(name)},
    getClientRects: () => hidden ? [] : [{}],
    getBoundingClientRect: () => ({top: 1200 - y + (classes.size ? mobile ? 8 : 12 : 0), bottom: 1360 - y + (classes.size ? mobile ? 8 : 12 : 0)}),
    matches: selector => selector === ':focus-within' ? focused : loading,
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
    getComputedStyle: () => ({translate: classes.size ? `0px ${mobile ? 8 : 12}px` : 'none'}),
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
    focus() {focused = true;const event = new Event('focusin');Object.defineProperty(event, 'target', {value: {closest: () => element}});root.dispatchEvent(event);},
    reduce() {motion.matches = true;motion.dispatchEvent(new Event('change'));},
    changeInput() {input.matches = !input.matches;input.dispatchEvent(new Event('change'));},
  };
}

test('desktop fades on entry without replaying while still visible', t => {
  const p = preview(t);
  assert(p.classes.has('lab-scroll-reveal'));
  assert.equal(p.animations.length, 0);
  p.scroll(410); // Wait until entry: do not spend the fade below the viewport.
  assert.equal(p.animations.length, 0);
  p.scroll(424);
  assert.equal(p.animations.length, 1);
  assert.equal(p.animations[0].keyframes[0].opacity, .2);
  assert.equal(p.animations[0].keyframes[0].translate, '0 12px');
  assert.equal(p.animations[0].options.duration, 260);
  p.animations[0].dispatchEvent(new Event('finish'));
  assert.equal(p.classes.size, 0);
  p.scroll(500);p.scroll(424);p.ready();
  assert.equal(p.animations.length, 1);
});

test('fast scroll steps still get a short fade', t => {
  const p = preview(t);
  p.scroll(800, 16);
  assert.equal(p.classes.size, 1);
  assert.equal(p.animations.length, 1);
});

test('jumps past a module rearm it without animating offscreen', t => {
  const p = preview(t);
  p.scroll(1500, 16);
  assert.equal(p.classes.size, 1);
  assert.equal(p.animations.length, 0);
});

for (const mobile of [false, true]) {
  test(`rearms after fully leaving either edge (${mobile ? 'mobile' : 'desktop'})`, t => {
    const p = preview(t, {mobile});
    p.scroll(424);
    p.animations[0].dispatchEvent(new Event('finish'));
    p.scroll(1300); // Still partly visible above: do not reset.
    assert.equal(p.classes.size, 0);
    p.scroll(1400); // Fully above the viewport.
    assert.equal(p.classes.size, 1);
    p.scroll(1300); // Return through the top edge.
    assert.equal(p.animations.length, 2);
    p.animations[1].dispatchEvent(new Event('finish'));
    p.scroll(0); // Fully below the viewport.
    assert.equal(p.classes.size, 1);
    p.scroll(424);
    assert.equal(p.animations.length, 3);
  });
}

test('leaving during an animation cancels it and allows a fresh entrance', t => {
  const p = preview(t);
  p.scroll(424);
  p.scroll(0);
  assert(p.animations[0].cancelled);
  p.scroll(424);
  assert.equal(p.animations.length, 2);
});

test('focused controls do not fade when they leave and return', t => {
  const p = preview(t);
  p.focus();
  p.scroll(0);
  p.scroll(424);
  assert.equal(p.classes.size, 0);
  assert.equal(p.animations.length, 0);
});

test('modules visible on initial load also rearm after leaving', t => {
  const p = preview(t, {initialY: 600});
  assert.equal(p.animations.length, 1);
  p.scroll(0);
  assert.equal(p.classes.size, 1);
  p.scroll(424);
  assert.equal(p.animations.length, 2);
});

test('reduced motion prevents offscreen rearming after it is enabled', t => {
  const p = preview(t);
  p.scroll(424);
  p.reduce();
  p.scroll(0);
  p.scroll(424);
  assert.equal(p.classes.size, 0);
  assert.equal(p.animations.length, 1);
});

test('a normal mouse wheel step still gets the desktop fade', t => {
  const p = preview(t);
  p.scroll(480, 200);
  assert.equal(p.animations.length, 1);
  assert(p.classes.has('lab-scroll-reveal'));
});

test('a fling does not cancel an active fade near the edge', t => {
  const p = preview(t);
  p.scroll(424);
  p.scroll(470, 16);
  assert.equal(p.classes.size, 1);
  assert(!p.animations[0].cancelled);
});

test('an active entrance is allowed to finish in the reading area', t => {
  const p = preview(t);
  p.scroll(424);
  p.scroll(535, 150);
  assert.equal(p.classes.size, 1);
  assert(!p.animations[0].cancelled);
});

test('mobile gets a visible fade with a smaller entrance', t => {
  const p = preview(t, {mobile: true});
  p.scroll(424);
  assert.equal(p.animations.length, 1);
  assert.equal(p.animations[0].keyframes[0].opacity, .2);
  assert.equal(p.animations[0].keyframes[0].translate, '0 8px');
  assert.equal(p.animations[0].options.duration, 260);
});

test('loading completion starts a waiting entrance but cannot replay it', t => {
  const p = preview(t, {loading: true});
  p.scroll(424);
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
  p.scroll(424);
  p.controller.abort();
  assert.equal(p.classes.size, 0);
  assert(p.animations[0].cancelled);
  assert(p.observers.every(observer => observer.disconnected));
  p.scroll(400);
  assert.equal(p.animations.length, 1);
});
