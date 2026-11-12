import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import test from 'node:test';

const source = stripTypeScriptTypes(readFileSync(new URL('../src/lib/poster-controls.ts', import.meta.url), 'utf8')).replace('export function', 'function');
function fixture() {
  const document = new EventTarget();
  const page = new AbortController();
  const classes = new Set(['is-touch-active']);
  const card = {dataset:{}, classList:{remove:name=>classes.delete(name)}};
  const context = {document, AbortController, card, page};
  runInNewContext(source + '\ndismissPosterControls({closest:()=>card}, page.signal);', context);
  const dispatch = (type, props={}) => document.dispatchEvent(Object.assign(new Event(type), props));
  return {card, classes, page, dispatch};
}
test('closing dismisses touch activation and ignores stationary pointer events', () => {
  const f = fixture();
  assert.equal(f.classes.has('is-touch-active'), false);
  f.dispatch('pointermove', {pointerType:'mouse', movementX:0, movementY:0});
  assert.equal(f.card.dataset.controlsDismissed, '');
  f.dispatch('pointermove', {pointerType:'mouse', movementX:1, movementY:0});
  assert.equal(f.card.dataset.controlsDismissed, undefined);
});
test('fresh touch and keyboard input restore access; navigation cleans up dismissal', () => {
  for (const type of ['pointerdown', 'keydown', 'abort']) {
    const f = fixture();
    if (type === 'abort') f.page.abort(); else f.dispatch(type);
    assert.equal(f.card.dataset.controlsDismissed, undefined);
  }
});
