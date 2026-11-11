import assert from 'node:assert/strict';
import test from 'node:test';
import { createStatsCardMotion } from '../src/lib/stats-card-motion.ts';

test('sorting moves visible matches and reveals replacements in place', t => {
  const grid = {
    isConnected: true,
    ownerDocument: { timeline: { currentTime: 125 } },
    children: [],
    replaceChildren(...cards) { this.children = cards; },
  };
  const top = [120, 390, 680, 940];
  const animations = [];
  const settled = [];
  function card(name) {
    return {
      name,
      getBoundingClientRect() {
        const y = top[grid.children.indexOf(this)];
        return { top: y, bottom: y + 220, left: 20, width: 300, height: 220 };
      },
      animate(frames, timing) {
        const animation = { name, frames, timing, finished: new Promise(() => {}), cancelled: false, cancel() { this.cancelled = true; } };
        animations.push(animation);
        return animation;
      },
    };
  }
  globalThis.innerHeight = 900;
  globalThis.getComputedStyle = card => ({ opacity: animations.some(animation => animation.name === card.name && !animation.cancelled) ? '.9' : '1' });
  t.after(() => {
    delete globalThis.innerHeight;
    delete globalThis.getComputedStyle;
  });
  const reduced = Object.assign(new EventTarget(), { matches: false });
  const controller = new AbortController();
  const update = createStatsCardMotion(grid, reduced, controller.signal, card => settled.push(card.name));
  const [first, second, third, offscreen, replacement, extra] = ['first', 'second', 'third', 'offscreen', 'replacement', 'extra'].map(card);
  grid.children = [first, second, third, offscreen];
  update([second, offscreen, replacement, extra], true);
  assert.deepEqual(settled, ['second', 'offscreen', 'replacement']);
  assert(animations.every(animation => animation.startTime === 125));
  assert.deepEqual(animations.map(a => [a.name, a.frames[0], a.timing.duration]), [
    ['second', { transform: 'translate(0px, 270px)', opacity: 1 }, 280],
    ['offscreen', { transform: 'translate(0px, 4px)', opacity: .78 }, 220],
    ['replacement', { transform: 'translate(0px, 4px)', opacity: .78 }, 220],
  ]);
  update([second, offscreen, replacement, extra], true);
  assert.equal(animations.length, 3);
  assert.equal(animations[0].cancelled, false);
  update([replacement, second, offscreen, extra], true);
  assert(animations.slice(0, 3).every(animation => animation.cancelled));
  assert.equal(animations[3].frames[0].opacity, .9);
  reduced.matches = true;
  reduced.dispatchEvent(new Event('change'));
  assert(animations.every(animation => animation.cancelled));
  const count = animations.length;
  update([first, third], true);
  assert.equal(animations.length, count);
  assert.deepEqual(grid.children, [first, third]);
  controller.abort();
});
