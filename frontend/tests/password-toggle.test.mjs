import assert from 'node:assert/strict';
import test from 'node:test';
import { wirePasswordToggles } from '../src/lib/password-toggle.ts';

class Target {
  constructor(toggle = null) { this.toggle = toggle; }
  closest(selector) {
    return this.toggle && selector.split(', ').includes(`.${this.toggle.marker}`) ? this.toggle : null;
  }
}

function page(t) {
  globalThis.Element = Target;
  t.after(() => delete globalThis.Element);
  const listeners = [];
  return {
    listeners,
    addEventListener(type, callback, options) { listeners.push({ type, callback, options }); },
    click(target) { for (const listener of listeners) listener.callback({ target }); },
  };
}

function control(marker = 'password-toggle') {
  const input = { type: 'password' };
  const eye = new Set(), slash = new Set(['hidden']);
  const icon = classes => ({ classList: { toggle(name, active) { if (active) classes.add(name); else classes.delete(name); } } });
  const toggle = {
    marker, parentElement: { querySelector: () => input },
    querySelector: selector => icon(selector === '.eye-icon' ? eye : slash),
  };
  return { input, eye, slash, target: new Target(toggle), toggle };
}

test('nested icon clicks reveal and conceal passwords with matching icon states', t => {
  const root = page(t), button = control();
  wirePasswordToggles(root);
  root.click(button.target);
  assert.equal(button.input.type, 'text');
  assert.equal(button.eye.has('hidden'), true);
  assert.equal(button.slash.has('hidden'), false);
  root.click(button.target);
  assert.equal(button.input.type, 'password');
  assert.equal(button.eye.has('hidden'), false);
  assert.equal(button.slash.has('hidden'), true);
});

test('repeated layout initialization installs one capture listener and navigation needs no rebinding', t => {
  const root = page(t);
  wirePasswordToggles(root);
  wirePasswordToggles(root);
  assert.equal(root.listeners.length, 1);
  assert.deepEqual(root.listeners[0].options, { capture: true });
  const first = control(), replacement = control('metadata-password-toggle');
  root.click(first.target);
  root.click(replacement.target);
  assert.equal(first.input.type, 'text');
  assert.equal(replacement.input.type, 'text');
});

test('unrelated clicks and incomplete controls are ignored', t => {
  const root = page(t), button = control('unrelated-button');
  wirePasswordToggles(root);
  root.click(button.target);
  root.click(null);
  root.click(new Target({ marker: 'password-toggle', parentElement: null }));
  assert.equal(button.input.type, 'password');
});
