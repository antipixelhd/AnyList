import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { transpile, ScriptTarget } from 'typescript';
import test from 'node:test';
import { dropdownTiming } from '../src/lib/dropdown-arrow-motion.ts';

function fixture() {
  const animations = [], rotations = [];
  const source = readFileSync(new URL('../src/lib/ui-motion.ts', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '').replace(/\bexport /g, '');
  const context = {
    dropdownTiming,
    matchMedia: () => ({ matches: false }),
    createDropdownArrowMotion: () => ({ play: (...args) => rotations.push(args), cancel() {} }),
    animate: (element, values, timing) => {
      const control = { stop() {}, cancel() {}, finished: new Promise(() => {}) };
      animations.push({ element, values, timing });
      return control;
    },
  };
  runInNewContext(transpile(source, { target: ScriptTarget.ES2022 }) + '\nthis.api = { showMenu, hideMenu, reveal };', context);
  const properties = new Map();
  const panel = {
    hidden: true, inert: true,
    getClientRects: () => [{}],
    hasAttribute: () => false,
    style: { removeProperty: name => properties.delete(name) },
  };
  return { ...context.api, animations, rotations, panel };
}

test('initial menu visibility settles without scheduling an entrance animation', () => {
  const f = fixture();
  f.showMenu(f.panel, true);
  assert.equal(f.panel.hidden, false);
  assert.equal(f.panel.inert, false);
  assert.equal(f.animations.length, 0);
  assert.equal(f.rotations[0][2].duration, 0);
});

test('user dropdown interactions share their slower duration with the arrow', () => {
  const f = fixture();
  f.showMenu(f.panel);
  assert.equal(f.animations[0].timing.duration, .26);
  assert.equal(f.rotations[0][2], f.animations[0].timing);
  void f.hideMenu(f.panel);
  assert.equal(f.animations[1].timing.duration, .18);
  assert.equal(f.rotations[1][2], f.animations[1].timing);
});

test('ordinary content reveals keep their timing and never rotate dropdown arrows', () => {
  const f = fixture();
  f.reveal(f.panel);
  assert.equal(f.rotations.length, 0);
  assert.equal(f.animations[0].timing.duration, .18);
});

test('native dropdown disclosures still synchronize their chevrons', () => {
  const f = fixture();
  f.panel.parentElement = { matches: () => true };
  f.reveal(f.panel);
  assert.equal(f.animations[0].timing.duration, .26);
  assert.equal(f.rotations[0][2], f.animations[0].timing);
});
