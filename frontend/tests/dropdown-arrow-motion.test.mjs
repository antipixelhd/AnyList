import assert from 'node:assert/strict';
import test from 'node:test';
import { createDropdownArrowMotion, dropdownTiming } from '../src/lib/dropdown-arrow-motion.ts';

function fixture() {
  const arrow = { style: {} }, panel = { contains: value => value === arrow };
  const calls = [];
  let reduced = false;
  const motion = createDropdownArrowMotion((element, values, timing) => {
    let finish;
    const control = {
      stopped: false, cancelled: false,
      stop() { this.stopped = true; },
      cancel() { this.cancelled = true; },
      finished: new Promise(resolve => { finish = resolve; }),
    };
    calls.push({ element, values, timing, control, finish });
    return control;
  }, () => reduced, () => arrow);
  return { arrow, panel, calls, motion, reduce: () => { reduced = true; } };
}

test('arrows use the panel timing for opening, closing, and fade-only dropdowns', async () => {
  for (const fading of [false, true]) {
    const f = fixture();
    for (const open of [true, false]) {
      const timing = dropdownTiming(open, fading);
      f.motion.play(f.panel, open, timing);
      const call = f.calls.at(-1);
      assert.equal(call.timing, timing);
      assert.equal(timing.duration, fading ? (open ? .24 : .16) : (open ? .26 : .18));
      assert.equal(call.values.transform.at(-1), `rotate(${open ? -180 : 0}deg)`);
      call.finish();
      await Promise.resolve();
      assert.equal(f.arrow.style.transform, call.values.transform.at(-1));
      assert.equal(f.arrow.style.transition, 'none');
    }
  }
});

test('rapid open-close-open reverses the active animation without stale completions resetting it', async () => {
  const f = fixture();
  f.motion.play(f.panel, true, dropdownTiming(true));
  f.motion.play(f.panel, false, dropdownTiming(false));
  f.motion.play(f.panel, true, dropdownTiming(true));
  assert.ok(f.calls[0].control.stopped);
  assert.ok(f.calls[1].control.stopped);
  f.calls[1].finish(); f.calls[0].finish();
  await Promise.resolve();
  assert.equal(f.arrow.style.transform, undefined);
  assert.equal(f.calls[2].control.cancelled, false);
  f.calls[2].finish();
  await Promise.resolve();
  assert.equal(f.arrow.style.transform, 'rotate(-180deg)');
});

test('reduced motion and immediate closes settle without running an animation', () => {
  const f = fixture();
  f.motion.play(f.panel, true, dropdownTiming(true));
  f.motion.play(f.panel, false, { ...dropdownTiming(false), duration: 0 });
  assert.equal(f.arrow.style.transform, 'rotate(0deg)');
  assert.equal(f.calls.length, 1);
  f.reduce();
  f.motion.play(f.panel, true, dropdownTiming(true));
  assert.equal(f.arrow.style.transform, 'rotate(-180deg)');
  assert.equal(f.calls.length, 1);
});

test('closing preserves the negative half turn instead of returning through the left', async () => {
  const f = fixture();
  f.motion.play(f.panel, true, dropdownTiming(true));
  f.calls[0].finish();
  await Promise.resolve();
  f.motion.play(f.panel, false, dropdownTiming(false));
  assert.deepEqual(f.calls[1].values.transform, ['rotate(-180deg)', 'rotate(0deg)']);
  // A stopped Web Animation commits a matrix, whose +/-180 sign is ambiguous.
  f.arrow.style.transform = 'matrix(-1, 0, 0, -1, 0, 0)';
  f.motion.play(f.panel, false, dropdownTiming(false));
  assert.deepEqual(f.calls[2].values.transform, ['rotate(-180deg)', 'rotate(0deg)']);
});

test('navigation cleanup settles only arrows belonging to the requested panel or trigger tree', () => {
  const f = fixture();
  f.motion.play(f.panel, true, dropdownTiming(true));
  f.motion.cancel({ contains: () => false });
  assert.equal(f.calls[0].control.cancelled, false);
  f.motion.cancel({ contains: node => node === f.arrow });
  assert.equal(f.calls[0].control.cancelled, true);
  assert.equal(f.arrow.style.transform, 'rotate(-180deg)');
});

test('menus without a dropdown arrow remain usable', () => {
  const motion = createDropdownArrowMotion(() => { throw Error('No arrow to animate'); }, () => false, () => null);
  assert.doesNotThrow(() => motion.play({}, true, dropdownTiming(true)));
});

test('portaled menus find their trigger through aria-controls rather than DOM ancestry', () => {
  const arrow = { style: {} };
  const trigger = {
    getAttribute: name => name === 'aria-controls' ? 'help portaled-menu' : null,
    querySelector: () => arrow,
    dataset: {},
  };
  const panel = { id: 'portaled-menu', ownerDocument: { querySelectorAll: () => [trigger] } };
  const motion = createDropdownArrowMotion(() => { throw Error('Reduced motion must not animate'); }, () => true);
  motion.play(panel, true, dropdownTiming(true));
  assert.equal(arrow.style.transform, 'rotate(-180deg)');
  motion.play(panel, false, dropdownTiming(false));
  assert.equal(arrow.style.transform, 'rotate(0deg)');
});

test('genre details and settings headers resolve their own chevrons', () => {
  for (const details of [true, false]) {
    const arrow = { style: {} };
    const trigger = { dataset: { target: 'settings' }, getAttribute: () => null, querySelector: () => arrow };
    const panel = details
      ? { id: '', parentElement: { matches: () => true, querySelector: () => trigger } }
      : { id: 'settings', ownerDocument: { querySelectorAll: () => [trigger] } };
    const motion = createDropdownArrowMotion(() => { throw Error('Reduced motion must not animate'); }, () => true);
    motion.play(panel, true, dropdownTiming(true));
    assert.equal(arrow.style.transform, 'rotate(-180deg)');
  }
});
