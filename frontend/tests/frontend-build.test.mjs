import assert from 'node:assert/strict';
import test from 'node:test';
import {guardFrontendBuild, routeAssetUnavailable} from '../src/lib/frontend-build.ts';

const doc = build => ({querySelector: () => build ? {content: build} : null});

test('navigation keeps same-build swaps and falls back for a different build', async () => {
  for (const [incoming, canceled] of [['current', false], ['next', true], [undefined, false]]) {
    const event = new Event('astro:before-preparation', {cancelable: true});
    event.signal = new AbortController().signal;
    event.loader = async () => { event.newDocument = doc(incoming); };
    guardFrontendBuild(event, doc('current'));
    await event.loader();
    assert.equal(event.defaultPrevented, canceled);
  }
});

test('a version change never replays a form submission or revives a superseded selection', async () => {
  for (const formData of [new FormData(), undefined]) {
    const event = new Event('astro:before-preparation', {cancelable: true});
    event.signal = formData ? new AbortController().signal : AbortSignal.abort();
    event.formData = formData;
    event.loader = async () => { event.newDocument = doc('next'); };
    guardFrontendBuild(event, doc('current'));
    await event.loader();
    assert.equal(event.defaultPrevented, false);
  }
});

test('module and preload failures recover separately from data or initialization errors', () => {
  for (const message of ['Failed to fetch dynamically imported module: /_astro/game-list.old.js', 'error loading dynamically imported module', 'Importing a module script failed.', 'Unable to preload CSS for /_astro/style.css']) {
    assert.equal(routeAssetUnavailable(new TypeError(message)), true);
  }
  assert.equal(routeAssetUnavailable(new TypeError('Failed to fetch')), false);
  assert.equal(routeAssetUnavailable(new Error('Unable to load this section.')), false);
});
