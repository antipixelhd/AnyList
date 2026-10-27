import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import vm from 'node:vm';
import test from 'node:test';

function fixture({reduced = false} = {}) {
  let now = 0, next = 0, acknowledgements = 0;
  const frames = new Map(), captured = new Set();
  const signal = new AbortController();
  const card = Object.assign(new EventTarget(), {
    hidden: true, inert: false, dataset: {},
    style: {removeProperty(name) {delete this[name];}},
    getBoundingClientRect: () => ({width: 358}),
    setPointerCapture: id => captured.add(id), hasPointerCapture: id => captured.has(id),
    releasePointerCapture: id => captured.delete(id),
  });
  const window = Object.assign(new EventTarget(), {innerWidth: 390});
  const media = query => Object.assign(new EventTarget(), {matches: query.includes('reduced') ? reduced : true});
  const spring = readFileSync(new URL('../src/lib/gallery-spring.ts', import.meta.url), 'utf8');
  const source = readFileSync(new URL('../src/lib/attention-prompt-motion.ts', import.meta.url), 'utf8').replace(/^import .*;\r?\n/, '');
  const context = vm.createContext({card, window, signal: signal.signal, matchMedia: media,
    performance: {now: () => now}, requestAnimationFrame: f => (frames.set(++next, f), next),
    cancelAnimationFrame: id => frames.delete(id), acknowledge: () => acknowledgements++,
  });
  vm.runInContext(stripTypeScriptTypes((spring + source).replaceAll('export ', '')) +
    '\nglobalThis.motion = bindAttentionPromptMotion(card, signal, acknowledge);', context);
  const advance = ms => {now += ms; const work = [...frames.values()]; frames.clear(); work.forEach(f => f(now));};
  const pointer = (type, x, y = 100) => {
    const event = Object.assign(new Event(type, {cancelable: true}), {clientX: x, clientY: y, pointerId: 1, isPrimary: true, button: 0});
    (type === 'pointerup' || type === 'pointercancel' ? window : card).dispatchEvent(event);
    return event;
  };
  context.motion.show(); advance(1000);
  const captureLoss = (target = card) => {
    const event = Object.assign(new Event('lostpointercapture'), {pointerId: 1});
    Object.defineProperty(event, 'target', {value: target});
    card.dispatchEvent(event);
  };
  return {card, motion: context.motion, pointer, captureLoss, advance, signal, frames, captured, acknowledgements: () => acknowledgements};
}

test('implicit touch capture transferring from child text or buttons does not cancel the drag', () => {
  for (const tagName of ['STRONG', 'P', 'A', 'BUTTON']) {
    const f = fixture(); f.pointer('pointerdown', 100); f.advance(20); f.pointer('pointermove', 120);
    f.captureLoss({tagName});
    f.advance(20); f.pointer('pointermove', 160);
    assert.equal(f.card.style.translate, '60px 0px', `${tagName} capture handoff retains finger tracking`);
    f.pointer('pointerup', 160); f.advance(1000);
    assert.equal(f.card.hidden, true);
    assert.equal(f.acknowledgements(), 1);
  }
});

test('genuine loss of the card capture safely returns it without acknowledgement', () => {
  const f = fixture(); f.pointer('pointerdown', 100); f.advance(20); f.pointer('pointermove', 170);
  f.captured.delete(1); f.captureLoss(); f.advance(1000);
  assert.equal(f.card.hidden, false); assert.equal(f.card.style.translate, '0px 0px');
  assert.equal(f.acknowledgements(), 0);
});

test('an ordinary quarter-card swipe dismisses even when released slowly', () => {
  for (const direction of [-1, 1]) {
    const f = fixture(); f.pointer('pointerdown', 180); f.advance(400);
    f.pointer('pointermove', 180 + direction * 95); f.advance(150);
    f.pointer('pointerup', 180 + direction * 95); f.advance(1000);
    assert.equal(f.card.hidden, true); assert.equal(f.acknowledgements(), 1);
  }
});

test('a slow short drag follows the finger, fades, and springs back without dismissing', () => {
  const f = fixture(); f.pointer('pointerdown', 100); f.advance(200); f.pointer('pointermove', 140);
  assert.equal(f.card.style.translate, '40px 0px');
  assert.ok(Number(f.card.style.opacity) < 1);
  assert.equal(f.captured.size, 1);
  f.advance(150); f.pointer('pointerup', 140); f.advance(1000);
  assert.equal(f.card.hidden, false); assert.equal(f.card.style.translate, '0px 0px');
  assert.equal(f.acknowledgements(), 0); assert.equal(f.captured.size, 0);
});

for (const direction of [-1, 1]) test(`a ${direction < 0 ? 'left' : 'right'} flick dismisses with continuous motion`, () => {
  const f = fixture(); f.pointer('pointerdown', 180); f.advance(20); f.pointer('pointermove', 180 + direction * 48);
  f.pointer('pointerup', 180 + direction * 48);
  assert.equal(f.acknowledgements(), 1); assert.equal(f.card.hidden, false);
  const click = new Event('click', {cancelable: true}); f.card.dispatchEvent(click);
  assert.equal(click.defaultPrevented, true, 'a swipe cannot activate the button beneath the finger');
  f.advance(1000); assert.equal(f.card.hidden, true); assert.equal(f.frames.size, 0);
});

test('a deliberate slow swipe dismisses while vertical scrolling and pointer cancellation do not', () => {
  const f = fixture(); f.pointer('pointerdown', 100); f.advance(400); f.pointer('pointermove', 105, 200);
  f.pointer('pointerup', 105, 200); f.advance(1000); assert.equal(f.acknowledgements(), 0);
  f.pointer('pointerdown', 100); f.advance(400); f.pointer('pointermove', 250);
  f.pointer('pointercancel', 250); f.advance(1000); assert.equal(f.acknowledgements(), 0);
  f.pointer('pointerdown', 100); f.advance(400); f.pointer('pointermove', 250);
  f.advance(150); f.pointer('pointerup', 250); f.advance(1000);
  assert.equal(f.card.hidden, true); assert.equal(f.acknowledgements(), 1);
});

test('ordinary taps remain clickable and reduced motion finishes without animation frames', () => {
  const f = fixture({reduced: true}); f.pointer('pointerdown', 100); f.pointer('pointerup', 100);
  const click = new Event('click', {cancelable: true}); f.card.dispatchEvent(click);
  assert.equal(click.defaultPrevented, false);
  f.motion.dismiss(); assert.equal(f.card.hidden, true); assert.equal(f.frames.size, 0);
});

test('new content interrupts an exit safely and route cleanup cancels outstanding frames', () => {
  const f = fixture(); f.motion.dismiss(1); f.advance(50); f.motion.show(); f.advance(1000);
  assert.equal(f.card.hidden, false); assert.equal(f.card.inert, false);
  f.motion.hide(); f.advance(20); f.signal.abort();
  assert.equal(f.frames.size, 0); assert.equal(f.card.style.translate, undefined);
  assert.equal(f.card.style.opacity, undefined);
});
